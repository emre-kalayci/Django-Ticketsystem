from __future__ import annotations

import logging
import time
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import OperationalError, transaction
from django.db.models import F
from django.utils import timezone
from django.utils.translation import gettext as _

from tickets.application.uploads import validate_uploaded_file
from tickets.models import (
    TemporaryUpload,
    Ticket,
    TicketAttachment,
    TicketEvent,
    TicketMessage,
    TicketSequence,
)
from tickets.policies import is_administrator, is_support
from users.emailing import send_email

logger = logging.getLogger(__name__)


class TicketService:
    def create_ticket(
        self,
        actor,
        *,
        cleaned_data: dict,
        attachments: list,
        temporary_upload_ids: list[int],
    ) -> Ticket:
        requester = cleaned_data.pop("requester", None) if is_support(actor) else actor
        if requester is None:
            raise ValidationError(_("Choose a requester."))

        for attempt in range(12):
            try:
                return self._create_ticket_once(
                    actor,
                    requester=requester,
                    cleaned_data=cleaned_data.copy(),
                    attachments=attachments,
                    temporary_upload_ids=temporary_upload_ids,
                )
            except OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 11:
                    raise
                time.sleep(min(0.03 * (attempt + 1), 0.25))
        raise RuntimeError("Ticket allocation retry loop exhausted.")

    @transaction.atomic
    def _create_ticket_once(
        self,
        actor,
        *,
        requester,
        cleaned_data: dict,
        attachments: list,
        temporary_upload_ids: list[int],
    ) -> Ticket:
        prepared_files = self._prepare_files(attachments)
        uploads = self._lock_temporary_uploads(actor, temporary_upload_ids)
        sequence, _ = TicketSequence.objects.select_for_update().get_or_create(pk=1)
        TicketSequence.objects.filter(pk=sequence.pk).update(
            next_number=F("next_number") + 1
        )
        number = sequence.next_number
        ticket = Ticket.objects.create(
            number=number,
            requester=requester,
            **cleaned_data,
        )
        self._event(
            ticket,
            actor,
            TicketEvent.Type.CREATED,
            visibility=TicketEvent.Visibility.PUBLIC,
            data={"display_id": ticket.display_id},
        )
        if uploads:
            ticket.description = self._promote_temporary_uploads(
                actor,
                ticket,
                uploads,
                ticket.description,
                visibility=TicketMessage.Visibility.PUBLIC,
            )
            ticket.save(update_fields=["description", "updated_at"])
        self._add_files(
            ticket,
            actor,
            [],
            visibility=TicketMessage.Visibility.PUBLIC,
            prepared_files=prepared_files,
        )
        transaction.on_commit(
            lambda: _run_notification(notify_ticket_created, ticket.pk)
        )
        return ticket

    @transaction.atomic
    def update_ticket(
        self,
        actor,
        ticket: Ticket,
        *,
        cleaned_data: dict,
        attachments: list,
        temporary_upload_ids: list[int],
    ) -> Ticket:
        if not is_support(actor):
            raise PermissionDenied(_("Support access required."))
        assignee = cleaned_data.get("assignee")
        if assignee and (not assignee.is_active or not assignee.is_support):
            raise ValidationError(_("Assignee must be an active support user."))
        has_public_resolution_reply = ticket.messages.filter(
            visibility=TicketMessage.Visibility.PUBLIC,
            author__role__in=["support_agent", "administrator"],
        ).exists()
        if (
            cleaned_data.get("status") == Ticket.Status.RESOLVED
            and not (cleaned_data.get("resolution_summary") or "").strip()
            and not has_public_resolution_reply
        ):
            raise ValidationError(
                _("Add a resolution summary before resolving this ticket.")
            )

        previous = {
            "subject": ticket.subject,
            "description": ticket.description,
            "requester_id": ticket.requester_id,
            "assignee_id": ticket.assignee_id,
            "category_id": ticket.category_id,
            "priority": ticket.priority,
            "status": ticket.status,
            "resolution_summary": ticket.resolution_summary,
        }
        if not is_administrator(actor):
            cleaned_data.pop("requester", None)
        prepared_files = self._prepare_files(attachments)
        uploads = self._lock_temporary_uploads(actor, temporary_upload_ids)
        for field, value in cleaned_data.items():
            setattr(ticket, field, value)

        if ticket.status == Ticket.Status.RESOLVED:
            if (
                not ticket.resolution_summary.strip()
                and not has_public_resolution_reply
            ):
                raise ValidationError(
                    _("Add a resolution summary before resolving this ticket.")
                )
            ticket.resolved_at = ticket.resolved_at or timezone.now()
        else:
            ticket.resolved_at = None

        if uploads:
            ticket.description = self._promote_temporary_uploads(
                actor,
                ticket,
                uploads,
                ticket.description,
                visibility=TicketMessage.Visibility.PUBLIC,
            )
        ticket.save()
        self._add_files(
            ticket,
            actor,
            [],
            visibility=TicketMessage.Visibility.PUBLIC,
            prepared_files=prepared_files,
        )

        changes = []
        change_types = {
            "assignee_id": TicketEvent.Type.ASSIGNED,
            "status": TicketEvent.Type.STATUS,
            "priority": TicketEvent.Type.PRIORITY,
            "category_id": TicketEvent.Type.CATEGORY,
            "requester_id": TicketEvent.Type.REQUESTER,
        }
        for field, old_value in previous.items():
            new_value = getattr(ticket, field)
            if old_value == new_value:
                continue
            changes.append(field)
            event_type = change_types.get(field, TicketEvent.Type.UPDATED)
            visibility = (
                TicketEvent.Visibility.PUBLIC
                if field in {"status", "priority", "category_id"}
                else TicketEvent.Visibility.INTERNAL
            )
            self._event(
                ticket,
                actor,
                event_type,
                visibility=visibility,
                data={"field": field, "old": old_value, "new": new_value},
            )

        if previous["status"] != ticket.status:
            if ticket.status == Ticket.Status.RESOLVED:
                self._event(
                    ticket,
                    actor,
                    TicketEvent.Type.RESOLVED,
                    visibility=TicketEvent.Visibility.PUBLIC,
                    data={"summary": ticket.resolution_summary},
                )
                transaction.on_commit(
                    lambda: _run_notification(notify_ticket_resolved, ticket.pk)
                )
            elif previous["status"] == Ticket.Status.RESOLVED:
                self._event(
                    ticket,
                    actor,
                    TicketEvent.Type.REOPENED,
                    visibility=TicketEvent.Visibility.PUBLIC,
                )
                transaction.on_commit(
                    lambda: _run_notification(notify_ticket_reopened, ticket.pk)
                )
        return ticket

    @transaction.atomic
    def add_message(
        self,
        actor,
        ticket: Ticket,
        *,
        body: str,
        visibility: str,
        attachments: list,
        temporary_upload_ids: list[int],
    ) -> TicketMessage:
        if not is_support(actor):
            visibility = TicketMessage.Visibility.PUBLIC
        if visibility == TicketMessage.Visibility.INTERNAL and not is_support(actor):
            raise PermissionDenied(_("Only support staff can create internal notes."))
        prepared_files = self._prepare_files(attachments)
        uploads = self._lock_temporary_uploads(actor, temporary_upload_ids)

        message = TicketMessage.objects.create(
            ticket=ticket,
            author=actor,
            visibility=visibility,
            body=body,
        )
        self._add_files(
            ticket,
            actor,
            [],
            message=message,
            visibility=visibility,
            prepared_files=prepared_files,
        )
        if uploads:
            message.body = self._promote_temporary_uploads(
                actor,
                ticket,
                uploads,
                message.body,
                message=message,
                visibility=visibility,
            )
            message.save(update_fields=["body"])

        event_type = (
            TicketEvent.Type.INTERNAL_NOTE
            if visibility == TicketMessage.Visibility.INTERNAL
            else TicketEvent.Type.PUBLIC_REPLY
        )
        self._event(
            ticket,
            actor,
            event_type,
            visibility=(
                TicketEvent.Visibility.INTERNAL
                if visibility == TicketMessage.Visibility.INTERNAL
                else TicketEvent.Visibility.PUBLIC
            ),
            data={"message_id": message.id},
        )

        reopened = False
        if actor.id == ticket.requester_id and ticket.status in {
            Ticket.Status.WAITING_FOR_REQUESTER,
            Ticket.Status.RESOLVED,
        }:
            previous_status = ticket.status
            ticket.status = Ticket.Status.IN_PROGRESS
            ticket.resolved_at = None
            ticket.save(update_fields=["status", "resolved_at", "updated_at"])
            self._event(
                ticket,
                actor,
                TicketEvent.Type.REOPENED,
                visibility=TicketEvent.Visibility.PUBLIC,
                data={
                    "old": previous_status,
                    "new": Ticket.Status.IN_PROGRESS,
                },
            )
            reopened = True
        else:
            Ticket.objects.filter(pk=ticket.pk).update(updated_at=timezone.now())

        if visibility == TicketMessage.Visibility.PUBLIC:
            transaction.on_commit(
                lambda: _run_notification(
                    notify_public_reply,
                    message.pk,
                    reopened=reopened,
                )
            )
        return message

    @transaction.atomic
    def redact_message(self, actor, message: TicketMessage) -> None:
        if not is_administrator(actor):
            raise PermissionDenied(_("Administrator access required."))
        if message.redacted_at:
            return
        message.body = "<p><em>This message was redacted by an administrator.</em></p>"
        message.redacted_at = timezone.now()
        message.redacted_by = actor
        message.save(update_fields=["body", "redacted_at", "redacted_by"])
        message.attachments.all().delete()
        self._event(
            message.ticket,
            actor,
            TicketEvent.Type.REDACTED,
            visibility=TicketEvent.Visibility.INTERNAL,
            data={"message_id": message.id},
        )

    @transaction.atomic
    def delete_ticket(self, actor, ticket: Ticket) -> None:
        if not is_administrator(actor):
            raise PermissionDenied(_("Administrator access required."))
        ticket.delete()

    def create_temporary_upload(self, actor, uploaded_file) -> TemporaryUpload:
        mime_type, original_name = validate_uploaded_file(uploaded_file)
        if mime_type not in {"image/jpeg", "image/png"}:
            raise ValidationError(_("Editor uploads must be PNG or JPEG images."))
        return TemporaryUpload.objects.create(
            uploaded_by=actor,
            original_name=original_name,
            mime_type=mime_type,
            size=uploaded_file.size,
            file=uploaded_file,
        )

    def get_temporary_upload(self, actor, upload_id: int) -> TemporaryUpload:
        try:
            return TemporaryUpload.objects.get(
                pk=upload_id,
                uploaded_by=actor,
                claimed_at__isnull=True,
                created_at__gte=timezone.now() - timedelta(hours=24),
            )
        except TemporaryUpload.DoesNotExist as exc:
            raise PermissionDenied(_("Temporary upload not found.")) from exc

    @staticmethod
    def cleanup_temporary_uploads() -> int:
        uploads = TemporaryUpload.objects.filter(
            claimed_at__isnull=True,
            created_at__lt=timezone.now() - timedelta(hours=24),
        )
        count = uploads.count()
        uploads.delete()
        return count

    def _promote_temporary_uploads(
        self,
        actor,
        ticket: Ticket,
        uploads: list[TemporaryUpload],
        content: str,
        *,
        visibility: str,
        message: TicketMessage | None = None,
    ) -> str:
        for upload in uploads:
            attachment = self._create_attachment(
                ticket,
                actor,
                upload.file,
                message=message,
                visibility=visibility,
                original_name=upload.original_name,
                mime_type=upload.mime_type,
                size=upload.size,
            )
            content = content.replace(
                f"/uploads/{upload.id}/file/",
                f"/attachments/{attachment.id}/file/",
            )
            upload.claimed_at = timezone.now()
            upload.save(update_fields=["claimed_at"])
            upload.file.delete(save=False)
            upload.delete()
        return content

    def _add_files(
        self,
        ticket: Ticket,
        actor,
        files: list,
        *,
        visibility: str,
        message: TicketMessage | None = None,
        prepared_files: list | None = None,
    ) -> list[TicketAttachment]:
        prepared_files = prepared_files or self._prepare_files(files)
        attachments = []
        for uploaded_file, mime_type, original_name, size in prepared_files:
            attachments.append(
                self._create_attachment(
                    ticket,
                    actor,
                    uploaded_file,
                    message=message,
                    visibility=visibility,
                    original_name=original_name,
                    mime_type=mime_type,
                    size=size,
                )
            )
        return attachments

    @staticmethod
    def _prepare_files(files: list) -> list[tuple]:
        prepared_files = []
        for uploaded_file in files:
            mime_type, original_name = validate_uploaded_file(uploaded_file)
            prepared_files.append(
                (uploaded_file, mime_type, original_name, uploaded_file.size)
            )
        return prepared_files

    @staticmethod
    def _lock_temporary_uploads(actor, upload_ids: list[int]) -> list[TemporaryUpload]:
        unique_ids = set(upload_ids)
        if not unique_ids:
            return []
        uploads = list(
            TemporaryUpload.objects.select_for_update().filter(
                id__in=unique_ids,
                uploaded_by=actor,
                claimed_at__isnull=True,
                created_at__gte=timezone.now() - timedelta(hours=24),
            )
        )
        if len(uploads) != len(unique_ids):
            raise ValidationError(_("One or more editor uploads are invalid."))
        return uploads

    def _create_attachment(
        self,
        ticket: Ticket,
        actor,
        uploaded_file,
        *,
        visibility: str,
        message: TicketMessage | None = None,
        original_name: str | None = None,
        mime_type: str | None = None,
        size: int | None = None,
    ) -> TicketAttachment:
        if original_name is None or mime_type is None or size is None:
            mime_type, original_name = validate_uploaded_file(uploaded_file)
            size = uploaded_file.size
        if hasattr(uploaded_file, "open"):
            uploaded_file.open("rb")
        attachment = TicketAttachment(
            ticket=ticket,
            message=message,
            uploaded_by=actor,
            visibility=visibility,
            original_name=original_name,
            mime_type=mime_type,
            size=size,
        )
        attachment.file.save(original_name, uploaded_file, save=False)
        attachment.save()
        self._event(
            ticket,
            actor,
            TicketEvent.Type.ATTACHMENT,
            visibility=(
                TicketEvent.Visibility.PUBLIC
                if visibility == TicketMessage.Visibility.PUBLIC
                else TicketEvent.Visibility.INTERNAL
            ),
            data={"attachment_id": attachment.id, "name": original_name},
        )
        return attachment

    @staticmethod
    def _event(
        ticket: Ticket,
        actor,
        event_type: str,
        *,
        visibility: str,
        data: dict | None = None,
    ) -> TicketEvent:
        return TicketEvent.objects.create(
            ticket=ticket,
            actor=actor,
            event_type=event_type,
            visibility=visibility,
            data=data or {},
        )


def _send(subject: str, body: str, recipients: list[str]) -> None:
    if not recipients:
        return
    try:
        send_email(subject, body, recipients)
    except Exception:
        logger.exception("Ticket email delivery failed; ticket mutation remains saved.")


def _run_notification(notification, *args, **kwargs) -> None:
    try:
        notification(*args, **kwargs)
    except Exception:
        logger.exception("Ticket notification failed; ticket mutation remains saved.")


def _ticket_url(ticket: Ticket) -> str:
    return f"{settings.SITE_URL}/tickets/{ticket.display_id}/"


def notify_ticket_created(ticket_id: int) -> None:
    ticket = Ticket.objects.select_related("requester").get(pk=ticket_id)
    _send(
        f"{ticket.display_id} received: {ticket.subject}",
        f"Your support ticket was created.\n\n{_ticket_url(ticket)}",
        [ticket.requester.email],
    )


def notify_public_reply(message_id: int, *, reopened: bool) -> None:
    message = TicketMessage.objects.select_related(
        "ticket__requester",
        "ticket__assignee",
        "author",
    ).get(pk=message_id)
    ticket = message.ticket
    if message.author_id == ticket.requester_id:
        recipients = [ticket.assignee.email] if ticket.assignee else []
    else:
        recipients = [ticket.requester.email]
    suffix = " and reopened" if reopened else ""
    _send(
        f"New reply on {ticket.display_id}",
        f"A public reply was added{suffix}.\n\n{_ticket_url(ticket)}",
        recipients,
    )


def notify_ticket_resolved(ticket_id: int) -> None:
    ticket = Ticket.objects.select_related("requester").get(pk=ticket_id)
    _send(
        f"{ticket.display_id} resolved",
        f"Your ticket was resolved. You can reply if more help is needed.\n\n"
        f"{_ticket_url(ticket)}",
        [ticket.requester.email],
    )


def notify_ticket_reopened(ticket_id: int) -> None:
    ticket = Ticket.objects.select_related("requester", "assignee").get(pk=ticket_id)
    recipients = [ticket.requester.email]
    if ticket.assignee:
        recipients.append(ticket.assignee.email)
    _send(
        f"{ticket.display_id} reopened",
        f"This ticket returned to In progress.\n\n{_ticket_url(ticket)}",
        recipients,
    )
