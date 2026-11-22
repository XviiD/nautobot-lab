"""Read installed firmware from locally managed Check Point Spark appliances."""
import base64
import os
import re
from ipaddress import ip_interface

import requests
from django.db import transaction
from nautobot.apps.jobs import BooleanVar, Job, JobHookReceiver, ObjectVar
from nautobot.dcim.models import Device, SoftwareVersion
from nautobot.extras.models import CustomField, Status

PLATFORM = "Check Point Gaia Embedded"
SKIP_TLS_FIELD = "spark_skip_tls_verify"
BUILD_FIELD = "spark_firmware_build"
INPUT_FIELDS = {"platform", "secrets_group", "primary_ip4", "primary_ip6"}


def read_firmware(device, address):
    host = f"[{address}]" if address.version == 6 else str(address)
    base = f"https://{host}:4434/web-api"
    group = device.secrets_group
    credentials = {
        "user": group.get_secret_value("REST", "username", obj=device),
        "password": group.get_secret_value("REST", "password", obj=device),
    }
    with requests.Session() as session:
        session.trust_env = False
        session.verify = os.environ.get("SPARK_CA_BUNDLE") or True
        if device.cf.get(SKIP_TLS_FIELD) is True:
            session.verify = False
        session.headers["Content-Type"] = "application/json"

        def post(command, payload=None):
            response = session.post(f"{base}/{command}", json=payload, timeout=(5, 20), allow_redirects=False)
            if response.status_code != 200:
                raise RuntimeError(f"Spark {command} returned HTTP {response.status_code}")
            return response

        session.headers["x-chkp-sid"] = post("login", credentials).json()["sid"]
        try:
            script = base64.b64encode(b"show software-version").decode("ascii")
            encoded = post("run-clish-command", {"script": script}).json()["output"]
            output = base64.b64decode("".join(encoded.split()), validate=True).decode("utf-8")
        finally:
            post("logout")
    match = re.search(r"\b(R\d+(?:\.\d+)+)\b[\s-]*Build\s*[:#-]?\s*(\d+)\b", output, re.I)
    if not match:
        raise ValueError("Kunde inte tolka release och build i svaret från show software-version.")
    return match[1].upper(), match[2]


def sync(device, logger, dry_run):
    device.refresh_from_db()
    if not device.platform_id or device.platform.name != PLATFORM:
        raise ValueError(f"Välj en enhet med plattformen {PLATFORM}.")
    if not device.primary_ip or not device.secrets_group_id:
        raise ValueError("Enheten behöver Primary IP och Secrets Group.")
    address = ip_interface(str(device.primary_ip.address)).ip
    version, build = read_firmware(device, address)
    logger.info("%s kör %s, build %s.", device, version, build)
    if dry_run:
        return
    with transaction.atomic():
        current = Device.objects.select_for_update().get(pk=device.pk)
        if (current.platform_id != device.platform_id or current.secrets_group_id != device.secrets_group_id
                or not current.primary_ip or ip_interface(str(current.primary_ip.address)).ip != address):
            raise ValueError("Enhetens anslutningsuppgifter ändrades under läsningen. Kör igen.")
        software, _ = SoftwareVersion.objects.get_or_create(
            platform=current.platform, version=version,
            defaults={"status": lambda: Status.objects.get(name="Active", content_types__app_label="dcim", content_types__model="softwareversion")},
        )
        changed = current.software_version_id != software.pk
        current.software_version = software
        if CustomField.objects.get_for_model(current).filter(key=BUILD_FIELD).exists():
            if current.cf.get(BUILD_FIELD) != build:
                current.cf[BUILD_FIELD] = build
                changed = True
        if changed:
            current.validated_save()
        logger.info("Uppdaterad." if changed else "Redan aktuell.")


class ReadSparkFirmware(Job):
    device = ObjectVar(model=Device)
    dry_run = BooleanVar(default=True, label="Provläge")

    class Meta:
        name = "Läs firmware från Quantum Spark"

    def run(self, device, dry_run=True):
        sync(device, self.logger, dry_run)


class SparkFirmwareHook(JobHookReceiver):
    class Meta:
        name = "Inventera ny eller ändrad Quantum Spark"

    def receive_job_hook(self, change, action, changed_object):
        if action not in ("create", "update") or not isinstance(changed_object, Device):
            return
        if not changed_object.platform_id or changed_object.platform.name != PLATFORM:
            return
        if not changed_object.primary_ip or not changed_object.secrets_group_id:
            self.logger.info("Väntar på Primary IP och Secrets Group.")
            return
        if action == "update":
            snapshots = change.get_snapshots()
            if snapshots["prechange"] and not INPUT_FIELDS.intersection(snapshots["differences"]["added"] or {}):
                return
        sync(changed_object, self.logger, False)
