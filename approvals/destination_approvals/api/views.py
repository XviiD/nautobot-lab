"""Read-only gate for automation; never mutates policy or reviews."""
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from django.utils.cache import add_never_cache_headers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from nautobot_firewall_models.models import PolicyRule

from ..models import Review
from ..routing import snapshot
from ..status import current_state


class ApprovalStatus(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        rule = get_object_or_404(
            PolicyRule.objects.restrict(request.user, "view").filter(request_id__startswith="LAB-FLOW-"),
            pk=pk,
        )
        try:
            data = snapshot(rule)
        except ValidationError as exc:
            response = Response({"state": "blocked", "deployment_ready": False, "errors": exc.messages})
        else:
            review = Review.objects.filter(rule_id=pk).first()
            state = current_state(review, data)
            ready = state == "approved"
            response = Response({
                "rule_id": str(pk),
                "risk_level": data["risk_level"],
                "state": state,
                "deployment_ready": ready,
                "review_id": review.pk if ready else None,
                "approved_snapshot": data if ready else None,
            })
        add_never_cache_headers(response)
        return response
