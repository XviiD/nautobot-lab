from nautobot.apps import NautobotAppConfig


class DestinationApprovalsConfig(NautobotAppConfig):
    name = "destination_approvals"
    verbose_name = "Destination Approvals"
    version = "0.2.0"
    base_url = "destination-approvals"
    default_auto_field = "django.db.models.BigAutoField"
    home_view_name = "plugins:destination_approvals:index"
    default_settings = {
        "team_groups": {team + "-team": team + "-team" for team in ("linux", "dba", "windows", "network")},
        "risk_groups": {"medium": "Network Approvers", "high": "Security Approvers"},
    }


config = DestinationApprovalsConfig
