from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views


urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("about/", views.AboutView.as_view(), name="about"),
    path("privacy/", views.PrivacyView.as_view(), name="privacy"),
    path("accessibility/", views.AccessibilityView.as_view(), name="accessibility"),
    path("speech/", views.QuickSpeechView.as_view(), name="quick_speech"),
    path("status/", views.StatusView.as_view(), name="status"),
    path("status/health/", views.StatusHealthView.as_view(), name="status_health"),
    path("dev/sign-in/", views.DevSignInView.as_view(), name="dev_sign_in"),
    path("auth/<str:provider>/start/", views.OAuthStartView.as_view(), name="oauth_sign_in_start"),
    path("auth/<str:provider>/callback/", views.OAuthCallbackView.as_view(), name="oauth_callback"),
    path("backup-key/sign-in/", views.BackupKeySignInView.as_view(), name="backup_key_sign_in"),
    path("passkey/sign-in/", views.PasskeySignInView.as_view(), name="passkey_sign_in"),
    path("passkey/sign-in/options/", views.PasskeyAuthenticationOptionsView.as_view(), name="passkey_authentication_options"),
    path("passkey/sign-in/complete/", views.PasskeyAuthenticationCompleteView.as_view(), name="passkey_authentication_complete"),
    path("profile/", views.ProfileView.as_view(), name="profile"),
    path("notifications/", views.ProjectNotificationListView.as_view(), name="notifications"),
    path("notifications/poll/", views.NotificationPollView.as_view(), name="notifications_poll"),
    path("notifications/read/", views.ProjectNotificationMarkReadView.as_view(), name="notifications_mark_read"),
    path("account/safety/", views.AccountSafetyView.as_view(), name="account_safety"),
    path(
        "account/linked-accounts/development/new/",
        views.DevelopmentLinkedAccountCreateView.as_view(),
        name="development_linked_account_create",
    ),
    path(
        "account/linked-accounts/<str:provider>/connect/",
        views.OAuthConnectStartView.as_view(),
        name="oauth_connect_start",
    ),
    path(
        "account/linked-accounts/<int:pk>/remove/",
        views.LinkedAccountRemoveView.as_view(),
        name="linked_account_remove",
    ),
    path("account/passkeys/<int:pk>/edit/", views.PasskeyUpdateView.as_view(), name="passkey_update"),
    path("account/passkeys/<int:pk>/remove/", views.PasskeyRemoveView.as_view(), name="passkey_remove"),
    path("account/passkeys/new/", views.PasskeyRegistrationView.as_view(), name="passkey_register"),
    path("account/passkeys/options/", views.PasskeyRegistrationOptionsView.as_view(), name="passkey_registration_options"),
    path("account/passkeys/complete/", views.PasskeyRegistrationCompleteView.as_view(), name="passkey_registration_complete"),
    path("sign-out/", LogoutView.as_view(), name="logout"),
    path("groups/new/", views.GroupCreateView.as_view(), name="group_create"),
    path("groups/<slug:slug>/", views.GroupDetailView.as_view(), name="group_detail"),
    path("groups/<slug:slug>/invitations/", views.GroupInvitationView.as_view(), name="group_invitations"),
    path(
        "groups/<slug:slug>/invitations/<int:pk>/revoke/",
        views.GroupInvitationRevokeView.as_view(),
        name="group_invitation_revoke",
    ),
    path("groups/<slug:slug>/members/", views.GroupMembersView.as_view(), name="group_members"),
    path("groups/<slug:slug>/leave/", views.MembershipLeaveView.as_view(), name="membership_leave"),
    path("groups/<slug:slug>/members/<int:pk>/", views.MemberProfileView.as_view(), name="member_profile"),
    path(
        "groups/<slug:slug>/members/<int:pk>/role/",
        views.MembershipRoleUpdateView.as_view(),
        name="membership_role_update",
    ),
    path(
        "groups/<slug:slug>/members/<int:pk>/remove/",
        views.MembershipRemoveView.as_view(),
        name="membership_remove",
    ),
    path("groups/<slug:slug>/projects/new/", views.ProjectCreateView.as_view(), name="project_create"),
    path("groups/<slug:group_slug>/projects/<int:pk>/", views.ProjectDetailView.as_view(), name="project_detail"),
    path(
        "groups/<slug:group_slug>/projects/<int:pk>/export/",
        views.ProjectExportView.as_view(),
        name="project_export",
    ),
    path(
        "groups/<slug:group_slug>/projects/<int:pk>/status/",
        views.ProjectStatusUpdateView.as_view(),
        name="project_status_update",
    ),
    path(
        "groups/<slug:group_slug>/projects/<int:pk>/notes/new/",
        views.ProjectNoteCreateView.as_view(),
        name="project_note_create",
    ),
    path(
        "groups/<slug:group_slug>/projects/<int:pk>/resources/new/",
        views.ProjectResourceCreateView.as_view(),
        name="project_resource_create",
    ),
    path(
        "groups/<slug:group_slug>/projects/<int:pk>/resources/<int:resource_pk>/remove/",
        views.ProjectResourceRemoveView.as_view(),
        name="project_resource_remove",
    ),
    path(
        "groups/<slug:group_slug>/projects/<int:pk>/archive/",
        views.ProjectArchiveView.as_view(),
        name="project_archive",
    ),
    path(
        "groups/<slug:group_slug>/projects/<int:pk>/restore/",
        views.ProjectRestoreView.as_view(),
        name="project_restore",
    ),
    path(
        "groups/<slug:group_slug>/projects/<int:pk>/delete/",
        views.ProjectDeleteView.as_view(),
        name="project_delete",
    ),
    path("groups/<slug:group_slug>/projects/<int:pk>/edit/", views.ProjectUpdateView.as_view(), name="project_update"),
    path("invites/<uuid:token>/", views.InvitationAcceptView.as_view(), name="invite_accept"),
]
