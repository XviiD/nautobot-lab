from django.urls import path
from .views import ApprovalStatus

urlpatterns = [
    path("rules/<uuid:pk>/approval-status/", ApprovalStatus.as_view(), name="approval-status"),
]
