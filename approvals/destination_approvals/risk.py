"""Risk routing for a PolicyRule, using its risk_level custom field."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q

RISK_LEVELS = ("low", "medium", "high")
REVIEW_LEVELS = ("medium", "high")


def risk_groups():
    return settings.PLUGINS_CONFIG["destination_approvals"]["risk_groups"]


def risk_level(rule):
    value = rule.cf.get("risk_level")
    if value not in RISK_LEVELS:
        raise ValidationError("Regeln kräver giltig riskklass (low, medium, high).")
    return value


def add_risk_approver(approvers, level, group_model):
    if level == "low":
        return
    if level not in REVIEW_LEVELS:
        raise ValidationError("Ogiltig riskklass.")
    name = risk_groups().get(level)
    group = group_model.objects.filter(name=name).first() if name else None
    if group is None:
        raise ValidationError(f"Godkännargrupp saknas för riskklass {level}.")
    gid = str(group.pk)
    # Destination ownership and risk are separate approval scopes, even if
    # the same group is responsible for both.
    approvers[f"risk:{level}"] = gid


def risk_visibility(group_names):
    levels = [level for level in REVIEW_LEVELS if risk_groups().get(level) in group_names]
    return Q(_custom_field_data__risk_level__in=levels)
