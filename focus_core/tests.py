from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import AuthIdentity, FocusUser, GroupInvitation, Membership, ProductionGroup, RecoveryCode, VideoProject, WebAuthnCredential


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

    @override_settings(FOCUS_ENABLE_DEV_SIGN_IN=True)
    def test_development_sign_in_creates_pseudonymous_session(self):
        response = self.client.post(reverse("dev_sign_in"), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Production groups")
        self.assertTrue(AuthIdentity.objects.filter(provider="GITHUB", subject_id="focus-dev-user").exists())

    @override_settings(FOCUS_ENABLE_DEV_SIGN_IN=True)
    def test_development_sign_in_links_to_backup_key_sign_in(self):
        response = self.client.get(reverse("dev_sign_in"))

        self.assertContains(response, "Use a saved backup key instead")
        self.assertContains(response, reverse("backup_key_sign_in"))

    def test_backup_key_sign_in_uses_unused_code_and_marks_it_used(self):
        user = FocusUser.objects.create(display_name="Creator")
        recovery_code = RecoveryCode.create_for_code(user, "2345-6789-ABCD-EFGH")

        response = self.client.post(reverse("backup_key_sign_in"), {"backup_key": "2345 6789 abcd efgh"})

        recovery_code.refresh_from_db()
        self.assertRedirects(response, reverse("dashboard"))
        self.assertIsNotNone(recovery_code.used_at)

        dashboard_response = self.client.get(reverse("dashboard"))
        self.assertContains(dashboard_response, "Signed in as Creator")

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
        self.assertContains(response, reverse("passkey_update", kwargs={"pk": passkey.pk}))
        self.assertContains(response, "Connected sign-in accounts")
        self.assertContains(response, "Saved passkeys")
        self.assertContains(response, 'scope="col"')
        self.assertContains(response, 'scope="row"')

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
