import json
import secrets
from importlib.metadata import PackageNotFoundError, version
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.models import Prefetch, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode, url_has_allowed_host_and_scheme
from django.utils.text import slugify
from django.views import View
from django.views.generic import CreateView, DetailView, FormView, TemplateView, UpdateView
from webauthn import generate_authentication_options, generate_registration_options, options_to_json, verify_authentication_response, verify_registration_response
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from .forms import BackupKeySignInForm, DevelopmentLinkedAccountForm, DisplayNameForm, GroupInvitationForm, MastodonServerForm, MembershipRoleForm, PasskeyNameForm, PasskeyRegistrationForm, ProductionGroupForm, ProjectNoteForm, ProjectResourceForm, ProjectStatusForm, VideoProjectForm
from .models import AuthIdentity, GroupInvitation, GroupNotification, Membership, ProductionGroup, ProjectNote, ProjectNotification, ProjectResource, RecoveryCode, VideoProject, WebAuthnCredential


RECOVERY_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
RECOVERY_CODE_COUNT = 8
PASSKEY_REGISTRATION_CHALLENGE_SESSION_KEY = "passkey_registration_challenge"
PASSKEY_REGISTRATION_RP_ID_SESSION_KEY = "passkey_registration_rp_id"
PASSKEY_REGISTRATION_ORIGIN_SESSION_KEY = "passkey_registration_origin"
PASSKEY_AUTHENTICATION_CHALLENGE_SESSION_KEY = "passkey_authentication_challenge"
PASSKEY_AUTHENTICATION_RP_ID_SESSION_KEY = "passkey_authentication_rp_id"
PASSKEY_AUTHENTICATION_ORIGIN_SESSION_KEY = "passkey_authentication_origin"
PASSKEY_AUTHENTICATION_NEXT_SESSION_KEY = "passkey_authentication_next"
OAUTH_STATE_SESSION_KEY = "oauth_state"
OAUTH_PROVIDER_SESSION_KEY = "oauth_provider"
OAUTH_NEXT_SESSION_KEY = "oauth_next"
OAUTH_MODE_SESSION_KEY = "oauth_mode"
OAUTH_DYNAMIC_CONFIG_SESSION_KEY = "oauth_dynamic_config"
OAUTH_MODE_SIGN_IN = "sign_in"
OAUTH_MODE_CONNECT = "connect"


def app_version():
    try:
        return version("focus")
    except PackageNotFoundError:
        return "development"


def production_settings_ready():
    has_secure_secret = settings.SECRET_KEY != getattr(settings, "DEFAULT_INSECURE_SECRET_KEY", "")
    return (
        not settings.DEBUG
        and has_secure_secret
        and bool(settings.ALLOWED_HOSTS)
        and getattr(settings, "SESSION_COOKIE_SECURE", False)
        and getattr(settings, "CSRF_COOKIE_SECURE", False)
        and getattr(settings, "SECURE_SSL_REDIRECT", False)
        and not getattr(settings, "FOCUS_ENABLE_DEV_SIGN_IN", False)
        and bool(available_oauth_provider_count())
    )


class AboutView(TemplateView):
    template_name = "focus_core/about.html"


class PrivacyView(TemplateView):
    template_name = "focus_core/privacy.html"


class AccessibilityView(TemplateView):
    template_name = "focus_core/accessibility.html"


class QuickSpeechView(TemplateView):
    template_name = "focus_core/quick_speech.html"


class StatusView(TemplateView):
    template_name = "focus_core/status.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        production_ready = production_settings_ready()
        provider_count = available_oauth_provider_count()
        provider_summary = "Provider sign-in is configured" if provider_count else "Provider sign-in is implemented but not configured"
        context["status_summary"] = [
            {
                "label": "Application version",
                "value": app_version(),
                "status": "Published",
            },
            {
                "label": "Service status",
                "value": "Available",
                "status": "Operational",
            },
            {
                "label": "Deployment mode",
                "value": "Production configuration" if production_ready else "Local development configuration",
                "status": "Ready" if production_ready else "Needs production setup",
            },
            {
                "label": "Authentication providers",
                "value": "GitHub, Discord, Mastodon, passkeys, and backup keys are implemented",
                "status": provider_summary,
            },
            {
                "label": "Accessibility evidence",
                "value": "Template checks and browser checks are running during development",
                "status": "Manual assistive technology testing still needed",
            },
        ]
        context["readiness_items"] = [
            {
                "area": "Core group and project workflows",
                "status": "In progress",
                "detail": "Groups, members, invitations, projects, resources, notes, lifecycle controls, and notifications are implemented.",
            },
            {
                "area": "Production authentication",
                "status": "Implemented" if provider_count else "Needs provider credentials",
                "detail": "Provider sign-in is implemented for GitHub, Discord, and user-selected Mastodon servers. GitHub and Discord need provider client IDs and secrets in production.",
            },
            {
                "area": "Production settings",
                "status": "Ready" if production_ready else "Needs environment setup",
                "detail": "Environment secrets, allowed hosts, secure cookies, HTTPS redirects, and any static provider credentials must be configured for each deployment.",
            },
            {
                "area": "Backups and retention",
                "status": "Needs decision",
                "detail": "Backup, restore, retention, and operational recovery policies still need documented decisions.",
            },
            {
                "area": "Accessibility testing",
                "status": "In progress",
                "detail": "Automated checks are useful, but broader NVDA, JAWS, Narrator, VoiceOver, zoom, and high contrast testing still needs to happen.",
            },
        ]
        return context


class StatusHealthView(View):
    def get(self, request, *args, **kwargs):
        database_ok = True
        try:
            connection.ensure_connection()
        except Exception:
            database_ok = False

        response_status = 200 if database_ok else 503
        return JsonResponse(
            {
                "status": "ok" if database_ok else "degraded",
                "version": app_version(),
                "database": "ok" if database_ok else "unavailable",
                "production_ready": production_settings_ready(),
            },
            status=response_status,
        )


def unique_group_slug(name):
    base_slug = slugify(name) or "group"
    slug = base_slug
    suffix = 2
    while ProductionGroup.objects.filter(slug=slug).exists():
        slug = f"{base_slug}-{suffix}"
        suffix += 1
    return slug


def user_group_membership(user, group):
    return Membership.objects.filter(user=user, group=group).first()


def user_can_archive_project(user, project):
    membership = user_group_membership(user, project.group)
    if not membership:
        return False
    return membership.role in {Membership.Role.OWNER, Membership.Role.ADMIN} or project.created_by_id == user.pk


def user_can_manage_project(user, project):
    return user_can_archive_project(user, project)


def user_can_create_projects(user, group):
    membership = user_group_membership(user, group)
    if not membership:
        return False
    return membership.role in {Membership.Role.OWNER, Membership.Role.ADMIN}


def user_can_delete_project(user, project):
    return user_can_manage_project(user, project)


def user_can_update_project_activity(user, project):
    if user_can_manage_project(user, project):
        return True
    membership = user_group_membership(user, project.group)
    if not membership:
        return False
    return (
        project.assigned_editors.filter(pk=user.pk).exists()
        or project.assigned_writers.filter(pk=user.pk).exists()
    )


def user_can_remove_project_resource(user, resource):
    membership = user_group_membership(user, resource.project.group)
    if not membership:
        return False
    return (
        user_can_manage_project(user, resource.project)
        or resource.added_by_id == user.pk
    )


def project_resource_rows(user, project):
    return [
        {
            "resource": resource,
            "can_remove": user_can_remove_project_resource(user, resource),
        }
        for resource in project.resources.select_related("added_by")
    ]


def project_permission_context(user, project):
    return {
        "can_manage_project": user_can_manage_project(user, project),
        "can_update_project_activity": user_can_update_project_activity(user, project),
        "can_archive_project": user_can_archive_project(user, project),
        "can_delete_project": user_can_delete_project(user, project),
    }


def project_notification_recipient_ids(project, actor):
    recipient_ids = set()
    if project.created_by_id:
        recipient_ids.add(project.created_by_id)

    recipient_ids.update(project.assigned_editors.values_list("pk", flat=True))
    recipient_ids.update(project.assigned_writers.values_list("pk", flat=True))
    recipient_ids.update(
        Membership.objects.filter(
            group=project.group,
            role__in=[Membership.Role.OWNER, Membership.Role.ADMIN],
        ).values_list("user_id", flat=True)
    )

    if actor and actor.pk:
        recipient_ids.discard(actor.pk)
    return recipient_ids


def notify_project_activity(project, actor, kind, message):
    recipient_ids = project_notification_recipient_ids(project, actor)
    ProjectNotification.objects.bulk_create(
        [
            ProjectNotification(
                recipient_id=recipient_id,
                actor=actor,
                group=project.group,
                project=project,
                kind=kind,
                message=message,
            )
            for recipient_id in recipient_ids
        ]
    )


def group_notification_recipient_ids(group, actor, include_user_ids=()):
    recipient_ids = set(include_user_ids)
    recipient_ids.update(
        Membership.objects.filter(
            group=group,
            role__in=[Membership.Role.OWNER, Membership.Role.ADMIN],
        ).values_list("user_id", flat=True)
    )

    if actor and actor.pk:
        recipient_ids.discard(actor.pk)
    return recipient_ids


def notify_group_activity(group, actor, kind, message, include_user_ids=()):
    recipient_ids = group_notification_recipient_ids(group, actor, include_user_ids)
    GroupNotification.objects.bulk_create(
        [
            GroupNotification(
                recipient_id=recipient_id,
                actor=actor,
                group=group,
                kind=kind,
                message=message,
            )
            for recipient_id in recipient_ids
        ]
    )


def positive_int(value):
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def unread_notifications_for(user):
    return (
        ProjectNotification.objects.filter(recipient=user, read_at__isnull=True).count()
        + GroupNotification.objects.filter(recipient=user, read_at__isnull=True).count()
    )


def serialize_project_notification(notification):
    return {
        "id": notification.pk,
        "type": "project",
        "message": notification.message,
        "url": reverse("project_detail", kwargs={"group_slug": notification.group.slug, "pk": notification.project.pk}),
        "link_text": f"Open {notification.project.title} from notification",
        "created_at": notification.created_at.isoformat(),
    }


def serialize_group_notification(notification):
    return {
        "id": notification.pk,
        "type": "group",
        "message": notification.message,
        "url": reverse("group_detail", kwargs={"slug": notification.group.slug}),
        "link_text": f"Open {notification.group.name} from notification",
        "created_at": notification.created_at.isoformat(),
    }


def export_date(value):
    return timezone.localtime(value).strftime("%Y-%m-%d %H:%M")


def markdown_label(value):
    return str(value).replace("[", "\\[").replace("]", "\\]").strip()


def project_export_markdown(project):
    lines = [
        f"# {project.title}",
        "",
        f"Exported from FOCUS on {export_date(timezone.now())}.",
        "",
        "## Project summary",
        "",
        f"- Group: {project.group.name}",
        f"- Status: {project.get_status_display()}",
        f"- Archive status: {'Archived' if project.archived_at else 'Active'}",
        f"- Last updated: {export_date(project.updated_at)}",
        f"- Created: {export_date(project.created_at)}",
    ]
    if project.created_by:
        lines.append(f"- Created by: {project.created_by.public_name}")

    lines.extend(["", "## Description", ""])
    lines.append(project.description.strip() if project.description.strip() else "No description.")

    lines.extend(["", "## Project links", ""])
    lines.append(f"- Asset folder: {project.asset_pipeline_url or 'Not set.'}")
    lines.append(f"- Script: {project.script_url or 'Not set.'}")

    lines.extend(["", "## Assignments", "", "### Editors", ""])
    editors = list(project.assigned_editors.all())
    lines.extend([f"- {editor.public_name}" for editor in editors] or ["No editors assigned."])

    lines.extend(["", "### Writers", ""])
    writers = list(project.assigned_writers.all())
    lines.extend([f"- {writer.public_name}" for writer in writers] or ["No writers assigned."])

    lines.extend(["", "## Resources", ""])
    resources = list(project.resources.select_related("added_by"))
    if resources:
        for resource in resources:
            added_by = f", added by {resource.added_by.public_name}" if resource.added_by else ""
            lines.append(
                f"- [{markdown_label(resource.title)}]({resource.url}) - "
                f"{resource.get_kind_display()}{added_by} on {export_date(resource.created_at)}"
            )
    else:
        lines.append("No resources added.")

    lines.extend(["", "## Notes", ""])
    notes = list(project.notes.select_related("author"))
    if notes:
        for note in notes:
            lines.extend(
                [
                    f"### {export_date(note.created_at)} - {note.author.public_name}",
                    "",
                    note.body.strip(),
                    "",
                ]
            )
    else:
        lines.append("No notes added.")

    return "\n".join(lines).strip() + "\n"


def generate_recovery_code():
    parts = [
        "".join(secrets.choice(RECOVERY_CODE_ALPHABET) for _ in range(4))
        for _ in range(4)
    ]
    return "-".join(parts)


def development_subject_id(provider, handle):
    normalized_handle = slugify(handle) or "account"
    return f"dev-{provider.lower()}-{normalized_handle}"


def request_rp_id(request):
    return request.get_host().split(":")[0]


def request_origin(request):
    return request.build_absolute_uri("/").rstrip("/")


def credential_id_bytes(credential):
    return base64url_to_bytes(credential.credential_id)


def safe_next_url(request):
    next_url = request.GET.get("next")
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return reverse("dashboard")


def url_with_next(request, view_name):
    url = reverse(view_name)
    next_url = request.GET.get("next")
    if next_url and safe_next_url(request) == next_url:
        return f"{url}?{urlencode({'next': next_url})}"
    return url


def url_with_next_path(view_name, next_path):
    return f"{reverse(view_name)}?{urlencode({'next': next_path})}"


def normalize_provider(provider):
    return provider.upper()


def enabled_oauth_provider_configs():
    providers = getattr(settings, "FOCUS_OAUTH_PROVIDERS", {})
    return {
        provider: config
        for provider, config in providers.items()
        if config.get("client_id") and config.get("client_secret")
    }


def enabled_oauth_provider(provider):
    return enabled_oauth_provider_configs().get(normalize_provider(provider))


def mastodon_sign_in_enabled():
    return getattr(settings, "FOCUS_ENABLE_MASTODON_SIGN_IN", True)


def available_oauth_provider_count():
    provider_count = len(enabled_oauth_provider_configs())
    if mastodon_sign_in_enabled():
        provider_count += 1
    return provider_count


def oauth_provider_rows(request, start_view_name):
    rows = []
    next_url = request.GET.get("next")
    for provider, config in enabled_oauth_provider_configs().items():
        start_url = reverse(start_view_name, kwargs={"provider": provider.lower()})
        if next_url and safe_next_url(request) == next_url:
            start_url = f"{start_url}?{urlencode({'next': next_url})}"
        rows.append({"provider": provider, "label": config["label"], "start_url": start_url})
    if mastodon_sign_in_enabled():
        start_url = reverse(start_view_name, kwargs={"provider": "mastodon"})
        if next_url and safe_next_url(request) == next_url:
            start_url = f"{start_url}?{urlencode({'next': next_url})}"
        rows.append({"provider": "MASTODON", "label": "Mastodon", "start_url": start_url})
    return rows


def oauth_callback_url(request, provider):
    return request.build_absolute_uri(reverse("oauth_callback", kwargs={"provider": provider.lower()}))


def post_form_json(url, data):
    request = Request(
        url,
        data=urlencode(data).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "FOCUS",
        },
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def get_json(url):
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "FOCUS",
        },
    )
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def get_bearer_json(url, token):
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "FOCUS",
        },
    )
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def normalize_mastodon_base_url(server):
    value = server.strip().removeprefix("@")
    if "://" not in value:
        value = f"https://{value}"

    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("Enter a Mastodon server, such as mastodon.social.")

    return f"https://{parsed.netloc.lower()}"


def discover_mastodon_oauth_config(server, request):
    base_url = normalize_mastodon_base_url(server)
    metadata = {}
    try:
        metadata = get_json(f"{base_url}/.well-known/oauth-authorization-server")
    except HTTPError as error:
        if error.code != 404:
            raise

    scopes = metadata.get("scopes_supported", [])
    scope = "profile" if "profile" in scopes else "read:accounts"
    callback_url = oauth_callback_url(request, "MASTODON")
    registration_url = metadata.get("app_registration_endpoint") or f"{base_url}/api/v1/apps"
    application = post_form_json(
        registration_url,
        {
            "client_name": "FOCUS",
            "redirect_uris": callback_url,
            "scopes": scope,
            "website": request.build_absolute_uri(reverse("about")),
        },
    )
    profile_url = metadata.get("userinfo_endpoint") if scope == "profile" else None
    return {
        "label": "Mastodon",
        "client_id": application["client_id"],
        "client_secret": application["client_secret"],
        "authorize_url": metadata.get("authorization_endpoint") or f"{base_url}/oauth/authorize",
        "token_url": metadata.get("token_endpoint") or f"{base_url}/oauth/token",
        "profile_url": profile_url or f"{base_url}/api/v1/accounts/verify_credentials",
        "scope": scope,
        "base_url": base_url,
    }


def oauth_identity_from_profile(provider, profile, config):
    if provider == "GITHUB":
        return str(profile["id"]), profile["login"]

    if provider == "DISCORD":
        return str(profile["id"]), profile["username"]

    if provider == "MASTODON":
        subject_host = urlparse(config["base_url"]).netloc
        if "sub" in profile:
            handle = profile.get("preferred_username") or profile.get("name") or "mastodon-user"
            if "@" not in handle:
                handle = f"{handle}@{subject_host}"
            return profile["sub"], handle

        subject_id = f"{subject_host}:{profile['id']}"
        handle = profile.get("acct") or profile.get("username")
        if handle and "@" not in handle:
            handle = f"{handle}@{subject_host}"
        return subject_id, handle

    raise ValueError("Unsupported provider.")


def user_has_access_method(user, excluding_identity=None, excluding_passkey=None):
    identities = user.identities.all()
    if excluding_identity:
        identities = identities.exclude(pk=excluding_identity.pk)

    passkeys = user.webauthn_credentials.all()
    if excluding_passkey:
        passkeys = passkeys.exclude(pk=excluding_passkey.pk)

    return (
        identities.exists()
        or passkeys.exists()
        or user.recovery_codes.filter(used_at__isnull=True).exists()
    )


class OAuthStartView(View):
    mode = OAUTH_MODE_SIGN_IN

    def get(self, request, provider, *args, **kwargs):
        provider = normalize_provider(provider)
        if self.mode == OAUTH_MODE_SIGN_IN and request.user.is_authenticated:
            return redirect(safe_next_url(request))

        if provider == "MASTODON" and mastodon_sign_in_enabled():
            return self.render_mastodon_server_form(request, MastodonServerForm())

        config = enabled_oauth_provider(provider)
        if not config:
            raise Http404()

        return self.start_oauth_redirect(request, provider, config)

    def post(self, request, provider, *args, **kwargs):
        provider = normalize_provider(provider)
        if provider != "MASTODON" or not mastodon_sign_in_enabled():
            raise Http404()

        if self.mode == OAUTH_MODE_SIGN_IN and request.user.is_authenticated:
            return redirect(safe_next_url(request))

        form = MastodonServerForm(request.POST)
        if not form.is_valid():
            return self.render_mastodon_server_form(request, form)

        try:
            config = discover_mastodon_oauth_config(form.cleaned_data["server"], request)
        except (HTTPError, URLError, KeyError, ValueError, json.JSONDecodeError):
            form.add_error("server", "FOCUS could not start sign in with that Mastodon server. Check the server name and try again.")
            return self.render_mastodon_server_form(request, form)

        request.session[OAUTH_DYNAMIC_CONFIG_SESSION_KEY] = config
        return self.start_oauth_redirect(request, provider, config)

    def render_mastodon_server_form(self, request, form):
        return render(
            request,
            "focus_core/mastodon_server_form.html",
            {
                "form": form,
                "is_connect_flow": self.mode == OAUTH_MODE_CONNECT,
                "passkey_sign_in_url": url_with_next(request, "passkey_sign_in"),
                "backup_key_sign_in_url": url_with_next(request, "backup_key_sign_in"),
            },
        )

    def start_oauth_redirect(self, request, provider, config):
        state = secrets.token_urlsafe(32)
        request.session[OAUTH_STATE_SESSION_KEY] = state
        request.session[OAUTH_PROVIDER_SESSION_KEY] = provider
        request.session[OAUTH_NEXT_SESSION_KEY] = safe_next_url(request)
        request.session[OAUTH_MODE_SESSION_KEY] = self.mode

        params = {
            "client_id": config["client_id"],
            "redirect_uri": oauth_callback_url(request, provider),
            "response_type": "code",
            "scope": config["scope"],
            "state": state,
        }
        return redirect(f"{config['authorize_url']}?{urlencode(params)}")


class OAuthConnectStartView(LoginRequiredMixin, OAuthStartView):
    mode = OAUTH_MODE_CONNECT


class OAuthCallbackView(View):
    def get(self, request, provider, *args, **kwargs):
        provider = normalize_provider(provider)
        expected_state = request.session.pop(OAUTH_STATE_SESSION_KEY, None)
        expected_provider = request.session.pop(OAUTH_PROVIDER_SESSION_KEY, None)
        next_url = request.session.pop(OAUTH_NEXT_SESSION_KEY, reverse("dashboard"))
        mode = request.session.pop(OAUTH_MODE_SESSION_KEY, OAUTH_MODE_SIGN_IN)
        dynamic_config = request.session.pop(OAUTH_DYNAMIC_CONFIG_SESSION_KEY, None)
        config = dynamic_config if provider == "MASTODON" else enabled_oauth_provider(provider)

        if not config or provider != expected_provider or request.GET.get("state") != expected_state:
            messages.error(request, "That sign-in request could not be verified. Try again.")
            return redirect("passkey_sign_in")

        code = request.GET.get("code")
        if not code:
            messages.error(request, "The provider did not finish sign in. Try again.")
            return redirect("passkey_sign_in")

        if mode == OAUTH_MODE_CONNECT and not request.user.is_authenticated:
            messages.error(request, "Sign in again before connecting another account.")
            return redirect("passkey_sign_in")

        try:
            token_data = post_form_json(
                config["token_url"],
                {
                    "client_id": config["client_id"],
                    "client_secret": config["client_secret"],
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": oauth_callback_url(request, provider),
                },
            )
            access_token = token_data["access_token"]
            profile = get_bearer_json(config["profile_url"], access_token)
            subject_id, handle = oauth_identity_from_profile(provider, profile, config)
        except (HTTPError, URLError, KeyError, ValueError, json.JSONDecodeError):
            messages.error(request, "FOCUS could not finish sign in with that provider. Try again.")
            return redirect("passkey_sign_in")

        with transaction.atomic():
            identity = AuthIdentity.objects.select_for_update().filter(provider=provider, subject_id=subject_id).select_related("user").first()
            if mode == OAUTH_MODE_CONNECT:
                if identity and identity.user_id != request.user.pk:
                    messages.error(request, "That account is already connected to another FOCUS user.")
                    return redirect("account_safety")
                if not identity:
                    AuthIdentity.objects.create(
                        user=request.user,
                        provider=provider,
                        subject_id=subject_id,
                        handle=handle,
                        last_seen_at=timezone.now(),
                    )
                else:
                    identity.handle = handle
                    identity.last_seen_at = timezone.now()
                    identity.save(update_fields=["handle", "last_seen_at"])
                messages.success(request, f"Connected {config['label']} {handle}.")
                return redirect("account_safety")

            if not identity:
                user = get_user_model().objects.create()
                identity = AuthIdentity.objects.create(
                    user=user,
                    provider=provider,
                    subject_id=subject_id,
                    handle=handle,
                    last_seen_at=timezone.now(),
                )
            else:
                user = identity.user
                identity.handle = handle
                identity.last_seen_at = timezone.now()
                identity.save(update_fields=["handle", "last_seen_at"])

        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, f"You are signed in with {config['label']}.")
        return redirect(next_url)


class DevSignInView(FormView):
    template_name = "focus_core/dev_sign_in.html"
    form_class = forms.Form

    def dispatch(self, request, *args, **kwargs):
        if not settings.FOCUS_ENABLE_DEV_SIGN_IN:
            raise Http404()
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["passkey_sign_in_url"] = url_with_next(self.request, "passkey_sign_in")
        context["backup_key_sign_in_url"] = url_with_next(self.request, "backup_key_sign_in")
        context["oauth_provider_rows"] = oauth_provider_rows(self.request, "oauth_sign_in_start")
        return context

    def post(self, request, *args, **kwargs):
        user_model = get_user_model()
        user, _ = user_model.objects.get_or_create(
            username="dev-user",
            defaults={"display_name": "Development User"},
        )
        AuthIdentity.objects.get_or_create(
            user=user,
            provider="GITHUB",
            subject_id="focus-dev-user",
            defaults={"handle": "dev_creator"},
        )
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        return redirect(safe_next_url(request))


class BackupKeySignInView(FormView):
    form_class = BackupKeySignInForm
    template_name = "focus_core/backup_key_sign_in.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect(safe_next_url(request))
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["passkey_sign_in_url"] = url_with_next(self.request, "passkey_sign_in")
        context["oauth_provider_rows"] = oauth_provider_rows(self.request, "oauth_sign_in_start")
        return context

    def form_valid(self, form):
        backup_key = form.cleaned_data["backup_key"]
        with transaction.atomic():
            for recovery_code in RecoveryCode.objects.filter(used_at__isnull=True).select_related("user"):
                if recovery_code.matches(backup_key):
                    user = recovery_code.user
                    recovery_code.mark_used()
                    login(self.request, user, backend="django.contrib.auth.backends.ModelBackend")
                    messages.success(self.request, "You are signed in. That backup key has now been used.")
                    return redirect(safe_next_url(self.request))

        form.add_error("backup_key", "That backup key did not work. Check it and try again.")
        return self.form_invalid(form)


class PasskeySignInView(TemplateView):
    template_name = "focus_core/passkey_sign_in.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect(safe_next_url(request))
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["can_use_development_sign_in"] = settings.FOCUS_ENABLE_DEV_SIGN_IN
        context["backup_key_sign_in_url"] = url_with_next(self.request, "backup_key_sign_in")
        context["dev_sign_in_url"] = url_with_next(self.request, "dev_sign_in")
        context["passkey_authentication_options_url"] = url_with_next(self.request, "passkey_authentication_options")
        context["passkey_authentication_complete_url"] = url_with_next(self.request, "passkey_authentication_complete")
        context["oauth_provider_rows"] = oauth_provider_rows(self.request, "oauth_sign_in_start")
        return context


class PasskeyAuthenticationOptionsView(View):
    def post(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return JsonResponse({"ok": True, "redirect_url": safe_next_url(request)})

        rp_id = request_rp_id(request)
        origin = request_origin(request)
        options = generate_authentication_options(
            rp_id=rp_id,
            user_verification=UserVerificationRequirement.REQUIRED,
        )

        request.session[PASSKEY_AUTHENTICATION_CHALLENGE_SESSION_KEY] = bytes_to_base64url(options.challenge)
        request.session[PASSKEY_AUTHENTICATION_RP_ID_SESSION_KEY] = rp_id
        request.session[PASSKEY_AUTHENTICATION_ORIGIN_SESSION_KEY] = origin
        request.session[PASSKEY_AUTHENTICATION_NEXT_SESSION_KEY] = safe_next_url(request)
        return JsonResponse(json.loads(options_to_json(options)))


class PasskeyAuthenticationCompleteView(View):
    def post(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return JsonResponse({"ok": True, "redirect_url": safe_next_url(request)})

        challenge = request.session.pop(PASSKEY_AUTHENTICATION_CHALLENGE_SESSION_KEY, None)
        rp_id = request.session.pop(PASSKEY_AUTHENTICATION_RP_ID_SESSION_KEY, None)
        origin = request.session.pop(PASSKEY_AUTHENTICATION_ORIGIN_SESSION_KEY, None)
        next_url = request.session.pop(PASSKEY_AUTHENTICATION_NEXT_SESSION_KEY, reverse("dashboard"))
        if not challenge or not rp_id or not origin:
            return JsonResponse({"ok": False, "error": "Start passkey sign in again."}, status=400)

        try:
            payload = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError:
            return JsonResponse({"ok": False, "error": "Passkey sign in did not send a valid response."}, status=400)

        credential = payload.get("credential")
        credential_id = (credential or {}).get("id") or (credential or {}).get("rawId")
        if not credential or not credential_id:
            return JsonResponse({"ok": False, "error": "Passkey sign in did not return a credential."}, status=400)

        passkey = WebAuthnCredential.objects.filter(credential_id=credential_id).select_related("user").first()
        if not passkey:
            return JsonResponse({"ok": False, "error": "That passkey did not work. Try again."}, status=400)

        try:
            verification = verify_authentication_response(
                credential=credential,
                expected_challenge=base64url_to_bytes(challenge),
                expected_rp_id=rp_id,
                expected_origin=origin,
                credential_public_key=base64url_to_bytes(passkey.public_key),
                credential_current_sign_count=passkey.sign_count,
                require_user_verification=True,
            )
        except Exception:
            return JsonResponse({"ok": False, "error": "That passkey did not work. Try again."}, status=400)

        if bytes_to_base64url(verification.credential_id) != passkey.credential_id:
            return JsonResponse({"ok": False, "error": "That passkey did not work. Try again."}, status=400)

        passkey.sign_count = verification.new_sign_count
        passkey.last_used_at = timezone.now()
        passkey.save(update_fields=["sign_count", "last_used_at"])
        login(request, passkey.user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, "You are signed in with a passkey.")
        return JsonResponse({"ok": True, "redirect_url": next_url})


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "focus_core/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        assigned_projects = (
            VideoProject.objects.filter(group__members__user=user)
            .filter(Q(assigned_editors=user) | Q(assigned_writers=user))
            .filter(archived_at__isnull=True)
            .select_related("group")
            .prefetch_related(
                "assigned_editors",
                "assigned_writers",
                Prefetch(
                    "notes",
                    queryset=ProjectNote.objects.select_related("author"),
                    to_attr="prefetched_notes",
                ),
            )
            .distinct()
            .order_by("-updated_at", "title")
        )
        context["groups"] = ProductionGroup.objects.filter(members__user=user).order_by("name")
        context["assignment_rows"] = [
            {
                "project": project,
                "roles": [
                    role
                    for role, assignees in (
                        ("Editor", project.assigned_editors.all()),
                        ("Writer", project.assigned_writers.all()),
                    )
                    if user in assignees
                ],
                "latest_note": project.prefetched_notes[0] if project.prefetched_notes else None,
            }
            for project in assigned_projects
        ]
        recent_notes = (
            ProjectNote.objects.filter(project__group__members__user=user)
            .select_related("author", "project", "project__group")
            .order_by("-created_at")[:30]
        )
        recent_activity = []
        seen_project_ids = set()
        for note in recent_notes:
            if note.project_id in seen_project_ids:
                continue
            recent_activity.append(note)
            seen_project_ids.add(note.project_id)
            if len(recent_activity) == 8:
                break
        context["recent_activity"] = recent_activity
        return context


class ProjectNotificationListView(LoginRequiredMixin, TemplateView):
    template_name = "focus_core/notifications.html"
    filter_options = {
        "all": "All updates",
        "unread": "Unread",
        "project": "Project updates",
        "group": "Group updates",
    }

    def selected_filter(self):
        requested_filter = self.request.GET.get("filter", "all")
        if requested_filter in self.filter_options:
            return requested_filter
        return "all"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project_notifications = ProjectNotification.objects.filter(recipient=self.request.user).select_related("actor", "group", "project")
        group_notifications = GroupNotification.objects.filter(recipient=self.request.user).select_related("actor", "group")
        unread_count = (
            project_notifications.filter(read_at__isnull=True).count()
            + group_notifications.filter(read_at__isnull=True).count()
        )
        filter_counts = {
            "all": project_notifications.count() + group_notifications.count(),
            "unread": unread_count,
            "project": project_notifications.count(),
            "group": group_notifications.count(),
        }
        selected_filter = self.selected_filter()

        if selected_filter == "unread":
            project_notifications = project_notifications.filter(read_at__isnull=True)
            group_notifications = group_notifications.filter(read_at__isnull=True)
        elif selected_filter == "project":
            group_notifications = group_notifications.none()
        elif selected_filter == "group":
            project_notifications = project_notifications.none()

        notification_rows = [
            {
                "message": notification.message,
                "group": notification.group,
                "kind_label": notification.get_kind_display(),
                "created_at": notification.created_at,
                "read_at": notification.read_at,
                "url": reverse("project_detail", kwargs={"group_slug": notification.group.slug, "pk": notification.project.pk}),
                "link_text": f"Open {notification.project.title} from notification",
            }
            for notification in project_notifications[:50]
        ]
        notification_rows.extend(
            {
                "message": notification.message,
                "group": notification.group,
                "kind_label": notification.get_kind_display(),
                "created_at": notification.created_at,
                "read_at": notification.read_at,
                "url": reverse("group_detail", kwargs={"slug": notification.group.slug}),
                "link_text": f"Open {notification.group.name} from notification",
            }
            for notification in group_notifications[:50]
        )
        context["notifications"] = sorted(notification_rows, key=lambda notification: notification["created_at"], reverse=True)[:50]
        context["unread_count"] = unread_count
        context["selected_filter"] = selected_filter
        context["empty_state_heading"] = {
            "all": "No notifications yet",
            "unread": "No unread notifications",
            "project": "No project notifications yet",
            "group": "No group notifications yet",
        }[selected_filter]
        context["empty_state_message"] = {
            "all": "Project and group updates for work you create, manage, or are assigned to will appear here.",
            "unread": "Unread project and group updates will appear here.",
            "project": "Project updates for work you create, manage, or are assigned to will appear here.",
            "group": "Group membership and invite updates will appear here.",
        }[selected_filter]
        context["notification_filter_links"] = [
            {
                "key": key,
                "label": label,
                "count": filter_counts[key],
                "url": reverse("notifications") if key == "all" else f"{reverse('notifications')}?filter={key}",
                "is_current": selected_filter == key,
            }
            for key, label in self.filter_options.items()
        ]
        return context


class ProjectNotificationMarkReadView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        read_at = timezone.now()
        updated = ProjectNotification.objects.filter(recipient=request.user, read_at__isnull=True).update(read_at=read_at)
        updated += GroupNotification.objects.filter(recipient=request.user, read_at__isnull=True).update(read_at=read_at)
        if updated:
            messages.success(request, "Marked all notifications as read.")
        else:
            messages.info(request, "There were no unread notifications.")
        return redirect("notifications")


class NotificationPollView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        after_project_id = positive_int(request.GET.get("after_project_id"))
        after_group_id = positive_int(request.GET.get("after_group_id"))
        project_notifications = list(
            ProjectNotification.objects.filter(
                recipient=request.user,
                pk__gt=after_project_id,
            )
            .select_related("group", "project")
            .order_by("pk")[:10]
        )
        group_notifications = list(
            GroupNotification.objects.filter(
                recipient=request.user,
                pk__gt=after_group_id,
            )
            .select_related("group")
            .order_by("pk")[:10]
        )
        notifications = [
            *[serialize_project_notification(notification) for notification in project_notifications],
            *[serialize_group_notification(notification) for notification in group_notifications],
        ]
        notifications.sort(key=lambda notification: notification["created_at"])
        latest_project_id = project_notifications[-1].pk if project_notifications else after_project_id
        latest_group_id = group_notifications[-1].pk if group_notifications else after_group_id
        return JsonResponse(
            {
                "notifications": notifications,
                "unread_count": unread_notifications_for(request.user),
                "latest_project_id": latest_project_id,
                "latest_group_id": latest_group_id,
            }
        )


class ProfileView(LoginRequiredMixin, UpdateView):
    form_class = DisplayNameForm
    template_name = "focus_core/profile_form.html"

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Profile updated.")
        return response

    def get_success_url(self):
        return reverse("profile")


class AccountSafetyView(LoginRequiredMixin, TemplateView):
    template_name = "focus_core/account_safety.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["can_connect_development_account"] = settings.FOCUS_ENABLE_DEV_SIGN_IN
        context["oauth_provider_rows"] = oauth_provider_rows(self.request, "oauth_connect_start")
        context["unused_recovery_code_count"] = user.recovery_codes.filter(used_at__isnull=True).count()
        context["identity_rows"] = [
            {
                "identity": identity,
                "can_remove": user_has_access_method(user, excluding_identity=identity),
            }
            for identity in user.identities.order_by("provider", "handle")
        ]
        context["passkey_rows"] = [
            {
                "passkey": passkey,
                "display_name": passkey.name or "Unnamed passkey",
                "can_remove": user_has_access_method(user, excluding_passkey=passkey),
            }
            for passkey in user.webauthn_credentials.order_by("created_at", "name")
        ]
        return context

    def post(self, request, *args, **kwargs):
        codes = [generate_recovery_code() for _ in range(RECOVERY_CODE_COUNT)]
        with transaction.atomic():
            request.user.recovery_codes.all().delete()
            for code in codes:
                RecoveryCode.create_for_code(request.user, code)

        context = self.get_context_data(generated_codes=codes)
        context["unused_recovery_code_count"] = len(codes)
        messages.success(request, "New backup keys created. Save them now, because they will not be shown again.")
        return self.render_to_response(context)


class DevelopmentLinkedAccountCreateView(LoginRequiredMixin, FormView):
    form_class = DevelopmentLinkedAccountForm
    template_name = "focus_core/dev_linked_account_form.html"

    def dispatch(self, request, *args, **kwargs):
        if not settings.FOCUS_ENABLE_DEV_SIGN_IN:
            raise Http404()
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        provider = form.cleaned_data["provider"]
        handle = form.cleaned_data["handle"]
        subject_id = development_subject_id(provider, handle)
        existing_identity = AuthIdentity.objects.filter(provider=provider, subject_id=subject_id).first()

        if existing_identity and existing_identity.user == self.request.user:
            form.add_error("handle", "That development account is already connected.")
            return self.form_invalid(form)

        if existing_identity:
            form.add_error("handle", "That development account is already connected to another FOCUS user.")
            return self.form_invalid(form)

        identity = AuthIdentity.objects.create(
            user=self.request.user,
            provider=provider,
            subject_id=subject_id,
            handle=handle,
        )
        messages.success(self.request, f"Connected {identity.get_provider_display()} {identity.handle}.")
        return redirect("account_safety")


class LinkedAccountRemoveView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        identity = get_object_or_404(AuthIdentity, pk=kwargs["pk"], user=request.user)
        if not user_has_access_method(request.user, excluding_identity=identity):
            messages.error(request, "Create backup keys or add another sign-in method before removing this connected account.")
            return redirect("account_safety")

        account_name = str(identity)
        identity.delete()
        messages.success(request, f"Removed {account_name}.")
        return redirect("account_safety")


class PasskeyRegistrationView(LoginRequiredMixin, FormView):
    form_class = PasskeyRegistrationForm
    template_name = "focus_core/passkey_register.html"

    def post(self, request, *args, **kwargs):
        messages.error(request, "Use the Add passkey button to start passkey setup.")
        return redirect("passkey_register")


class PasskeyRegistrationOptionsView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        excluded_credentials = [
            PublicKeyCredentialDescriptor(id=credential_id_bytes(credential))
            for credential in request.user.webauthn_credentials.all()
        ]
        rp_id = request_rp_id(request)
        origin = request_origin(request)
        options = generate_registration_options(
            rp_id=rp_id,
            rp_name="FOCUS",
            user_id=str(request.user.pk).encode("utf-8"),
            user_name=f"focus-user-{request.user.pk}",
            user_display_name=request.user.public_name,
            exclude_credentials=excluded_credentials,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
            attestation=AttestationConveyancePreference.NONE,
        )

        request.session[PASSKEY_REGISTRATION_CHALLENGE_SESSION_KEY] = bytes_to_base64url(options.challenge)
        request.session[PASSKEY_REGISTRATION_RP_ID_SESSION_KEY] = rp_id
        request.session[PASSKEY_REGISTRATION_ORIGIN_SESSION_KEY] = origin
        return JsonResponse(json.loads(options_to_json(options)))


class PasskeyRegistrationCompleteView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        challenge = request.session.pop(PASSKEY_REGISTRATION_CHALLENGE_SESSION_KEY, None)
        rp_id = request.session.pop(PASSKEY_REGISTRATION_RP_ID_SESSION_KEY, None)
        origin = request.session.pop(PASSKEY_REGISTRATION_ORIGIN_SESSION_KEY, None)
        if not challenge or not rp_id or not origin:
            return JsonResponse(
                {"ok": False, "error": "Start passkey setup again."},
                status=400,
            )

        try:
            payload = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError:
            return JsonResponse({"ok": False, "error": "Passkey setup did not send a valid response."}, status=400)

        credential = payload.get("credential")
        passkey_name = (payload.get("name") or "").strip()
        if not credential:
            return JsonResponse({"ok": False, "error": "Passkey setup did not return a credential."}, status=400)

        try:
            verification = verify_registration_response(
                credential=credential,
                expected_challenge=base64url_to_bytes(challenge),
                expected_rp_id=rp_id,
                expected_origin=origin,
                require_user_verification=True,
            )
        except Exception:
            return JsonResponse({"ok": False, "error": "Passkey setup could not be verified. Try again."}, status=400)

        credential_id = bytes_to_base64url(verification.credential_id)
        if WebAuthnCredential.objects.filter(credential_id=credential_id).exists():
            return JsonResponse({"ok": False, "error": "That passkey is already connected."}, status=400)

        WebAuthnCredential.objects.create(
            user=request.user,
            credential_id=credential_id,
            public_key=bytes_to_base64url(verification.credential_public_key),
            name=passkey_name[:150],
            sign_count=verification.sign_count,
        )
        messages.success(request, "Passkey added.")
        return JsonResponse({"ok": True, "redirect_url": reverse("account_safety")})


class PasskeyUpdateView(LoginRequiredMixin, UpdateView):
    model = WebAuthnCredential
    form_class = PasskeyNameForm
    template_name = "focus_core/passkey_form.html"

    def get_queryset(self):
        return self.request.user.webauthn_credentials.all()

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Passkey name updated.")
        return response

    def get_success_url(self):
        return reverse("account_safety")


class PasskeyRemoveView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        passkey = get_object_or_404(WebAuthnCredential, pk=kwargs["pk"], user=request.user)
        if not user_has_access_method(request.user, excluding_passkey=passkey):
            messages.error(request, "Create backup keys or add another sign-in method before removing this passkey.")
            return redirect("account_safety")

        passkey_name = passkey.name or "Unnamed passkey"
        passkey.delete()
        messages.success(request, f"Removed {passkey_name}.")
        return redirect("account_safety")


class GroupCreateView(LoginRequiredMixin, CreateView):
    model = ProductionGroup
    form_class = ProductionGroupForm
    template_name = "focus_core/group_form.html"

    def form_valid(self, form):
        form.instance.slug = unique_group_slug(form.cleaned_data["name"])
        response = super().form_valid(form)
        Membership.objects.create(
            user=self.request.user,
            group=self.object,
            role=Membership.Role.OWNER,
        )
        return response

    def get_success_url(self):
        return reverse("group_detail", kwargs={"slug": self.object.slug})


class GroupDetailView(LoginRequiredMixin, DetailView):
    model = ProductionGroup
    template_name = "focus_core/group_detail.html"

    def get_queryset(self):
        return ProductionGroup.objects.filter(members__user=self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        all_projects = self.object.projects.all()
        active_projects = all_projects.filter(archived_at__isnull=True)
        archived_projects = all_projects.filter(archived_at__isnull=False)
        selected_archive = self.request.GET.get("archive") == "1"
        selected_status = "" if selected_archive else self.request.GET.get("status", "")
        valid_statuses = {value for value, _label in VideoProject.Status.choices}
        if selected_status not in valid_statuses:
            selected_status = ""

        projects = archived_projects if selected_archive else active_projects
        if selected_status:
            projects = projects.filter(status=selected_status)

        detail_url = reverse("group_detail", kwargs={"slug": self.object.slug})
        projects = projects.order_by("-updated_at", "title").prefetch_related(
            Prefetch(
                "notes",
                queryset=ProjectNote.objects.select_related("author"),
                to_attr="prefetched_notes",
            )
        )
        context["project_rows"] = [
            {
                "project": project,
                "latest_note": project.prefetched_notes[0] if project.prefetched_notes else None,
                "can_archive": user_can_archive_project(self.request.user, project),
                "can_manage": user_can_manage_project(self.request.user, project),
            }
            for project in projects
        ]
        active_project_count = active_projects.count()
        archived_project_count = archived_projects.count()
        context["project_total_count"] = archived_project_count if selected_archive else active_project_count
        context["active_project_count"] = active_project_count
        context["archived_project_count"] = archived_project_count
        context["selected_archive"] = selected_archive
        context["selected_status"] = selected_status
        context["can_create_projects"] = user_can_create_projects(self.request.user, self.object)
        context["project_scope_filters"] = [
            {
                "label": "Active projects",
                "count": active_project_count,
                "url": detail_url,
                "is_current": not selected_archive,
            },
            {
                "label": "Archived projects",
                "count": archived_project_count,
                "url": f"{detail_url}?archive=1",
                "is_current": selected_archive,
            },
        ]
        context["status_filters"] = [
            {
                "label": "All projects",
                "count": active_project_count,
                "url": detail_url,
                "is_current": selected_status == "",
            },
            *[
                {
                    "label": label,
                    "count": active_projects.filter(status=value).count(),
                    "url": f"{detail_url}?status={value}",
                    "is_current": selected_status == value,
                }
                for value, label in VideoProject.Status.choices
            ],
        ]
        context["membership"] = user_group_membership(self.request.user, self.object)
        return context


class GroupInvitationView(LoginRequiredMixin, FormView):
    form_class = GroupInvitationForm
    template_name = "focus_core/group_invitations.html"

    def dispatch(self, request, *args, **kwargs):
        self.group = get_object_or_404(ProductionGroup, slug=kwargs["slug"], members__user=request.user)
        self.membership = user_group_membership(request.user, self.group)
        if self.membership.role != Membership.Role.OWNER:
            raise PermissionDenied("Only group owners can manage invite links.")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        invitation = form.save(commit=False)
        invitation.group = self.group
        invitation.save()
        notify_group_activity(
            self.group,
            self.request.user,
            GroupNotification.Kind.INVITE_CREATED,
            f"{self.request.user.public_name} created an invite link for {invitation.get_role_to_assign_display()} in {self.group.name}.",
        )
        messages.success(self.request, "Invite link created.")
        return redirect("group_invitations", slug=self.group.slug)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        invitations = self.group.invitations.order_by("-created_at")
        context["group"] = self.group
        context["membership"] = self.membership
        context["invite_rows"] = [
            {
                "invitation": invitation,
                "accept_url": self.request.build_absolute_uri(
                    reverse("invite_accept", kwargs={"token": invitation.token})
                ),
            }
            for invitation in invitations
        ]
        return context


class GroupInvitationRevokeView(LoginRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        self.group = get_object_or_404(ProductionGroup, slug=kwargs["slug"], members__user=request.user)
        self.membership = user_group_membership(request.user, self.group)
        if self.membership.role != Membership.Role.OWNER:
            raise PermissionDenied("Only group owners can revoke invite links.")
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        invitation = get_object_or_404(GroupInvitation, pk=kwargs["pk"], group=self.group)
        if invitation.is_used:
            messages.error(request, "Used invite links cannot be revoked.")
        elif invitation.revoked_at:
            messages.info(request, "That invite link is already revoked.")
        else:
            invitation.revoked_at = timezone.now()
            invitation.save(update_fields=["revoked_at"])
            notify_group_activity(
                self.group,
                request.user,
                GroupNotification.Kind.INVITE_REVOKED,
                f"{request.user.public_name} revoked an invite link for {invitation.get_role_to_assign_display()} in {self.group.name}.",
            )
            messages.success(request, "Invite link revoked.")
        return redirect("group_invitations", slug=self.group.slug)


class GroupMembersView(LoginRequiredMixin, TemplateView):
    template_name = "focus_core/group_members.html"

    def dispatch(self, request, *args, **kwargs):
        self.group = get_object_or_404(ProductionGroup, slug=kwargs["slug"], members__user=request.user)
        self.membership = user_group_membership(request.user, self.group)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        members = self.group.members.select_related("user").order_by("joined_at")
        can_manage_members = self.membership.role == Membership.Role.OWNER
        context["group"] = self.group
        context["membership"] = self.membership
        context["can_manage_members"] = can_manage_members
        context["can_leave_group"] = self.membership.role != Membership.Role.OWNER or self.group.has_another_owner(self.membership)
        context["member_rows"] = [
            {
                "membership": membership,
                "form": MembershipRoleForm(
                    instance=membership,
                    prefix=f"membership-{membership.pk}",
                ),
                "can_remove": membership.role != Membership.Role.OWNER or self.group.has_another_owner(membership),
            }
            for membership in members
        ]
        return context


class MemberProfileView(LoginRequiredMixin, DetailView):
    model = Membership
    template_name = "focus_core/member_profile.html"

    def dispatch(self, request, *args, **kwargs):
        self.group = get_object_or_404(ProductionGroup, slug=kwargs["slug"], members__user=request.user)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return Membership.objects.filter(group=self.group).select_related("group", "user")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile_user = self.object.user
        assigned_projects = VideoProject.objects.none()
        if profile_user.show_assigned_projects:
            assigned_projects = (
                VideoProject.objects.filter(group=self.group)
                .filter(Q(assigned_editors=profile_user) | Q(assigned_writers=profile_user))
                .filter(archived_at__isnull=True)
                .prefetch_related(
                    Prefetch(
                        "notes",
                        queryset=ProjectNote.objects.select_related("author"),
                        to_attr="prefetched_notes",
                    )
                )
                .distinct()
                .order_by("-updated_at", "title")
            )

        context["group"] = self.group
        context["profile_user"] = profile_user
        context["assignment_rows"] = [
            {
                "project": project,
                "roles": [
                    role
                    for role, assignees in (
                        ("Editor", project.assigned_editors.all()),
                        ("Writer", project.assigned_writers.all()),
                    )
                    if profile_user in assignees
                ],
                "latest_note": project.prefetched_notes[0] if project.prefetched_notes else None,
            }
            for project in assigned_projects.prefetch_related("assigned_editors", "assigned_writers")
        ]
        return context


class MembershipRoleUpdateView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        group = get_object_or_404(ProductionGroup, slug=kwargs["slug"], members__user=request.user)
        current_membership = user_group_membership(request.user, group)
        if current_membership.role != Membership.Role.OWNER:
            raise PermissionDenied("Only group owners can update member roles.")

        membership = get_object_or_404(Membership, pk=kwargs["pk"], group=group)
        previous_role = membership.role
        form = MembershipRoleForm(
            request.POST,
            instance=membership,
            prefix=f"membership-{membership.pk}",
        )
        if form.is_valid():
            updated_membership = form.save()
            if previous_role != updated_membership.role:
                notify_group_activity(
                    group,
                    request.user,
                    GroupNotification.Kind.MEMBER_ROLE,
                    f"{request.user.public_name} changed {updated_membership.user.public_name}'s role in {group.name} to {updated_membership.get_role_display()}.",
                    include_user_ids=[updated_membership.user_id],
                )
            messages.success(request, f"Updated {updated_membership.user.public_name}'s role.")
        else:
            messages.error(request, " ".join(error for errors in form.errors.values() for error in errors))

        return redirect("group_members", slug=group.slug)


class MembershipRemoveView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        group = get_object_or_404(ProductionGroup, slug=kwargs["slug"], members__user=request.user)
        current_membership = user_group_membership(request.user, group)
        if current_membership.role != Membership.Role.OWNER:
            raise PermissionDenied("Only group owners can remove members.")

        membership = get_object_or_404(Membership, pk=kwargs["pk"], group=group)
        removed_user = membership.user
        try:
            membership.delete()
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
            return redirect("group_members", slug=group.slug)

        notify_group_activity(
            group,
            request.user,
            GroupNotification.Kind.MEMBER_REMOVED,
            f"{request.user.public_name} removed {removed_user.public_name} from {group.name}.",
        )
        messages.success(request, f"Removed {removed_user.public_name} from {group.name}.")
        if removed_user == request.user:
            return redirect("dashboard")
        return redirect("group_members", slug=group.slug)


class MembershipLeaveView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        group = get_object_or_404(ProductionGroup, slug=kwargs["slug"], members__user=request.user)
        membership = get_object_or_404(Membership, group=group, user=request.user)
        try:
            membership.delete()
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
            return redirect("group_members", slug=group.slug)

        notify_group_activity(
            group,
            request.user,
            GroupNotification.Kind.MEMBER_LEFT,
            f"{request.user.public_name} left {group.name}.",
        )
        messages.success(request, f"You left {group.name}.")
        return redirect("dashboard")


class InvitationAcceptView(TemplateView):
    template_name = "focus_core/invite_accept.html"

    def dispatch(self, request, *args, **kwargs):
        self.invitation = get_object_or_404(
            GroupInvitation.objects.select_related("group"),
            token=kwargs["token"],
        )
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            messages.info(request, "Sign in before accepting this invite.")
            sign_in_view = "dev_sign_in" if settings.FOCUS_ENABLE_DEV_SIGN_IN else "passkey_sign_in"
            return redirect(url_with_next_path(sign_in_view, request.path))

        existing_membership = user_group_membership(request.user, self.invitation.group)
        if existing_membership:
            messages.info(request, "You already belong to this production group.")
            return redirect("group_detail", slug=self.invitation.group.slug)

        if self.invitation.is_used or self.invitation.revoked_at:
            return self.get(request, *args, **kwargs)

        membership = Membership.objects.create(
            user=request.user,
            group=self.invitation.group,
            role=self.invitation.role_to_assign,
        )
        self.invitation.is_used = True
        self.invitation.save(update_fields=["is_used"])
        notify_group_activity(
            self.invitation.group,
            request.user,
            GroupNotification.Kind.INVITE_ACCEPTED,
            f"{request.user.public_name} joined {self.invitation.group.name} as {membership.get_role_display()}.",
        )
        messages.success(request, f"You joined {self.invitation.group.name}.")
        return redirect("group_detail", slug=self.invitation.group.slug)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["invitation"] = self.invitation
        context["is_revoked"] = self.invitation.revoked_at is not None
        context["already_member"] = (
            self.request.user.is_authenticated
            and user_group_membership(self.request.user, self.invitation.group) is not None
        )
        context["can_accept_invite"] = self.request.user.is_authenticated
        context["can_use_development_sign_in"] = settings.FOCUS_ENABLE_DEV_SIGN_IN
        context["passkey_sign_in_url"] = url_with_next_path("passkey_sign_in", self.request.path)
        context["backup_key_sign_in_url"] = url_with_next_path("backup_key_sign_in", self.request.path)
        context["dev_sign_in_url"] = url_with_next_path("dev_sign_in", self.request.path)
        return context


class ProjectCreateView(LoginRequiredMixin, CreateView):
    model = VideoProject
    form_class = VideoProjectForm
    template_name = "focus_core/project_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.group = get_object_or_404(ProductionGroup, slug=kwargs["slug"], members__user=request.user)
        if not user_can_create_projects(request.user, self.group):
            raise PermissionDenied("Only group owners or admins can create projects.")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.group = self.group
        form.instance.created_by = self.request.user
        return super().form_valid(form)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["group"] = self.group
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["group"] = self.group
        return context

    def get_success_url(self):
        return reverse("group_detail", kwargs={"slug": self.group.slug})


class ProjectDetailView(LoginRequiredMixin, DetailView):
    model = VideoProject
    template_name = "focus_core/project_detail.html"

    def get_queryset(self):
        return (
            VideoProject.objects.filter(
                group__slug=self.kwargs["group_slug"],
                group__members__user=self.request.user,
            )
            .select_related("group", "created_by")
            .prefetch_related("assigned_editors", "assigned_writers")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["group"] = self.object.group
        context.setdefault("status_form", ProjectStatusForm(instance=self.object))
        context.setdefault("note_form", ProjectNoteForm())
        context.setdefault("resource_form", ProjectResourceForm())
        context["resource_rows"] = project_resource_rows(self.request.user, self.object)
        context["notes"] = self.object.notes.select_related("author")
        context.update(project_permission_context(self.request.user, self.object))
        return context


class ProjectExportView(LoginRequiredMixin, View):
    def get_project(self):
        return get_object_or_404(
            VideoProject.objects.filter(
                group__slug=self.kwargs["group_slug"],
                group__members__user=self.request.user,
            )
            .select_related("group", "created_by")
            .prefetch_related("assigned_editors", "assigned_writers"),
            pk=self.kwargs["pk"],
        )

    def get(self, request, *args, **kwargs):
        project = self.get_project()
        filename = f"{slugify(project.title) or 'project'}-focus-export.md"
        response = HttpResponse(project_export_markdown(project), content_type="text/markdown; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class ProjectNoteCreateView(LoginRequiredMixin, View):
    def get_project(self):
        return get_object_or_404(
            VideoProject.objects.filter(
                group__slug=self.kwargs["group_slug"],
                group__members__user=self.request.user,
            )
            .select_related("group")
            .prefetch_related("assigned_editors", "assigned_writers"),
            pk=self.kwargs["pk"],
        )

    def post(self, request, *args, **kwargs):
        project = self.get_project()
        if not user_can_update_project_activity(request.user, project):
            raise PermissionDenied("Only assigned collaborators, the project creator, group owners, or admins can add project notes.")

        form = ProjectNoteForm(request.POST)
        if form.is_valid():
            note = form.save(commit=False)
            note.project = project
            note.author = request.user
            note.save()
            notify_project_activity(
                project,
                request.user,
                ProjectNotification.Kind.NOTE,
                f"{request.user.public_name} added a note to {project.title}.",
            )
            messages.success(request, "Project note added.")
            return redirect(f"{reverse('project_detail', kwargs={'group_slug': project.group.slug, 'pk': project.pk})}#project-notes")

        context = {
            "object": project,
            "group": project.group,
            "status_form": ProjectStatusForm(instance=project),
            "note_form": form,
            "resource_form": ProjectResourceForm(),
            "resource_rows": project_resource_rows(request.user, project),
            "notes": project.notes.select_related("author"),
            **project_permission_context(request.user, project),
        }
        return render(request, "focus_core/project_detail.html", context)


class ProjectResourceCreateView(LoginRequiredMixin, View):
    def get_project(self):
        return get_object_or_404(
            VideoProject.objects.filter(
                group__slug=self.kwargs["group_slug"],
                group__members__user=self.request.user,
            )
            .select_related("group", "created_by")
            .prefetch_related("assigned_editors", "assigned_writers"),
            pk=self.kwargs["pk"],
        )

    def post(self, request, *args, **kwargs):
        project = self.get_project()
        if not user_can_update_project_activity(request.user, project):
            raise PermissionDenied("Only assigned collaborators, the project creator, group owners, or admins can add project resources.")

        form = ProjectResourceForm(request.POST)
        if form.is_valid():
            resource = form.save(commit=False)
            resource.project = project
            resource.added_by = request.user
            resource.save()
            ProjectNote.objects.create(
                project=project,
                author=request.user,
                body=f"Resource added: {resource.title} ({resource.get_kind_display()}).",
            )
            notify_project_activity(
                project,
                request.user,
                ProjectNotification.Kind.RESOURCE,
                f"{request.user.public_name} added {resource.title} to {project.title}.",
            )
            messages.success(request, f"Added {resource.title} to {project.title}.")
            return redirect(f"{reverse('project_detail', kwargs={'group_slug': project.group.slug, 'pk': project.pk})}#project-resources")

        context = {
            "object": project,
            "group": project.group,
            "status_form": ProjectStatusForm(instance=project),
            "note_form": ProjectNoteForm(),
            "resource_form": form,
            "resource_rows": project_resource_rows(request.user, project),
            "notes": project.notes.select_related("author"),
            **project_permission_context(request.user, project),
        }
        return render(request, "focus_core/project_detail.html", context)


class ProjectResourceRemoveView(LoginRequiredMixin, View):
    def get_resource(self):
        return get_object_or_404(
            ProjectResource.objects.filter(
                project__group__slug=self.kwargs["group_slug"],
                project__group__members__user=self.request.user,
            ).select_related("project", "project__group", "project__created_by", "added_by"),
            pk=self.kwargs["resource_pk"],
            project_id=self.kwargs["pk"],
        )

    def post(self, request, *args, **kwargs):
        resource = self.get_resource()
        project = resource.project
        if not user_can_remove_project_resource(request.user, resource):
            raise PermissionDenied("Only the person who added this resource, the project creator, group owners, or admins can remove it.")

        title = resource.title
        resource.delete()
        ProjectNote.objects.create(project=project, author=request.user, body=f"Resource removed: {title}.")
        notify_project_activity(
            project,
            request.user,
            ProjectNotification.Kind.RESOURCE,
            f"{request.user.public_name} removed {title} from {project.title}.",
        )
        messages.success(request, f"Removed {title} from {project.title}.")
        return redirect(f"{reverse('project_detail', kwargs={'group_slug': project.group.slug, 'pk': project.pk})}#project-resources")


class ProjectArchiveView(LoginRequiredMixin, View):
    def get_project(self):
        return get_object_or_404(
            VideoProject.objects.filter(
                group__slug=self.kwargs["group_slug"],
                group__members__user=self.request.user,
            ).select_related("group", "created_by"),
            pk=self.kwargs["pk"],
        )

    def post(self, request, *args, **kwargs):
        project = self.get_project()
        if not user_can_archive_project(request.user, project):
            raise PermissionDenied("Only the project creator, group owners, or admins can archive projects.")

        if project.archived_at:
            messages.info(request, f"{project.title} is already archived.")
        else:
            project.archived_at = timezone.now()
            project.save(update_fields=["archived_at", "updated_at"])
            ProjectNote.objects.create(project=project, author=request.user, body="Project archived.")
            notify_project_activity(
                project,
                request.user,
                ProjectNotification.Kind.ARCHIVE,
                f"{request.user.public_name} archived {project.title}.",
            )
            messages.success(request, f"Archived {project.title}.")
        return redirect("project_detail", group_slug=project.group.slug, pk=project.pk)


class ProjectRestoreView(LoginRequiredMixin, View):
    def get_project(self):
        return get_object_or_404(
            VideoProject.objects.filter(
                group__slug=self.kwargs["group_slug"],
                group__members__user=self.request.user,
            ).select_related("group", "created_by"),
            pk=self.kwargs["pk"],
        )

    def post(self, request, *args, **kwargs):
        project = self.get_project()
        if not user_can_archive_project(request.user, project):
            raise PermissionDenied("Only the project creator, group owners, or admins can restore projects.")

        if project.archived_at:
            project.archived_at = None
            project.save(update_fields=["archived_at", "updated_at"])
            ProjectNote.objects.create(project=project, author=request.user, body="Project restored.")
            notify_project_activity(
                project,
                request.user,
                ProjectNotification.Kind.RESTORE,
                f"{request.user.public_name} restored {project.title}.",
            )
            messages.success(request, f"Restored {project.title}.")
        else:
            messages.info(request, f"{project.title} is already active.")
        return redirect("project_detail", group_slug=project.group.slug, pk=project.pk)


class ProjectDeleteView(LoginRequiredMixin, View):
    def get_project(self):
        return get_object_or_404(
            VideoProject.objects.filter(
                group__slug=self.kwargs["group_slug"],
                group__members__user=self.request.user,
            ).select_related("group", "created_by"),
            pk=self.kwargs["pk"],
        )

    def get_context_data(self, project):
        return {
            "object": project,
            "group": project.group,
            "note_count": project.notes.count(),
            "resource_count": project.resources.count(),
        }

    def get(self, request, *args, **kwargs):
        project = self.get_project()
        if not user_can_delete_project(request.user, project):
            raise PermissionDenied("Only the project creator, group owners, or admins can delete projects.")
        return render(request, "focus_core/project_confirm_delete.html", self.get_context_data(project))

    def post(self, request, *args, **kwargs):
        project = self.get_project()
        if not user_can_delete_project(request.user, project):
            raise PermissionDenied("Only the project creator, group owners, or admins can delete projects.")

        group = project.group
        title = project.title
        project.delete()
        messages.success(request, f"Deleted {title}.")
        return redirect("group_detail", slug=group.slug)


class ProjectStatusUpdateView(LoginRequiredMixin, UpdateView):
    model = VideoProject
    form_class = ProjectStatusForm
    template_name = "focus_core/project_detail.html"

    def get_queryset(self):
        return (
            VideoProject.objects.filter(
                group__slug=self.kwargs["group_slug"],
                group__members__user=self.request.user,
            )
            .select_related("group", "created_by")
            .prefetch_related("assigned_editors", "assigned_writers")
        )

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        if not user_can_update_project_activity(request.user, self.object):
            raise PermissionDenied("Only assigned collaborators, the project creator, group owners, or admins can update project status.")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        previous_status = VideoProject.objects.values_list("status", flat=True).get(pk=self.object.pk)
        response = super().form_valid(form)
        if previous_status != self.object.status:
            ProjectNote.objects.create(
                project=self.object,
                author=self.request.user,
                body=(
                    "Status changed from "
                    f"{dict(VideoProject.Status.choices)[previous_status]} "
                    f"to {self.object.get_status_display()}."
                ),
            )
            notify_project_activity(
                self.object,
                self.request.user,
                ProjectNotification.Kind.STATUS,
                f"{self.request.user.public_name} changed {self.object.title}'s status to {self.object.get_status_display()}.",
            )
        messages.success(self.request, f"Updated {self.object.title}'s status.")
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["group"] = self.object.group
        context["status_form"] = context["form"]
        context["note_form"] = ProjectNoteForm()
        context["resource_form"] = ProjectResourceForm()
        context["resource_rows"] = project_resource_rows(self.request.user, self.object)
        context["notes"] = self.object.notes.select_related("author")
        context.update(project_permission_context(self.request.user, self.object))
        return context

    def get_success_url(self):
        return reverse("project_detail", kwargs={"group_slug": self.object.group.slug, "pk": self.object.pk})


class ProjectUpdateView(LoginRequiredMixin, UpdateView):
    model = VideoProject
    form_class = VideoProjectForm
    template_name = "focus_core/project_form.html"

    def get_queryset(self):
        return VideoProject.objects.filter(group__slug=self.kwargs["group_slug"], group__members__user=self.request.user)

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        if not user_can_manage_project(request.user, self.object):
            raise PermissionDenied("Only the project creator, group owners, or admins can edit projects.")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["group"] = self.object.group
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["group"] = self.object.group
        return context

    def get_success_url(self):
        return reverse("group_detail", kwargs={"slug": self.object.group.slug})
