from django.contrib import admin
from django.urls import include, path


urlpatterns = [
    path("", include("focus_core.urls")),
    path("admin/", admin.site.urls),
]
