"""Approval status and shared-workflow REST actions."""
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from django.utils.cache import add_never_cache_headers
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import Review
from ..routing import snapshot
from ..status import current_state
from ..delta import decisions_for, changed_fields
from ..workflow import Conflict, decide, fingerprint, group_ids, my_approvals, readable_rules, submit
from .serializers import DecisionSerializer, SubmitSerializer


def approval_payload(user, pk, data, review):
    state = current_state(review, data)
    ready = state == "approved"
    decisions = decisions_for(review, data)
    mine = my_approvals(data, group_ids(user))
    pending = [key for key in data["approvers"] if decisions.get(key, {}).get("action") != "approve"]
    payload = {
        "rule_id": str(pk),
        "risk_level": data["risk_level"],
        "state": state,
        "deployment_ready": ready,
        "pending_approvals": pending,
        "retained_approvals": [key for key, decision in decisions.items()
                               if key in data["approvers"] and decision.get("action") == "approve"
                               and "carried_from_review" in decision],
        # Preserve the deployment API's approved-only review_id contract.
        "review_id": review.pk if ready else None,
        "approved_snapshot": data if ready else None,
        "active_review_id": review.pk if review else None,
        "my_pending_approvals": [key for key in mine if key in pending] if state == "pending" else [],
        "can_submit": bool(mine) and state in ("draft", "denied", "superseded"),
        "required_approvals": list(data["approvers"]),
    }
    if mine:
        baseline = review
        if review and state != "superseded":
            baseline = Review.objects.filter(rule_id=pk, pk__lt=review.pk).first()
        payload.update({
            "snapshot_hash": fingerprint(data),
            "current_snapshot": data,
            "my_approval_scopes": mine,
            "decisions": decisions,
            "changed_fields": {key: changed_fields(baseline.snapshot if baseline else None, data, key)
                               for key in mine},
        })
    return payload


def status_payload(user, pk):
    rule = get_object_or_404(readable_rules(user), pk=pk)
    try:
        data = snapshot(rule)
    except ValidationError as exc:
        return {"rule_id": str(pk), "state": "blocked", "deployment_ready": False, "errors": exc.messages}
    review = Review.objects.filter(rule_id=pk).first()
    return approval_payload(user, pk, data, review)


def no_cache(data, code=status.HTTP_200_OK):
    response = Response(data, status=code)
    add_never_cache_headers(response)
    return response


class ApprovalStatus(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        return no_cache(status_payload(request.user, pk))


class WriteEnabled(BasePermission):
    message = "En skrivaktiverad API-token eller autentiserad session krävs."

    def has_permission(self, request, view):
        # SessionAuthentication enforces CSRF. Unknown token types fail closed.
        if request.auth is None:
            from rest_framework.authentication import SessionAuthentication
            return isinstance(request.successful_authenticator, SessionAuthentication)
        return getattr(request.auth, "write_enabled", False) is True


class ApprovalAction(APIView):
    permission_classes = [IsAuthenticated, WriteEnabled]

    def perform(self, request, pk, serializer, operation):
        serializer.is_valid(raise_exception=True)
        try:
            review = operation(request.user, pk, **serializer.validated_data)
        except Conflict as exc:
            return no_cache({"detail": str(exc)}, status.HTTP_409_CONFLICT)
        except ValidationError as exc:
            return no_cache({"detail": exc.messages}, status.HTTP_400_BAD_REQUEST)
        # The mutation result is tied to the committed review. Re-read current
        # status separately so concurrent changes do not imply deployment ready.
        payload = status_payload(request.user, pk)
        payload["processed_review_id"] = review.pk
        return no_cache(payload)


class SubmitApproval(ApprovalAction):
    def post(self, request, pk):
        serializer = SubmitSerializer(data=request.data)

        def operation(user, rule_id, expected_review_id, snapshot_hash):
            return submit(user, rule_id, expected_review_id, snapshot_hash)

        return self.perform(request, pk, serializer, operation)


class ApprovalDecision(ApprovalAction):
    def post(self, request, pk):
        serializer = DecisionSerializer(data=request.data)

        def operation(user, rule_id, **values):
            return decide(user, rule_id, channel="api", **values)

        return self.perform(request, pk, serializer, operation)
