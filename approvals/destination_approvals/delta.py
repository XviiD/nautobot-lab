"""Compare approval scopes, not just the whole policy snapshot.

Only the immediately preceding submission may supply carried approvals.
Each new submission still stores the complete current policy snapshot.
"""
from .status import current_state

# Request identity, shared source/services and enforcement settings apply to
# every destination. The display name and risk level are not destination scope.
SHARED_FIELDS = ("request", "action", "log", "status", "source", "services")
FIELD_LABELS = {
    "group": "Godkännargrupp",
    "request": "Beställnings-ID",
    "action": "Åtgärd",
    "log": "Loggning",
    "status": "Regelstatus",
    "source": "Källa",
    "services": "Tjänster",
    "destination": "Destination",
    "risk_level": "Riskklass",
}


def approval_scope(data, key):
    if key not in data["approvers"]:
        return None
    # Incomplete historic snapshots must never authorize a new submission.
    if any(field not in data for field in (*SHARED_FIELDS, "destination")):
        return None
    scope = {field: data[field] for field in SHARED_FIELDS}
    scope["group"] = data["approvers"][key]
    if key.startswith("risk:"):
        if "risk_level" not in data:
            return None
        scope["risk_level"] = data["risk_level"]
        scope["destination"] = data["destination"]
    else:
        scope["destination"] = [row for row in data["destination"] if row.get("owner") == key]
        if not scope["destination"]:
            return None
    return scope


def carry_approvals(previous, data):
    """Keep positive decisions only when their individual scope is unchanged."""
    if previous is None:
        return {}
    carried = {}
    for key in data["approvers"]:
        decision = previous.decisions.get(key, {})
        if decision.get("action") != "approve":
            continue
        old_scope = approval_scope(previous.snapshot, key)
        if old_scope is not None and old_scope == approval_scope(data, key):
            carried[key] = {
                **decision,
                "carried_from_review": previous.pk,
                "origin_review": decision.get("origin_review", previous.pk),
            }
    return carried


def all_approved(data, decisions):
    return bool(data["approvers"]) and all(
        decisions.get(key, {}).get("action") == "approve" for key in data["approvers"]
    )


def decisions_for(review, data):
    if review is None:
        return {}
    if current_state(review, data) == "superseded":
        return carry_approvals(review, data)
    return review.decisions


def changed_fields(previous_data, data, key):
    """Human-readable delta for the relevant approval, without unrelated targets."""
    new = approval_scope(data, key)
    old = approval_scope(previous_data, key) if previous_data is not None else None
    if new is None:
        return []
    if old is None:
        return ["Nytt godkännandeansvar"]
    return [FIELD_LABELS[field] for field in new if new[field] != old.get(field)]
