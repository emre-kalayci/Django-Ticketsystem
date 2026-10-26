from __future__ import annotations

import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import OperationalError, close_old_connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from tickets.application.services import TicketService
from tickets.models import (
    Category,
    TemporaryUpload,
    Ticket,
    TicketAttachment,
    TicketEvent,
    TicketMessage,
)
from tickets.richtext import sanitize_rich_text
from users.models import User

PASSWORD = "A-strong-demo-password-123!"


def create_user(email: str, role: str = User.Role.REQUESTER):
    return User.objects.create_user(
        email=email,
        password=PASSWORD,
        first_name=email.split("@")[0].title(),
        last_name="User",
        role=role,
    )


def text_file(name="notes.txt", value=b"hello"):
    return SimpleUploadedFile(name, value, content_type="text/plain")


def png_file(name="image.png"):
    return SimpleUploadedFile(
        name,
        b"\x89PNG\r\n\x1a\n" + b"demo image bytes",
        content_type="image/png",
    )


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class TicketTestCase(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.media_override = override_settings(MEDIA_ROOT=self.media.name)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)
        self.addCleanup(self.media.cleanup)
        self.category, _ = Category.objects.get_or_create(name="General")
        self.requester = create_user("requester@example.test")
        self.other_requester = create_user("other@example.test")
        self.agent = create_user("agent@example.test", User.Role.SUPPORT_AGENT)
        self.other_agent = create_user("agent2@example.test", User.Role.SUPPORT_AGENT)
        self.admin = create_user("admin@example.test", User.Role.ADMINISTRATOR)
        self.service = TicketService()

    def create_ticket(self, requester=None, **overrides):
        data = {
            "subject": "Printer does not work",
            "description": "<p>The office printer is unavailable.</p>",
            "category": self.category,
            "priority": Ticket.Priority.NORMAL,
        }
        data.update(overrides)
        return self.service.create_ticket(
            requester or self.requester,
            cleaned_data=data,
            attachments=[],
            temporary_upload_ids=[],
        )


class TicketModelAndWorkflowTests(TicketTestCase):
    def test_global_numbering_defaults_and_non_reuse(self):
        first = self.create_ticket()
        second = self.create_ticket(requester=self.other_requester)
        self.assertEqual(first.display_id, "TKT-000001")
        self.assertEqual(second.display_id, "TKT-000002")
        self.assertEqual(first.status, Ticket.Status.NEW)
        self.assertEqual(first.priority, Ticket.Priority.NORMAL)
        self.assertIsNone(first.assignee)
        first.delete()
        third = self.create_ticket()
        self.assertEqual(third.display_id, "TKT-000003")

    def test_creation_records_public_event(self):
        ticket = self.create_ticket()
        event = ticket.events.get(event_type=TicketEvent.Type.CREATED)
        self.assertEqual(event.actor, self.requester)
        self.assertEqual(event.visibility, TicketEvent.Visibility.PUBLIC)

    def test_support_update_records_assignment_priority_category_and_status(self):
        ticket = self.create_ticket()
        new_category = Category.objects.create(name="Hardware")
        self.service.update_ticket(
            self.agent,
            ticket,
            cleaned_data={
                "subject": ticket.subject,
                "description": ticket.description,
                "assignee": self.other_agent,
                "category": new_category,
                "priority": Ticket.Priority.HIGH,
                "status": Ticket.Status.IN_PROGRESS,
                "resolution_summary": "",
            },
            attachments=[],
            temporary_upload_ids=[],
        )
        ticket.refresh_from_db()
        self.assertEqual(ticket.assignee, self.other_agent)
        self.assertEqual(ticket.category, new_category)
        self.assertEqual(ticket.priority, Ticket.Priority.HIGH)
        self.assertEqual(ticket.status, Ticket.Status.IN_PROGRESS)
        event_types = set(ticket.events.values_list("event_type", flat=True))
        self.assertTrue(
            {
                TicketEvent.Type.ASSIGNED,
                TicketEvent.Type.CATEGORY,
                TicketEvent.Type.PRIORITY,
                TicketEvent.Type.STATUS,
            }.issubset(event_types)
        )

    def test_resolution_requires_summary_and_sets_timestamp(self):
        ticket = self.create_ticket()
        with self.assertRaises(ValidationError):
            self.service.update_ticket(
                self.agent,
                ticket,
                cleaned_data={
                    "status": Ticket.Status.RESOLVED,
                    "resolution_summary": "",
                },
                attachments=[],
                temporary_upload_ids=[],
            )
        self.service.update_ticket(
            self.agent,
            ticket,
            cleaned_data={
                "status": Ticket.Status.RESOLVED,
                "resolution_summary": "Replaced the damaged cable.",
            },
            attachments=[],
            temporary_upload_ids=[],
        )
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.resolved_at)
        self.assertTrue(
            ticket.events.filter(event_type=TicketEvent.Type.RESOLVED).exists()
        )

    def test_public_support_reply_can_supply_resolution_without_summary(self):
        ticket = self.create_ticket()
        self.service.add_message(
            self.agent,
            ticket,
            body="<p>Replacing the cable restored service.</p>",
            visibility=TicketMessage.Visibility.PUBLIC,
            attachments=[],
            temporary_upload_ids=[],
        )
        self.service.update_ticket(
            self.agent,
            ticket,
            cleaned_data={
                "status": Ticket.Status.RESOLVED,
                "resolution_summary": "",
            },
            attachments=[],
            temporary_upload_ids=[],
        )
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, Ticket.Status.RESOLVED)
        self.assertIsNotNone(ticket.resolved_at)

    def test_requester_reply_reopens_waiting_and_resolved_tickets(self):
        for status in [Ticket.Status.WAITING_FOR_REQUESTER, Ticket.Status.RESOLVED]:
            ticket = self.create_ticket()
            ticket.status = status
            ticket.resolution_summary = (
                "Done" if status == Ticket.Status.RESOLVED else ""
            )
            ticket.resolved_at = (
                timezone.now() if status == Ticket.Status.RESOLVED else None
            )
            ticket.save()
            self.service.add_message(
                self.requester,
                ticket,
                body="<p>I have more information.</p>",
                visibility=TicketMessage.Visibility.INTERNAL,
                attachments=[],
                temporary_upload_ids=[],
            )
            ticket.refresh_from_db()
            self.assertEqual(ticket.status, Ticket.Status.IN_PROGRESS)
            message = ticket.messages.latest("id")
            self.assertEqual(message.visibility, TicketMessage.Visibility.PUBLIC)
            self.assertTrue(
                ticket.events.filter(event_type=TicketEvent.Type.REOPENED).exists()
            )


class TicketAuthorizationTests(TicketTestCase):
    def setUp(self):
        super().setUp()
        self.ticket = self.create_ticket()

    def test_requester_lists_and_opens_only_own_tickets(self):
        other_ticket = self.create_ticket(
            requester=self.other_requester, subject="Secret issue"
        )
        self.client.force_login(self.requester)
        response = self.client.get(reverse("ticket-list"))
        self.assertContains(response, self.ticket.subject)
        self.assertNotContains(response, other_ticket.subject)
        self.assertEqual(
            self.client.get(
                reverse("ticket-detail", args=[other_ticket.number])
            ).status_code,
            404,
        )
        search = self.client.get(reverse("ticket-list"), {"q": "Secret issue"})
        self.assertNotContains(
            search, reverse("ticket-detail", args=[other_ticket.number])
        )

    def test_support_and_administrator_see_all_tickets(self):
        other_ticket = self.create_ticket(
            requester=self.other_requester, subject="Other issue"
        )
        for user in [self.agent, self.admin]:
            self.client.force_login(user)
            response = self.client.get(reverse("ticket-list"))
            self.assertContains(response, self.ticket.subject)
            self.assertContains(response, other_ticket.subject)
            self.assertEqual(
                self.client.get(
                    reverse("ticket-detail", args=[other_ticket.number])
                ).status_code,
                200,
            )

    def test_requester_cannot_update_delete_or_use_administration(self):
        self.client.force_login(self.requester)
        self.assertEqual(
            self.client.post(
                reverse("ticket-update", args=[self.ticket.number]), {}
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                reverse("ticket-delete", args=[self.ticket.number])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse("administration-categories")).status_code,
            404,
        )

    def test_administrator_ticket_delete_requires_server_confirmation(self):
        self.client.force_login(self.admin)
        delete_url = reverse("ticket-delete", args=[self.ticket.number])
        self.assertEqual(self.client.post(delete_url).status_code, 400)
        self.assertTrue(Ticket.objects.filter(pk=self.ticket.pk).exists())
        response = self.client.post(
            delete_url,
            {"confirmation": self.ticket.display_id},
        )
        self.assertRedirects(response, reverse("ticket-list"))
        self.assertFalse(Ticket.objects.filter(pk=self.ticket.pk).exists())

    def test_internal_note_and_attachment_never_leak_to_requester(self):
        attachment = text_file("private.txt", b"private details")
        message = self.service.add_message(
            self.agent,
            self.ticket,
            body="<p>PRIVATE AGENT THOUGHT</p>",
            visibility=TicketMessage.Visibility.INTERNAL,
            attachments=[attachment],
            temporary_upload_ids=[],
        )
        private_attachment = message.attachments.get()
        self.client.force_login(self.requester)
        response = self.client.get(reverse("ticket-detail", args=[self.ticket.number]))
        self.assertNotContains(response, "PRIVATE AGENT THOUGHT")
        self.assertNotContains(response, "private.txt")
        self.assertEqual(
            self.client.get(
                reverse("attachment-file", args=[private_attachment.id])
            ).status_code,
            404,
        )
        self.client.force_login(self.agent)
        self.assertContains(
            self.client.get(reverse("ticket-detail", args=[self.ticket.number])),
            "PRIVATE AGENT THOUGHT",
        )
        self.assertEqual(
            self.client.get(
                reverse("attachment-file", args=[private_attachment.id])
            ).status_code,
            200,
        )

    def test_anonymous_access_redirects_to_login(self):
        response = self.client.get(reverse("ticket-detail", args=[self.ticket.number]))
        self.assertRedirects(
            response,
            f"{reverse('login')}?next="
            f"{reverse('ticket-detail', args=[self.ticket.number])}",
        )

    @override_settings(DEBUG=False)
    def test_unknown_authenticated_page_uses_clear_not_found_state(self):
        self.client.force_login(self.requester)
        response = self.client.get("/this-page-does-not-exist/")
        self.assertContains(response, "Page not found", status_code=404)
        self.assertContains(response, "Return to tickets", status_code=404)

    def test_ticket_mutations_require_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.agent)
        response = csrf_client.post(
            reverse("ticket-message-create", args=[self.ticket.number]),
            {"body": "<p>Forged reply</p>", "visibility": "public"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.ticket.messages.exists())


class TicketViewAndQueueTests(TicketTestCase):
    def test_requester_creates_ticket_and_cannot_choose_requester(self):
        self.client.force_login(self.requester)
        response = self.client.post(
            reverse("ticket-create"),
            {
                "subject": "Cannot connect to Wi-Fi",
                "description": "<p>Connection fails in room 12.</p>",
                "requester": self.other_requester.id,
                "category": self.category.id,
                "priority": Ticket.Priority.HIGH,
                "temporary_upload_ids": "[]",
            },
        )
        ticket = Ticket.objects.get(subject="Cannot connect to Wi-Fi")
        self.assertRedirects(response, reverse("ticket-detail", args=[ticket.number]))
        self.assertEqual(ticket.requester, self.requester)
        self.assertEqual(ticket.display_id, "TKT-000001")
        self.assertIn("/TKT-000001/", response.url)

    def test_support_creates_ticket_on_behalf_of_requester(self):
        self.client.force_login(self.agent)
        response = self.client.post(
            reverse("ticket-create"),
            {
                "subject": "Account locked",
                "description": "<p>User called the desk.</p>",
                "requester": self.other_requester.id,
                "category": self.category.id,
                "priority": Ticket.Priority.NORMAL,
                "temporary_upload_ids": "[]",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Ticket.objects.get().requester, self.other_requester)

    def test_agent_queue_tabs_filters_search_and_sort(self):
        mine = self.create_ticket(subject="Mine", priority=Ticket.Priority.LOW)
        mine.assignee = self.agent
        mine.save()
        waiting = self.create_ticket(requester=self.other_requester, subject="Waiting")
        waiting.status = Ticket.Status.WAITING_FOR_REQUESTER
        waiting.save()
        unassigned = self.create_ticket(
            subject="Urgent network", priority=Ticket.Priority.URGENT
        )
        self.client.force_login(self.agent)

        response = self.client.get(reverse("ticket-list"), {"queue": "mine"})
        self.assertContains(response, "Mine")
        self.assertNotContains(
            response,
            reverse("ticket-detail", args=[waiting.number]),
        )
        response = self.client.get(reverse("ticket-list"), {"queue": "unassigned"})
        self.assertContains(response, "Waiting")
        self.assertContains(response, "Urgent network")
        self.assertNotContains(response, "Mine")
        response = self.client.get(
            reverse("ticket-list"), {"q": waiting.requester.email}
        )
        self.assertContains(response, "Waiting")
        response = self.client.get(
            reverse("ticket-list"),
            {"priority": Ticket.Priority.URGENT, "sort": "priority"},
        )
        self.assertContains(response, unassigned.subject)
        self.assertNotContains(response, mine.subject)

    def test_filter_controls_auto_submit_without_apply_button(self):
        self.client.force_login(self.agent)
        response = self.client.get(reverse("ticket-list"))
        self.assertContains(response, "data-auto-submit")
        self.assertNotContains(response, ">Apply</button>")

    def test_agent_list_shows_requester_assignee_and_preserves_pagination_filters(self):
        for index in range(26):
            ticket = self.create_ticket(subject=f"Paged ticket {index}")
            ticket.assignee = self.agent
            ticket.save(update_fields=["assignee", "updated_at"])
        self.client.force_login(self.agent)
        response = self.client.get(
            reverse("ticket-list"),
            {
                "queue": "all",
                "status": Ticket.Status.NEW,
                "priority": Ticket.Priority.NORMAL,
                "sort": "oldest",
            },
        )
        self.assertContains(response, "Requester")
        self.assertContains(response, "Assignee")
        self.assertContains(response, self.agent.full_name)
        self.assertContains(
            response,
            "queue=all&amp;status=new&amp;priority=normal&amp;sort=oldest&amp;page=2",
        )

    def test_sidebar_exposes_role_appropriate_main_routes(self):
        self.client.force_login(self.requester)
        response = self.client.get(reverse("ticket-list"))
        self.assertContains(response, "My tickets")
        self.assertContains(response, reverse("ticket-create"))
        self.assertNotContains(response, "Administration")
        self.client.force_login(self.admin)
        response = self.client.get(reverse("ticket-list"))
        self.assertContains(response, "All tickets")
        self.assertContains(response, "Assigned to me")
        self.assertContains(response, "Administration")

    def test_ticket_edit_metadata_controls_belong_to_sidebar_form(self):
        ticket = self.create_ticket()
        self.client.force_login(self.agent)
        response = self.client.get(reverse("ticket-detail", args=[ticket.number]))
        self.assertContains(response, "data-description-edit-sidebar")
        update_form = response.context["update_form"]
        for field_name in ("status", "category", "assignee", "priority"):
            self.assertEqual(
                update_form.fields[field_name].widget.attrs["form"],
                "ticket-update-form",
            )

    def test_administrator_manages_categories(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("administration-categories"),
            {"name": "Software", "is_active": "on"},
        )
        self.assertRedirects(response, reverse("administration-categories"))
        category = Category.objects.get(name="Software")
        self.client.post(
            reverse("administration-category-update", args=[category.id]),
            {"name": "Applications"},
        )
        category.refresh_from_db()
        self.assertEqual(category.name, "Applications")
        self.assertFalse(category.is_active)


class ConversationAndRedactionTests(TicketTestCase):
    def setUp(self):
        super().setUp()
        self.ticket = self.create_ticket()

    def test_public_and_internal_message_permissions(self):
        self.client.force_login(self.requester)
        response = self.client.post(
            reverse("ticket-message-create", args=[self.ticket.number]),
            {
                "visibility": TicketMessage.Visibility.INTERNAL,
                "body": "<p>Requester update.</p>",
                "temporary_upload_ids": "[]",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.ticket.messages.get().visibility,
            TicketMessage.Visibility.PUBLIC,
        )

        self.client.force_login(self.agent)
        response = self.client.post(
            reverse("ticket-message-create", args=[self.ticket.number]),
            {
                "visibility": TicketMessage.Visibility.INTERNAL,
                "body": "<p>Agent-only diagnosis.</p>",
                "temporary_upload_ids": "[]",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.ticket.messages.latest("id").visibility,
            TicketMessage.Visibility.INTERNAL,
        )

    def test_only_administrator_can_redact_message(self):
        message = self.service.add_message(
            self.agent,
            self.ticket,
            body="<p>Sensitive mistake</p>",
            visibility=TicketMessage.Visibility.PUBLIC,
            attachments=[],
            temporary_upload_ids=[],
        )
        self.client.force_login(self.agent)
        self.assertEqual(
            self.client.post(
                reverse("ticket-message-redact", args=[self.ticket.number, message.id])
            ).status_code,
            404,
        )
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("ticket-message-redact", args=[self.ticket.number, message.id])
        )
        self.assertRedirects(
            response, reverse("ticket-detail", args=[self.ticket.number])
        )
        message.refresh_from_db()
        self.assertTrue(message.is_redacted)
        self.assertNotIn("Sensitive mistake", message.body)
        self.assertTrue(
            self.ticket.events.filter(event_type=TicketEvent.Type.REDACTED).exists()
        )

    def test_message_update_and_delete_routes_do_not_exist(self):
        message = self.service.add_message(
            self.requester,
            self.ticket,
            body="<p>Permanent reply</p>",
            visibility=TicketMessage.Visibility.PUBLIC,
            attachments=[],
            temporary_upload_ids=[],
        )
        self.client.force_login(self.requester)
        self.assertEqual(
            self.client.post(
                f"/tickets/{self.ticket.display_id}/messages/{message.id}/"
            ).status_code,
            404,
        )


class RichTextAndAttachmentTests(TicketTestCase):
    def setUp(self):
        super().setUp()
        self.ticket = self.create_ticket()

    def test_rich_text_sanitizer_removes_executable_content(self):
        cleaned = sanitize_rich_text(
            '<p onclick="alert(1)">Safe</p><script>alert(2)</script>'
            '<a href="javascript:alert(3)">bad</a>'
            '<img src="https://tracker.example/pixel.png" onerror="alert(4)">'
        )
        self.assertNotIn("onclick", cleaned)
        self.assertNotIn("<script", cleaned)
        self.assertNotIn("javascript:", cleaned)
        self.assertNotIn("tracker.example", cleaned)

    def test_attachment_type_and_size_are_validated(self):
        with self.assertRaises(ValidationError):
            self.service.add_message(
                self.agent,
                self.ticket,
                body="<p>File</p>",
                visibility=TicketMessage.Visibility.PUBLIC,
                attachments=[SimpleUploadedFile("bad.exe", b"\x00\x01\x02")],
                temporary_upload_ids=[],
            )
        with self.assertRaises(ValidationError):
            self.service.add_message(
                self.agent,
                self.ticket,
                body="<p>Large</p>",
                visibility=TicketMessage.Visibility.PUBLIC,
                attachments=[text_file(value=b"x" * (10 * 1024 * 1024 + 1))],
                temporary_upload_ids=[],
            )

    def test_temporary_image_is_private_promoted_and_cleaned(self):
        upload = self.service.create_temporary_upload(self.requester, png_file())
        self.client.force_login(self.other_requester)
        self.assertEqual(
            self.client.get(
                reverse("temporary-upload-file", args=[upload.id])
            ).status_code,
            404,
        )
        ticket = self.service.create_ticket(
            self.requester,
            cleaned_data={
                "subject": "Screenshot attached",
                "description": (
                    f'<p>See image</p><img src="/uploads/{upload.id}/file/">'
                ),
                "category": self.category,
                "priority": Ticket.Priority.NORMAL,
            },
            attachments=[],
            temporary_upload_ids=[upload.id],
        )
        ticket.refresh_from_db()
        attachment = ticket.attachments.get()
        self.assertIn(f"/attachments/{attachment.id}/file/", ticket.description)
        self.assertFalse(TemporaryUpload.objects.filter(pk=upload.id).exists())

        expired = self.service.create_temporary_upload(
            self.requester, png_file("old.png")
        )
        TemporaryUpload.objects.filter(pk=expired.pk).update(
            created_at=timezone.now() - timedelta(hours=25)
        )
        self.assertEqual(TicketService.cleanup_temporary_uploads(), 1)
        self.assertFalse(TemporaryUpload.objects.filter(pk=expired.pk).exists())

    def test_invalid_temporary_reference_does_not_orphan_valid_attachment(self):
        with self.assertRaises(ValidationError):
            self.service.create_ticket(
                self.requester,
                cleaned_data={
                    "subject": "Must roll back",
                    "description": '<p>Bad image</p><img src="/uploads/999999/file/">',
                    "category": self.category,
                    "priority": Ticket.Priority.NORMAL,
                },
                attachments=[text_file("valid.txt")],
                temporary_upload_ids=[999999],
            )
        self.assertFalse(Ticket.objects.filter(subject="Must roll back").exists())
        self.assertFalse(TicketAttachment.objects.exists())
        stored_files = [
            item for item in Path(self.media.name).rglob("*") if item.is_file()
        ]
        self.assertEqual(stored_files, [])

    def test_public_attachment_download_is_owner_scoped(self):
        message = self.service.add_message(
            self.requester,
            self.ticket,
            body="<p>Public file</p>",
            visibility=TicketMessage.Visibility.PUBLIC,
            attachments=[text_file()],
            temporary_upload_ids=[],
        )
        attachment = message.attachments.get()
        self.client.force_login(self.requester)
        self.assertEqual(
            self.client.get(
                reverse("attachment-file", args=[attachment.id])
            ).status_code,
            200,
        )
        self.client.force_login(self.other_requester)
        self.assertEqual(
            self.client.get(
                reverse("attachment-file", args=[attachment.id])
            ).status_code,
            404,
        )


class TicketEmailTests(TicketTestCase):
    def test_creation_public_reply_resolution_and_reopening_notifications(self):
        with self.captureOnCommitCallbacks(execute=True):
            ticket = self.create_ticket()
        self.assertEqual(mail.outbox[-1].to, [self.requester.email])

        ticket.assignee = self.agent
        ticket.save()
        with self.captureOnCommitCallbacks(execute=True):
            self.service.add_message(
                self.agent,
                ticket,
                body="<p>Please restart it.</p>",
                visibility=TicketMessage.Visibility.PUBLIC,
                attachments=[],
                temporary_upload_ids=[],
            )
        self.assertEqual(mail.outbox[-1].to, [self.requester.email])

        before_internal = len(mail.outbox)
        with self.captureOnCommitCallbacks(execute=True):
            self.service.add_message(
                self.agent,
                ticket,
                body="<p>PRIVATE CONTENT MUST NOT EMAIL</p>",
                visibility=TicketMessage.Visibility.INTERNAL,
                attachments=[],
                temporary_upload_ids=[],
            )
        self.assertEqual(len(mail.outbox), before_internal)
        self.assertTrue(all("PRIVATE CONTENT" not in item.body for item in mail.outbox))

        with self.captureOnCommitCallbacks(execute=True):
            self.service.update_ticket(
                self.agent,
                ticket,
                cleaned_data={
                    "status": Ticket.Status.RESOLVED,
                    "resolution_summary": "Restart restored service.",
                },
                attachments=[],
                temporary_upload_ids=[],
            )
        self.assertIn("resolved", mail.outbox[-1].subject.lower())

        with self.captureOnCommitCallbacks(execute=True):
            self.service.add_message(
                self.requester,
                ticket,
                body="<p>Still broken.</p>",
                visibility=TicketMessage.Visibility.PUBLIC,
                attachments=[],
                temporary_upload_ids=[],
            )
        self.assertEqual(mail.outbox[-1].to, [self.agent.email])
        self.assertIn("reopened", mail.outbox[-1].body)

    def test_multi_recipient_reopen_email_is_sent_separately(self):
        ticket = self.create_ticket()
        ticket.assignee = self.agent
        ticket.status = Ticket.Status.RESOLVED
        ticket.resolution_summary = "Previously fixed"
        ticket.resolved_at = timezone.now()
        ticket.save()
        before = len(mail.outbox)
        with self.captureOnCommitCallbacks(execute=True):
            self.service.update_ticket(
                self.agent,
                ticket,
                cleaned_data={
                    "status": Ticket.Status.IN_PROGRESS,
                    "resolution_summary": "",
                },
                attachments=[],
                temporary_upload_ids=[],
            )
        deliveries = mail.outbox[before:]
        self.assertEqual(len(deliveries), 2)
        self.assertEqual(
            {tuple(item.to) for item in deliveries},
            {(self.requester.email,), (self.agent.email,)},
        )

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.dummy.EmailBackend")
    def test_delivery_backend_does_not_change_saved_mutation(self):
        with patch(
            "tickets.application.services.send_email",
            side_effect=RuntimeError("provider unavailable"),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                ticket = self.create_ticket()
        self.assertTrue(Ticket.objects.filter(pk=ticket.pk).exists())

        with patch(
            "tickets.application.services.notify_ticket_created",
            side_effect=OperationalError("database is locked"),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                ticket = self.create_ticket()
        self.assertTrue(Ticket.objects.filter(pk=ticket.pk).exists())


@override_settings(EMAIL_BACKEND="django.core.mail.backends.dummy.EmailBackend")
class TicketConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.category, _ = Category.objects.get_or_create(name="General")
        self.requester = create_user("concurrent@example.test")
        self.barrier = Barrier(4)

    def create_in_thread(self, index: int) -> int:
        close_old_connections()
        try:
            requester = User.objects.get(pk=self.requester.pk)
            category = Category.objects.get(pk=self.category.pk)
            self.barrier.wait(timeout=10)
            ticket = TicketService().create_ticket(
                requester,
                cleaned_data={
                    "subject": f"Concurrent {index}",
                    "description": "<p>Concurrent ticket</p>",
                    "category": category,
                    "priority": Ticket.Priority.NORMAL,
                },
                attachments=[],
                temporary_upload_ids=[],
            )
            return ticket.number
        finally:
            close_old_connections()

    def test_concurrent_creation_allocates_unique_monotonic_numbers(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            numbers = list(executor.map(self.create_in_thread, range(4)))
        self.assertEqual(sorted(numbers), [1, 2, 3, 4])
        self.assertEqual(Ticket.objects.count(), 4)
