from types import SimpleNamespace
from unittest import TestCase

from django.core.exceptions import PermissionDenied, ValidationError
from rest_framework.authentication import SessionAuthentication

from destination_approvals.api.serializers import DecisionSerializer, SubmitSerializer
from destination_approvals.api.views import WriteEnabled
from destination_approvals.workflow import Conflict, check_decision, check_submission, fingerprint, my_approvals
from .test_delta import policy, review


class WorkflowTests(TestCase):
    def setUp(self):
        self.data = policy()
        self.review = review(self.data, "pending")
        self.review.decisions = {}
        self.mine = ["linux-team"]

    def test_fingerprint_stable_for_dictionary_order(self):
        self.assertEqual(fingerprint(self.data), fingerprint(dict(reversed(list(self.data.items())))))

    def test_submit_first_version(self):
        check_submission(None, self.data, None, fingerprint(self.data))

    def test_submit_conflict_wrong_hash_or_review(self):
        for rid, digest in ((999, fingerprint(self.data)), (None, "0" * 64)):
            with self.assertRaises(Conflict):
                check_submission(None, self.data, rid, digest)

    def test_submit_cannot_duplicate_pending(self):
        with self.assertRaises(Conflict):
            check_submission(self.review, self.data, self.review.pk, fingerprint(self.data))

    def test_submit_changed_version(self):
        new = dict(self.data, risk_level="high")
        check_submission(self.review, new, self.review.pk, fingerprint(new))

    def test_decision_allowed_for_own_scope(self):
        check_decision(self.review, self.data, self.mine, self.review.pk, "linux-team", "approve")

    def test_decision_wrong_team(self):
        with self.assertRaises(PermissionDenied):
            check_decision(self.review, self.data, self.mine, self.review.pk, "risk:medium", "approve")

    def test_stale_review_and_changed_snapshot(self):
        with self.assertRaises(Conflict):
            check_decision(self.review, self.data, self.mine, 999, "linux-team", "approve")
        with self.assertRaises(Conflict):
            check_decision(self.review, dict(self.data, risk_level="high"), self.mine,
                           self.review.pk, "linux-team", "approve")

    def test_repeated_or_closed_decision(self):
        self.review.decisions["linux-team"] = {"action": "approve"}
        with self.assertRaises(Conflict):
            check_decision(self.review, self.data, self.mine, self.review.pk, "linux-team", "deny")
        self.review.decisions = {}
        self.review.state = "denied"
        with self.assertRaises(Conflict):
            check_decision(self.review, self.data, self.mine, self.review.pk, "linux-team", "approve")

    def test_invalid_action(self):
        with self.assertRaises(ValidationError):
            check_decision(self.review, self.data, self.mine, self.review.pk, "linux-team", "approved")

    def test_membership_uses_group_id(self):
        self.assertEqual(my_approvals(self.data, {"1"}), ["linux-team"])
        self.assertEqual(my_approvals(self.data, {"unknown"}), [])

    def test_serializer_rejects_impersonation_fields(self):
        serializer = DecisionSerializer(data={"review_id": 42, "team": "linux-team", "action": "approve", "by": "admin"})
        self.assertFalse(serializer.is_valid())

    def test_serializer_requires_version(self):
        self.assertFalse(DecisionSerializer(data={"team": "linux-team", "action": "approve"}).is_valid())
        self.assertFalse(SubmitSerializer(data={"expected_review_id": None, "snapshot_hash": "bad"}).is_valid())

    def test_serializer_valid(self):
        self.assertTrue(DecisionSerializer(data={"review_id": 42, "team": "linux-team", "action": "deny", "comment": "test"}).is_valid())
        self.assertTrue(SubmitSerializer(data={"expected_review_id": None, "snapshot_hash": fingerprint(self.data)}).is_valid())

    def test_token_write_permission(self):
        permission = WriteEnabled()
        for token, allowed in ((SimpleNamespace(write_enabled=True), True),
                               (SimpleNamespace(write_enabled=False), False),
                               (object(), False)):
            self.assertEqual(permission.has_permission(SimpleNamespace(auth=token), None), allowed)
        self.assertTrue(permission.has_permission(SimpleNamespace(auth=None, successful_authenticator=SessionAuthentication()), None))
        self.assertFalse(permission.has_permission(SimpleNamespace(auth=None, successful_authenticator=None), None))
