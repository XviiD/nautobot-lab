from copy import deepcopy
from types import SimpleNamespace
from unittest import TestCase

from destination_approvals.delta import (
    all_approved, approval_scope, carry_approvals, changed_fields, decisions_for,
)
from destination_approvals.status import current_state


def policy():
    return {
        "name": "lab", "request": "LAB-FLOW-003", "action": "ALLOW",
        "log": True, "status": "active", "risk_level": "medium",
        "source": [{"id": "src", "address": "10.0.0.1/32"}],
        "destination": [
            {"id": "linux", "owner": "linux-team", "address": "10.0.1.1/32"},
            {"id": "db", "owner": "dba-team", "address": "10.0.2.1/32"},
        ],
        "services": [{"id": "https", "port": "443"}],
        "approvers": {"linux-team": "1", "dba-team": "2", "risk:medium": "3"},
    }


def review(data, state="approved"):
    return SimpleNamespace(pk=42, snapshot=deepcopy(data), state=state, decisions={
        key: {"action": "approve", "by": key, "at": "original"} for key in data["approvers"]
    })


class DeltaTests(TestCase):
    def setUp(self):
        self.data = policy()
        self.previous = review(self.data)

    def carried(self):
        return carry_approvals(self.previous, self.data)

    def test_risk_only(self):
        self.data["risk_level"] = "high"
        del self.data["approvers"]["risk:medium"]
        self.data["approvers"]["risk:high"] = "4"
        self.assertEqual(set(self.carried()), {"linux-team", "dba-team"})
        self.assertEqual(current_state(self.previous, self.data), "superseded")
        self.assertFalse(all_approved(self.data, self.carried()))
        self.assertEqual(changed_fields(self.previous.snapshot, self.data, "linux-team"), [])
        self.assertEqual(self.carried()["linux-team"]["origin_review"], 42)

    def test_low_to_medium(self):
        old = policy()
        old["risk_level"] = "low"
        del old["approvers"]["risk:medium"]
        self.previous = review(old)
        self.assertEqual(set(self.carried()), {"linux-team", "dba-team"})

    def test_destination_addition_only_impacts_owner_and_risk(self):
        self.data["destination"].append({"id": "linux2", "owner": "linux-team", "address": "10.0.1.2/32"})
        self.assertEqual(set(self.carried()), {"dba-team"})
        self.assertEqual(changed_fields(self.previous.snapshot, self.data, "linux-team"), ["Destination"])

    def test_new_team(self):
        self.data["destination"].append({"id": "win", "owner": "windows-team"})
        self.data["approvers"]["windows-team"] = "5"
        self.assertEqual(set(self.carried()), {"linux-team", "dba-team"})

    def test_remove_team(self):
        self.data["destination"].pop()
        del self.data["approvers"]["dba-team"]
        self.assertEqual(set(self.carried()), {"linux-team"})

    def test_source_service_or_enforcement_change(self):
        for field, value in (("source", []), ("services", []), ("log", False), ("status", "other"), ("action", "DENY"), ("request", "new")):
            with self.subTest(field=field):
                data = deepcopy(self.data)
                data[field] = value
                self.assertEqual(carry_approvals(self.previous, data), {})

    def test_group_mapping_change(self):
        self.data["approvers"]["linux-team"] = "new-group"
        self.assertEqual(set(self.carried()), {"dba-team", "risk:medium"})

    def test_rejected_decision_not_carried(self):
        self.previous.state = "denied"
        self.previous.decisions["dba-team"]["action"] = "deny"
        self.assertEqual(set(self.carried()), {"linux-team", "risk:medium"})

    def test_original_provenance_preserved(self):
        self.previous.decisions["linux-team"]["origin_review"] = 12
        self.assertEqual(self.carried()["linux-team"]["origin_review"], 12)
        self.assertEqual(self.carried()["linux-team"]["carried_from_review"], 42)
        self.assertNotIn("carried_from_review", self.previous.decisions["linux-team"])

    def test_only_display_name_change_all_carried_but_gate_stale_until_submit(self):
        self.data["name"] = "renamed"
        self.assertTrue(all_approved(self.data, self.carried()))
        self.assertEqual(current_state(self.previous, self.data), "superseded")
        new_review = review(self.data)
        new_review.decisions = self.carried()
        self.assertEqual(current_state(new_review, self.data), "approved")

    def test_risk_and_destination_same_group_have_distinct_scopes(self):
        self.data["approvers"]["risk:medium"] = self.data["approvers"]["linux-team"]
        self.assertNotEqual(approval_scope(self.data, "linux-team"), approval_scope(self.data, "risk:medium"))

    def test_no_old_risk_decision_in_legacy_deduplicated_review(self):
        self.previous.snapshot["approvers"].pop("risk:medium")
        self.previous.decisions.pop("risk:medium")
        self.assertEqual(set(self.carried()), {"linux-team", "dba-team"})

    def test_no_reuse_from_unsubmitted_or_older_versions(self):
        self.assertEqual(carry_approvals(None, self.data), {})
        self.previous.decisions.pop("linux-team")
        self.assertNotIn("linux-team", self.carried())

    def test_incomplete_snapshot_not_carried(self):
        del self.previous.snapshot["source"]
        self.assertEqual(self.carried(), {})

    def test_preview_keeps_valid_approvals_without_enabling_gate(self):
        self.data["risk_level"] = "high"
        del self.data["approvers"]["risk:medium"]
        self.data["approvers"]["risk:high"] = "4"
        self.assertEqual(set(decisions_for(self.previous, self.data)), {"linux-team", "dba-team"})
        self.assertEqual(current_state(self.previous, self.data), "superseded")
