from django import forms

from .models import GroupInvitation, ProductionGroup, VideoProject


class AccessibleModelForm(forms.ModelForm):
    error_css_class = "field-error"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.widget.attrs.setdefault("class", "field-control")
            descriptions = [f"{name}-help"]
            if self.is_bound and name in self.errors:
                descriptions.append(f"{name}-error")
                field.widget.attrs["aria-invalid"] = "true"
            field.widget.attrs["aria-describedby"] = " ".join(descriptions)
            if field.required:
                field.widget.attrs["required"] = True


class ProductionGroupForm(AccessibleModelForm):
    class Meta:
        model = ProductionGroup
        fields = ["name"]
        labels = {
            "name": "Group name",
        }
        help_texts = {
            "name": "Use the studio, channel, or project team name people will recognize.",
        }


class VideoProjectForm(AccessibleModelForm):
    class Meta:
        model = VideoProject
        fields = ["title", "description", "status", "asset_pipeline_url", "script_url"]
        labels = {
            "title": "Project title",
            "description": "Project description",
            "status": "Pipeline status",
            "asset_pipeline_url": "Asset folder link",
            "script_url": "Script link",
        }
        help_texts = {
            "title": "Use the working title your team uses in production.",
            "description": "Add any context the team needs before working on this project.",
            "status": "Choose the current stage of production.",
            "asset_pipeline_url": "Optional link to the shared asset folder.",
            "script_url": "Optional link to the current script document or file.",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 5}),
        }


class GroupInvitationForm(AccessibleModelForm):
    class Meta:
        model = GroupInvitation
        fields = ["role_to_assign"]
        labels = {
            "role_to_assign": "Role for this invite",
        }
        help_texts = {
            "role_to_assign": "Choose the role the next person receives when they accept this link.",
        }
