from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import (
    AuthIdentity,
    FocusUser,
    GroupInvitation,
    Membership,
    ProductionGroup,
    RecoveryCode,
    VideoProject,
    WebAuthnCredential,
)


@admin.register(FocusUser)
class FocusUserAdmin(UserAdmin):
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Public profile", {"fields": ("display_name",)}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"fields": ("username", "display_name")}),
    )
    list_display = ("public_name", "username", "is_staff")
    search_fields = ("username", "display_name", "identities__handle")


admin.site.register(AuthIdentity)
admin.site.register(RecoveryCode)
admin.site.register(WebAuthnCredential)
admin.site.register(ProductionGroup)
admin.site.register(Membership)
admin.site.register(VideoProject)
admin.site.register(GroupInvitation)
