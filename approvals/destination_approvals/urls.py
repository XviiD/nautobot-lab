from django.urls import path
from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("<uuid:pk>/", views.detail, name="detail"),
]
