"""Import the Git checkout's firewall inventory into Nautobot."""
from collections import Counter
from ipaddress import ip_interface, ip_network
from pathlib import Path

import yaml
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from nautobot.apps.jobs import BooleanVar, Job, register_jobs
from nautobot.extras.models import Status, Tag
from nautobot.ipam.models import IPAddress, Namespace, Prefix
from nautobot_firewall_models.models import AddressObject, AddressObjectGroup, PolicyRule, ServiceObject


SOURCE = Path(__file__).resolve().parents[1] / "data" / "firewall-objects.yaml"


def load_inventory(path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data["schema_version"] != 1 or data["namespace"] != "Firewall-Lab":
        raise ValueError("Expected schema_version 1 and namespace Firewall-Lab.")
    names, values = set(), set()
    for row in data["objects"]:
        fields = set(row) & {"address", "prefix"}
        if len(fields) != 1 or not row["owner_team"].endswith("-team"):
            raise ValueError(f"Invalid address type or owner for {row['name']}")
        field = fields.pop()
        value = ip_network(row[field]) if field == "prefix" else ip_interface(row[field])
        if field == "address" and value.network.prefixlen != value.max_prefixlen:
            raise ValueError(f"Expected a host address: {value}")
        key = (field, str(value))
        if key in values or row["name"] in names:
            raise ValueError(f"Duplicate object: {row}")
        values.add(key)
        names.add(row["name"])
    group_names = set()
    for row in data["address_groups"]:
        members = row["members"]
        if (row["name"] in group_names or not isinstance(members, list)
                or not members or len(members) != len(set(members)) or set(members) - names):
            raise ValueError(f"Invalid group or member reference: {row['name']}")
        group_names.add(row["name"])
    return data


def ensure(model, lookup, defaults, counts):
    try:
        return model.objects.get(**lookup)
    except model.DoesNotExist:
        obj = model(**lookup, **defaults)
        obj.validated_save()
        counts[f"{model.__name__} created"] += 1
        return obj


def sync_inventory(data):
    counts = Counter()
    ns = ensure(Namespace, {"name": data["namespace"]}, {}, counts)
    ns = Namespace.objects.select_for_update().get(pk=ns.pk)
    active = Status.objects.get(name="Active")
    marker = f"Git inventory: {ns.name}/firewall-objects.yaml"
    tags = {}
    for name in sorted({row["owner_team"] for row in data["objects"]}):
        tag = ensure(Tag, {"name": name}, {}, counts)
        tag.content_types.add(*ContentType.objects.get_for_models(IPAddress, Prefix, AddressObject).values())
        tags[name] = tag

    objects = {}
    for row in sorted(data["objects"], key=lambda item: "address" in item):
        if "prefix" in row:
            cidr = ip_network(row["prefix"])
            lookup = dict(namespace=ns, network=str(cidr.network_address), prefix_length=cidr.prefixlen)
            ipam = ensure(Prefix, lookup, {"status": active, "prefix": str(cidr), "type": "network"}, counts)
            field = "prefix"
        else:
            address = ip_interface(row["address"])
            ipam = IPAddress.objects.filter(parent__namespace=ns, host=str(address.ip)).first()
            if ipam is None:
                ipam = IPAddress(address=str(address), namespace=ns, status=active)
                ipam.validated_save()
                counts["IPAddress created"] += 1
            elif ipam.mask_length != address.network.prefixlen:
                raise ValueError(f"Host mask conflict: {row['name']}")
            field = "ip_address"

        obj = ensure(AddressObject, {"name": row["name"]},
                     {field: ipam, "status": active, "description": marker}, counts)
        if obj.description != marker or getattr(obj, field + "_id") != ipam.pk:
            raise ValueError(f"AddressObject name or target conflict: {obj.name}")

        owner = tags[row["owner_team"]]
        for target, counter in ((ipam, "IPAM ownership updated"), (obj, "AddressObject ownership updated")):
            existing = set(target.tags.filter(name__endswith="-team").values_list("name", flat=True))
            if existing != {owner.name}:
                for old_name in existing - {owner.name}:
                    target.tags.remove(old_name)
                target.tags.add(owner)
                counts[counter] += 1
        objects[obj.name] = obj.pk

    for row in data["address_groups"]:
        group = ensure(AddressObjectGroup, {"name": row["name"]},
                       {"description": marker, "status": active}, counts)
        if group.description != marker:
            raise ValueError(f"AddressObjectGroup name conflict: {group.name}")
        desired = {objects[name] for name in row["members"]}
        if desired != set(group.address_objects.values_list("pk", flat=True)):
            group.address_objects.set(desired)
            counts["Group membership updated"] += 1
    sync_test_rules(objects, data["address_groups"], active, marker, counts)
    return counts


def choice(model, field, token):
    for value, label in model._meta.get_field(field).flatchoices:
        if token.casefold() in (str(value).casefold(), str(label).casefold()):
            return value
    raise ValueError(f"Unsupported {model.__name__}.{field} choice: {token}")


def sync_test_rules(objects, group_rows, active, marker, counts):
    source = SOURCE.with_name("firewall-rules.yaml")
    if not source.exists():
        return
    spec = yaml.safe_load(source.read_text(encoding="utf-8"))
    groups = {row["name"]: AddressObjectGroup.objects.get(name=row["name"], description=marker).pk
              for row in group_rows}
    services = {}
    for name, row in spec["services"].items():
        protocol = choice(ServiceObject, "ip_protocol", row["protocol"])
        port = str(row["port"])
        service = ensure(ServiceObject, {"name": name}, {
            "ip_protocol": protocol, "port": port, "description": marker, "status": active
        }, counts)
        if service.description != marker or str(service.port) != port or service.ip_protocol != protocol:
            raise ValueError(f"Service name or definition conflict: {name}")
        services[name] = service.pk

    seen = set()
    for row in spec["rules"]:
        ref = row["id"]
        if not ref.startswith("LAB-FLOW-") or ref in seen:
            raise ValueError(f"Invalid or duplicate rule ID: {ref}")
        seen.add(ref)
        destinations = {objects[name] for name in row.get("destinations", [])}
        dest_groups = {groups[name] for name in row.get("destination_groups", [])}
        if not destinations and not dest_groups:
            raise ValueError(f"Missing destination: {ref}")
        fields = {"name": row["name"], "action": choice(PolicyRule, "action", "ALLOW"),
                  "status": active, "log": True}
        rule = ensure(PolicyRule, {"request_id": ref}, fields, counts)
        changed = [key for key, value in fields.items() if getattr(rule, key) != value]
        if changed:
            for key in changed:
                setattr(rule, key, fields[key])
            rule.validated_save()
            counts["PolicyRule updated"] += 1
        desired = {"source_address": {objects[row["source"]]}, "destination_address": destinations,
                   "destination_address_group": dest_groups, "service": {services[row["service"]]}}
        for field, member_ids in desired.items():
            relation = getattr(rule, field)
            if set(relation.values_list("pk", flat=True)) != member_ids:
                relation.set(member_ids)
                counts["PolicyRule relations updated"] += 1


class ImportFirewallObjects(Job):
    class Meta:
        name = "Import firewall objects from Git"

    dry_run = BooleanVar(default=True, label="Provläge (rulla tillbaka ändringar)")

    def run(self, dry_run=True):
        data = load_inventory(SOURCE)
        with transaction.atomic():
            counts = sync_inventory(data)
            if dry_run:
                transaction.set_rollback(True)
        self.logger.info("%s: %s", "Provläge" if dry_run else "Import",
                         dict(counts) or "Inga objektändringar")


register_jobs(ImportFirewallObjects)
