import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from webauthn.helpers import bytes_to_base64url

from .models import AuthIdentity, FocusUser, GroupInvitation, Membership, ProductionGroup, ProjectNote, ProjectResource, RecoveryCode, VideoProject, WebAuthnCredential


class FocusUserTests(TestCase):
    def test_public_name_prefers_display_name(self):
        user = FocusUser.objects.create(display_name="Alex")
        AuthIdentity.objects.create(
            user=user,
            provider="DISCORD",
            subject_id="123",
            handle="alex_handle",
        )

        self.assertEqual(user.public_name, "Alex")

    def test_public_name_falls_back_to_provider_handle(self):
        user = FocusUser.objects.create()
        AuthIdentity.objects.create(
            user=user,
            provider="DISCORD",
            subject_id="123",
            handle="alex_handle",
        )

        self.assertEqual(user.public_name, "alex_handle")

    def test_new_users_do_not_have_usable_passwords(self):
        user = FocusUser.objects.create()

        self.assertFalse(user.has_usable_password())

    def test_new_users_start_with_private_profile_defaults(self):
        user = FocusUser.objects.create()

        self.assertEqual(user.bio, "")
        self.assertEqual(user.availability, FocusUser.Availability.AVAILABLE)
        self.assertFalse(user.show_assigned_projects)


class RecoveryCodeTests(TestCase):
    def test_recovery_code_is_hashed_and_single_use(self):
        user = FocusUser.objects.create()
        recovery_code = RecoveryCode.create_for_code(user, "plain-text-code")

        self.assertNotEqual(recovery_code.code_hash, "plain-text-code")
        self.assertTrue(recovery_code.matches("plain-text-code"))

        recovery_code.mark_used()

        self.assertFalse(recovery_code.matches("plain-text-code"))


class MembershipTests(TestCase):
    def test_last_owner_cannot_be_demoted(self):
        user = FocusUser.objects.create(display_name="Owner")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        membership = Membership.objects.create(
            user=user,
            group=group,
            role=Membership.Role.OWNER,
        )

        membership.role = Membership.Role.ADMIN

        with self.assertRaises(ValidationError):
            membership.full_clean()

    def test_last_owner_cannot_be_deleted(self):
        user = FocusUser.objects.create(display_name="Owner")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        membership = Membership.objects.create(
            user=user,
            group=group,
            role=Membership.Role.OWNER,
        )

        with self.assertRaises(ValidationError):
            membership.delete()

    def test_owner_can_be_removed_when_another_owner_exists(self):
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        first_owner = FocusUser.objects.create(display_name="First")
        second_owner = FocusUser.objects.create(display_name="Second")
        removable = Membership.objects.create(
            user=first_owner,
            group=group,
            role=Membership.Role.OWNER,
        )
        Membership.objects.create(
            user=second_owner,
            group=group,
            role=Membership.Role.OWNER,
        )

        removable.delete()

        self.assertFalse(Membership.objects.filter(pk=removable.pk).exists())


class ProductionFlowTests(TestCase):
    def test_dashboard_requires_sign_in(self):
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("dev_sign_in"), response["Location"])

    def test_protected_page_redirect_includes_original_destination(self):
        response = self.client.get(reverse("profile"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("dev_sign_in"), response["Location"])
        self.assertIn("next=/profile/", response["Location"])

    @override_settings(FOCUS_ENABLE_DEV_SIGN_IN=True)
    def test_development_sign_in_creates_pseudonymous_session(self):
        response = self.client.post(reverse("dev_sign_in"), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Production groups")
        self.assertTrue(AuthIdentity.objects.filter(provider="GITHUB", subject_id="focus-dev-user").exists())

    @override_settings(FOCUS_ENABLE_DEV_SIGN_IN=True)
    def test_development_sign_in_redirects_to_safe_next_destination(self):
        response = self.client.post(f"{reverse('dev_sign_in')}?next=/profile/")

        self.assertRedirects(response, reverse("profile"))

    @override_settings(FOCUS_ENABLE_DEV_SIGN_IN=True)
    def test_development_sign_in_ignores_external_next_destination(self):
        response = self.client.post(f"{reverse('dev_sign_in')}?next=https://example.com/profile")

        self.assertRedirects(response, reverse("dashboard"))

    @override_settings(FOCUS_ENABLE_DEV_SIGN_IN=True)
    def test_development_sign_in_links_to_backup_key_sign_in(self):
        response = self.client.get(reverse("dev_sign_in"))

        self.assertContains(response, "Use a passkey instead")
        self.assertContains(response, reverse("passkey_sign_in"))
        self.assertContains(response, "Use a saved backup key instead")
        self.assertContains(response, reverse("backup_key_sign_in"))

    @override_settings(FOCUS_ENABLE_DEV_SIGN_IN=True)
    def test_development_sign_in_preserves_next_on_method_links(self):
        response = self.client.get(f"{reverse('dev_sign_in')}?next=/profile/")

        self.assertContains(response, f"{reverse('passkey_sign_in')}?next=%2Fprofile%2F")
        self.assertContains(response, f"{reverse('backup_key_sign_in')}?next=%2Fprofile%2F")

    def test_passkey_sign_in_page_has_accessible_status_and_fallback_links(self):
        response = self.client.get(reverse("passkey_sign_in"))

        self.assertContains(response, "Use a passkey")
        self.assertContains(response, "Sign in with passkey")
        self.assertContains(response, 'role="status"')
        self.assertContains(response, 'role="alert"')
        self.assertContains(response, "Passkey sign in needs JavaScript")
        self.assertContains(response, reverse("backup_key_sign_in"))
        self.assertContains(response, reverse("dev_sign_in"))

    def test_passkey_sign_in_page_preserves_next_on_links_and_requests(self):
        response = self.client.get(f"{reverse('passkey_sign_in')}?next=/profile/")

        self.assertContains(response, f"{reverse('backup_key_sign_in')}?next=%2Fprofile%2F")
        self.assertContains(response, f"{reverse('dev_sign_in')}?next=%2Fprofile%2F")
        self.assertEqual(
            response.context["passkey_authentication_options_url"],
            f"{reverse('passkey_authentication_options')}?next=%2Fprofile%2F",
        )
        self.assertEqual(
            response.context["passkey_authentication_complete_url"],
            f"{reverse('passkey_authentication_complete')}?next=%2Fprofile%2F",
        )

    @override_settings(FOCUS_ENABLE_DEV_SIGN_IN=False)
    def test_passkey_sign_in_page_hides_development_link_when_disabled(self):
        response = self.client.get(reverse("passkey_sign_in"))

        self.assertContains(response, reverse("backup_key_sign_in"))
        self.assertNotContains(response, "Use development sign in instead")
        self.assertNotContains(response, reverse("dev_sign_in"))

    def test_passkey_sign_in_redirects_signed_in_user(self):
        user = FocusUser.objects.create(display_name="Creator")
        self.client.force_login(user)

        response = self.client.get(reverse("passkey_sign_in"))

        self.assertRedirects(response, reverse("dashboard"))

    def test_passkey_sign_in_redirects_signed_in_user_to_safe_next_destination(self):
        user = FocusUser.objects.create(display_name="Creator")
        self.client.force_login(user)

        response = self.client.get(f"{reverse('passkey_sign_in')}?next=/profile/")

        self.assertRedirects(response, reverse("profile"))

    def test_passkey_authentication_options_store_challenge_without_account_identifier(self):
        response = self.client.post(reverse("passkey_authentication_options"))
        data = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["rpId"], "testserver")
        self.assertEqual(data["userVerification"], "required")
        self.assertEqual(data["allowCredentials"], [])
        self.assertNotIn("user", data)
        self.assertIn("challenge", data)
        self.assertEqual(self.client.session["passkey_authentication_challenge"], data["challenge"])
        self.assertEqual(self.client.session["passkey_authentication_rp_id"], "testserver")
        self.assertEqual(self.client.session["passkey_authentication_origin"], "http://testserver")
        self.assertEqual(self.client.session["passkey_authentication_next"], reverse("dashboard"))

    def test_passkey_authentication_options_store_safe_next_destination(self):
        response = self.client.post(f"{reverse('passkey_authentication_options')}?next=/profile/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.session["passkey_authentication_next"], reverse("profile"))

    def test_passkey_authentication_options_ignore_external_next_destination(self):
        response = self.client.post(f"{reverse('passkey_authentication_options')}?next=https://example.com/profile")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.session["passkey_authentication_next"], reverse("dashboard"))

    def test_passkey_authentication_complete_signs_in_and_updates_passkey(self):
        user = FocusUser.objects.create(display_name="Creator")
        passkey = WebAuthnCredential.objects.create(
            user=user,
            credential_id=bytes_to_base64url(b"credential-id"),
            public_key=bytes_to_base64url(b"public-key"),
            name="Laptop",
            sign_count=7,
        )
        session = self.client.session
        session["passkey_authentication_challenge"] = bytes_to_base64url(b"challenge")
        session["passkey_authentication_rp_id"] = "testserver"
        session["passkey_authentication_origin"] = "http://testserver"
        session["passkey_authentication_next"] = reverse("profile")
        session.save()
        verification = SimpleNamespace(
            credential_id=b"credential-id",
            new_sign_count=8,
        )

        with patch("focus_core.views.verify_authentication_response", return_value=verification) as verify_authentication:
            response = self.client.post(
                reverse("passkey_authentication_complete"),
                data=json.dumps({"credential": {"id": bytes_to_base64url(b"credential-id")}}),
                content_type="application/json",
            )

        passkey.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["redirect_url"], reverse("profile"))
        self.assertEqual(self.client.session["_auth_user_id"], str(user.pk))
        self.assertEqual(passkey.sign_count, 8)
        self.assertIsNotNone(passkey.last_used_at)
        verify_authentication.assert_called_once()
        self.assertNotIn("passkey_authentication_challenge", self.client.session)

    def test_passkey_authentication_complete_rejects_unknown_credential(self):
        session = self.client.session
        session["passkey_authentication_challenge"] = bytes_to_base64url(b"challenge")
        session["passkey_authentication_rp_id"] = "testserver"
        session["passkey_authentication_origin"] = "http://testserver"
        session.save()

        response = self.client.post(
            reverse("passkey_authentication_complete"),
            data=json.dumps({"credential": {"id": bytes_to_base64url(b"unknown-credential")}}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        self.assertEqual(response.json()["error"], "That passkey did not work. Try again.")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_passkey_authentication_complete_rejects_missing_challenge(self):
        response = self.client.post(
            reverse("passkey_authentication_complete"),
            data=json.dumps({"credential": {"id": bytes_to_base64url(b"credential-id")}}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        self.assertEqual(response.json()["error"], "Start passkey sign in again.")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_passkey_authentication_complete_rejects_verification_failure(self):
        user = FocusUser.objects.create(display_name="Creator")
        WebAuthnCredential.objects.create(
            user=user,
            credential_id=bytes_to_base64url(b"credential-id"),
            public_key=bytes_to_base64url(b"public-key"),
            name="Laptop",
            sign_count=7,
        )
        session = self.client.session
        session["passkey_authentication_challenge"] = bytes_to_base64url(b"challenge")
        session["passkey_authentication_rp_id"] = "testserver"
        session["passkey_authentication_origin"] = "http://testserver"
        session.save()

        with patch("focus_core.views.verify_authentication_response", side_effect=ValueError("bad credential")):
            response = self.client.post(
                reverse("passkey_authentication_complete"),
                data=json.dumps({"credential": {"id": bytes_to_base64url(b"credential-id")}}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        self.assertEqual(response.json()["error"], "That passkey did not work. Try again.")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_backup_key_sign_in_uses_unused_code_and_marks_it_used(self):
        user = FocusUser.objects.create(display_name="Creator")
        recovery_code = RecoveryCode.create_for_code(user, "2345-6789-ABCD-EFGH")

        response = self.client.post(reverse("backup_key_sign_in"), {"backup_key": "2345 6789 abcd efgh"})

        recovery_code.refresh_from_db()
        self.assertRedirects(response, reverse("dashboard"))
        self.assertIsNotNone(recovery_code.used_at)

        dashboard_response = self.client.get(reverse("dashboard"))
        self.assertContains(dashboard_response, "Signed in as Creator")

    def test_backup_key_sign_in_redirects_to_safe_next_destination(self):
        user = FocusUser.objects.create(display_name="Creator")
        recovery_code = RecoveryCode.create_for_code(user, "2345-6789-ABCD-EFGH")

        response = self.client.post(
            f"{reverse('backup_key_sign_in')}?next=/profile/",
            {"backup_key": "2345 6789 abcd efgh"},
        )

        recovery_code.refresh_from_db()
        self.assertRedirects(response, reverse("profile"))
        self.assertIsNotNone(recovery_code.used_at)

    def test_backup_key_sign_in_ignores_external_next_destination(self):
        user = FocusUser.objects.create(display_name="Creator")
        RecoveryCode.create_for_code(user, "2345-6789-ABCD-EFGH")

        response = self.client.post(
            f"{reverse('backup_key_sign_in')}?next=https://example.com/profile",
            {"backup_key": "2345 6789 abcd efgh"},
        )

        self.assertRedirects(response, reverse("dashboard"))

    def test_backup_key_sign_in_rejects_used_code(self):
        user = FocusUser.objects.create(display_name="Creator")
        recovery_code = RecoveryCode.create_for_code(user, "2345-6789-ABCD-EFGH")
        recovery_code.mark_used()

        response = self.client.post(reverse("backup_key_sign_in"), {"backup_key": "2345-6789-ABCD-EFGH"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "That backup key did not work. Check it and try again.")
        self.assertContains(response, 'role="alert"')
        self.assertContains(response, 'aria-invalid="true"')
        self.assertContains(response, 'id="backup_key-error"')
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_backup_key_sign_in_rejects_unknown_code(self):
        response = self.client.post(reverse("backup_key_sign_in"), {"backup_key": "UNKNOWN-BACKUP-KEY"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "That backup key did not work. Check it and try again.")
        self.assertContains(response, 'id="backup_key-error"')
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_backup_key_sign_in_redirects_signed_in_user(self):
        user = FocusUser.objects.create(display_name="Creator")
        self.client.force_login(user)

        response = self.client.get(reverse("backup_key_sign_in"))

        self.assertRedirects(response, reverse("dashboard"))

    def test_backup_key_sign_in_redirects_signed_in_user_to_safe_next_destination(self):
        user = FocusUser.objects.create(display_name="Creator")
        self.client.force_login(user)

        response = self.client.get(f"{reverse('backup_key_sign_in')}?next=/profile/")

        self.assertRedirects(response, reverse("profile"))

    def test_backup_key_sign_in_links_to_passkey_sign_in(self):
        response = self.client.get(reverse("backup_key_sign_in"))

        self.assertContains(response, "Use a passkey instead")
        self.assertContains(response, reverse("passkey_sign_in"))

    def test_backup_key_sign_in_preserves_next_on_passkey_link(self):
        response = self.client.get(f"{reverse('backup_key_sign_in')}?next=/profile/")

        self.assertContains(response, f"{reverse('passkey_sign_in')}?next=%2Fprofile%2F")

    def test_group_create_adds_current_user_as_owner(self):
        user = FocusUser.objects.create(display_name="Creator")
        self.client.force_login(user)

        response = self.client.post(reverse("group_create"), {"name": "Studio Team"})

        group = ProductionGroup.objects.get(slug="studio-team")
        self.assertRedirects(response, reverse("group_detail", kwargs={"slug": "studio-team"}))
        self.assertTrue(Membership.objects.filter(user=user, group=group, role=Membership.Role.OWNER).exists())

    def test_dashboard_only_shows_user_groups(self):
        user = FocusUser.objects.create(display_name="Member")
        other_user = FocusUser.objects.create(display_name="Other")
        visible_group = ProductionGroup.objects.create(name="Visible Studio", slug="visible-studio")
        hidden_group = ProductionGroup.objects.create(name="Hidden Studio", slug="hidden-studio")
        Membership.objects.create(user=user, group=visible_group, role=Membership.Role.OWNER)
        Membership.objects.create(user=other_user, group=hidden_group, role=Membership.Role.OWNER)
        self.client.force_login(user)

        response = self.client.get(reverse("dashboard"))

        self.assertContains(response, "Visible Studio")
        self.assertNotContains(response, "Hidden Studio")

    def test_dashboard_shows_current_user_assignments_once(self):
        user = FocusUser.objects.create(display_name="Creator")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=user, group=group, role=Membership.Role.OWNER)
        project = VideoProject.objects.create(group=group, title="Launch Video", status=VideoProject.Status.EDITING)
        project.assigned_editors.add(user)
        project.assigned_writers.add(user)
        self.client.force_login(user)

        response = self.client.get(reverse("dashboard"))

        self.assertContains(response, "Projects assigned to Creator")
        self.assertContains(response, "Launch Video", count=1)
        self.assertContains(response, "Editor, Writer")
        self.assertContains(response, "Currently Being Edited")
        self.assertContains(response, reverse("project_detail", kwargs={"group_slug": group.slug, "pk": project.pk}))

    def test_dashboard_shows_latest_note_for_assigned_project(self):
        creator = FocusUser.objects.create(display_name="Creator")
        editor = FocusUser.objects.create(display_name="Editor")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=creator, group=group, role=Membership.Role.OWNER)
        Membership.objects.create(user=editor, group=group, role=Membership.Role.EDITOR)
        project = VideoProject.objects.create(group=group, title="Launch Video")
        project.assigned_writers.add(creator)
        older_note = ProjectNote.objects.create(project=project, author=creator, body="Older dashboard note.")
        ProjectNote.objects.filter(pk=older_note.pk).update(created_at=timezone.now() - timedelta(days=1))
        ProjectNote.objects.create(project=project, author=editor, body="Latest dashboard handoff.")
        self.client.force_login(creator)

        response = self.client.get(reverse("dashboard"))

        self.assertContains(response, "Latest note")
        self.assertContains(response, "Latest dashboard handoff.")
        self.assertContains(response, "By Editor")
        self.assertContains(response, reverse("project_detail", kwargs={"group_slug": group.slug, "pk": project.pk}) + "#project-notes")
        self.assertNotContains(response, "Older dashboard note.")

    def test_dashboard_shows_empty_latest_note_state_for_assigned_project(self):
        user = FocusUser.objects.create(display_name="Creator")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=user, group=group, role=Membership.Role.OWNER)
        project = VideoProject.objects.create(group=group, title="Launch Video")
        project.assigned_writers.add(user)
        self.client.force_login(user)

        response = self.client.get(reverse("dashboard"))

        self.assertContains(response, "No notes yet.")

    def test_dashboard_shows_recent_project_activity_for_user_groups(self):
        user = FocusUser.objects.create(display_name="Creator")
        producer = FocusUser.objects.create(display_name="Producer")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=user, group=group, role=Membership.Role.OWNER)
        Membership.objects.create(user=producer, group=group, role=Membership.Role.ADMIN)
        project = VideoProject.objects.create(group=group, title="Launch Video")
        ProjectNote.objects.create(project=project, author=producer, body="Review handoff is ready.")
        self.client.force_login(user)

        response = self.client.get(reverse("dashboard"))

        self.assertContains(response, "Recent project activity")
        self.assertContains(response, "Launch Video")
        self.assertContains(response, "Review handoff is ready.")
        self.assertContains(response, "Studio. By Producer")
        self.assertContains(response, reverse("project_detail", kwargs={"group_slug": group.slug, "pk": project.pk}) + "#project-notes")

    def test_dashboard_recent_project_activity_hides_other_group_notes(self):
        user = FocusUser.objects.create(display_name="Creator")
        other_user = FocusUser.objects.create(display_name="Other")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        hidden_group = ProductionGroup.objects.create(name="Hidden Studio", slug="hidden-studio")
        Membership.objects.create(user=user, group=group, role=Membership.Role.OWNER)
        Membership.objects.create(user=other_user, group=hidden_group, role=Membership.Role.OWNER)
        visible_project = VideoProject.objects.create(group=group, title="Visible Project")
        hidden_project = VideoProject.objects.create(group=hidden_group, title="Hidden Project")
        ProjectNote.objects.create(project=visible_project, author=user, body="Visible update.")
        ProjectNote.objects.create(project=hidden_project, author=other_user, body="Hidden update.")
        self.client.force_login(user)

        response = self.client.get(reverse("dashboard"))

        self.assertContains(response, "Visible update.")
        self.assertNotContains(response, "Hidden update.")
        self.assertNotContains(response, "Hidden Project")

    def test_dashboard_recent_project_activity_shows_latest_note_per_project(self):
        user = FocusUser.objects.create(display_name="Creator")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=user, group=group, role=Membership.Role.OWNER)
        project = VideoProject.objects.create(group=group, title="Launch Video")
        older_note = ProjectNote.objects.create(project=project, author=user, body="Older project update.")
        ProjectNote.objects.filter(pk=older_note.pk).update(created_at=timezone.now() - timedelta(days=1))
        ProjectNote.objects.create(project=project, author=user, body="Newer project update.")
        self.client.force_login(user)

        response = self.client.get(reverse("dashboard"))

        self.assertContains(response, "Newer project update.")
        self.assertNotContains(response, "Older project update.")

    def test_dashboard_recent_project_activity_has_empty_state(self):
        user = FocusUser.objects.create(display_name="Creator")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=user, group=group, role=Membership.Role.OWNER)
        self.client.force_login(user)

        response = self.client.get(reverse("dashboard"))

        self.assertContains(response, "No project activity yet")
        self.assertContains(response, "Project notes and status updates from your groups will appear here.")

    def test_dashboard_does_not_show_assignments_outside_user_groups(self):
        user = FocusUser.objects.create(display_name="Creator")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        hidden_group = ProductionGroup.objects.create(name="Hidden Studio", slug="hidden-studio")
        Membership.objects.create(user=user, group=group, role=Membership.Role.OWNER)
        visible_project = VideoProject.objects.create(group=group, title="Visible Assignment")
        hidden_project = VideoProject.objects.create(group=hidden_group, title="Hidden Assignment")
        visible_project.assigned_writers.add(user)
        hidden_project.assigned_writers.add(user)
        self.client.force_login(user)

        response = self.client.get(reverse("dashboard"))

        self.assertContains(response, "Visible Assignment")
        self.assertContains(response, "Writer")
        self.assertNotContains(response, "Hidden Assignment")
        self.assertNotContains(response, "Hidden Studio")

    def test_profile_requires_sign_in(self):
        response = self.client.get(reverse("profile"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("dev_sign_in"), response["Location"])

    def test_profile_updates_public_profile_without_pii_fields(self):
        user = FocusUser.objects.create(display_name="Creator")
        self.client.force_login(user)

        response = self.client.post(
            reverse("profile"),
            {
                "display_name": "Stage Name",
                "bio": "I edit short documentaries and audio-led interviews.",
                "availability": FocusUser.Availability.LIMITED,
                "show_assigned_projects": "on",
            },
        )

        user.refresh_from_db()
        self.assertRedirects(response, reverse("profile"))
        self.assertEqual(user.display_name, "Stage Name")
        self.assertEqual(user.bio, "I edit short documentaries and audio-led interviews.")
        self.assertEqual(user.availability, FocusUser.Availability.LIMITED)
        self.assertTrue(user.show_assigned_projects)

        profile_response = self.client.get(reverse("profile"))
        self.assertContains(profile_response, "Your current public name is Stage Name.")
        self.assertContains(profile_response, "Display name")
        self.assertContains(profile_response, "Bio")
        self.assertContains(profile_response, "Availability")
        self.assertContains(profile_response, "Show projects I am working on")
        self.assertContains(profile_response, "Avoid email addresses, legal names, phone numbers, or private schedule details.")
        self.assertNotContains(profile_response, 'name="email"')
        self.assertNotContains(profile_response, 'name="password"')

    def test_profile_can_clear_optional_profile_fields(self):
        user = FocusUser.objects.create(
            display_name="Creator",
            bio="Existing intro",
            availability=FocusUser.Availability.BUSY,
            show_assigned_projects=True,
        )
        AuthIdentity.objects.create(
            user=user,
            provider="DISCORD",
            subject_id="creator-123",
            handle="creator_handle",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("profile"),
            {
                "display_name": "",
                "bio": "",
                "availability": FocusUser.Availability.UNAVAILABLE,
            },
            follow=True,
        )

        user.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(user.display_name, "")
        self.assertEqual(user.bio, "")
        self.assertEqual(user.availability, FocusUser.Availability.UNAVAILABLE)
        self.assertFalse(user.show_assigned_projects)
        self.assertContains(response, "Your current public name is creator_handle.")

    def test_profile_form_errors_are_exposed_to_assistive_technology(self):
        user = FocusUser.objects.create(display_name="Creator")
        self.client.force_login(user)

        response = self.client.post(
            reverse("profile"),
            {
                "display_name": "Creator",
                "bio": "A" * 501,
                "availability": FocusUser.Availability.AVAILABLE,
            },
        )

        user.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(user.display_name, "Creator")
        self.assertEqual(user.bio, "")
        self.assertContains(response, 'role="alert"')
        self.assertContains(response, 'aria-invalid="true"')
        self.assertContains(response, 'id="bio-error"')

    def test_account_safety_requires_sign_in(self):
        response = self.client.get(reverse("account_safety"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("dev_sign_in"), response["Location"])

    def test_account_management_actions_require_sign_in(self):
        user = FocusUser.objects.create(display_name="Creator")
        identity = AuthIdentity.objects.create(
            user=user,
            provider="DISCORD",
            subject_id="creator-123",
            handle="creator_handle",
        )
        passkey = WebAuthnCredential.objects.create(
            user=user,
            credential_id="credential-1",
            public_key="public-key",
            name="Laptop",
        )

        responses = [
            self.client.post(reverse("linked_account_remove", kwargs={"pk": identity.pk})),
            self.client.get(reverse("passkey_update", kwargs={"pk": passkey.pk})),
            self.client.post(reverse("passkey_remove", kwargs={"pk": passkey.pk})),
        ]

        for response in responses:
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse("dev_sign_in"), response["Location"])

    def test_passkey_registration_routes_require_sign_in(self):
        responses = [
            self.client.get(reverse("passkey_register")),
            self.client.post(reverse("passkey_registration_options")),
            self.client.post(reverse("passkey_registration_complete")),
        ]

        for response in responses:
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse("dev_sign_in"), response["Location"])

    def test_account_safety_shows_unused_backup_key_count(self):
        user = FocusUser.objects.create(display_name="Creator")
        RecoveryCode.create_for_code(user, "first-code")
        used_code = RecoveryCode.create_for_code(user, "used-code")
        used_code.mark_used()
        self.client.force_login(user)

        response = self.client.get(reverse("account_safety"))

        self.assertContains(response, "Account safety")
        self.assertContains(response, "Backup keys")
        self.assertContains(response, "You have 1 unused backup key.")
        self.assertContains(response, "Create new backup keys")

    def test_account_safety_generates_hashed_backup_keys_shown_once(self):
        user = FocusUser.objects.create(display_name="Creator")
        self.client.force_login(user)

        response = self.client.post(reverse("account_safety"))

        codes = response.context["generated_codes"]
        stored_codes = list(user.recovery_codes.all())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(codes), 8)
        self.assertEqual(len(stored_codes), 8)
        self.assertContains(response, "Save these backup keys now")
        self.assertContains(response, "You have 8 unused backup keys.")
        for code in codes:
            self.assertContains(response, code)
            self.assertTrue(any(stored_code.matches(code) for stored_code in stored_codes))
            self.assertFalse(RecoveryCode.objects.filter(code_hash=code).exists())

        follow_up_response = self.client.get(reverse("account_safety"))
        self.assertContains(follow_up_response, "You have 8 unused backup keys.")
        self.assertNotContains(follow_up_response, codes[0])

    def test_account_safety_replaces_existing_backup_keys(self):
        user = FocusUser.objects.create(display_name="Creator")
        old_code = RecoveryCode.create_for_code(user, "old-backup-key")
        self.client.force_login(user)

        response = self.client.post(reverse("account_safety"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(RecoveryCode.objects.filter(pk=old_code.pk).exists())
        self.assertEqual(user.recovery_codes.filter(used_at__isnull=True).count(), 8)

    def test_account_safety_lists_connected_accounts_and_passkeys(self):
        user = FocusUser.objects.create(display_name="Creator")
        AuthIdentity.objects.create(
            user=user,
            provider="DISCORD",
            subject_id="creator-123",
            handle="creator_handle",
        )
        passkey = WebAuthnCredential.objects.create(
            user=user,
            credential_id="credential-1",
            public_key="public-key",
            name="Laptop",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("account_safety"))

        self.assertContains(response, "Connected accounts")
        self.assertContains(response, "Discord")
        self.assertContains(response, "creator_handle")
        self.assertContains(response, "Passkeys")
        self.assertContains(response, "Laptop")
        self.assertContains(response, "Add passkey")
        self.assertContains(response, reverse("passkey_register"))
        self.assertContains(response, reverse("passkey_update", kwargs={"pk": passkey.pk}))
        self.assertContains(response, "Connected sign-in accounts")
        self.assertContains(response, "Saved passkeys")
        self.assertContains(response, 'scope="col"')
        self.assertContains(response, 'scope="row"')

    def test_passkey_registration_page_has_accessible_status_and_form(self):
        user = FocusUser.objects.create(display_name="Creator")
        self.client.force_login(user)

        response = self.client.get(reverse("passkey_register"))

        self.assertContains(response, "Add passkey")
        self.assertContains(response, "Passkey name")
        self.assertContains(response, 'role="status"')
        self.assertContains(response, 'role="alert"')
        self.assertContains(response, 'id="name-help"')
        self.assertContains(response, 'aria-describedby="name-help"')
        self.assertContains(response, "Passkey setup needs JavaScript")

    def test_passkey_registration_plain_form_post_redirects_to_page(self):
        user = FocusUser.objects.create(display_name="Creator")
        self.client.force_login(user)

        response = self.client.post(reverse("passkey_register"), data={"name": "Laptop"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("passkey_register"))
        self.assertFalse(WebAuthnCredential.objects.filter(user=user).exists())

    def test_passkey_registration_options_store_challenge_and_return_public_key_options(self):
        user = FocusUser.objects.create(display_name="Creator")
        self.client.force_login(user)

        response = self.client.post(reverse("passkey_registration_options"))
        data = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["rp"]["name"], "FOCUS")
        self.assertEqual(data["rp"]["id"], "testserver")
        self.assertEqual(data["user"]["name"], f"focus-user-{user.pk}")
        self.assertEqual(data["user"]["displayName"], "Creator")
        self.assertEqual(data["authenticatorSelection"]["residentKey"], "required")
        self.assertEqual(data["authenticatorSelection"]["userVerification"], "required")
        self.assertEqual(data["attestation"], "none")
        self.assertIn("challenge", data)
        self.assertEqual(self.client.session["passkey_registration_challenge"], data["challenge"])
        self.assertEqual(self.client.session["passkey_registration_rp_id"], "testserver")
        self.assertEqual(self.client.session["passkey_registration_origin"], "http://testserver")

    def test_passkey_registration_options_exclude_existing_credentials(self):
        user = FocusUser.objects.create(display_name="Creator")
        WebAuthnCredential.objects.create(
            user=user,
            credential_id=bytes_to_base64url(b"existing-credential"),
            public_key="public-key",
            name="Laptop",
        )
        self.client.force_login(user)

        response = self.client.post(reverse("passkey_registration_options"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["excludeCredentials"][0]["id"], bytes_to_base64url(b"existing-credential"))

    def test_passkey_registration_complete_creates_passkey(self):
        user = FocusUser.objects.create(display_name="Creator")
        self.client.force_login(user)
        session = self.client.session
        session["passkey_registration_challenge"] = bytes_to_base64url(b"challenge")
        session["passkey_registration_rp_id"] = "testserver"
        session["passkey_registration_origin"] = "http://testserver"
        session.save()
        verification = SimpleNamespace(
            credential_id=b"credential-id",
            credential_public_key=b"public-key",
            sign_count=7,
        )

        with patch("focus_core.views.verify_registration_response", return_value=verification) as verify_registration:
            response = self.client.post(
                reverse("passkey_registration_complete"),
                data=json.dumps({"credential": {"id": "credential"}, "name": "Laptop"}),
                content_type="application/json",
            )

        passkey = WebAuthnCredential.objects.get(user=user)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["redirect_url"], reverse("account_safety"))
        self.assertEqual(passkey.credential_id, bytes_to_base64url(b"credential-id"))
        self.assertEqual(passkey.public_key, bytes_to_base64url(b"public-key"))
        self.assertEqual(passkey.name, "Laptop")
        self.assertEqual(passkey.sign_count, 7)
        verify_registration.assert_called_once()
        self.assertNotIn("passkey_registration_challenge", self.client.session)

    def test_passkey_registration_complete_rejects_duplicate_credential(self):
        user = FocusUser.objects.create(display_name="Creator")
        WebAuthnCredential.objects.create(
            user=user,
            credential_id=bytes_to_base64url(b"credential-id"),
            public_key="public-key",
            name="Existing",
        )
        self.client.force_login(user)
        session = self.client.session
        session["passkey_registration_challenge"] = bytes_to_base64url(b"challenge")
        session["passkey_registration_rp_id"] = "testserver"
        session["passkey_registration_origin"] = "http://testserver"
        session.save()
        verification = SimpleNamespace(
            credential_id=b"credential-id",
            credential_public_key=b"public-key",
            sign_count=7,
        )

        with patch("focus_core.views.verify_registration_response", return_value=verification):
            response = self.client.post(
                reverse("passkey_registration_complete"),
                data=json.dumps({"credential": {"id": "credential"}, "name": "Laptop"}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        self.assertContains(response, "That passkey is already connected.", status_code=400)
        self.assertEqual(WebAuthnCredential.objects.filter(user=user).count(), 1)

    def test_passkey_registration_complete_rejects_missing_challenge(self):
        user = FocusUser.objects.create(display_name="Creator")
        self.client.force_login(user)

        response = self.client.post(
            reverse("passkey_registration_complete"),
            data=json.dumps({"credential": {"id": "credential"}, "name": "Laptop"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        self.assertEqual(response.json()["error"], "Start passkey setup again.")
        self.assertFalse(WebAuthnCredential.objects.filter(user=user).exists())

    def test_passkey_registration_complete_rejects_verification_failure(self):
        user = FocusUser.objects.create(display_name="Creator")
        self.client.force_login(user)
        session = self.client.session
        session["passkey_registration_challenge"] = bytes_to_base64url(b"challenge")
        session["passkey_registration_rp_id"] = "testserver"
        session["passkey_registration_origin"] = "http://testserver"
        session.save()

        with patch("focus_core.views.verify_registration_response", side_effect=ValueError("bad credential")):
            response = self.client.post(
                reverse("passkey_registration_complete"),
                data=json.dumps({"credential": {"id": "credential"}, "name": "Laptop"}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        self.assertEqual(response.json()["error"], "Passkey setup could not be verified. Try again.")
        self.assertFalse(WebAuthnCredential.objects.filter(user=user).exists())

    @override_settings(FOCUS_ENABLE_DEV_SIGN_IN=True)
    def test_account_safety_links_to_development_account_connection_when_enabled(self):
        user = FocusUser.objects.create(display_name="Creator")
        self.client.force_login(user)

        response = self.client.get(reverse("account_safety"))

        self.assertContains(response, "Connect development account")
        self.assertContains(response, reverse("development_linked_account_create"))

    @override_settings(FOCUS_ENABLE_DEV_SIGN_IN=False)
    def test_account_safety_hides_development_account_connection_when_disabled(self):
        user = FocusUser.objects.create(display_name="Creator")
        self.client.force_login(user)

        response = self.client.get(reverse("account_safety"))

        self.assertNotContains(response, "Connect development account")
        self.assertNotContains(response, reverse("development_linked_account_create"))

    @override_settings(FOCUS_ENABLE_DEV_SIGN_IN=True)
    def test_development_account_connection_requires_sign_in(self):
        response = self.client.get(reverse("development_linked_account_create"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("dev_sign_in"), response["Location"])

    @override_settings(FOCUS_ENABLE_DEV_SIGN_IN=False)
    def test_development_account_connection_is_unavailable_when_disabled(self):
        user = FocusUser.objects.create(display_name="Creator")
        self.client.force_login(user)

        response = self.client.get(reverse("development_linked_account_create"))

        self.assertEqual(response.status_code, 404)

    @override_settings(FOCUS_ENABLE_DEV_SIGN_IN=True)
    def test_development_account_connection_adds_provider_identity(self):
        user = FocusUser.objects.create(display_name="Creator")
        self.client.force_login(user)

        response = self.client.post(
            reverse("development_linked_account_create"),
            {"provider": "DISCORD", "handle": "creator_handle"},
        )

        identity = AuthIdentity.objects.get(user=user, provider="DISCORD")
        self.assertRedirects(response, reverse("account_safety"))
        self.assertEqual(identity.handle, "creator_handle")
        self.assertEqual(identity.subject_id, "dev-discord-creator_handle")

    @override_settings(FOCUS_ENABLE_DEV_SIGN_IN=True)
    def test_development_account_connection_blocks_duplicate_for_current_user(self):
        user = FocusUser.objects.create(display_name="Creator")
        AuthIdentity.objects.create(
            user=user,
            provider="DISCORD",
            subject_id="dev-discord-creator_handle",
            handle="creator_handle",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("development_linked_account_create"),
            {"provider": "DISCORD", "handle": "creator_handle"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "That development account is already connected.")
        self.assertContains(response, 'role="alert"')
        self.assertContains(response, 'aria-invalid="true"')
        self.assertContains(response, 'id="handle-error"')
        self.assertEqual(AuthIdentity.objects.filter(user=user, provider="DISCORD").count(), 1)

    @override_settings(FOCUS_ENABLE_DEV_SIGN_IN=True)
    def test_development_account_connection_blocks_identity_owned_by_another_user(self):
        user = FocusUser.objects.create(display_name="Creator")
        other_user = FocusUser.objects.create(display_name="Other")
        AuthIdentity.objects.create(
            user=other_user,
            provider="DISCORD",
            subject_id="dev-discord-creator_handle",
            handle="creator_handle",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("development_linked_account_create"),
            {"provider": "DISCORD", "handle": "creator_handle"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "That development account is already connected to another FOCUS user.")
        self.assertFalse(AuthIdentity.objects.filter(user=user, provider="DISCORD").exists())

    @override_settings(FOCUS_ENABLE_DEV_SIGN_IN=True)
    def test_development_account_connection_rejects_unknown_provider(self):
        user = FocusUser.objects.create(display_name="Creator")
        self.client.force_login(user)

        response = self.client.post(
            reverse("development_linked_account_create"),
            {"provider": "UNKNOWN", "handle": "creator_handle"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="provider-error"')
        self.assertFalse(AuthIdentity.objects.filter(user=user).exists())

    def test_connected_account_can_be_removed_when_backup_key_exists(self):
        user = FocusUser.objects.create(display_name="Creator")
        identity = AuthIdentity.objects.create(
            user=user,
            provider="DISCORD",
            subject_id="creator-123",
            handle="creator_handle",
        )
        RecoveryCode.create_for_code(user, "backup-key")
        self.client.force_login(user)

        response = self.client.post(
            reverse("linked_account_remove", kwargs={"pk": identity.pk}),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Removed Discord: creator_handle.")
        self.assertFalse(AuthIdentity.objects.filter(pk=identity.pk).exists())

    def test_last_connected_account_cannot_be_removed_without_another_access_method(self):
        user = FocusUser.objects.create(display_name="Creator")
        identity = AuthIdentity.objects.create(
            user=user,
            provider="DISCORD",
            subject_id="creator-123",
            handle="creator_handle",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("linked_account_remove", kwargs={"pk": identity.pk}),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create backup keys or add another sign-in method before removing this connected account.")
        self.assertTrue(AuthIdentity.objects.filter(pk=identity.pk).exists())

    def test_connected_account_remove_rejects_other_users_account(self):
        user = FocusUser.objects.create(display_name="Creator")
        other_user = FocusUser.objects.create(display_name="Other")
        identity = AuthIdentity.objects.create(
            user=other_user,
            provider="DISCORD",
            subject_id="other-123",
            handle="other_handle",
        )
        self.client.force_login(user)

        response = self.client.post(reverse("linked_account_remove", kwargs={"pk": identity.pk}))

        self.assertEqual(response.status_code, 404)
        self.assertTrue(AuthIdentity.objects.filter(pk=identity.pk).exists())

    def test_passkey_name_can_be_updated(self):
        user = FocusUser.objects.create(display_name="Creator")
        passkey = WebAuthnCredential.objects.create(
            user=user,
            credential_id="credential-1",
            public_key="public-key",
            name="Old laptop",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("passkey_update", kwargs={"pk": passkey.pk}),
            {"name": "New laptop"},
        )

        passkey.refresh_from_db()
        self.assertRedirects(response, reverse("account_safety"))
        self.assertEqual(passkey.name, "New laptop")

    def test_passkey_name_errors_are_exposed_to_assistive_technology(self):
        user = FocusUser.objects.create(display_name="Creator")
        passkey = WebAuthnCredential.objects.create(
            user=user,
            credential_id="credential-1",
            public_key="public-key",
            name="Laptop",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("passkey_update", kwargs={"pk": passkey.pk}),
            {"name": "A" * 151},
        )

        passkey.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(passkey.name, "Laptop")
        self.assertContains(response, 'role="alert"')
        self.assertContains(response, 'aria-invalid="true"')
        self.assertContains(response, 'id="name-error"')

    def test_passkey_update_rejects_other_users_passkey(self):
        user = FocusUser.objects.create(display_name="Creator")
        other_user = FocusUser.objects.create(display_name="Other")
        passkey = WebAuthnCredential.objects.create(
            user=other_user,
            credential_id="credential-1",
            public_key="public-key",
            name="Other laptop",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("passkey_update", kwargs={"pk": passkey.pk}),
            {"name": "New name"},
        )

        passkey.refresh_from_db()
        self.assertEqual(response.status_code, 404)
        self.assertEqual(passkey.name, "Other laptop")

    def test_passkey_can_be_removed_when_connected_account_exists(self):
        user = FocusUser.objects.create(display_name="Creator")
        AuthIdentity.objects.create(
            user=user,
            provider="DISCORD",
            subject_id="creator-123",
            handle="creator_handle",
        )
        passkey = WebAuthnCredential.objects.create(
            user=user,
            credential_id="credential-1",
            public_key="public-key",
            name="Laptop",
        )
        self.client.force_login(user)

        response = self.client.post(reverse("passkey_remove", kwargs={"pk": passkey.pk}), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Removed Laptop.")
        self.assertFalse(WebAuthnCredential.objects.filter(pk=passkey.pk).exists())

    def test_last_passkey_cannot_be_removed_without_another_access_method(self):
        user = FocusUser.objects.create(display_name="Creator")
        passkey = WebAuthnCredential.objects.create(
            user=user,
            credential_id="credential-1",
            public_key="public-key",
            name="Laptop",
        )
        self.client.force_login(user)

        response = self.client.post(reverse("passkey_remove", kwargs={"pk": passkey.pk}), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create backup keys or add another sign-in method before removing this passkey.")
        self.assertTrue(WebAuthnCredential.objects.filter(pk=passkey.pk).exists())

    def test_passkey_remove_rejects_other_users_passkey(self):
        user = FocusUser.objects.create(display_name="Creator")
        other_user = FocusUser.objects.create(display_name="Other")
        passkey = WebAuthnCredential.objects.create(
            user=other_user,
            credential_id="credential-1",
            public_key="public-key",
            name="Other laptop",
        )
        self.client.force_login(user)

        response = self.client.post(reverse("passkey_remove", kwargs={"pk": passkey.pk}))

        self.assertEqual(response.status_code, 404)
        self.assertTrue(WebAuthnCredential.objects.filter(pk=passkey.pk).exists())

    def test_group_detail_rejects_non_member(self):
        user = FocusUser.objects.create(display_name="Member")
        group = ProductionGroup.objects.create(name="Other Studio", slug="other-studio")
        self.client.force_login(user)

        response = self.client.get(reverse("group_detail", kwargs={"slug": group.slug}))

        self.assertEqual(response.status_code, 404)

    def test_group_detail_filters_projects_by_status(self):
        user = FocusUser.objects.create(display_name="Producer")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=user, group=group, role=Membership.Role.OWNER)
        scripting_project = VideoProject.objects.create(
            group=group,
            title="Script Draft",
            status=VideoProject.Status.SCRIPTING,
        )
        VideoProject.objects.create(
            group=group,
            title="Edit Pass",
            status=VideoProject.Status.EDITING,
        )
        self.client.force_login(user)

        response = self.client.get(
            reverse("group_detail", kwargs={"slug": group.slug}),
            {"status": VideoProject.Status.SCRIPTING},
        )

        self.assertContains(response, "Script Draft")
        self.assertNotContains(response, "Edit Pass")
        self.assertContains(response, 'aria-current="page"')
        self.assertContains(response, f"?status={VideoProject.Status.SCRIPTING}")
        self.assertContains(response, reverse("project_detail", kwargs={"group_slug": group.slug, "pk": scripting_project.pk}))

    def test_group_detail_ignores_invalid_status_filter(self):
        user = FocusUser.objects.create(display_name="Producer")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=user, group=group, role=Membership.Role.OWNER)
        VideoProject.objects.create(group=group, title="Script Draft", status=VideoProject.Status.SCRIPTING)
        VideoProject.objects.create(group=group, title="Edit Pass", status=VideoProject.Status.EDITING)
        self.client.force_login(user)

        response = self.client.get(reverse("group_detail", kwargs={"slug": group.slug}), {"status": "UNKNOWN"})

        self.assertContains(response, "Script Draft")
        self.assertContains(response, "Edit Pass")
        self.assertContains(response, "All projects (2)")

    def test_group_detail_shows_latest_project_note(self):
        producer = FocusUser.objects.create(display_name="Producer")
        editor = FocusUser.objects.create(display_name="Editor")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=producer, group=group, role=Membership.Role.OWNER)
        Membership.objects.create(user=editor, group=group, role=Membership.Role.EDITOR)
        project = VideoProject.objects.create(group=group, title="Launch Video")
        ProjectNote.objects.create(project=project, author=producer, body="Older note.")
        ProjectNote.objects.create(project=project, author=editor, body="Latest handoff note.")
        self.client.force_login(producer)

        response = self.client.get(reverse("group_detail", kwargs={"slug": group.slug}))

        self.assertContains(response, "Latest note")
        self.assertContains(response, "Latest handoff note.")
        self.assertContains(response, "By Editor")
        self.assertContains(response, reverse("project_detail", kwargs={"group_slug": group.slug, "pk": project.pk}) + "#project-notes")
        self.assertNotContains(response, "Older note.")

    def test_group_detail_shows_empty_latest_note_state(self):
        producer = FocusUser.objects.create(display_name="Producer")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=producer, group=group, role=Membership.Role.OWNER)
        VideoProject.objects.create(group=group, title="Launch Video")
        self.client.force_login(producer)

        response = self.client.get(reverse("group_detail", kwargs={"slug": group.slug}))

        self.assertContains(response, "No notes yet.")

    def test_group_detail_explains_empty_status_filter(self):
        user = FocusUser.objects.create(display_name="Producer")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=user, group=group, role=Membership.Role.OWNER)
        VideoProject.objects.create(group=group, title="Script Draft", status=VideoProject.Status.SCRIPTING)
        self.client.force_login(user)

        response = self.client.get(
            reverse("group_detail", kwargs={"slug": group.slug}),
            {"status": VideoProject.Status.READY},
        )

        self.assertContains(response, "No projects match this status")
        self.assertContains(response, "Show all projects")
        self.assertNotContains(response, "Script Draft")

    def test_project_create_and_edit_within_member_group(self):
        user = FocusUser.objects.create(display_name="Producer")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=user, group=group, role=Membership.Role.OWNER)
        self.client.force_login(user)

        create_response = self.client.post(
            reverse("project_create", kwargs={"slug": group.slug}),
            {
                "title": "Launch Video",
                "description": "Opening episode",
                "status": "SCRIPTING",
                "asset_pipeline_url": "",
                "script_url": "",
            },
        )

        project = group.projects.get(title="Launch Video")
        self.assertRedirects(create_response, reverse("group_detail", kwargs={"slug": group.slug}))
        self.assertEqual(project.status, "SCRIPTING")
        self.assertEqual(project.created_by, user)

        edit_response = self.client.post(
            reverse("project_update", kwargs={"group_slug": group.slug, "pk": project.pk}),
            {
                "title": "Launch Video",
                "description": "Opening episode",
                "status": "EDITING",
                "asset_pipeline_url": "",
                "script_url": "",
            },
        )

        project.refresh_from_db()
        self.assertRedirects(edit_response, reverse("group_detail", kwargs={"slug": group.slug}))
        self.assertEqual(project.status, "EDITING")

    def test_admin_can_create_project(self):
        admin = FocusUser.objects.create(display_name="Admin")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=admin, group=group, role=Membership.Role.ADMIN)
        self.client.force_login(admin)

        response = self.client.post(
            reverse("project_create", kwargs={"slug": group.slug}),
            {
                "title": "Admin Project",
                "description": "",
                "status": VideoProject.Status.IDEA,
                "asset_pipeline_url": "",
                "script_url": "",
            },
        )

        project = group.projects.get(title="Admin Project")
        self.assertRedirects(response, reverse("group_detail", kwargs={"slug": group.slug}))
        self.assertEqual(project.created_by, admin)

    def test_member_who_is_not_owner_or_admin_cannot_create_project(self):
        owner = FocusUser.objects.create(display_name="Owner")
        writer = FocusUser.objects.create(display_name="Writer")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        Membership.objects.create(user=writer, group=group, role=Membership.Role.WRITER)
        self.client.force_login(writer)

        page_response = self.client.get(reverse("project_create", kwargs={"slug": group.slug}))
        create_response = self.client.post(
            reverse("project_create", kwargs={"slug": group.slug}),
            {
                "title": "Writer Project",
                "description": "",
                "status": VideoProject.Status.IDEA,
                "asset_pipeline_url": "",
                "script_url": "",
            },
        )

        self.assertEqual(page_response.status_code, 403)
        self.assertEqual(create_response.status_code, 403)
        self.assertFalse(group.projects.filter(title="Writer Project").exists())

    def test_group_detail_hides_project_create_links_from_members_who_cannot_create_projects(self):
        owner = FocusUser.objects.create(display_name="Owner")
        writer = FocusUser.objects.create(display_name="Writer")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        Membership.objects.create(user=writer, group=group, role=Membership.Role.WRITER)
        self.client.force_login(writer)

        response = self.client.get(reverse("group_detail", kwargs={"slug": group.slug}))

        self.assertContains(response, "Projects will appear here when a group owner or admin creates them.")
        self.assertNotContains(response, reverse("project_create", kwargs={"slug": group.slug}))
        self.assertNotContains(response, "Create project")
        self.assertNotContains(response, "Create first project")

    def test_project_creator_can_edit_their_project(self):
        creator = FocusUser.objects.create(display_name="Creator")
        owner = FocusUser.objects.create(display_name="Owner")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=creator, group=group, role=Membership.Role.WRITER)
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        project = VideoProject.objects.create(group=group, title="Creator Project", created_by=creator)
        self.client.force_login(creator)

        response = self.client.post(
            reverse("project_update", kwargs={"group_slug": group.slug, "pk": project.pk}),
            {
                "title": "Updated Creator Project",
                "description": "",
                "status": VideoProject.Status.SCRIPTING,
                "asset_pipeline_url": "",
                "script_url": "",
            },
        )

        project.refresh_from_db()
        self.assertRedirects(response, reverse("group_detail", kwargs={"slug": group.slug}))
        self.assertEqual(project.title, "Updated Creator Project")

    def test_assigned_collaborator_cannot_edit_core_project_details(self):
        owner = FocusUser.objects.create(display_name="Owner")
        editor = FocusUser.objects.create(display_name="Editor")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        Membership.objects.create(user=editor, group=group, role=Membership.Role.EDITOR)
        project = VideoProject.objects.create(group=group, title="Launch Video", created_by=owner)
        project.assigned_editors.add(editor)
        self.client.force_login(editor)

        get_response = self.client.get(reverse("project_update", kwargs={"group_slug": group.slug, "pk": project.pk}))
        post_response = self.client.post(
            reverse("project_update", kwargs={"group_slug": group.slug, "pk": project.pk}),
            {
                "title": "Changed By Editor",
                "description": "",
                "status": VideoProject.Status.REVIEW,
                "asset_pipeline_url": "",
                "script_url": "",
            },
        )

        project.refresh_from_db()
        self.assertEqual(get_response.status_code, 403)
        self.assertEqual(post_response.status_code, 403)
        self.assertEqual(project.title, "Launch Video")
        self.assertEqual(project.status, VideoProject.Status.IDEA)

    def test_project_assignments_can_be_created_and_updated(self):
        producer = FocusUser.objects.create(display_name="Producer")
        editor = FocusUser.objects.create(display_name="Editor")
        writer = FocusUser.objects.create(display_name="Writer")
        second_writer = FocusUser.objects.create(display_name="Second Writer")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=producer, group=group, role=Membership.Role.OWNER)
        Membership.objects.create(user=editor, group=group, role=Membership.Role.EDITOR)
        Membership.objects.create(user=writer, group=group, role=Membership.Role.WRITER)
        Membership.objects.create(user=second_writer, group=group, role=Membership.Role.WRITER)
        self.client.force_login(producer)

        self.client.post(
            reverse("project_create", kwargs={"slug": group.slug}),
            {
                "title": "Launch Video",
                "description": "",
                "status": "SCRIPTING",
                "asset_pipeline_url": "",
                "script_url": "",
                "assigned_editors": [str(editor.pk)],
                "assigned_writers": [str(writer.pk)],
            },
        )

        project = group.projects.get(title="Launch Video")
        self.assertQuerySetEqual(project.assigned_editors.all(), [editor])
        self.assertQuerySetEqual(project.assigned_writers.all(), [writer])

        self.client.post(
            reverse("project_update", kwargs={"group_slug": group.slug, "pk": project.pk}),
            {
                "title": "Launch Video",
                "description": "",
                "status": "EDITING",
                "asset_pipeline_url": "",
                "script_url": "",
                "assigned_editors": [],
                "assigned_writers": [str(second_writer.pk)],
            },
        )

        project.refresh_from_db()
        self.assertQuerySetEqual(project.assigned_editors.all(), [])
        self.assertQuerySetEqual(project.assigned_writers.all(), [second_writer])

    def test_project_creator_can_archive_and_restore_project(self):
        creator = FocusUser.objects.create(display_name="Creator")
        owner = FocusUser.objects.create(display_name="Owner")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=creator, group=group, role=Membership.Role.WRITER)
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        project = VideoProject.objects.create(
            group=group,
            title="Creator Project",
            status=VideoProject.Status.SCRIPTING,
            created_by=creator,
        )
        self.client.force_login(creator)

        archive_response = self.client.post(reverse("project_archive", kwargs={"group_slug": group.slug, "pk": project.pk}))

        project.refresh_from_db()
        self.assertRedirects(archive_response, reverse("project_detail", kwargs={"group_slug": group.slug, "pk": project.pk}))
        self.assertIsNotNone(project.archived_at)
        self.assertTrue(ProjectNote.objects.filter(project=project, body="Project archived.").exists())

        restore_response = self.client.post(reverse("project_restore", kwargs={"group_slug": group.slug, "pk": project.pk}))

        project.refresh_from_db()
        self.assertRedirects(restore_response, reverse("project_detail", kwargs={"group_slug": group.slug, "pk": project.pk}))
        self.assertIsNone(project.archived_at)
        self.assertTrue(ProjectNote.objects.filter(project=project, body="Project restored.").exists())

    def test_owner_and_admin_can_archive_and_restore_project(self):
        creator = FocusUser.objects.create(display_name="Creator")
        owner = FocusUser.objects.create(display_name="Owner")
        admin = FocusUser.objects.create(display_name="Admin")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=creator, group=group, role=Membership.Role.WRITER)
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        Membership.objects.create(user=admin, group=group, role=Membership.Role.ADMIN)
        project = VideoProject.objects.create(group=group, title="Team Project", created_by=creator)
        self.client.force_login(owner)

        archive_response = self.client.post(reverse("project_archive", kwargs={"group_slug": group.slug, "pk": project.pk}))

        project.refresh_from_db()
        self.assertRedirects(archive_response, reverse("project_detail", kwargs={"group_slug": group.slug, "pk": project.pk}))
        self.assertIsNotNone(project.archived_at)

        self.client.force_login(admin)
        restore_response = self.client.post(reverse("project_restore", kwargs={"group_slug": group.slug, "pk": project.pk}))

        project.refresh_from_db()
        self.assertRedirects(restore_response, reverse("project_detail", kwargs={"group_slug": group.slug, "pk": project.pk}))
        self.assertIsNone(project.archived_at)

    def test_member_who_is_not_creator_owner_or_admin_cannot_archive_project(self):
        creator = FocusUser.objects.create(display_name="Creator")
        editor = FocusUser.objects.create(display_name="Editor")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=creator, group=group, role=Membership.Role.WRITER)
        Membership.objects.create(user=editor, group=group, role=Membership.Role.EDITOR)
        project = VideoProject.objects.create(group=group, title="Team Project", created_by=creator)
        self.client.force_login(editor)

        response = self.client.post(reverse("project_archive", kwargs={"group_slug": group.slug, "pk": project.pk}))

        project.refresh_from_db()
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(project.archived_at)

    def test_archived_projects_are_hidden_from_active_assignment_lists(self):
        owner = FocusUser.objects.create(display_name="Owner")
        member = FocusUser.objects.create(display_name="Editor", show_assigned_projects=True)
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        member_membership = Membership.objects.create(user=member, group=group, role=Membership.Role.EDITOR)
        active_project = VideoProject.objects.create(group=group, title="Active Assignment")
        archived_project = VideoProject.objects.create(
            group=group,
            title="Archived Assignment",
            archived_at=timezone.now(),
        )
        active_project.assigned_editors.add(member)
        archived_project.assigned_editors.add(member)

        self.client.force_login(owner)
        group_response = self.client.get(reverse("group_detail", kwargs={"slug": group.slug}))
        archived_response = self.client.get(reverse("group_detail", kwargs={"slug": group.slug}), {"archive": "1"})
        profile_response = self.client.get(reverse("member_profile", kwargs={"slug": group.slug, "pk": member_membership.pk}))

        self.assertContains(group_response, "Active Assignment")
        self.assertNotContains(group_response, "Archived Assignment")
        self.assertContains(group_response, "Archived projects (1)")
        self.assertContains(archived_response, "Archived Assignment")
        self.assertNotContains(archived_response, "Active Assignment")
        self.assertContains(profile_response, "Active Assignment")
        self.assertNotContains(profile_response, "Archived Assignment")

        self.client.force_login(member)
        dashboard_response = self.client.get(reverse("dashboard"))

        self.assertContains(dashboard_response, "Active Assignment")
        self.assertNotContains(dashboard_response, "Archived Assignment")

    def test_archived_project_detail_remains_available_to_group_members(self):
        owner = FocusUser.objects.create(display_name="Owner")
        member = FocusUser.objects.create(display_name="Editor")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        Membership.objects.create(user=member, group=group, role=Membership.Role.EDITOR)
        project = VideoProject.objects.create(group=group, title="Archived Project", archived_at=timezone.now())
        self.client.force_login(member)

        response = self.client.get(reverse("project_detail", kwargs={"group_slug": group.slug, "pk": project.pk}))

        self.assertContains(response, "Archived Project")
        self.assertContains(response, "Archive status")
        self.assertContains(response, "Archived on")

    def test_project_assignment_form_uses_group_member_checkbox_groups(self):
        producer = FocusUser.objects.create(display_name="Producer")
        editor = FocusUser.objects.create(display_name="Editor")
        outsider = FocusUser.objects.create(display_name="Outsider")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=producer, group=group, role=Membership.Role.OWNER)
        Membership.objects.create(user=editor, group=group, role=Membership.Role.EDITOR)
        self.client.force_login(producer)

        response = self.client.get(reverse("project_create", kwargs={"slug": group.slug}))

        self.assertContains(response, '<fieldset class="field-group checkbox-group"', html=False)
        self.assertContains(response, "Assigned editors")
        self.assertContains(response, "Assigned writers")
        self.assertContains(response, editor.public_name)
        self.assertNotContains(response, outsider.public_name)

    def test_project_detail_is_visible_to_group_member(self):
        producer = FocusUser.objects.create(display_name="Producer")
        editor = FocusUser.objects.create(display_name="Editor")
        writer = FocusUser.objects.create(display_name="Writer")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=producer, group=group, role=Membership.Role.OWNER)
        Membership.objects.create(user=editor, group=group, role=Membership.Role.EDITOR)
        Membership.objects.create(user=writer, group=group, role=Membership.Role.WRITER)
        project = VideoProject.objects.create(
            group=group,
            title="Launch Video",
            description="Opening episode",
            status=VideoProject.Status.REVIEW,
            asset_pipeline_url="https://example.com/assets",
            script_url="https://example.com/script",
        )
        project.assigned_editors.add(editor)
        project.assigned_writers.add(writer)
        self.client.force_login(editor)

        response = self.client.get(reverse("project_detail", kwargs={"group_slug": group.slug, "pk": project.pk}))

        self.assertContains(response, "Launch Video")
        self.assertContains(response, "In Internal Review")
        self.assertContains(response, "Open asset folder for Launch Video")
        self.assertContains(response, "Open script for Launch Video")
        self.assertContains(response, editor.public_name)
        self.assertContains(response, writer.public_name)
        self.assertContains(response, reverse("project_status_update", kwargs={"group_slug": group.slug, "pk": project.pk}))
        self.assertContains(response, "Update status")
        self.assertContains(response, "Download Launch Video export (Markdown)")
        self.assertContains(response, reverse("project_export", kwargs={"group_slug": group.slug, "pk": project.pk}))
        self.assertNotContains(response, reverse("project_delete", kwargs={"group_slug": group.slug, "pk": project.pk}))
        self.assertContains(response, "Project notes")
        self.assertContains(response, reverse("project_note_create", kwargs={"group_slug": group.slug, "pk": project.pk}))
        self.assertContains(response, "No notes have been added yet.")

    def test_project_detail_shows_delete_link_to_project_creator(self):
        creator = FocusUser.objects.create(display_name="Creator")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=creator, group=group, role=Membership.Role.WRITER)
        project = VideoProject.objects.create(group=group, title="Launch Video", created_by=creator)
        self.client.force_login(creator)

        response = self.client.get(reverse("project_detail", kwargs={"group_slug": group.slug, "pk": project.pk}))

        self.assertContains(response, "Delete Launch Video")
        self.assertContains(response, reverse("project_delete", kwargs={"group_slug": group.slug, "pk": project.pk}))

    def test_project_detail_hides_change_controls_from_unassigned_member(self):
        owner = FocusUser.objects.create(display_name="Owner")
        talent = FocusUser.objects.create(display_name="Talent")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        Membership.objects.create(user=talent, group=group, role=Membership.Role.TALENT)
        project = VideoProject.objects.create(group=group, title="Launch Video", created_by=owner)
        self.client.force_login(talent)

        response = self.client.get(reverse("project_detail", kwargs={"group_slug": group.slug, "pk": project.pk}))

        self.assertContains(response, "Launch Video")
        self.assertContains(response, "Download Launch Video export (Markdown)")
        self.assertNotContains(response, reverse("project_update", kwargs={"group_slug": group.slug, "pk": project.pk}))
        self.assertNotContains(response, reverse("project_status_update", kwargs={"group_slug": group.slug, "pk": project.pk}))
        self.assertNotContains(response, reverse("project_resource_create", kwargs={"group_slug": group.slug, "pk": project.pk}))
        self.assertNotContains(response, reverse("project_note_create", kwargs={"group_slug": group.slug, "pk": project.pk}))
        self.assertNotContains(response, "Update status")
        self.assertNotContains(response, "Add resource")
        self.assertNotContains(response, "Add note")

    def test_group_detail_shows_view_action_for_members_who_cannot_manage_project(self):
        owner = FocusUser.objects.create(display_name="Owner")
        talent = FocusUser.objects.create(display_name="Talent")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        Membership.objects.create(user=talent, group=group, role=Membership.Role.TALENT)
        project = VideoProject.objects.create(group=group, title="Launch Video", created_by=owner)
        self.client.force_login(talent)

        response = self.client.get(reverse("group_detail", kwargs={"slug": group.slug}))

        self.assertContains(response, "View Launch Video")
        self.assertNotContains(response, reverse("project_update", kwargs={"group_slug": group.slug, "pk": project.pk}))

    def test_group_member_can_export_project_as_markdown(self):
        producer = FocusUser.objects.create(display_name="Producer")
        editor = FocusUser.objects.create(display_name="Editor")
        writer = FocusUser.objects.create(display_name="Writer")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=producer, group=group, role=Membership.Role.OWNER)
        Membership.objects.create(user=editor, group=group, role=Membership.Role.EDITOR)
        Membership.objects.create(user=writer, group=group, role=Membership.Role.WRITER)
        project = VideoProject.objects.create(
            group=group,
            title="Launch Video",
            description="Opening episode",
            status=VideoProject.Status.REVIEW,
            asset_pipeline_url="https://example.com/assets",
            script_url="https://example.com/script",
            created_by=producer,
        )
        project.assigned_editors.add(editor)
        project.assigned_writers.add(writer)
        ProjectResource.objects.create(
            project=project,
            added_by=editor,
            kind=ProjectResource.Kind.REVIEW,
            title="Review cut",
            url="https://example.com/review",
        )
        ProjectNote.objects.create(project=project, author=producer, body="First export note.")
        ProjectNote.objects.create(project=project, author=editor, body="Second export note.")
        self.client.force_login(writer)

        response = self.client.get(reverse("project_export", kwargs={"group_slug": group.slug, "pk": project.pk}))
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/markdown; charset=utf-8")
        self.assertEqual(response["Content-Disposition"], 'attachment; filename="launch-video-focus-export.md"')
        self.assertIn("# Launch Video", content)
        self.assertIn("- Group: Studio", content)
        self.assertIn("- Status: In Internal Review", content)
        self.assertIn("- Created by: Producer", content)
        self.assertIn("Opening episode", content)
        self.assertIn("- Asset folder: https://example.com/assets", content)
        self.assertIn("- Script: https://example.com/script", content)
        self.assertIn("- Editor", content)
        self.assertIn("- Writer", content)
        self.assertIn("[Review cut](https://example.com/review) - Review link, added by Editor", content)
        self.assertIn("First export note.", content)
        self.assertIn("Second export note.", content)

    def test_project_export_uses_empty_states_when_project_has_no_optional_content(self):
        producer = FocusUser.objects.create(display_name="Producer")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=producer, group=group, role=Membership.Role.OWNER)
        project = VideoProject.objects.create(group=group, title="Quiet Project")
        self.client.force_login(producer)

        response = self.client.get(reverse("project_export", kwargs={"group_slug": group.slug, "pk": project.pk}))
        content = response.content.decode()

        self.assertIn("No description.", content)
        self.assertIn("- Asset folder: Not set.", content)
        self.assertIn("- Script: Not set.", content)
        self.assertIn("No editors assigned.", content)
        self.assertIn("No writers assigned.", content)
        self.assertIn("No resources added.", content)
        self.assertIn("No notes added.", content)

    def test_non_member_cannot_export_project(self):
        producer = FocusUser.objects.create(display_name="Producer")
        outsider = FocusUser.objects.create(display_name="Outsider")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=producer, group=group, role=Membership.Role.OWNER)
        project = VideoProject.objects.create(group=group, title="Launch Video")
        self.client.force_login(outsider)

        response = self.client.get(reverse("project_export", kwargs={"group_slug": group.slug, "pk": project.pk}))

        self.assertEqual(response.status_code, 404)

    def test_project_creator_can_delete_project_from_confirmation_page(self):
        creator = FocusUser.objects.create(display_name="Creator")
        editor = FocusUser.objects.create(display_name="Editor")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=creator, group=group, role=Membership.Role.WRITER)
        Membership.objects.create(user=editor, group=group, role=Membership.Role.EDITOR)
        project = VideoProject.objects.create(group=group, title="Launch Video", created_by=creator)
        project.assigned_editors.add(editor)
        resource = ProjectResource.objects.create(
            project=project,
            added_by=creator,
            kind=ProjectResource.Kind.REFERENCE,
            title="Visual references",
            url="https://example.com/reference",
        )
        note = ProjectNote.objects.create(project=project, author=creator, body="Ready to remove.")
        self.client.force_login(creator)

        page_response = self.client.get(reverse("project_delete", kwargs={"group_slug": group.slug, "pk": project.pk}))

        self.assertContains(page_response, "Delete Launch Video")
        self.assertContains(page_response, "This cannot be undone")
        self.assertContains(page_response, "permanently removes its notes, resources, assignments, and project details")
        self.assertContains(page_response, "Keep Launch Video")

        delete_response = self.client.post(reverse("project_delete", kwargs={"group_slug": group.slug, "pk": project.pk}))

        self.assertRedirects(delete_response, reverse("group_detail", kwargs={"slug": group.slug}))
        self.assertFalse(VideoProject.objects.filter(pk=project.pk).exists())
        self.assertFalse(ProjectResource.objects.filter(pk=resource.pk).exists())
        self.assertFalse(ProjectNote.objects.filter(pk=note.pk).exists())

    def test_owner_and_admin_can_delete_project(self):
        creator = FocusUser.objects.create(display_name="Creator")
        owner = FocusUser.objects.create(display_name="Owner")
        admin = FocusUser.objects.create(display_name="Admin")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=creator, group=group, role=Membership.Role.WRITER)
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        Membership.objects.create(user=admin, group=group, role=Membership.Role.ADMIN)
        owner_project = VideoProject.objects.create(group=group, title="Owner Delete", created_by=creator)
        admin_project = VideoProject.objects.create(group=group, title="Admin Delete", created_by=creator)

        self.client.force_login(owner)
        owner_response = self.client.post(reverse("project_delete", kwargs={"group_slug": group.slug, "pk": owner_project.pk}))

        self.assertRedirects(owner_response, reverse("group_detail", kwargs={"slug": group.slug}))
        self.assertFalse(VideoProject.objects.filter(pk=owner_project.pk).exists())

        self.client.force_login(admin)
        admin_response = self.client.post(reverse("project_delete", kwargs={"group_slug": group.slug, "pk": admin_project.pk}))

        self.assertRedirects(admin_response, reverse("group_detail", kwargs={"slug": group.slug}))
        self.assertFalse(VideoProject.objects.filter(pk=admin_project.pk).exists())

    def test_member_who_is_not_creator_owner_or_admin_cannot_delete_project(self):
        creator = FocusUser.objects.create(display_name="Creator")
        editor = FocusUser.objects.create(display_name="Editor")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=creator, group=group, role=Membership.Role.WRITER)
        Membership.objects.create(user=editor, group=group, role=Membership.Role.EDITOR)
        project = VideoProject.objects.create(group=group, title="Team Project", created_by=creator)
        self.client.force_login(editor)

        page_response = self.client.get(reverse("project_delete", kwargs={"group_slug": group.slug, "pk": project.pk}))
        delete_response = self.client.post(reverse("project_delete", kwargs={"group_slug": group.slug, "pk": project.pk}))

        self.assertEqual(page_response.status_code, 403)
        self.assertEqual(delete_response.status_code, 403)
        self.assertTrue(VideoProject.objects.filter(pk=project.pk).exists())

    def test_non_member_cannot_delete_project(self):
        producer = FocusUser.objects.create(display_name="Producer")
        outsider = FocusUser.objects.create(display_name="Outsider")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=producer, group=group, role=Membership.Role.OWNER)
        project = VideoProject.objects.create(group=group, title="Launch Video")
        self.client.force_login(outsider)

        response = self.client.get(reverse("project_delete", kwargs={"group_slug": group.slug, "pk": project.pk}))

        self.assertEqual(response.status_code, 404)
        self.assertTrue(VideoProject.objects.filter(pk=project.pk).exists())

    def test_project_detail_shows_resources_and_add_form(self):
        producer = FocusUser.objects.create(display_name="Producer")
        editor = FocusUser.objects.create(display_name="Editor")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=producer, group=group, role=Membership.Role.OWNER)
        Membership.objects.create(user=editor, group=group, role=Membership.Role.EDITOR)
        project = VideoProject.objects.create(group=group, title="Launch Video", created_by=producer)
        project.assigned_editors.add(editor)
        resource = ProjectResource.objects.create(
            project=project,
            added_by=editor,
            kind=ProjectResource.Kind.SCRIPT,
            title="Final script",
            url="https://example.com/script",
        )
        self.client.force_login(editor)

        response = self.client.get(reverse("project_detail", kwargs={"group_slug": group.slug, "pk": project.pk}))

        self.assertContains(response, "Project resources")
        self.assertContains(response, "Add a resource")
        self.assertContains(response, reverse("project_resource_create", kwargs={"group_slug": group.slug, "pk": project.pk}))
        self.assertContains(response, "Final script")
        self.assertContains(response, "Script")
        self.assertContains(response, "Added by Editor")
        self.assertContains(response, "Open Final script for Launch Video")
        self.assertContains(response, reverse("project_resource_remove", kwargs={"group_slug": group.slug, "pk": project.pk, "resource_pk": resource.pk}))

    def test_group_member_can_add_project_resource(self):
        producer = FocusUser.objects.create(display_name="Producer")
        editor = FocusUser.objects.create(display_name="Editor")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=producer, group=group, role=Membership.Role.OWNER)
        Membership.objects.create(user=editor, group=group, role=Membership.Role.EDITOR)
        project = VideoProject.objects.create(group=group, title="Launch Video")
        project.assigned_editors.add(editor)
        self.client.force_login(editor)

        response = self.client.post(
            reverse("project_resource_create", kwargs={"group_slug": group.slug, "pk": project.pk}),
            {
                "kind": ProjectResource.Kind.ASSETS,
                "title": "Shared asset folder",
                "url": "https://example.com/assets",
            },
        )

        resource = ProjectResource.objects.get(project=project)
        self.assertRedirects(
            response,
            f"{reverse('project_detail', kwargs={'group_slug': group.slug, 'pk': project.pk})}#project-resources",
            fetch_redirect_response=False,
        )
        self.assertEqual(resource.added_by, editor)
        self.assertEqual(resource.title, "Shared asset folder")
        self.assertEqual(resource.kind, ProjectResource.Kind.ASSETS)
        self.assertTrue(ProjectNote.objects.filter(project=project, body="Resource added: Shared asset folder (Asset folder).").exists())

    def test_unassigned_member_cannot_add_project_resource(self):
        owner = FocusUser.objects.create(display_name="Owner")
        editor = FocusUser.objects.create(display_name="Editor")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        Membership.objects.create(user=editor, group=group, role=Membership.Role.EDITOR)
        project = VideoProject.objects.create(group=group, title="Launch Video", created_by=owner)
        self.client.force_login(editor)

        response = self.client.post(
            reverse("project_resource_create", kwargs={"group_slug": group.slug, "pk": project.pk}),
            {
                "kind": ProjectResource.Kind.SCRIPT,
                "title": "Private script",
                "url": "https://example.com/script",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(ProjectResource.objects.filter(project=project).exists())

    def test_project_resource_errors_are_exposed_to_assistive_technology(self):
        producer = FocusUser.objects.create(display_name="Producer")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=producer, group=group, role=Membership.Role.OWNER)
        project = VideoProject.objects.create(group=group, title="Launch Video")
        self.client.force_login(producer)

        response = self.client.post(
            reverse("project_resource_create", kwargs={"group_slug": group.slug, "pk": project.pk}),
            {"kind": ProjectResource.Kind.SCRIPT, "title": "", "url": ""},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ProjectResource.objects.filter(project=project).exists())
        self.assertContains(response, 'role="alert"')
        self.assertContains(response, 'aria-invalid="true"')
        self.assertContains(response, 'id="title-error"')
        self.assertContains(response, 'id="url-error"')

    def test_non_member_cannot_add_project_resource(self):
        producer = FocusUser.objects.create(display_name="Producer")
        outsider = FocusUser.objects.create(display_name="Outsider")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=producer, group=group, role=Membership.Role.OWNER)
        project = VideoProject.objects.create(group=group, title="Launch Video")
        self.client.force_login(outsider)

        response = self.client.post(
            reverse("project_resource_create", kwargs={"group_slug": group.slug, "pk": project.pk}),
            {
                "kind": ProjectResource.Kind.SCRIPT,
                "title": "Private script",
                "url": "https://example.com/script",
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(ProjectResource.objects.filter(project=project).exists())

    def test_resource_adder_can_remove_project_resource(self):
        producer = FocusUser.objects.create(display_name="Producer")
        editor = FocusUser.objects.create(display_name="Editor")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=producer, group=group, role=Membership.Role.OWNER)
        Membership.objects.create(user=editor, group=group, role=Membership.Role.EDITOR)
        project = VideoProject.objects.create(group=group, title="Launch Video")
        resource = ProjectResource.objects.create(
            project=project,
            added_by=editor,
            kind=ProjectResource.Kind.REVIEW,
            title="Review cut",
            url="https://example.com/review",
        )
        self.client.force_login(editor)

        response = self.client.post(reverse("project_resource_remove", kwargs={"group_slug": group.slug, "pk": project.pk, "resource_pk": resource.pk}))

        self.assertRedirects(
            response,
            f"{reverse('project_detail', kwargs={'group_slug': group.slug, 'pk': project.pk})}#project-resources",
            fetch_redirect_response=False,
        )
        self.assertFalse(ProjectResource.objects.filter(pk=resource.pk).exists())
        self.assertTrue(ProjectNote.objects.filter(project=project, body="Resource removed: Review cut.").exists())

    def test_owner_can_remove_project_resource_added_by_member(self):
        owner = FocusUser.objects.create(display_name="Owner")
        editor = FocusUser.objects.create(display_name="Editor")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        Membership.objects.create(user=editor, group=group, role=Membership.Role.EDITOR)
        project = VideoProject.objects.create(group=group, title="Launch Video")
        resource = ProjectResource.objects.create(
            project=project,
            added_by=editor,
            kind=ProjectResource.Kind.REVIEW,
            title="Review cut",
            url="https://example.com/review",
        )
        self.client.force_login(owner)

        response = self.client.post(reverse("project_resource_remove", kwargs={"group_slug": group.slug, "pk": project.pk, "resource_pk": resource.pk}))

        self.assertRedirects(
            response,
            f"{reverse('project_detail', kwargs={'group_slug': group.slug, 'pk': project.pk})}#project-resources",
            fetch_redirect_response=False,
        )
        self.assertFalse(ProjectResource.objects.filter(pk=resource.pk).exists())

    def test_member_who_did_not_add_resource_cannot_remove_it(self):
        creator = FocusUser.objects.create(display_name="Creator")
        editor = FocusUser.objects.create(display_name="Editor")
        writer = FocusUser.objects.create(display_name="Writer")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=creator, group=group, role=Membership.Role.WRITER)
        Membership.objects.create(user=editor, group=group, role=Membership.Role.EDITOR)
        Membership.objects.create(user=writer, group=group, role=Membership.Role.WRITER)
        project = VideoProject.objects.create(group=group, title="Launch Video", created_by=creator)
        resource = ProjectResource.objects.create(
            project=project,
            added_by=editor,
            kind=ProjectResource.Kind.REVIEW,
            title="Review cut",
            url="https://example.com/review",
        )
        self.client.force_login(writer)

        response = self.client.post(reverse("project_resource_remove", kwargs={"group_slug": group.slug, "pk": project.pk, "resource_pk": resource.pk}))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(ProjectResource.objects.filter(pk=resource.pk).exists())

    def test_group_member_can_add_project_note(self):
        producer = FocusUser.objects.create(display_name="Producer")
        editor = FocusUser.objects.create(display_name="Editor")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=producer, group=group, role=Membership.Role.OWNER)
        Membership.objects.create(user=editor, group=group, role=Membership.Role.EDITOR)
        project = VideoProject.objects.create(group=group, title="Launch Video")
        project.assigned_editors.add(editor)
        self.client.force_login(editor)

        response = self.client.post(
            reverse("project_note_create", kwargs={"group_slug": group.slug, "pk": project.pk}),
            {"body": "Script draft updated. Ready for edit pass."},
        )

        note = ProjectNote.objects.get(project=project)
        self.assertRedirects(
            response,
            f"{reverse('project_detail', kwargs={'group_slug': group.slug, 'pk': project.pk})}#project-notes",
            fetch_redirect_response=False,
        )
        self.assertEqual(note.author, editor)
        self.assertEqual(note.body, "Script draft updated. Ready for edit pass.")

    def test_unassigned_member_cannot_add_project_note(self):
        owner = FocusUser.objects.create(display_name="Owner")
        editor = FocusUser.objects.create(display_name="Editor")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        Membership.objects.create(user=editor, group=group, role=Membership.Role.EDITOR)
        project = VideoProject.objects.create(group=group, title="Launch Video", created_by=owner)
        self.client.force_login(editor)

        response = self.client.post(
            reverse("project_note_create", kwargs={"group_slug": group.slug, "pk": project.pk}),
            {"body": "I should not be able to add this."},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(ProjectNote.objects.filter(project=project).exists())

    def test_project_detail_shows_notes_newest_first(self):
        producer = FocusUser.objects.create(display_name="Producer")
        editor = FocusUser.objects.create(display_name="Editor")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=producer, group=group, role=Membership.Role.OWNER)
        Membership.objects.create(user=editor, group=group, role=Membership.Role.EDITOR)
        project = VideoProject.objects.create(group=group, title="Launch Video")
        older_note = ProjectNote.objects.create(project=project, author=producer, body="First update.")
        newer_note = ProjectNote.objects.create(project=project, author=editor, body="Second update.")
        self.client.force_login(producer)

        response = self.client.get(reverse("project_detail", kwargs={"group_slug": group.slug, "pk": project.pk}))
        content = response.content.decode()

        self.assertContains(response, older_note.body)
        self.assertContains(response, newer_note.body)
        self.assertContains(response, producer.public_name)
        self.assertContains(response, editor.public_name)
        self.assertLess(content.index(newer_note.body), content.index(older_note.body))

    def test_project_note_errors_are_exposed_to_assistive_technology(self):
        producer = FocusUser.objects.create(display_name="Producer")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=producer, group=group, role=Membership.Role.OWNER)
        project = VideoProject.objects.create(group=group, title="Launch Video")
        self.client.force_login(producer)

        response = self.client.post(
            reverse("project_note_create", kwargs={"group_slug": group.slug, "pk": project.pk}),
            {"body": ""},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ProjectNote.objects.filter(project=project).exists())
        self.assertContains(response, 'role="alert"')
        self.assertContains(response, 'aria-invalid="true"')
        self.assertContains(response, 'id="body-error"')

    def test_non_member_cannot_add_project_note(self):
        producer = FocusUser.objects.create(display_name="Producer")
        outsider = FocusUser.objects.create(display_name="Outsider")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=producer, group=group, role=Membership.Role.OWNER)
        project = VideoProject.objects.create(group=group, title="Launch Video")
        self.client.force_login(outsider)

        response = self.client.post(
            reverse("project_note_create", kwargs={"group_slug": group.slug, "pk": project.pk}),
            {"body": "I should not be able to add this."},
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(ProjectNote.objects.filter(project=project).exists())

    def test_group_member_can_update_project_status_from_detail_page(self):
        producer = FocusUser.objects.create(display_name="Producer")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=producer, group=group, role=Membership.Role.OWNER)
        project = VideoProject.objects.create(
            group=group,
            title="Launch Video",
            status=VideoProject.Status.SCRIPTING,
        )
        self.client.force_login(producer)

        response = self.client.post(
            reverse("project_status_update", kwargs={"group_slug": group.slug, "pk": project.pk}),
            {"status": VideoProject.Status.REVIEW},
        )

        project.refresh_from_db()
        self.assertRedirects(response, reverse("project_detail", kwargs={"group_slug": group.slug, "pk": project.pk}))
        self.assertEqual(project.status, VideoProject.Status.REVIEW)

    def test_assigned_collaborator_can_update_project_status(self):
        owner = FocusUser.objects.create(display_name="Owner")
        writer = FocusUser.objects.create(display_name="Writer")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        Membership.objects.create(user=writer, group=group, role=Membership.Role.WRITER)
        project = VideoProject.objects.create(
            group=group,
            title="Launch Video",
            status=VideoProject.Status.SCRIPTING,
            created_by=owner,
        )
        project.assigned_writers.add(writer)
        self.client.force_login(writer)

        response = self.client.post(
            reverse("project_status_update", kwargs={"group_slug": group.slug, "pk": project.pk}),
            {"status": VideoProject.Status.REVIEW},
        )

        project.refresh_from_db()
        self.assertRedirects(response, reverse("project_detail", kwargs={"group_slug": group.slug, "pk": project.pk}))
        self.assertEqual(project.status, VideoProject.Status.REVIEW)

    def test_unassigned_member_cannot_update_project_status(self):
        owner = FocusUser.objects.create(display_name="Owner")
        writer = FocusUser.objects.create(display_name="Writer")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        Membership.objects.create(user=writer, group=group, role=Membership.Role.WRITER)
        project = VideoProject.objects.create(
            group=group,
            title="Launch Video",
            status=VideoProject.Status.SCRIPTING,
            created_by=owner,
        )
        self.client.force_login(writer)

        response = self.client.post(
            reverse("project_status_update", kwargs={"group_slug": group.slug, "pk": project.pk}),
            {"status": VideoProject.Status.REVIEW},
        )

        project.refresh_from_db()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(project.status, VideoProject.Status.SCRIPTING)

    def test_status_update_creates_project_note(self):
        producer = FocusUser.objects.create(display_name="Producer")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=producer, group=group, role=Membership.Role.OWNER)
        project = VideoProject.objects.create(
            group=group,
            title="Launch Video",
            status=VideoProject.Status.SCRIPTING,
        )
        self.client.force_login(producer)

        self.client.post(
            reverse("project_status_update", kwargs={"group_slug": group.slug, "pk": project.pk}),
            {"status": VideoProject.Status.REVIEW},
        )

        note = ProjectNote.objects.get(project=project)
        self.assertEqual(note.author, producer)
        self.assertEqual(note.body, "Status changed from Scripting to In Internal Review.")

    def test_same_status_update_does_not_create_project_note(self):
        producer = FocusUser.objects.create(display_name="Producer")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=producer, group=group, role=Membership.Role.OWNER)
        project = VideoProject.objects.create(
            group=group,
            title="Launch Video",
            status=VideoProject.Status.SCRIPTING,
        )
        self.client.force_login(producer)

        self.client.post(
            reverse("project_status_update", kwargs={"group_slug": group.slug, "pk": project.pk}),
            {"status": VideoProject.Status.SCRIPTING},
        )

        self.assertFalse(ProjectNote.objects.filter(project=project).exists())

    def test_project_status_update_errors_are_exposed_to_assistive_technology(self):
        producer = FocusUser.objects.create(display_name="Producer")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=producer, group=group, role=Membership.Role.OWNER)
        project = VideoProject.objects.create(group=group, title="Launch Video")
        self.client.force_login(producer)

        response = self.client.post(
            reverse("project_status_update", kwargs={"group_slug": group.slug, "pk": project.pk}),
            {"status": "NOT_A_STATUS"},
        )

        project.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(project.status, VideoProject.Status.IDEA)
        self.assertFalse(ProjectNote.objects.filter(project=project).exists())
        self.assertContains(response, 'role="alert"')
        self.assertContains(response, 'aria-invalid="true"')
        self.assertContains(response, 'id="status-error"')

    def test_non_member_cannot_update_project_status(self):
        member = FocusUser.objects.create(display_name="Member")
        outsider = FocusUser.objects.create(display_name="Outsider")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=member, group=group, role=Membership.Role.OWNER)
        project = VideoProject.objects.create(group=group, title="Launch Video")
        self.client.force_login(outsider)

        response = self.client.post(
            reverse("project_status_update", kwargs={"group_slug": group.slug, "pk": project.pk}),
            {"status": VideoProject.Status.REVIEW},
        )

        project.refresh_from_db()
        self.assertEqual(response.status_code, 404)
        self.assertEqual(project.status, VideoProject.Status.IDEA)

    def test_project_detail_rejects_non_member(self):
        member = FocusUser.objects.create(display_name="Member")
        outsider = FocusUser.objects.create(display_name="Outsider")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=member, group=group, role=Membership.Role.OWNER)
        project = VideoProject.objects.create(group=group, title="Launch Video")
        self.client.force_login(outsider)

        response = self.client.get(reverse("project_detail", kwargs={"group_slug": group.slug, "pk": project.pk}))

        self.assertEqual(response.status_code, 404)

    def test_group_detail_links_project_title_to_detail_page(self):
        producer = FocusUser.objects.create(display_name="Producer")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=producer, group=group, role=Membership.Role.OWNER)
        project = VideoProject.objects.create(group=group, title="Launch Video")
        self.client.force_login(producer)

        response = self.client.get(reverse("group_detail", kwargs={"slug": group.slug}))

        self.assertContains(response, reverse("project_detail", kwargs={"group_slug": group.slug, "pk": project.pk}))

    def test_project_form_errors_are_exposed_to_assistive_technology(self):
        user = FocusUser.objects.create(display_name="Producer")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=user, group=group, role=Membership.Role.OWNER)
        self.client.force_login(user)

        response = self.client.post(
            reverse("project_create", kwargs={"slug": group.slug}),
            {
                "title": "",
                "description": "",
                "status": "IDEA",
                "asset_pipeline_url": "",
                "script_url": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'role="alert"')
        self.assertContains(response, 'aria-invalid="true"')
        self.assertContains(response, 'id="title-error"')


class InvitationFlowTests(TestCase):
    def test_owner_can_create_invite_link(self):
        owner = FocusUser.objects.create(display_name="Owner")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        self.client.force_login(owner)

        response = self.client.post(
            reverse("group_invitations", kwargs={"slug": group.slug}),
            {"role_to_assign": Membership.Role.EDITOR},
        )

        invitation = GroupInvitation.objects.get(group=group)
        self.assertRedirects(response, reverse("group_invitations", kwargs={"slug": group.slug}))
        self.assertEqual(invitation.role_to_assign, Membership.Role.EDITOR)

    def test_non_owner_cannot_create_invite_link(self):
        member = FocusUser.objects.create(display_name="Member")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=member, group=group, role=Membership.Role.EDITOR)
        self.client.force_login(member)

        response = self.client.post(
            reverse("group_invitations", kwargs={"slug": group.slug}),
            {"role_to_assign": Membership.Role.WRITER},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(GroupInvitation.objects.filter(group=group).exists())

    def test_non_owner_cannot_view_existing_invite_links(self):
        owner = FocusUser.objects.create(display_name="Owner")
        member = FocusUser.objects.create(display_name="Member")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        Membership.objects.create(user=member, group=group, role=Membership.Role.EDITOR)
        GroupInvitation.objects.create(group=group, role_to_assign=Membership.Role.WRITER)
        self.client.force_login(member)

        response = self.client.get(reverse("group_invitations", kwargs={"slug": group.slug}))

        self.assertEqual(response.status_code, 403)

    def test_owner_can_view_existing_invite_links(self):
        owner = FocusUser.objects.create(display_name="Owner")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        invitation = GroupInvitation.objects.create(group=group, role_to_assign=Membership.Role.WRITER)
        self.client.force_login(owner)

        response = self.client.get(reverse("group_invitations", kwargs={"slug": group.slug}))

        self.assertContains(response, "Invite links for Studio")
        self.assertContains(response, str(invitation.token))
        self.assertContains(response, "Create invite link")
        self.assertContains(response, "Available")
        self.assertContains(response, f'id="invite-url-{invitation.pk}"')
        self.assertContains(response, "Copy invite link for Script Writer")
        self.assertContains(response, 'role="status"')
        self.assertContains(response, "Revoke invite for Script Writer")

    def test_owner_can_revoke_unused_invite_link(self):
        owner = FocusUser.objects.create(display_name="Owner")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        invitation = GroupInvitation.objects.create(group=group, role_to_assign=Membership.Role.WRITER)
        self.client.force_login(owner)

        response = self.client.post(reverse("group_invitation_revoke", kwargs={"slug": group.slug, "pk": invitation.pk}))

        invitation.refresh_from_db()
        self.assertRedirects(response, reverse("group_invitations", kwargs={"slug": group.slug}))
        self.assertIsNotNone(invitation.revoked_at)
        self.assertFalse(invitation.is_used)

    def test_non_owner_cannot_revoke_invite_link(self):
        owner = FocusUser.objects.create(display_name="Owner")
        member = FocusUser.objects.create(display_name="Member")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        Membership.objects.create(user=member, group=group, role=Membership.Role.EDITOR)
        invitation = GroupInvitation.objects.create(group=group, role_to_assign=Membership.Role.WRITER)
        self.client.force_login(member)

        response = self.client.post(reverse("group_invitation_revoke", kwargs={"slug": group.slug, "pk": invitation.pk}))

        invitation.refresh_from_db()
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(invitation.revoked_at)

    def test_used_invite_cannot_be_revoked(self):
        owner = FocusUser.objects.create(display_name="Owner")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        invitation = GroupInvitation.objects.create(
            group=group,
            role_to_assign=Membership.Role.WRITER,
            is_used=True,
        )
        self.client.force_login(owner)

        response = self.client.post(reverse("group_invitation_revoke", kwargs={"slug": group.slug, "pk": invitation.pk}))

        invitation.refresh_from_db()
        self.assertRedirects(response, reverse("group_invitations", kwargs={"slug": group.slug}))
        self.assertIsNone(invitation.revoked_at)

    def test_revoked_invite_status_hides_revoke_action(self):
        owner = FocusUser.objects.create(display_name="Owner")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        invitation = GroupInvitation.objects.create(
            group=group,
            role_to_assign=Membership.Role.WRITER,
            revoked_at=timezone.now(),
        )
        self.client.force_login(owner)

        response = self.client.get(reverse("group_invitations", kwargs={"slug": group.slug}))

        self.assertContains(response, "Revoked")
        self.assertNotContains(response, reverse("group_invitation_revoke", kwargs={"slug": group.slug, "pk": invitation.pk}))
        self.assertNotContains(response, "Copy invite link for Script Writer")
        self.assertContains(response, "Copying is unavailable because this invite has been revoked.")

    def test_used_invite_status_hides_copy_action(self):
        owner = FocusUser.objects.create(display_name="Owner")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        GroupInvitation.objects.create(
            group=group,
            role_to_assign=Membership.Role.WRITER,
            is_used=True,
        )
        self.client.force_login(owner)

        response = self.client.get(reverse("group_invitations", kwargs={"slug": group.slug}))

        self.assertContains(response, "Used")
        self.assertNotContains(response, "Copy invite link for Script Writer")
        self.assertContains(response, "Copying is unavailable because this invite has already been used.")

    @override_settings(FOCUS_ENABLE_DEV_SIGN_IN=True)
    def test_signed_out_user_can_preview_unused_invite(self):
        owner = FocusUser.objects.create(display_name="Owner")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        invitation = GroupInvitation.objects.create(group=group, role_to_assign=Membership.Role.WRITER)
        invite_path = reverse("invite_accept", kwargs={"token": invitation.token})
        encoded_next = f"%2Finvites%2F{invitation.token}%2F"

        response = self.client.get(invite_path)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Join Studio")
        self.assertContains(response, "This invite will add you to Studio as Script Writer.")
        self.assertContains(response, "Sign in first, then FOCUS will bring you back here to accept the invite.")
        self.assertContains(response, f"{reverse('passkey_sign_in')}?next={encoded_next}")
        self.assertContains(response, f"{reverse('backup_key_sign_in')}?next={encoded_next}")
        self.assertContains(response, f"{reverse('dev_sign_in')}?next={encoded_next}")
        self.assertNotContains(response, "Accept invite")
        self.assertFalse(invitation.is_used)

    @override_settings(FOCUS_ENABLE_DEV_SIGN_IN=False)
    def test_signed_out_invite_preview_hides_development_sign_in_when_disabled(self):
        owner = FocusUser.objects.create(display_name="Owner")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        invitation = GroupInvitation.objects.create(group=group, role_to_assign=Membership.Role.WRITER)

        response = self.client.get(reverse("invite_accept", kwargs={"token": invitation.token}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sign in with passkey")
        self.assertContains(response, "Use a backup key")
        self.assertNotContains(response, "Use development sign in")

    @override_settings(FOCUS_ENABLE_DEV_SIGN_IN=True)
    def test_signed_out_user_posting_invite_redirects_to_sign_in(self):
        owner = FocusUser.objects.create(display_name="Owner")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        invitation = GroupInvitation.objects.create(group=group, role_to_assign=Membership.Role.WRITER)
        invite_path = reverse("invite_accept", kwargs={"token": invitation.token})

        response = self.client.post(invite_path)

        self.assertRedirects(response, f"{reverse('dev_sign_in')}?next=%2Finvites%2F{invitation.token}%2F")
        self.assertFalse(GroupInvitation.objects.get(pk=invitation.pk).is_used)

    @override_settings(FOCUS_ENABLE_DEV_SIGN_IN=False)
    def test_signed_out_invite_post_uses_passkey_sign_in_when_development_is_disabled(self):
        owner = FocusUser.objects.create(display_name="Owner")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        invitation = GroupInvitation.objects.create(group=group, role_to_assign=Membership.Role.WRITER)
        invite_path = reverse("invite_accept", kwargs={"token": invitation.token})

        response = self.client.post(invite_path)

        self.assertRedirects(response, f"{reverse('passkey_sign_in')}?next=%2Finvites%2F{invitation.token}%2F")
        self.assertFalse(GroupInvitation.objects.get(pk=invitation.pk).is_used)

    def test_signed_in_user_can_accept_unused_invite(self):
        owner = FocusUser.objects.create(display_name="Owner")
        new_member = FocusUser.objects.create(display_name="New Member")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        invitation = GroupInvitation.objects.create(group=group, role_to_assign=Membership.Role.WRITER)
        self.client.force_login(new_member)

        response = self.client.post(reverse("invite_accept", kwargs={"token": invitation.token}))

        invitation.refresh_from_db()
        self.assertRedirects(response, reverse("group_detail", kwargs={"slug": group.slug}))
        self.assertTrue(invitation.is_used)
        self.assertTrue(
            Membership.objects.filter(user=new_member, group=group, role=Membership.Role.WRITER).exists()
        )

    def test_used_invite_cannot_be_reused(self):
        owner = FocusUser.objects.create(display_name="Owner")
        new_member = FocusUser.objects.create(display_name="New Member")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        invitation = GroupInvitation.objects.create(
            group=group,
            role_to_assign=Membership.Role.WRITER,
            is_used=True,
        )
        self.client.force_login(new_member)

        response = self.client.post(reverse("invite_accept", kwargs={"token": invitation.token}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This invite has already been used")
        self.assertFalse(Membership.objects.filter(user=new_member, group=group).exists())

    def test_revoked_invite_cannot_be_accepted(self):
        owner = FocusUser.objects.create(display_name="Owner")
        new_member = FocusUser.objects.create(display_name="New Member")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        invitation = GroupInvitation.objects.create(
            group=group,
            role_to_assign=Membership.Role.WRITER,
            revoked_at=timezone.now(),
        )
        self.client.force_login(new_member)

        response = self.client.post(reverse("invite_accept", kwargs={"token": invitation.token}))

        invitation.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This invite has been revoked")
        self.assertFalse(invitation.is_used)
        self.assertFalse(Membership.objects.filter(user=new_member, group=group).exists())

    def test_existing_member_does_not_consume_invite(self):
        member = FocusUser.objects.create(display_name="Member")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=member, group=group, role=Membership.Role.EDITOR)
        invitation = GroupInvitation.objects.create(group=group, role_to_assign=Membership.Role.WRITER)
        self.client.force_login(member)

        response = self.client.post(reverse("invite_accept", kwargs={"token": invitation.token}))

        invitation.refresh_from_db()
        self.assertRedirects(response, reverse("group_detail", kwargs={"slug": group.slug}))
        self.assertFalse(invitation.is_used)
        self.assertEqual(Membership.objects.get(user=member, group=group).role, Membership.Role.EDITOR)

    def test_invitation_form_errors_are_exposed_to_assistive_technology(self):
        owner = FocusUser.objects.create(display_name="Owner")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        self.client.force_login(owner)

        response = self.client.post(
            reverse("group_invitations", kwargs={"slug": group.slug}),
            {"role_to_assign": ""},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'role="alert"')
        self.assertContains(response, 'aria-invalid="true"')
        self.assertContains(response, 'id="role_to_assign-error"')


class MemberManagementFlowTests(TestCase):
    def test_group_member_can_view_roster(self):
        owner = FocusUser.objects.create(display_name="Owner")
        member = FocusUser.objects.create(
            display_name="Editor",
            availability=FocusUser.Availability.BUSY,
        )
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        member_membership = Membership.objects.create(user=member, group=group, role=Membership.Role.EDITOR)
        self.client.force_login(member)

        response = self.client.get(reverse("group_members", kwargs={"slug": group.slug}))

        self.assertContains(response, "Members of Studio")
        self.assertContains(response, "Owner")
        self.assertContains(response, "Editor")
        self.assertContains(response, "Availability")
        self.assertContains(response, "Busy")
        self.assertContains(response, reverse("member_profile", kwargs={"slug": group.slug, "pk": member_membership.pk}))
        self.assertNotContains(response, "Save role for Owner")

    def test_group_member_can_view_member_profile(self):
        viewer = FocusUser.objects.create(display_name="Viewer")
        member = FocusUser.objects.create(
            display_name="Editor",
            bio="Audio cleanup and captions.",
            availability=FocusUser.Availability.LIMITED,
        )
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=viewer, group=group, role=Membership.Role.OWNER)
        member_membership = Membership.objects.create(user=member, group=group, role=Membership.Role.EDITOR)
        private_project = VideoProject.objects.create(group=group, title="Private Assignment")
        private_project.assigned_editors.add(member)
        self.client.force_login(viewer)

        response = self.client.get(reverse("member_profile", kwargs={"slug": group.slug, "pk": member_membership.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Editor")
        self.assertContains(response, "Limited availability")
        self.assertContains(response, "Audio cleanup and captions.")
        self.assertContains(response, "Projects not shared")
        self.assertNotContains(response, "Private Assignment")

    def test_member_profile_rejects_non_member_viewer(self):
        viewer = FocusUser.objects.create(display_name="Viewer")
        member = FocusUser.objects.create(display_name="Editor")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        member_membership = Membership.objects.create(user=member, group=group, role=Membership.Role.EDITOR)
        self.client.force_login(viewer)

        response = self.client.get(reverse("member_profile", kwargs={"slug": group.slug, "pk": member_membership.pk}))

        self.assertEqual(response.status_code, 404)

    def test_member_profile_rejects_membership_from_another_group(self):
        viewer = FocusUser.objects.create(display_name="Viewer")
        member = FocusUser.objects.create(display_name="Editor")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        other_group = ProductionGroup.objects.create(name="Other Studio", slug="other-studio")
        Membership.objects.create(user=viewer, group=group, role=Membership.Role.OWNER)
        other_membership = Membership.objects.create(user=member, group=other_group, role=Membership.Role.EDITOR)
        self.client.force_login(viewer)

        response = self.client.get(reverse("member_profile", kwargs={"slug": group.slug, "pk": other_membership.pk}))

        self.assertEqual(response.status_code, 404)

    def test_member_profile_shows_opted_in_assignments_only_for_current_group(self):
        viewer = FocusUser.objects.create(display_name="Viewer")
        member = FocusUser.objects.create(display_name="Editor", show_assigned_projects=True)
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        other_group = ProductionGroup.objects.create(name="Other Studio", slug="other-studio")
        Membership.objects.create(user=viewer, group=group, role=Membership.Role.OWNER)
        Membership.objects.create(user=viewer, group=other_group, role=Membership.Role.OWNER)
        member_membership = Membership.objects.create(user=member, group=group, role=Membership.Role.EDITOR)
        Membership.objects.create(user=member, group=other_group, role=Membership.Role.EDITOR)
        visible_project = VideoProject.objects.create(group=group, title="Visible Assignment")
        hidden_project = VideoProject.objects.create(group=other_group, title="Other Group Assignment")
        visible_project.assigned_editors.add(member)
        hidden_project.assigned_editors.add(member)
        self.client.force_login(viewer)

        response = self.client.get(reverse("member_profile", kwargs={"slug": group.slug, "pk": member_membership.pk}))

        self.assertContains(response, "Projects assigned to Editor in Studio")
        self.assertContains(response, "Visible Assignment")
        self.assertContains(response, "Editor")
        self.assertNotContains(response, "Other Group Assignment")

    def test_member_profile_shows_latest_note_for_opted_in_assignment(self):
        viewer = FocusUser.objects.create(display_name="Viewer")
        member = FocusUser.objects.create(display_name="Editor", show_assigned_projects=True)
        producer = FocusUser.objects.create(display_name="Producer")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=viewer, group=group, role=Membership.Role.OWNER)
        member_membership = Membership.objects.create(user=member, group=group, role=Membership.Role.EDITOR)
        Membership.objects.create(user=producer, group=group, role=Membership.Role.ADMIN)
        project = VideoProject.objects.create(group=group, title="Visible Assignment")
        project.assigned_editors.add(member)
        older_note = ProjectNote.objects.create(project=project, author=member, body="Older profile note.")
        ProjectNote.objects.filter(pk=older_note.pk).update(created_at=timezone.now() - timedelta(days=1))
        ProjectNote.objects.create(project=project, author=producer, body="Latest profile handoff.")
        self.client.force_login(viewer)

        response = self.client.get(reverse("member_profile", kwargs={"slug": group.slug, "pk": member_membership.pk}))

        self.assertContains(response, "Latest note")
        self.assertContains(response, "Latest profile handoff.")
        self.assertContains(response, "By Producer")
        self.assertContains(response, reverse("project_detail", kwargs={"group_slug": group.slug, "pk": project.pk}) + "#project-notes")
        self.assertNotContains(response, "Older profile note.")

    def test_member_profile_shows_empty_latest_note_state_for_assignment(self):
        viewer = FocusUser.objects.create(display_name="Viewer")
        member = FocusUser.objects.create(display_name="Editor", show_assigned_projects=True)
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=viewer, group=group, role=Membership.Role.OWNER)
        member_membership = Membership.objects.create(user=member, group=group, role=Membership.Role.EDITOR)
        project = VideoProject.objects.create(group=group, title="Visible Assignment")
        project.assigned_editors.add(member)
        self.client.force_login(viewer)

        response = self.client.get(reverse("member_profile", kwargs={"slug": group.slug, "pk": member_membership.pk}))

        self.assertContains(response, "No notes yet.")

    def test_non_member_cannot_view_roster(self):
        user = FocusUser.objects.create(display_name="Outsider")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        self.client.force_login(user)

        response = self.client.get(reverse("group_members", kwargs={"slug": group.slug}))

        self.assertEqual(response.status_code, 404)

    def test_owner_can_update_member_role(self):
        owner = FocusUser.objects.create(display_name="Owner")
        member = FocusUser.objects.create(display_name="Writer")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        membership = Membership.objects.create(user=member, group=group, role=Membership.Role.WRITER)
        self.client.force_login(owner)

        response = self.client.post(
            reverse("membership_role_update", kwargs={"slug": group.slug, "pk": membership.pk}),
            {f"membership-{membership.pk}-role": Membership.Role.EDITOR.value},
        )

        membership.refresh_from_db()
        self.assertRedirects(response, reverse("group_members", kwargs={"slug": group.slug}))
        self.assertEqual(membership.role, Membership.Role.EDITOR)

    def test_non_owner_cannot_update_member_role(self):
        editor = FocusUser.objects.create(display_name="Editor")
        writer = FocusUser.objects.create(display_name="Writer")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=editor, group=group, role=Membership.Role.EDITOR)
        membership = Membership.objects.create(user=writer, group=group, role=Membership.Role.WRITER)
        self.client.force_login(editor)

        response = self.client.post(
            reverse("membership_role_update", kwargs={"slug": group.slug, "pk": membership.pk}),
            {f"membership-{membership.pk}-role": Membership.Role.TALENT.value},
        )

        membership.refresh_from_db()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(membership.role, Membership.Role.WRITER)

    def test_owner_can_remove_member(self):
        owner = FocusUser.objects.create(display_name="Owner")
        member = FocusUser.objects.create(display_name="Editor")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        membership = Membership.objects.create(user=member, group=group, role=Membership.Role.EDITOR)
        self.client.force_login(owner)

        response = self.client.post(reverse("membership_remove", kwargs={"slug": group.slug, "pk": membership.pk}))

        self.assertRedirects(response, reverse("group_members", kwargs={"slug": group.slug}))
        self.assertFalse(Membership.objects.filter(pk=membership.pk).exists())

    def test_removed_member_loses_group_access(self):
        owner = FocusUser.objects.create(display_name="Owner")
        member = FocusUser.objects.create(display_name="Editor")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        owner_membership = Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        membership = Membership.objects.create(user=member, group=group, role=Membership.Role.EDITOR)
        project = VideoProject.objects.create(group=group, title="Private Project")
        self.client.force_login(owner)

        response = self.client.post(reverse("membership_remove", kwargs={"slug": group.slug, "pk": membership.pk}))

        self.assertRedirects(response, reverse("group_members", kwargs={"slug": group.slug}))
        self.client.force_login(member)
        self.assertEqual(self.client.get(reverse("group_detail", kwargs={"slug": group.slug})).status_code, 404)
        self.assertEqual(self.client.get(reverse("group_members", kwargs={"slug": group.slug})).status_code, 404)
        self.assertEqual(
            self.client.get(reverse("member_profile", kwargs={"slug": group.slug, "pk": owner_membership.pk})).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse("project_detail", kwargs={"group_slug": group.slug, "pk": project.pk})).status_code,
            404,
        )

    def test_member_can_leave_group(self):
        owner = FocusUser.objects.create(display_name="Owner")
        member = FocusUser.objects.create(display_name="Editor")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        membership = Membership.objects.create(user=member, group=group, role=Membership.Role.EDITOR)
        self.client.force_login(member)

        response = self.client.post(reverse("membership_leave", kwargs={"slug": group.slug}))

        self.assertRedirects(response, reverse("dashboard"))
        self.assertFalse(Membership.objects.filter(pk=membership.pk).exists())

    def test_owner_can_leave_group_when_another_owner_remains(self):
        owner = FocusUser.objects.create(display_name="Owner")
        other_owner = FocusUser.objects.create(display_name="Other Owner")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        membership = Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        other_membership = Membership.objects.create(user=other_owner, group=group, role=Membership.Role.OWNER)
        self.client.force_login(owner)

        response = self.client.post(reverse("membership_leave", kwargs={"slug": group.slug}))

        self.assertRedirects(response, reverse("dashboard"))
        self.assertFalse(Membership.objects.filter(pk=membership.pk).exists())
        self.assertTrue(Membership.objects.filter(pk=other_membership.pk).exists())

    def test_member_loses_group_access_after_leaving(self):
        owner = FocusUser.objects.create(display_name="Owner")
        member = FocusUser.objects.create(display_name="Editor")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        owner_membership = Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        Membership.objects.create(user=member, group=group, role=Membership.Role.EDITOR)
        project = VideoProject.objects.create(group=group, title="Private Project")
        self.client.force_login(member)

        response = self.client.post(reverse("membership_leave", kwargs={"slug": group.slug}))

        self.assertRedirects(response, reverse("dashboard"))
        self.assertEqual(self.client.get(reverse("group_detail", kwargs={"slug": group.slug})).status_code, 404)
        self.assertEqual(self.client.get(reverse("group_members", kwargs={"slug": group.slug})).status_code, 404)
        self.assertEqual(self.client.get(reverse("group_invitations", kwargs={"slug": group.slug})).status_code, 404)
        self.assertEqual(
            self.client.get(reverse("member_profile", kwargs={"slug": group.slug, "pk": owner_membership.pk})).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse("project_detail", kwargs={"group_slug": group.slug, "pk": project.pk})).status_code,
            404,
        )

    def test_non_owner_cannot_remove_member(self):
        editor = FocusUser.objects.create(display_name="Editor")
        writer = FocusUser.objects.create(display_name="Writer")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=editor, group=group, role=Membership.Role.EDITOR)
        membership = Membership.objects.create(user=writer, group=group, role=Membership.Role.WRITER)
        self.client.force_login(editor)

        response = self.client.post(reverse("membership_remove", kwargs={"slug": group.slug, "pk": membership.pk}))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Membership.objects.filter(pk=membership.pk).exists())

    def test_last_owner_cannot_be_demoted_through_web_ui(self):
        owner = FocusUser.objects.create(display_name="Owner")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        membership = Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        self.client.force_login(owner)

        response = self.client.post(
            reverse("membership_role_update", kwargs={"slug": group.slug, "pk": membership.pk}),
            {f"membership-{membership.pk}-role": Membership.Role.ADMIN.value},
            follow=True,
        )

        membership.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A production group must keep at least one owner.")
        self.assertEqual(membership.role, Membership.Role.OWNER)

    def test_last_owner_cannot_be_removed_through_web_ui(self):
        owner = FocusUser.objects.create(display_name="Owner")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        membership = Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        self.client.force_login(owner)

        response = self.client.post(
            reverse("membership_remove", kwargs={"slug": group.slug, "pk": membership.pk}),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A production group must keep at least one owner.")
        self.assertTrue(Membership.objects.filter(pk=membership.pk).exists())

    def test_last_owner_cannot_leave_group(self):
        owner = FocusUser.objects.create(display_name="Owner")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        membership = Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        self.client.force_login(owner)

        response = self.client.post(
            reverse("membership_leave", kwargs={"slug": group.slug}),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A production group must keep at least one owner.")
        self.assertTrue(Membership.objects.filter(pk=membership.pk).exists())

    def test_only_owner_remove_action_is_not_shown(self):
        owner = FocusUser.objects.create(display_name="Owner")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        membership = Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        self.client.force_login(owner)

        response = self.client.get(reverse("group_members", kwargs={"slug": group.slug}))

        self.assertContains(response, "This member is the only group owner, so they cannot be removed.")
        self.assertNotContains(response, reverse("membership_remove", kwargs={"slug": group.slug, "pk": membership.pk}))

    def test_group_members_page_shows_leave_action_when_allowed(self):
        owner = FocusUser.objects.create(display_name="Owner")
        member = FocusUser.objects.create(display_name="Editor")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        Membership.objects.create(user=member, group=group, role=Membership.Role.EDITOR)
        self.client.force_login(member)

        response = self.client.get(reverse("group_members", kwargs={"slug": group.slug}))

        self.assertContains(response, f"Leave {group.name}")
        self.assertContains(response, reverse("membership_leave", kwargs={"slug": group.slug}))

    def test_group_members_page_hides_leave_action_for_only_owner(self):
        owner = FocusUser.objects.create(display_name="Owner")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        self.client.force_login(owner)

        response = self.client.get(reverse("group_members", kwargs={"slug": group.slug}))

        self.assertContains(response, "You are the only group owner, so you cannot leave this group.")
        self.assertNotContains(response, reverse("membership_leave", kwargs={"slug": group.slug}))

    def test_owner_forms_have_unique_accessible_descriptions(self):
        owner = FocusUser.objects.create(display_name="Owner")
        member = FocusUser.objects.create(display_name="Editor")
        group = ProductionGroup.objects.create(name="Studio", slug="studio")
        owner_membership = Membership.objects.create(user=owner, group=group, role=Membership.Role.OWNER)
        member_membership = Membership.objects.create(user=member, group=group, role=Membership.Role.EDITOR)
        self.client.force_login(owner)

        response = self.client.get(reverse("group_members", kwargs={"slug": group.slug}))

        self.assertContains(response, f'id="membership-{owner_membership.pk}-role-help"')
        self.assertContains(response, f'id="membership-{member_membership.pk}-role-help"')
        self.assertContains(response, f'aria-describedby="membership-{owner_membership.pk}-role-help"')
        self.assertContains(response, f'aria-describedby="membership-{member_membership.pk}-role-help"')
