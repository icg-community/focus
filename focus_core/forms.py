from django import forms

from .models import FocusUser, GroupInvitation, Membership, ProductionGroup, VideoProject, WebAuthnCredential


class AccessibleForm(forms.Form):
    error_css_class = "field-error"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.widget.attrs.setdefault("class", "field-control")
            prefixed_name = self.add_prefix(name)
            field.widget.attrs.setdefault("id", f"id_{prefixed_name}")
            descriptions = [f"{prefixed_name}-help"]
            if self.is_bound and name in self.errors:
                descriptions.append(f"{prefixed_name}-error")
                field.widget.attrs["aria-invalid"] = "true"
            field.widget.attrs["aria-describedby"] = " ".join(descriptions)
            if field.required:
                field.widget.attrs["required"] = True


class AccessibleModelForm(forms.ModelForm):
    error_css_class = "field-error"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxSelectMultiple):
                field.widget.attrs.setdefault("class", "checkbox-input")
            elif isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", "checkbox-input")
            else:
                field.widget.attrs.setdefault("class", "field-control")
            prefixed_name = self.add_prefix(name)
            field.widget.attrs.setdefault("id", f"id_{prefixed_name}")
            descriptions = [f"{prefixed_name}-help"]
            if self.is_bound and name in self.errors:
                descriptions.append(f"{prefixed_name}-error")
                field.widget.attrs["aria-invalid"] = "true"
            field.widget.attrs["aria-describedby"] = " ".join(descriptions)
            if field.required:
                field.widget.attrs["required"] = True


class BackupKeySignInForm(AccessibleForm):
    backup_key = forms.CharField(
        label="Backup key",
        help_text="Enter one saved backup key. Spaces and hyphens are okay.",
        max_length=64,
    )

    def clean_backup_key(self):
        value = self.cleaned_data["backup_key"]
        compact_value = "".join(character for character in value.upper() if character not in " -\t\r\n")
        if len(compact_value) == 16:
            return "-".join(compact_value[index : index + 4] for index in range(0, 16, 4))
        return value.strip().upper()


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


class DisplayNameForm(AccessibleModelForm):
    class Meta:
        model = FocusUser
        fields = ["display_name", "bio", "availability", "show_assigned_projects"]
        labels = {
            "display_name": "Display name",
            "bio": "Bio",
            "availability": "Availability",
            "show_assigned_projects": "Show projects I am working on",
        }
        help_texts = {
            "display_name": "Optional public alias shown to your production groups. Leave blank to use your connected account handle.",
            "bio": "Optional short intro for people in your production groups. Avoid email addresses, legal names, phone numbers, or private schedule details.",
            "availability": "Choose the simple status you want group members to see.",
            "show_assigned_projects": "When profile sharing is added, only people who already share access to a group with you will be able to see your assigned projects.",
        }
        widgets = {
            "bio": forms.Textarea(attrs={"rows": 4}),
        }


class PasskeyNameForm(AccessibleModelForm):
    class Meta:
        model = WebAuthnCredential
        fields = ["name"]
        labels = {
            "name": "Passkey name",
        }
        help_texts = {
            "name": "Use a name that helps you recognize this device, such as Laptop or Phone.",
        }


class VideoProjectForm(AccessibleModelForm):
    def __init__(self, *args, group=None, **kwargs):
        super().__init__(*args, **kwargs)
        member_queryset = FocusUser.objects.none()
        if group:
            member_queryset = FocusUser.objects.filter(memberships__group=group).order_by("display_name", "username")
        self.fields["assigned_editors"].queryset = member_queryset
        self.fields["assigned_writers"].queryset = member_queryset

    class Meta:
        model = VideoProject
        fields = [
            "title",
            "description",
            "status",
            "asset_pipeline_url",
            "script_url",
            "assigned_editors",
            "assigned_writers",
        ]
        labels = {
            "title": "Project title",
            "description": "Project description",
            "status": "Pipeline status",
            "asset_pipeline_url": "Asset folder link",
            "script_url": "Script link",
            "assigned_editors": "Assigned editors",
            "assigned_writers": "Assigned writers",
        }
        help_texts = {
            "title": "Use the working title your team uses in production.",
            "description": "Add any context the team needs before working on this project.",
            "status": "Choose the current stage of production.",
            "asset_pipeline_url": "Optional link to the shared asset folder.",
            "script_url": "Optional link to the current script document or file.",
            "assigned_editors": "Choose group members responsible for editing this project.",
            "assigned_writers": "Choose group members responsible for writing or script updates.",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 5}),
            "assigned_editors": forms.CheckboxSelectMultiple,
            "assigned_writers": forms.CheckboxSelectMultiple,
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


class ProjectStatusForm(AccessibleModelForm):
    class Meta:
        model = VideoProject
        fields = ["status"]
        labels = {
            "status": "Pipeline status",
        }
        help_texts = {
            "status": "Choose the current stage for this project.",
        }


class MembershipRoleForm(AccessibleModelForm):
    class Meta:
        model = Membership
        fields = ["role"]
        labels = {
            "role": "Role",
        }
        help_texts = {
            "role": "Choose this member's role in the production group.",
        }
