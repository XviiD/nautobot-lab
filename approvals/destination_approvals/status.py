"""Shared approval state for the UI and deployment API."""


def current_state(review, data):
    if review is None:
        return "draft"
    if review.snapshot != data:
        return "superseded"
    if review.state == "approved" and (
        not data["approvers"]
        or any(review.decisions.get(key, {}).get("action") != "approve"
               for key in data["approvers"])
    ):
        return "superseded"
    return review.state
