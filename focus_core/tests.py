from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import AuthIdentity, FocusUser, GroupInvitation, Membership, ProductionGroup, RecoveryCode


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

    def test_group_detail_rejects_non_member(self):
        user = FocusUser.objects.create(display_name="Member")
        group = ProductionGroup.objects.create(name="Other Studio", slug="other-studio")
        self.client.force_login(user)

        response = self.client.get(reverse("group_detail", kwargs={"slug": group.slug}))

        self.assertEqual(response.status_code, 404)

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
