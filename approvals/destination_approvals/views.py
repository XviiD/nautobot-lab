from django.conf import settings
from django.db.models import Q
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from nautobot_firewall_models.models import PolicyRule

from .models import Review
from .routing import snapshot
from .risk import risk_visibility
from .status import current_state
from .delta import all_approved, carry_approvals, changed_fields, decisions_for


def visible(user):
    group_names = set(user.groups.values_list("name", flat=True))
    mapping = settings.PLUGINS_CONFIG["destination_approvals"]["team_groups"]
    tags = [tag for tag, group in mapping.items() if group in group_names]
    return PolicyRule.objects.restrict(user, "view").filter(
        Q(destination_addresses__tags__name__in=tags)
        | Q(destination_address_groups__address_objects__tags__name__in=tags)
        | Q(destination_address_groups__tags__name__in=tags, destination_address_groups__address_objects__isnull=True)
        | risk_visibility(group_names),
        request_id__startswith="LAB-FLOW-",
    ).distinct()


def context_for(rule, groups):
    try:
        data = snapshot(rule)
    except ValidationError:
        return None
    mine = [team for team, gid in data["approvers"].items() if gid in groups]
    if not mine:
        return None
    review = Review.objects.filter(rule_id=rule.pk).first()
    state = current_state(review, data)
    decisions = decisions_for(review, data)
    missing = [team for team in mine if decisions.get(team, {}).get("action") != "approve"]
    waiting = state == "pending" and bool(missing)
    # Compare against the previous submitted version even after resubmission.
    baseline = review
    if review and state != "superseded":
        baseline = Review.objects.filter(rule_id=rule.pk, pk__lt=review.pk).first()
    baseline_data = baseline.snapshot if baseline else None
    labels = {"draft": "Utkast", "pending": "Väntar på andra", "denied": "Avslagen",
              "approved": "Godkänd", "superseded": "Ändrad"}
    label = "Ditt beslut behövs" if waiting else labels[state]
    if state == "superseded":
        label = "Ändrad – nytt beslut efter inskick" if missing else "Ändrad – dina beslut kvarstår"
    return {"rule": rule, "data": data, "mine": mine, "review": review, "state": state,
            "waiting": waiting, "label": label,
            "rows": [(team, decisions.get(team), state == "pending" and team in mine and team not in decisions,
                      changed_fields(baseline_data, data, team)) for team in data["approvers"]],
            "can_submit": state in ("draft", "denied", "superseded")}


@login_required
def index(request):
    groups = {str(g) for g in request.user.groups.values_list("pk", flat=True)}
    rows = []
    for rule in visible(request.user).order_by("request_id", "pk"):
        row = context_for(rule, groups)
        if row:
            rows.append(row)
    rows.sort(key=lambda row: not row["waiting"])
    return render(request, "destination_approvals/index.html", {"rows": rows})


@login_required
@require_http_methods(["GET", "POST"])
def detail(request, pk):
    rule = get_object_or_404(visible(request.user), pk=pk)
    groups = {str(g) for g in request.user.groups.values_list("pk", flat=True)}
    if request.method == "POST":
        with transaction.atomic():
            # Serialize submissions and decisions for this rule, including the first submission.
            rule = get_object_or_404(PolicyRule.objects.select_for_update(), pk=rule.pk)
            if not visible(request.user).filter(pk=rule.pk).exists():
                raise Http404
            context = context_for(rule, groups)
            if not context:
                raise Http404
            current = context["review"]
            data = context["data"]
            if current and context["state"] == "superseded" and current.state != "superseded":
                current.state = "superseded"
                current.save(update_fields=["state"])
            action = request.POST.get("action")
            if action == "submit":
                if current is None or current.state in ("denied", "superseded"):
                    carried = carry_approvals(current, data)
                    Review.objects.create(
                        rule_id=pk, snapshot=data, decisions=carried,
                        state="approved" if all_approved(data, carried) else "pending",
                        submitted_by=request.user.get_username(),
                    )
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
                    elif all_approved(data, current.decisions):
                        current.state = "approved"
                    current.save(update_fields=["decisions", "state"])
        return redirect("plugins:destination_approvals:detail", pk=pk)

    context = context_for(rule, groups)
    if not context:
        raise Http404
    return render(request, "destination_approvals/detail.html", context)
