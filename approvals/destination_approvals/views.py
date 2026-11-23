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


def visible(user):
    group_names = set(user.groups.values_list("name", flat=True))
    mapping = settings.PLUGINS_CONFIG["destination_approvals"]["team_groups"]
    tags = [tag for tag, group in mapping.items() if group in group_names]
    return PolicyRule.objects.restrict(user, "view").filter(
        Q(destination_addresses__tags__name__in=tags)
        | Q(destination_address_groups__address_objects__tags__name__in=tags)
        | Q(destination_address_groups__tags__name__in=tags, destination_address_groups__address_objects__isnull=True),
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
    state = review.state if review and review.snapshot == data else ("superseded" if review else "draft")
    decisions = review.decisions if review and review.snapshot == data else {}
    waiting = state == "pending" and any(team not in decisions for team in mine)
    labels = {"draft": "Utkast", "pending": "Väntar på andra", "denied": "Avslagen",
              "approved": "Godkänd", "superseded": "Ändrad"}
    return {"rule": rule, "data": data, "mine": mine, "review": review, "state": state,
            "waiting": waiting, "label": "Ditt beslut behövs" if waiting else labels[state],
            "rows": [(team, decisions.get(team), state == "pending" and team in mine and team not in decisions)
                     for team in data["approvers"]],
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
            rule = PolicyRule.objects.select_for_update().get(pk=rule.pk)
            context = context_for(rule, groups)
            if not context:
                raise Http404
            current = context["review"]
            data = context["data"]
            if current and current.snapshot != data and current.state != "superseded":
                current.state = "superseded"
                current.save(update_fields=["state"])
            action = request.POST.get("action")
            if action == "submit":
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

    context = context_for(rule, groups)
    if not context:
        raise Http404
    return render(request, "destination_approvals/detail.html", context)
