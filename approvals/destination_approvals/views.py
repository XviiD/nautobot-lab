from django.conf import settings
from django.db.models import Q
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from nautobot_firewall_models.models import PolicyRule

from .models import Review
from .routing import snapshot
from .risk import risk_visibility
from .status import current_state
from .delta import changed_fields, decisions_for
from .workflow import Conflict, decide, fingerprint, submit


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
            "snapshot_hash": fingerprint(data),
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
        action = request.POST.get("action")
        try:
            if action == "submit":
                raw_id = request.POST.get("review", "")
                submit(request.user, pk, int(raw_id) if raw_id else None,
                       request.POST.get("snapshot_hash", ""))
            elif action in ("approve", "deny"):
                decide(request.user, pk, int(request.POST.get("review", "")),
                       request.POST.get("team"), action, channel="gui")
            else:
                raise ValidationError("Ogiltig åtgärd.")
        except (Conflict, ValidationError, ValueError) as exc:
            messages.error(request, str(exc))
        return redirect("plugins:destination_approvals:detail", pk=pk)

    context = context_for(rule, groups)
    if not context:
        raise Http404
    return render(request, "destination_approvals/detail.html", context)
