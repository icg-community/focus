from django.core.exceptions import ValidationError
from django.test import TestCase

from .models import AuthIdentity, FocusUser, Membership, ProductionGroup, RecoveryCode


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
