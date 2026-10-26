from django import template
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _

from tickets.richtext import sanitize_rich_text

register = template.Library()


@register.filter
def safe_rich_text(value):
    return mark_safe(sanitize_rich_text(value or ""))


@register.filter
def initials(user):
    first = (user.first_name or "")[:1]
    last = (user.last_name or "")[:1]
    return (first + last).upper() or "?"


@register.filter
def event_description(event):
    labels = {
        "created": _("created the ticket"),
        "updated": _("updated ticket content"),
        "assigned": _("changed the assignment"),
        "status": _("changed the status"),
        "priority": _("changed the priority"),
        "category": _("changed the category"),
        "requester": _("changed the requester"),
        "redacted": _("redacted a message"),
        "resolved": _("resolved the ticket"),
        "reopened": _("returned the ticket to In progress"),
    }
    return labels.get(event.event_type, event.get_event_type_display())
