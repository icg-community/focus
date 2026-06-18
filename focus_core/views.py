from django.conf import settings
from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.text import slugify
from django.views import View
from django.views.generic import CreateView, DetailView, FormView, TemplateView, UpdateView

from .forms import DisplayNameForm, GroupInvitationForm, MembershipRoleForm, ProductionGroupForm, ProjectStatusForm, VideoProjectForm
from .models import AuthIdentity, GroupInvitation, Membership, ProductionGroup, VideoProject


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


class DevSignInView(FormView):
    template_name = "focus_core/dev_sign_in.html"
    form_class = forms.Form

    def dispatch(self, request, *args, **kwargs):
        if not settings.FOCUS_ENABLE_DEV_SIGN_IN:
            raise Http404()
        return super().dispatch(request, *args, **kwargs)

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
        return redirect(request.GET.get("next") or reverse("dashboard"))


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


class InvitationAcceptView(LoginRequiredMixin, TemplateView):
    template_name = "focus_core/invite_accept.html"

    def dispatch(self, request, *args, **kwargs):
        self.invitation = get_object_or_404(
            GroupInvitation.objects.select_related("group"),
            token=kwargs["token"],
        )
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
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
        context["already_member"] = user_group_membership(self.request.user, self.invitation.group) is not None
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
