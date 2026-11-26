"""Shared mutation boundary for GUI and REST approval operations."""
import hashlib
import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from nautobot_firewall_models.models import PolicyRule

from .delta import all_approved, carry_approvals
from .models import Review
from .routing import snapshot
from .status import current_state


class Conflict(Exception):
    """The caller must reload the current revision before retrying."""


def fingerprint(data):
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def readable_rules(user):
    return PolicyRule.objects.restrict(user, "view").filter(request_id__startswith="LAB-FLOW-")


def group_ids(user):
    return {str(gid) for gid in user.groups.values_list("pk", flat=True)}


def my_approvals(data, groups):
    return [team for team, gid in data["approvers"].items() if gid in groups]


def locked_context(user, pk):
    # Avoid locking unrelated/inaccessible rules. Recheck permission after lock.
    get_object_or_404(readable_rules(user), pk=pk)
    rule = get_object_or_404(PolicyRule.objects.select_for_update(), pk=pk)
    get_object_or_404(readable_rules(user), pk=pk)
    data = snapshot(rule)
    mine = my_approvals(data, group_ids(user))
    if not mine:
        raise PermissionDenied("Du är inte godkännare för regeln.")
    review = Review.objects.filter(rule_id=pk).first()
    return data, mine, review


def check_submission(current, data, expected_review_id, expected_snapshot):
    if (current.pk if current else None) != expected_review_id or fingerprint(data) != expected_snapshot:
        raise Conflict("Regeln eller granskningen har ändrats. Läs om aktuell version.")
    if current_state(current, data) not in ("draft", "denied", "superseded"):
        raise Conflict("Den aktuella versionen är redan inskickad.")


def check_decision(current, data, mine, review_id, team, action):
    if action not in ("approve", "deny"):
        raise ValidationError("Ogiltigt beslut.")
    if team not in mine:
        raise PermissionDenied("Du saknar behörighet för detta godkännande.")
    if (not current or current.pk != review_id or current_state(current, data) != "pending"
            or team in current.decisions):
        raise Conflict("Beslutet gäller en tidigare eller redan hanterad beställning.")


@transaction.atomic
def submit(user, pk, expected_review_id, expected_snapshot):
    data, mine, current = locked_context(user, pk)
    check_submission(current, data, expected_review_id, expected_snapshot)
    carried = carry_approvals(current, data)
    if current and current_state(current, data) == "superseded" and current.state != "superseded":
        current.state = "superseded"
        current.save(update_fields=["state"])
    return Review.objects.create(
        rule_id=pk, snapshot=data, decisions=carried,
        state="approved" if all_approved(data, carried) else "pending",
        submitted_by=user.get_username(),
    )


@transaction.atomic
def decide(user, pk, review_id, team, action, comment="", channel="gui"):
    if not isinstance(comment, str) or len(comment) > 2000:
        raise ValidationError("Kommentar får vara högst 2000 tecken.")
    data, mine, current = locked_context(user, pk)
    check_decision(current, data, mine, review_id, team, action)
    current.decisions[team] = {
        "action": action, "by": user.get_username(), "user_id": str(user.pk),
        "at": timezone.now().isoformat(), "channel": channel, "comment": comment,
    }
    if action == "deny":
        current.state = "denied"
    elif all_approved(data, current.decisions):
        current.state = "approved"
    current.save(update_fields=["decisions", "state"])
    return current
