from django.contrib import admin

from tickets.models import (
    Category,
    TemporaryUpload,
    Ticket,
    TicketAttachment,
    TicketEvent,
    TicketMessage,
    TicketSequence,
)


class ReadOnlyDomainAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "is_active", "updated_at"]
    list_filter = ["is_active"]
    search_fields = ["name"]


@admin.register(Ticket)
class TicketAdmin(ReadOnlyDomainAdmin):
    list_display = [
        "display_id",
        "subject",
        "status",
        "priority",
        "assignee",
        "updated_at",
    ]
    list_filter = ["status", "priority", "category"]
    search_fields = [
        "subject",
        "requester__email",
        "requester__first_name",
        "requester__last_name",
    ]
    autocomplete_fields = ["requester", "assignee", "category"]
    readonly_fields = ["number", "created_at", "updated_at", "resolved_at"]


@admin.register(TicketMessage)
class TicketMessageAdmin(ReadOnlyDomainAdmin):
    list_display = ["ticket", "author", "visibility", "created_at", "redacted_at"]
    list_filter = ["visibility", "redacted_at"]
    search_fields = ["ticket__subject", "author__email"]
    readonly_fields = ["created_at", "redacted_at", "redacted_by"]


@admin.register(TicketEvent)
class TicketEventAdmin(ReadOnlyDomainAdmin):
    list_display = ["ticket", "event_type", "actor", "visibility", "created_at"]
    list_filter = ["event_type", "visibility"]
    readonly_fields = [
        "ticket",
        "actor",
        "event_type",
        "visibility",
        "data",
        "created_at",
    ]


@admin.register(TicketAttachment)
class TicketAttachmentAdmin(ReadOnlyDomainAdmin):
    list_display = ["original_name", "ticket", "visibility", "uploaded_by", "size"]
    list_filter = ["visibility", "mime_type"]
    readonly_fields = ["original_name", "mime_type", "size", "created_at"]


@admin.register(TemporaryUpload)
class TemporaryUploadAdmin(admin.ModelAdmin):
    list_display = ["original_name", "uploaded_by", "created_at", "claimed_at"]
    readonly_fields = ["created_at", "claimed_at"]


@admin.register(TicketSequence)
class TicketSequenceAdmin(ReadOnlyDomainAdmin):
    readonly_fields = ["next_number"]
