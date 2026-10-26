from __future__ import annotations

import json
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from users.application.services import UserService
from users.bootstrap import provision_default_users, provision_root_user
from users.emailing import send_email
from users.middleware import RootProvisioningMiddleware
from users.models import User, UserInvitation

PASSWORD = "A-strong-demo-password-123!"


def create_user(email: str, role: str = User.Role.REQUESTER, **kwargs):
    return get_user_model().objects.create_user(
        email=email,
        password=PASSWORD,
        first_name=kwargs.pop("first_name", "Test"),
        last_name=kwargs.pop("last_name", "User"),
        role=role,
        **kwargs,
    )


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class AuthenticationTests(TestCase):
    def setUp(self):
        self.user = create_user("Person@Example.TEST")

    def test_email_identity_is_normalized_and_password_is_hashed(self):
        self.assertEqual(self.user.email, "person@example.test")
        self.assertNotEqual(self.user.password, PASSWORD)
        self.assertTrue(self.user.check_password(PASSWORD))

    def test_login_logout_and_safe_next(self):
        response = self.client.post(
            reverse("login") + "?next=/settings/profile/",
            {
                "email": self.user.email,
                "password": PASSWORD,
                "next": "/settings/profile/",
            },
        )
        self.assertRedirects(response, reverse("profile"))
        response = self.client.post(reverse("logout"))
        self.assertRedirects(response, reverse("login"))

        response = self.client.post(
            reverse("login"),
            {
                "email": self.user.email,
                "password": PASSWORD,
                "next": "https://attacker.example/",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))

    def test_invalid_login_has_generic_error(self):
        response = self.client.post(
            reverse("login"),
            {"email": self.user.email, "password": "wrong"},
        )
        self.assertContains(response, "Invalid email or password", status_code=200)

    def test_deactivated_user_cannot_login(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        response = self.client.post(
            reverse("login"),
            {"email": self.user.email, "password": PASSWORD},
        )
        self.assertContains(response, "Invalid email or password")

    def test_password_reset_is_non_enumerating_and_single_use(self):
        unknown = self.client.post(
            reverse("password-reset-request"),
            {"email": "unknown@example.test"},
        )
        self.assertRedirects(unknown, reverse("login"))
        self.assertEqual(len(mail.outbox), 0)

        response = self.client.post(
            reverse("password-reset-request"),
            {"email": self.user.email},
        )
        self.assertRedirects(response, reverse("login"))
        self.assertEqual(len(mail.outbox), 1)
        path = re.search(r"http://127\.0\.0\.1:8000(\S+)", mail.outbox[0].body).group(1)
        page = self.client.get(path)
        self.assertEqual(page.status_code, 200)
        changed = self.client.post(
            path,
            {
                "password": "Another-strong-password-987!",
                "password_confirm": "Another-strong-password-987!",
            },
        )
        self.assertRedirects(changed, reverse("login"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Another-strong-password-987!"))
        self.assertEqual(self.client.get(path).status_code, 400)


class EmailDeliveryTests(TestCase):
    @override_settings(
        RESEND_API_KEY="re_test_key",
        RESEND_FROM_EMAIL="Support Portal <support@example.test>",
    )
    @patch("users.emailing.urlopen")
    def test_resend_uses_fixed_https_endpoint_and_one_recipient_per_request(
        self,
        mocked_urlopen,
    ):
        response = MagicMock(status=200)
        mocked_urlopen.return_value.__enter__.return_value = response
        send_email(
            "Private update",
            "Message body",
            ["first@example.test", "second@example.test", "first@example.test"],
        )
        self.assertEqual(mocked_urlopen.call_count, 2)
        payloads = []
        for call in mocked_urlopen.call_args_list:
            request = call.args[0]
            self.assertEqual(request.full_url, "https://api.resend.com/emails")
            self.assertEqual(call.kwargs["timeout"], 15)
            self.assertEqual(
                request.get_header("Authorization"),
                "Bearer re_test_key",
            )
            payloads.append(json.loads(request.data))
        self.assertEqual(
            {tuple(payload["to"]) for payload in payloads},
            {("first@example.test",), ("second@example.test",)},
        )
        self.assertTrue(
            all(
                payload["from"] == "Support Portal <support@example.test>"
                and payload["subject"] == "Private update"
                and payload["text"] == "Message body"
                for payload in payloads
            )
        )

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        RESEND_API_KEY="",
    )
    def test_django_backend_also_keeps_recipients_private(self):
        send_email(
            "Private update",
            "Message body",
            ["first@example.test", "second@example.test"],
        )
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(
            {tuple(message.to) for message in mail.outbox},
            {("first@example.test",), ("second@example.test",)},
        )


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class InvitationTests(TestCase):
    def setUp(self):
        self.admin = create_user("admin@example.test", User.Role.ADMINISTRATOR)
        self.client.force_login(self.admin)

    def test_administrator_invites_and_new_user_registers_atomically(self):
        response = self.client.post(
            reverse("administration-users"),
            {"email": "new@example.test", "role": User.Role.SUPPORT_AGENT},
        )
        self.assertRedirects(response, reverse("administration-users"))
        invitation = UserInvitation.objects.get(email="new@example.test")
        self.assertTrue(invitation.is_pending)
        self.assertEqual(len(mail.outbox), 1)
        path = re.search(r"http://127\.0\.0\.1:8000(\S+)", mail.outbox[0].body).group(1)

        self.client.logout()
        signup = self.client.post(
            path,
            {
                "email": invitation.email,
                "first_name": "New",
                "last_name": "Agent",
                "password": PASSWORD,
                "password_confirm": PASSWORD,
            },
        )
        self.assertEqual(signup.status_code, 302)
        self.assertEqual(signup.url, reverse("home"))
        user = User.objects.get(email="new@example.test")
        self.assertEqual(user.role, User.Role.SUPPORT_AGENT)
        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.accepted_at)
        self.assertEqual(self.client.get(path).status_code, 400)

    def test_resend_rotates_token_and_revoke_invalidates_invitation(self):
        old_token = UserService().invite(
            self.admin,
            email="invite@example.test",
            role=User.Role.REQUESTER,
        )
        invitation = UserInvitation.objects.get(email="invite@example.test")
        self.client.post(
            reverse("administration-invitation-action", args=[invitation.id, "resend"])
        )
        invitation.refresh_from_db()
        self.assertIsNone(UserService().get_invitation(old_token))
        new_path = re.search(
            r"http://127\.0\.0\.1:8000(\S+)", mail.outbox[-1].body
        ).group(1)
        self.assertEqual(self.client.get(new_path).status_code, 200)
        self.client.force_login(self.admin)
        self.client.post(
            reverse("administration-invitation-action", args=[invitation.id, "revoke"])
        )
        self.assertEqual(self.client.get(new_path).status_code, 400)

    def test_expired_invitation_is_rejected(self):
        token = UserService().invite(
            self.admin,
            email="expired@example.test",
            role=User.Role.REQUESTER,
        )
        invitation = UserInvitation.objects.get(email="expired@example.test")
        invitation.expires_at = timezone.now() - timedelta(seconds=1)
        invitation.save(update_fields=["expires_at"])
        self.client.logout()
        self.assertEqual(
            self.client.get(reverse("invitation-accept", args=[token])).status_code, 400
        )

    def test_existing_account_confirms_invitation_with_post(self):
        existing = create_user("existing@example.test")
        token = UserService().invite(
            self.admin,
            email=existing.email,
            role=User.Role.SUPPORT_AGENT,
        )
        path = reverse("invitation-accept", args=[token])
        self.client.logout()
        response = self.client.get(path)
        self.assertRedirects(response, f"{reverse('login')}?next={path}")
        self.client.post(
            reverse("login"),
            {"email": existing.email, "password": PASSWORD, "next": path},
        )
        response = self.client.get(path)
        self.assertContains(response, "Accept role invitation")
        existing.refresh_from_db()
        self.assertEqual(existing.role, User.Role.REQUESTER)
        response = self.client.post(path)
        self.assertEqual(response.status_code, 302)
        existing.refresh_from_db()
        self.assertEqual(existing.role, User.Role.SUPPORT_AGENT)
        self.assertIsNotNone(
            UserInvitation.objects.get(email=existing.email).accepted_at
        )

    def test_removing_support_access_unassigns_tickets_with_event(self):
        from tickets.application.services import TicketService
        from tickets.models import Category, TicketEvent

        requester = create_user("ticket-owner@example.test")
        agent = create_user("assigned@example.test", User.Role.SUPPORT_AGENT)
        category, _ = Category.objects.get_or_create(name="General")
        ticket = TicketService().create_ticket(
            requester,
            cleaned_data={
                "subject": "Assigned ticket",
                "description": "<p>Help</p>",
                "category": category,
                "priority": "normal",
            },
            attachments=[],
            temporary_upload_ids=[],
        )
        ticket.assignee = agent
        ticket.save(update_fields=["assignee", "updated_at"])
        UserService().update_user_access(
            self.admin,
            target_id=agent.id,
            role=User.Role.REQUESTER,
            is_active=True,
        )
        ticket.refresh_from_db()
        self.assertIsNone(ticket.assignee)
        event = ticket.events.filter(event_type=TicketEvent.Type.ASSIGNED).latest("id")
        self.assertEqual(event.data["old"], agent.id)
        self.assertIsNone(event.data["new"])

    def test_non_administrator_cannot_access_user_administration(self):
        requester = create_user("requester@example.test")
        self.client.force_login(requester)
        self.assertEqual(
            self.client.get(reverse("administration-users")).status_code, 404
        )

    def test_last_administrator_and_environment_root_are_protected(self):
        response = self.client.post(
            reverse("administration-user-update", args=[self.admin.id]),
            {"role": User.Role.REQUESTER},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("administration-users"))
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, User.Role.ADMINISTRATOR)

        second = create_user(
            "second@example.test", User.Role.ADMINISTRATOR, is_env_root=True
        )
        response = self.client.post(
            reverse("administration-user-update", args=[second.id]),
            {"role": User.Role.SUPPORT_AGENT},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("administration-users"))
        second.refresh_from_db()
        self.assertEqual(second.role, User.Role.ADMINISTRATOR)
        self.assertTrue(second.is_active)


class ProfileAndRootTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=self.media.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(self.media.cleanup)
        self.user = create_user("profile@example.test")
        self.client.force_login(self.user)

    def test_profile_updates_email_language_and_password_without_logout(self):
        response = self.client.post(
            reverse("profile"),
            {
                "first_name": "Changed",
                "last_name": "Name",
                "email": "changed@example.test",
                "language": "de",
                "new_password": "Another-strong-password-987!",
            },
        )
        self.assertRedirects(response, reverse("profile"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "changed@example.test")
        self.assertEqual(self.user.language, "de")
        self.assertTrue(self.user.check_password("Another-strong-password-987!"))
        profile = self.client.get(reverse("profile"))
        self.assertEqual(profile.status_code, 200)
        ticket_list = self.client.get(reverse("ticket-list"))
        self.assertContains(ticket_list, "Meine Tickets")
        self.assertContains(ticket_list, "Neues Ticket")

    def test_avatar_upload_validation_and_removal(self):
        image = SimpleUploadedFile(
            "avatar.png",
            b"\x89PNG\r\n\x1a\nvalid demo image",
            content_type="image/png",
        )
        response = self.client.post(
            reverse("profile"),
            {
                "first_name": self.user.first_name,
                "last_name": self.user.last_name,
                "email": self.user.email,
                "language": "en",
                "avatar": image,
            },
        )
        self.assertRedirects(response, reverse("profile"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.avatar.name.endswith(".png"))
        self.assertEqual(
            self.client.get(reverse("avatar-file", args=[self.user.id])).status_code,
            200,
        )

        response = self.client.post(
            reverse("profile"),
            {
                "first_name": self.user.first_name,
                "last_name": self.user.last_name,
                "email": self.user.email,
                "language": "en",
                "remove_avatar": "on",
            },
        )
        self.assertRedirects(response, reverse("profile"))
        self.user.refresh_from_db()
        self.assertFalse(self.user.avatar)

        invalid = SimpleUploadedFile("avatar.gif", b"GIF89a", content_type="image/gif")
        response = self.client.post(
            reverse("profile"),
            {
                "first_name": self.user.first_name,
                "last_name": self.user.last_name,
                "email": self.user.email,
                "language": "en",
                "avatar": invalid,
            },
        )
        self.assertContains(response, "PNG or JPEG", status_code=200)

    @override_settings(
        ROOT_USER_EMAIL="root@example.test",
        ROOT_USER_PASSWORD="Environment-root-password-123!",
    )
    def test_root_is_provisioned_and_repeated_sync_preserves_session_hash(self):
        provision_root_user()
        root = User.objects.get(email="root@example.test")
        original_password_hash = root.password
        provision_root_user()
        root.refresh_from_db()
        self.assertEqual(root.password, original_password_hash)
        self.assertTrue(root.is_staff)
        self.assertTrue(root.is_superuser)
        self.assertTrue(root.is_env_root)
        self.assertEqual(root.role, User.Role.ADMINISTRATOR)

    @override_settings(
        ROOT_USER_EMAIL="root@example.test",
        ROOT_USER_PASSWORD="Environment-root-password-123!",
        SUPPORT_USER_EMAIL="support-demo@example.test",
        SUPPORT_USER_PASSWORD="Support-demo-password-123!",
        REQUESTER_USER_EMAIL="requester-demo@example.test",
        REQUESTER_USER_PASSWORD="Requester-demo-password-123!",
    )
    def test_all_three_default_accounts_are_provisioned_from_environment(self):
        provision_default_users()
        root = User.objects.get(email="root@example.test")
        support = User.objects.get(email="support-demo@example.test")
        requester = User.objects.get(email="requester-demo@example.test")

        self.assertEqual(root.role, User.Role.ADMINISTRATOR)
        self.assertTrue(root.is_superuser)
        self.assertEqual(support.role, User.Role.SUPPORT_AGENT)
        self.assertFalse(support.is_staff)
        self.assertTrue(support.check_password("Support-demo-password-123!"))
        self.assertEqual(requester.role, User.Role.REQUESTER)
        self.assertFalse(requester.is_staff)
        self.assertTrue(requester.check_password("Requester-demo-password-123!"))

        support_password_hash = support.password
        requester_password_hash = requester.password
        provision_default_users()
        support.refresh_from_db()
        requester.refresh_from_db()
        self.assertEqual(support.password, support_password_hash)
        self.assertEqual(requester.password, requester_password_hash)

    @override_settings(
        SUPPORT_USER_EMAIL="support-demo@example.test",
        SUPPORT_USER_PASSWORD="",
        REQUESTER_USER_EMAIL="",
        REQUESTER_USER_PASSWORD="Requester-demo-password-123!",
    )
    def test_incomplete_demo_account_pairs_are_skipped(self):
        provision_default_users()
        self.assertFalse(
            User.objects.filter(email="support-demo@example.test").exists()
        )
        self.assertFalse(
            User.objects.filter(email="requester-demo@example.test").exists()
        )

    @override_settings(ROOT_USER_EMAIL="", ROOT_USER_PASSWORD="")
    def test_missing_root_configuration_does_not_create_user(self):
        RootProvisioningMiddleware.provisioned = False
        provision_root_user()
        self.assertFalse(User.objects.filter(is_env_root=True).exists())


class AdministratorConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.first = create_user("first-admin@example.test", User.Role.ADMINISTRATOR)
        self.second = create_user("second-admin@example.test", User.Role.ADMINISTRATOR)
        self.barrier = Barrier(2)

    def demote(self, user_id: int) -> str:
        close_old_connections()
        try:
            actor = User.objects.get(pk=user_id)
            self.barrier.wait(timeout=10)
            try:
                UserService().update_user_access(
                    actor,
                    target_id=user_id,
                    role=User.Role.REQUESTER,
                    is_active=True,
                )
            except Exception as exc:
                return type(exc).__name__
            return "updated"
        finally:
            close_old_connections()

    def test_concurrent_demotion_keeps_one_active_administrator(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(self.demote, [self.first.id, self.second.id]))
        self.assertIn("updated", results)
        self.assertEqual(
            User.objects.filter(
                role=User.Role.ADMINISTRATOR,
                is_active=True,
            ).count(),
            1,
        )
