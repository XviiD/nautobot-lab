from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from nautobot_firewall_models.models import PolicyRule

from .models import Review
from .routing import snapshot


def visible(user):
    return PolicyRule.objects.restrict(user, "view").filter(request_id__startswith="LAB-FLOW-").distinct()


@login_required
def index(request):
    return render(request, "destination_approvals/index.html", {"rules": visible(request.user)})


@login_required
@require_http_methods(["GET", "POST"])
def detail(request, pk):
    rule = get_object_or_404(visible(request.user), pk=pk)
    groups = {str(g) for g in request.user.groups.values_list("pk", flat=True)}
    if request.method == "POST":
        with transaction.atomic():
            # Serialize submissions and decisions for this rule, including the first submission.
            rule = PolicyRule.objects.select_for_update().get(pk=rule.pk)
            current = Review.objects.filter(rule_id=pk).first()
            try:
                data = snapshot(rule)
            except ValidationError as error:
                data = None
                messages.error(request, "; ".join(error.messages))
            if current and current.snapshot != data and current.state != "superseded":
                current.state = "superseded"
                current.save(update_fields=["state"])
            if data:
                action = request.POST.get("action")
                if action == "submit":
                    # A user must have object-level view permission to submit this lab rule.
                    if current is None or current.state in ("denied", "superseded"):
                        Review.objects.create(rule_id=pk, snapshot=data, submitted_by=request.user.get_username())
                elif action in ("approve", "deny"):
                    team = request.POST.get("team")
                    if team not in data["approvers"] or data["approvers"][team] not in groups:
                        raise PermissionDenied
                    if (not current or current.state != "pending" or str(current.pk) != request.POST.get("review")
                            or team in current.decisions):
                        messages.error(request, "Beslutet gäller en tidigare eller redan hanterad beställning.")
                    else:
                        current.decisions[team] = {"action": action, "by": request.user.get_username(),
                                                   "user_id": str(request.user.pk), "at": timezone.now().isoformat()}
                        if action == "deny":
                            current.state = "denied"
                        elif current.decisions.keys() == data["approvers"].keys():
                            current.state = "approved"
                        current.save(update_fields=["decisions", "state"])
        return redirect("plugins:destination_approvals:detail", pk=pk)

    current = Review.objects.filter(rule_id=pk).first()
    try:
        data = snapshot(rule)
    except ValidationError as error:
        data = None
        messages.error(request, "; ".join(error.messages))
    state = current.state if current and current.snapshot == data else ("superseded" if current else "draft")
    rows = []
    for team, gid in (data or {}).get("approvers", {}).items():
        decision = current.decisions.get(team) if current and current.snapshot == data else None
        rows.append((team, decision, state == "pending" and gid in groups and not decision))
    return render(request, "destination_approvals/detail.html", {
        "rule": rule, "data": data, "review": current, "state": state, "rows": rows,
        "can_submit": data and state in ("draft", "denied", "superseded"),
    })
