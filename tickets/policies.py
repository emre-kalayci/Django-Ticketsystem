from __future__ import annotations

from django.db.models import QuerySet
from django.http import Http404

from tickets.models import Ticket, TicketAttachment, TicketMessage


def is_support(user) -> bool:
    return bool(user.is_authenticated and user.is_active and user.is_support)


def is_administrator(user) -> bool:
    return bool(user.is_authenticated and user.is_active and user.is_administrator)


def visible_tickets(user) -> QuerySet[Ticket]:
    queryset = Ticket.objects.select_related(
        "requester",
        "assignee",
        "category",
    )
    if is_support(user):
        return queryset
    if user.is_authenticated and user.is_active:
        return queryset.filter(requester=user)
    return queryset.none()


def get_visible_ticket_or_404(user, number: int) -> Ticket:
    try:
        return visible_tickets(user).get(number=number)
    except Ticket.DoesNotExist as exc:
        raise Http404("Ticket not found.") from exc


def visible_messages(user, ticket: Ticket) -> QuerySet[TicketMessage]:
    queryset = ticket.messages.select_related("author", "redacted_by")
    if is_support(user):
        return queryset
    return queryset.filter(visibility=TicketMessage.Visibility.PUBLIC)


def can_view_attachment(user, attachment: TicketAttachment) -> bool:
    if is_support(user):
        return True
    return (
        user.is_authenticated
        and user.is_active
        and attachment.ticket.requester_id == user.id
        and attachment.visibility == TicketMessage.Visibility.PUBLIC
    )
