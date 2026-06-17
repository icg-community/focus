from django.conf import settings
from django import forms
from django.contrib.auth import get_user_model, login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.text import slugify
from django.views.generic import CreateView, DetailView, FormView, TemplateView, UpdateView

from .forms import ProductionGroupForm, VideoProjectForm
from .models import AuthIdentity, Membership, ProductionGroup, VideoProject


def unique_group_slug(name):
    base_slug = slugify(name) or "group"
    slug = base_slug
    suffix = 2
    while ProductionGroup.objects.filter(slug=slug).exists():
        slug = f"{base_slug}-{suffix}"
        suffix += 1
    return slug


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
        context["groups"] = ProductionGroup.objects.filter(members__user=self.request.user).order_by("name")
        return context


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
        context["projects"] = self.object.projects.order_by("-updated_at", "title")
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["group"] = self.group
        return context

    def get_success_url(self):
        return reverse("group_detail", kwargs={"slug": self.group.slug})


class ProjectUpdateView(LoginRequiredMixin, UpdateView):
    model = VideoProject
    form_class = VideoProjectForm
    template_name = "focus_core/project_form.html"

    def get_queryset(self):
        return VideoProject.objects.filter(group__slug=self.kwargs["group_slug"], group__members__user=self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["group"] = self.object.group
        return context

    def get_success_url(self):
        return reverse("group_detail", kwargs={"slug": self.object.group.slug})
