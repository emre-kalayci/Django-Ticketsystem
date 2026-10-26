from django.urls import path, register_converter

from tickets import views
from tickets.converters import TicketNumberConverter

register_converter(TicketNumberConverter, "ticket_number")

urlpatterns = [
    path("", views.home, name="home"),
    path("tickets/", views.ticket_list, name="ticket-list"),
    path("tickets/new/", views.ticket_create, name="ticket-create"),
    path(
        "tickets/TKT-<ticket_number:number>/",
        views.ticket_detail,
        name="ticket-detail",
    ),
    path(
        "tickets/TKT-<ticket_number:number>/update/",
        views.ticket_update,
        name="ticket-update",
    ),
    path(
        "tickets/TKT-<ticket_number:number>/reply/",
        views.ticket_message_create,
        name="ticket-message-create",
    ),
    path(
        "tickets/TKT-<ticket_number:number>/messages/<int:message_id>/redact/",
        views.ticket_message_redact,
        name="ticket-message-redact",
    ),
    path(
        "tickets/TKT-<ticket_number:number>/delete/",
        views.ticket_delete,
        name="ticket-delete",
    ),
    path(
        "uploads/images/", views.temporary_upload_create, name="temporary-upload-create"
    ),
    path(
        "uploads/<int:upload_id>/file/",
        views.temporary_upload_file,
        name="temporary-upload-file",
    ),
    path(
        "attachments/<int:attachment_id>/file/",
        views.attachment_file,
        name="attachment-file",
    ),
    path(
        "administration/categories/",
        views.administration_categories,
        name="administration-categories",
    ),
    path(
        "administration/categories/<int:category_id>/update/",
        views.administration_category_update,
        name="administration-category-update",
    ),
]
