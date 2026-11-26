from django.urls import path
from .views import ApprovalStatus, SubmitApproval, ApprovalDecision

urlpatterns = [
    path("rules/<uuid:pk>/approval-status/", ApprovalStatus.as_view(), name="approval-status"),
    path("rules/<uuid:pk>/submit/", SubmitApproval.as_view(), name="submit"),
    path("rules/<uuid:pk>/decision/", ApprovalDecision.as_view(), name="decision"),
]
