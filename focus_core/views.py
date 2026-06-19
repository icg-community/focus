import json
import secrets

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
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

from .forms import BackupKeySignInForm, DevelopmentLinkedAccountForm, DisplayNameForm, GroupInvitationForm, MembershipRoleForm, PasskeyNameForm, PasskeyRegistrationForm, ProductionGroupForm, ProjectStatusForm, VideoProjectForm
from .models import AuthIdentity, GroupInvitation, Membership, ProductionGroup, RecoveryCode, VideoProject, WebAuthnCredential


RECOVERY_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
RECOVERY_CODE_COUNT = 8
PASSKEY_REGISTRATION_CHALLENGE_SESSION_KEY = "passkey_registration_challenge"
PASSKEY_REGISTRATION_RP_ID_SESSION_KEY = "passkey_registration_rp_id"
PASSKEY_REGISTRATION_ORIGIN_SESSION_KEY = "passkey_registration_origin"
PASSKEY_AUTHENTICATION_CHALLENGE_SESSION_KEY = "passkey_authentication_challenge"
PASSKEY_AUTHENTICATION_RP_ID_SESSION_KEY = "passkey_authentication_rp_id"
PASSKEY_AUTHENTICATION_ORIGIN_SESSION_KEY = "passkey_authentication_origin"
PASSKEY_AUTHENTICATION_NEXT_SESSION_KEY = "passkey_authentication_next"


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
            .select_related("group")
            .prefetch_related("assigned_editors", "assigned_writers")
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
            }
            for project in assigned_projects
        ]
        return context


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
        selected_status = self.request.GET.get("status", "")
        valid_statuses = {value for value, _label in VideoProject.Status.choices}
        if selected_status not in valid_statuses:
            selected_status = ""

        projects = all_projects
        if selected_status:
            projects = projects.filter(status=selected_status)

        detail_url = reverse("group_detail", kwargs={"slug": self.object.slug})
        context["projects"] = projects.order_by("-updated_at", "title")
        context["project_total_count"] = all_projects.count()
        context["selected_status"] = selected_status
        context["status_filters"] = [
            {
                "label": "All projects",
                "count": all_projects.count(),
                "url": detail_url,
                "is_current": selected_status == "",
            },
            *[
                {
                    "label": label,
                    "count": all_projects.filter(status=value).count(),
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
        context["member_rows"] = [
            {
                "membership": membership,
                "form": MembershipRoleForm(
                    instance=membership,
                    prefix=f"membership-{membership.pk}",
                ),
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
        form = MembershipRoleForm(
            request.POST,
            instance=membership,
            prefix=f"membership-{membership.pk}",
        )
        if form.is_valid():
            updated_membership = form.save()
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

        messages.success(request, f"Removed {removed_user.public_name} from {group.name}.")
        if removed_user == request.user:
            return redirect("dashboard")
        return redirect("group_members", slug=group.slug)


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

        if self.invitation.is_used:
            return self.get(request, *args, **kwargs)

        Membership.objects.create(
            user=request.user,
            group=self.invitation.group,
            role=self.invitation.role_to_assign,
        )
        self.invitation.is_used = True
        self.invitation.save(update_fields=["is_used"])
        messages.success(request, f"You joined {self.invitation.group.name}.")
        return redirect("group_detail", slug=self.invitation.group.slug)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["invitation"] = self.invitation
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
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.group = self.group
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
            .select_related("group")
            .prefetch_related("assigned_editors", "assigned_writers")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["group"] = self.object.group
        context.setdefault("status_form", ProjectStatusForm(instance=self.object))
        return context


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
            .select_related("group")
            .prefetch_related("assigned_editors", "assigned_writers")
        )

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"Updated {self.object.title}'s status.")
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["group"] = self.object.group
        context["status_form"] = context["form"]
        return context

    def get_success_url(self):
        return reverse("project_detail", kwargs={"group_slug": self.object.group.slug, "pk": self.object.pk})


class ProjectUpdateView(LoginRequiredMixin, UpdateView):
    model = VideoProject
    form_class = VideoProjectForm
    template_name = "focus_core/project_form.html"

    def get_queryset(self):
        return VideoProject.objects.filter(group__slug=self.kwargs["group_slug"], group__members__user=self.request.user)

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
