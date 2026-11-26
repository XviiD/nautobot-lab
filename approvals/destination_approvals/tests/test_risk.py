from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from destination_approvals.risk import add_risk_approver, risk_level, risk_visibility
from destination_approvals.status import current_state


class RiskTests(TestCase):
    def setUp(self):
        mapping = patch("destination_approvals.risk.risk_groups", return_value={
            "medium": "Network Approvers", "high": "Security Approvers"
        })
        mapping.start()
        self.addCleanup(mapping.stop)
        self.groups = Mock()
        self.groups.objects.filter.return_value.first.return_value = SimpleNamespace(pk="risk-id")

    def test_required_classification(self):
        for level in ("low", "medium", "high"):
            self.assertEqual(risk_level(SimpleNamespace(cf={"risk_level": level})), level)
        for level in (None, "", "HIGH", "unknown", ["medium", "high"]):
            with self.assertRaises(ValidationError):
                risk_level(SimpleNamespace(cf={"risk_level": level}))

    def test_low(self):
        approvers = {"linux-team": "linux-id"}
        add_risk_approver(approvers, "low", self.groups)
        self.groups.objects.filter.assert_not_called()
        self.assertEqual(approvers, {"linux-team": "linux-id"})

    def test_medium_and_high(self):
        for level, name in (("medium", "Network Approvers"), ("high", "Security Approvers")):
            approvers = {"linux-team": "linux-id"}
            add_risk_approver(approvers, level, self.groups)
            self.groups.objects.filter.assert_called_with(name=name)
            self.assertEqual(approvers, {"linux-team": "linux-id", f"risk:{level}": "risk-id"})

    def test_same_destination_group(self):
        approvers = {"network-team": "risk-id"}
        add_risk_approver(approvers, "medium", self.groups)
        self.assertEqual(approvers, {"network-team": "risk-id"})

    def test_missing_group_blocks(self):
        self.groups.objects.filter.return_value.first.return_value = None
        with self.assertRaises(ValidationError):
            add_risk_approver({}, "high", self.groups)

    def test_queue_filter(self):
        for group, levels in (("Network Approvers", ["medium"]), ("Security Approvers", ["high"]), ("Linux Approvers", [])):
            self.assertEqual(risk_visibility({group}).children, [("_custom_field_data__risk_level__in", levels)])

    def test_state_requires_all_positive_decisions_and_current_snapshot(self):
        data = {"risk_level": "medium", "approvers": {"linux-team": "1", "risk:medium": "2"}}
        self.assertEqual(current_state(None, data), "draft")
        review = SimpleNamespace(snapshot=data, state="pending",
                                 decisions={"linux-team": {"action": "approve"}})
        self.assertEqual(current_state(review, data), "pending")
        review.state = "approved"
        self.assertEqual(current_state(review, data), "superseded")
        review.decisions["risk:medium"] = {"action": "approve"}
        self.assertEqual(current_state(review, data), "approved")
        self.assertEqual(current_state(review, dict(data, risk_level="high")), "superseded")
        review.decisions["risk:medium"] = {"action": "deny"}
        self.assertEqual(current_state(review, data), "superseded")
