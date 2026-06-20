from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import (
    AuthIdentity,
    FocusUser,
    GroupInvitation,
    Membership,
    ProductionGroup,
    ProjectNote,
    ProjectNotification,
    ProjectResource,
    RecoveryCode,
    VideoProject,
    WebAuthnCredential,
)


@admin.register(FocusUser)
class FocusUserAdmin(UserAdmin):
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Public profile", {"fields": ("display_name", "bio", "availability", "show_assigned_projects")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"fields": ("username", "display_name", "bio", "availability", "show_assigned_projects")}),
    )
    list_display = ("public_name", "username", "is_staff")
    search_fields = ("username", "display_name", "bio", "identities__handle")


admin.site.register(AuthIdentity)
admin.site.register(RecoveryCode)
admin.site.register(WebAuthnCredential)
admin.site.register(ProductionGroup)
admin.site.register(Membership)
admin.site.register(VideoProject)
admin.site.register(ProjectNote)
admin.site.register(ProjectNotification)
admin.site.register(ProjectResource)
admin.site.register(GroupInvitation)
