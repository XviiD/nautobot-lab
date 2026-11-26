from django.conf import settings
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model

from .risk import add_risk_approver, risk_level


def address_record(obj):
    if obj.ip_address_id:
        target = obj.ip_address
        network = str(target.address)
        namespace = target.parent.namespace
    elif obj.prefix_id:
        target = obj.prefix
        network = str(target.prefix)
        namespace = target.namespace
    else:
        raise ValidationError(f"{obj.name}: endast hostar och prefix stöds i labbet.")
    if namespace.name != "Firewall-Lab":
        raise ValidationError("Adressen ligger utanför Firewall-Lab.")
    return {"id": str(obj.pk), "target": str(target.pk), "name": obj.name, "address": network}


def snapshot(rule):
    if str(rule.action).upper() != "ALLOW":
        raise ValidationError("Labbet stöder ALLOW-regler.")
    for field in ("source_users", "source_user_groups", "applications", "application_groups",
                  "source_services", "source_service_groups", "destination_service_groups"):
        if getattr(rule, field).exists():
            raise ValidationError(f"Manuell granskning krävs för {field}.")
    if rule.source_zone_id or rule.destination_zone_id:
        raise ValidationError("Manuell granskning krävs för zoner.")

    result = {"name": rule.name, "request": rule.request_id, "action": rule.action,
              "log": rule.log, "status": str(rule.status_id)}
    result["risk_level"] = risk_level(rule)
    teams = set()
    for side in ("source", "destination"):
        rows = []
        def collect(obj, group):
            row = address_record(obj)
            row["group"] = group
            if side == "destination":
                owners = list(obj.tags.filter(name__endswith="-team").values_list("name", flat=True))
                if len(owners) != 1:
                    raise ValidationError(f"{obj.name}: exakt en ägartagg krävs.")
                row["owner"] = owners[0]
                teams.add(owners[0])
            rows.append(row)

        for obj in getattr(rule, side + "_addresses").all():
            collect(obj, "")
        for group in getattr(rule, side + "_address_groups").all():
            members = list(group.address_objects.all())
            if not members:
                owners = list(group.tags.filter(name__endswith="-team").values_list("name", flat=True))
                if len(owners) != 1:
                    raise ValidationError(f"{group.name}: en tom grupp kräver exakt en ägartagg.")
                rows.append({"id": str(group.pk), "group": str(group.pk), "name": group.name,
                             "kind": "external_group", "owner": owners[0]})
                if side == "destination":
                    teams.add(owners[0])
            for obj in members:
                collect(obj, str(group.pk))
        if not rows:
            raise ValidationError(f"Tomt adressurval eller Any: {side}")
        result[side] = sorted(rows, key=lambda row: (row["group"], row["id"]))

    mapping = settings.PLUGINS_CONFIG["destination_approvals"]["team_groups"]
    group_model = get_user_model()._meta.get_field("groups").remote_field.model
    result["approvers"] = {}
    for team in sorted(teams):
        group = group_model.objects.filter(name=mapping.get(team)).first()
        if not group:
            raise ValidationError(f"Godkännargrupp saknas för {team}.")
        result["approvers"][team] = str(group.pk)
    add_risk_approver(result["approvers"], result["risk_level"], group_model)
    result["services"] = [
        {"id": str(s.pk), "name": s.name, "protocol": str(s.ip_protocol), "port": str(s.port)}
        for s in rule.destination_services.order_by("pk")
    ]
    if not result["services"]:
        raise ValidationError("En explicit destinationstjänst krävs.")
    return result
