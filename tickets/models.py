from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


def _extension_for_mime_type(mime_type: str) -> str:
    return {
        "application/pdf": "pdf",
        "image/jpeg": "jpg",
        "image/png": "png",
        "text/plain": "txt",
    }[mime_type]


def ticket_attachment_path(instance: TicketAttachment, filename: str) -> str:
    extension = _extension_for_mime_type(instance.mime_type)
    return f"tickets/{instance.ticket_id}/{uuid.uuid4().hex}.{extension}"


def temporary_upload_path(instance: TemporaryUpload, filename: str) -> str:
    extension = _extension_for_mime_type(instance.mime_type)
    return f"temporary/{instance.uploaded_by_id}/{uuid.uuid4().hex}.{extension}"


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        verbose_name_plural = "categories"

    def __str__(self) -> str:
        return self.name


class TicketSequence(models.Model):
    next_number = models.PositiveBigIntegerField(default=1)

    def __str__(self) -> str:
        return f"Next ticket: {self.next_number}"


class Ticket(models.Model):
    class Status(models.TextChoices):
        NEW = "new", _("New")
        IN_PROGRESS = "in_progress", _("In progress")
        WAITING_FOR_REQUESTER = "waiting_for_requester", _("Waiting for requester")
        RESOLVED = "resolved", _("Resolved")

    class Priority(models.TextChoices):
        LOW = "low", _("Low")
        NORMAL = "normal", _("Normal")
        HIGH = "high", _("High")
        URGENT = "urgent", _("Urgent")

    number = models.PositiveBigIntegerField(unique=True)
    subject = models.CharField(max_length=255)
    description = models.TextField()
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="requested_tickets",
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_support_tickets",
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="tickets",
    )
    priority = models.CharField(
        max_length=10,
        choices=Priority.choices,
        default=Priority.NORMAL,
    )
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.NEW,
    )
    resolution_summary = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-updated_at", "-id"]

    def __str__(self) -> str:
        return f"{self.display_id} {self.subject}"

    @property
    def display_id(self) -> str:
        return f"TKT-{self.number:06d}"


class TicketMessage(models.Model):
    class Visibility(models.TextChoices):
        PUBLIC = "public", _("Public reply")
        INTERNAL = "internal", _("Internal note")

    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="ticket_messages",
    )
    visibility = models.CharField(
        max_length=10,
        choices=Visibility.choices,
        default=Visibility.PUBLIC,
    )
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    redacted_at = models.DateTimeField(null=True, blank=True)
    redacted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="redacted_ticket_messages",
    )

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"Message {self.pk} on {self.ticket.display_id}"

    @property
    def is_redacted(self) -> bool:
        return self.redacted_at is not None


class TicketEvent(models.Model):
    class Visibility(models.TextChoices):
        PUBLIC = "public", "Public"
        INTERNAL = "internal", "Internal"

    class Type(models.TextChoices):
        CREATED = "created", "Ticket created"
        UPDATED = "updated", "Ticket updated"
        ASSIGNED = "assigned", "Assignment changed"
        STATUS = "status", "Status changed"
        PRIORITY = "priority", "Priority changed"
        CATEGORY = "category", "Category changed"
        REQUESTER = "requester", "Requester changed"
        PUBLIC_REPLY = "public_reply", "Public reply added"
        INTERNAL_NOTE = "internal_note", "Internal note added"
        ATTACHMENT = "attachment", "Attachment added"
        REDACTED = "redacted", "Message redacted"
        RESOLVED = "resolved", "Ticket resolved"
        REOPENED = "reopened", "Ticket reopened"
        DELETED = "deleted", "Ticket deleted"

    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        related_name="events",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ticket_events",
    )
    event_type = models.CharField(max_length=30, choices=Type.choices)
    visibility = models.CharField(
        max_length=10,
        choices=Visibility.choices,
        default=Visibility.INTERNAL,
    )
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"{self.ticket.display_id}: {self.event_type}"


class TicketAttachment(models.Model):
    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    message = models.ForeignKey(
        TicketMessage,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="attachments",
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="ticket_attachments",
    )
    visibility = models.CharField(
        max_length=10,
        choices=TicketMessage.Visibility.choices,
        default=TicketMessage.Visibility.PUBLIC,
    )
    original_name = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=100)
    size = models.PositiveBigIntegerField()
    file = models.FileField(upload_to=ticket_attachment_path)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return self.original_name

    @property
    def is_image(self) -> bool:
        return self.mime_type in {"image/jpeg", "image/png"}


class TemporaryUpload(models.Model):
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="temporary_uploads",
    )
    original_name = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=100)
    size = models.PositiveBigIntegerField()
    file = models.FileField(upload_to=temporary_upload_path)
    created_at = models.DateTimeField(auto_now_add=True)
    claimed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return self.original_name

    @property
    def is_image(self) -> bool:
        return self.mime_type in {"image/jpeg", "image/png"}
