from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views


urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("dev/sign-in/", views.DevSignInView.as_view(), name="dev_sign_in"),
    path("sign-out/", LogoutView.as_view(), name="logout"),
    path("groups/new/", views.GroupCreateView.as_view(), name="group_create"),
    path("groups/<slug:slug>/", views.GroupDetailView.as_view(), name="group_detail"),
    path("groups/<slug:slug>/projects/new/", views.ProjectCreateView.as_view(), name="project_create"),
    path("groups/<slug:group_slug>/projects/<int:pk>/edit/", views.ProjectUpdateView.as_view(), name="project_update"),
]
