import uuid

from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class FocusUser(AbstractUser):
    """
    Pseudonymous user account.

    The username is an internal opaque identifier. Public names come from a
    linked provider handle unless the user chooses a display alias.
    """

    first_name = None
    last_name = None
    email = None

    class Availability(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available for new work"
        LIMITED = "LIMITED", "Limited availability"
        BUSY = "BUSY", "Busy"
        UNAVAILABLE = "UNAVAILABLE", "Not available"

    display_name = models.CharField(max_length=150, blank=True)
    bio = models.TextField(blank=True, max_length=500)
    availability = models.CharField(
        max_length=12,
        choices=Availability.choices,
        default=Availability.AVAILABLE,
    )
    show_assigned_projects = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        if not self.username:
            self.username = f"user-{uuid.uuid4()}"
        if not self.password or self.has_usable_password():
            self.set_unusable_password()
        super().save(*args, **kwargs)

    @property
    def public_name(self):
        if self.display_name:
            return self.display_name

        identity = self.identities.order_by("created_at").first()
        if identity:
            return identity.handle

        return self.username

    def __str__(self):
        return self.public_name


class AuthIdentity(models.Model):
    PROVIDER_CHOICES = [
        ("DISCORD", "Discord"),
        ("GITHUB", "GitHub"),
        ("MASTODON", "Mastodon"),
    ]

    user = models.ForeignKey(FocusUser, on_delete=models.CASCADE, related_name="identities")
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES)
    subject_id = models.CharField(max_length=255)
    handle = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "subject_id"],
                name="unique_provider_subject",
            ),
        ]

    def __str__(self):
        return f"{self.get_provider_display()}: {self.handle}"


class RecoveryCode(models.Model):
    user = models.ForeignKey(FocusUser, on_delete=models.CASCADE, related_name="recovery_codes")
    code_hash = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    @classmethod
    def create_for_code(cls, user, code):
        return cls.objects.create(user=user, code_hash=make_password(code))

    def matches(self, code):
        return self.used_at is None and check_password(code, self.code_hash)

    def mark_used(self):
        self.used_at = timezone.now()
        self.save(update_fields=["used_at"])

    def __str__(self):
        status = "used" if self.used_at else "available"
        return f"Recovery code for {self.user.public_name} ({status})"


class WebAuthnCredential(models.Model):
    user = models.ForeignKey(FocusUser, on_delete=models.CASCADE, related_name="webauthn_credentials")
    credential_id = models.CharField(max_length=512, unique=True)
    public_key = models.TextField()
    name = models.CharField(max_length=150, blank=True)
    sign_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name or f"Passkey for {self.user.public_name}"


class ProductionGroup(models.Model):
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    slug = models.SlugField(unique=True, max_length=255)

    def has_another_owner(self, membership):
        return self.members.filter(role=Membership.Role.OWNER).exclude(pk=membership.pk).exists()

    def __str__(self):
        return self.name


class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER = "OWNER", "Group Owner"
        ADMIN = "ADMIN", "Admin / Producer"
        EDITOR = "EDITOR", "Video Editor"
        TALENT = "TALENT", "Voice Actor / Talent"
        WRITER = "WRITER", "Script Writer"

    user = models.ForeignKey(FocusUser, on_delete=models.CASCADE, related_name="memberships")
    group = models.ForeignKey(ProductionGroup, on_delete=models.CASCADE, related_name="members")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.TALENT)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "group"], name="unique_user_group_membership"),
        ]

    def clean(self):
        if not self.pk:
            return

        previous = Membership.objects.get(pk=self.pk)
        if previous.role == self.Role.OWNER and self.role != self.Role.OWNER:
            if not self.group.has_another_owner(self):
                raise ValidationError("A production group must keep at least one owner.")

    def delete(self, *args, **kwargs):
        if self.role == self.Role.OWNER and not self.group.has_another_owner(self):
            raise ValidationError("A production group must keep at least one owner.")
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.user.public_name} - {self.group.name} ({self.get_role_display()})"


class VideoProject(models.Model):
    class Status(models.TextChoices):
        IDEA = "IDEA", "Idea / Brainstorming"
        SCRIPTING = "SCRIPTING", "Scripting"
        VOICE_LINES = "VOICE_LINES", "Awaiting Voice Lines"
        EDITING = "EDITING", "Currently Being Edited"
        REVIEW = "REVIEW", "In Internal Review"
        READY = "READY", "Ready for Upload / Published"

    group = models.ForeignKey(ProductionGroup, on_delete=models.CASCADE, related_name="projects")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.IDEA)
    asset_pipeline_url = models.URLField(blank=True, max_length=500)
    script_url = models.URLField(blank=True, max_length=500)
    assigned_editors = models.ManyToManyField(FocusUser, blank=True, related_name="assigned_edits")
    assigned_writers = models.ManyToManyField(FocusUser, blank=True, related_name="assigned_scripts")
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"


class ProjectNote(models.Model):
    project = models.ForeignKey(VideoProject, on_delete=models.CASCADE, related_name="notes")
    author = models.ForeignKey(FocusUser, on_delete=models.CASCADE, related_name="project_notes")
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Note on {self.project.title} by {self.author.public_name}"


class GroupInvitation(models.Model):
    group = models.ForeignKey(ProductionGroup, on_delete=models.CASCADE, related_name="invitations")
    token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    role_to_assign = models.CharField(max_length=10, choices=Membership.Role.choices, default=Membership.Role.TALENT)
    is_used = models.BooleanField(default=False)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Invite Link: {self.group.name} [{self.role_to_assign}]"
