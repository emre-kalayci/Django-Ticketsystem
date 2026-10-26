from __future__ import annotations

import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Case, IntegerField, Q, When
from django.http import (
    FileResponse,
    Http404,
    HttpResponseBadRequest,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _

from tickets.application.services import TicketService
from tickets.forms import CategoryForm, MessageForm, TicketCreateForm, TicketUpdateForm
from tickets.models import (
    Category,
    Ticket,
    TicketAttachment,
    TicketEvent,
)
from tickets.policies import (
    can_view_attachment,
    get_visible_ticket_or_404,
    is_administrator,
    is_support,
    visible_messages,
    visible_tickets,
)
from users.models import User
from users.views import administrator_required

TICKET_ID = re.compile(r"^TKT-0*(\d+)$", re.IGNORECASE)


@login_required
def home(request):
    return redirect("ticket-list")


@login_required
def ticket_list(request):
    queryset = visible_tickets(request.user)
    queue = request.GET.get("queue", "all") if is_support(request.user) else "mine"
    if is_support(request.user):
        if queue == "mine":
            queryset = queryset.filter(assignee=request.user)
        elif queue == "unassigned":
            queryset = queryset.filter(assignee__isnull=True)
        elif queue == "waiting":
            queryset = queryset.filter(status=Ticket.Status.WAITING_FOR_REQUESTER)
        elif queue == "resolved":
            queryset = queryset.filter(status=Ticket.Status.RESOLVED)
        elif queue != "all":
            queue = "all"

    search = request.GET.get("q", "").strip()
    if search:
        conditions = Q(subject__icontains=search)
        id_match = TICKET_ID.fullmatch(search)
        if id_match:
            conditions |= Q(number=int(id_match.group(1)))
        if is_support(request.user):
            conditions |= (
                Q(requester__email__icontains=search)
                | Q(requester__first_name__icontains=search)
                | Q(requester__last_name__icontains=search)
            )
        queryset = queryset.filter(conditions)

    status = request.GET.get("status", "")
    priority = request.GET.get("priority", "")
    category = request.GET.get("category", "")
    assignee = request.GET.get("assignee", "")
    if status in Ticket.Status.values:
        queryset = queryset.filter(status=status)
    if priority in Ticket.Priority.values:
        queryset = queryset.filter(priority=priority)
    if category.isdigit():
        queryset = queryset.filter(category_id=int(category))
    if is_support(request.user) and assignee.isdigit():
        queryset = queryset.filter(assignee_id=int(assignee))

    sort = request.GET.get("sort", "updated")
    if sort == "newest":
        queryset = queryset.order_by("-created_at", "-id")
    elif sort == "oldest":
        queryset = queryset.order_by("created_at", "id")
    elif sort == "priority":
        queryset = queryset.annotate(
            priority_order=Case(
                When(priority=Ticket.Priority.URGENT, then=0),
                When(priority=Ticket.Priority.HIGH, then=1),
                When(priority=Ticket.Priority.NORMAL, then=2),
                default=3,
                output_field=IntegerField(),
            )
        ).order_by("priority_order", "-updated_at")
    else:
        sort = "updated"
        queryset = queryset.order_by("-updated_at", "-id")

    page = Paginator(queryset, 25).get_page(request.GET.get("page"))
    page_query = request.GET.copy()
    page_query.pop("page", None)
    return render(
        request,
        "tickets/ticket_list.html",
        {
            "page_obj": page,
            "queue": queue,
            "search": search,
            "selected_status": status,
            "selected_priority": priority,
            "selected_category": category,
            "selected_assignee": assignee,
            "selected_sort": sort,
            "page_query": page_query.urlencode(),
            "ticket_status_choices": Ticket.Status.choices,
            "ticket_priority_choices": Ticket.Priority.choices,
            "categories": Category.objects.filter(is_active=True),
            "agents": User.objects.filter(
                is_active=True,
                role__in=[User.Role.SUPPORT_AGENT, User.Role.ADMINISTRATOR],
            ),
        },
    )


@login_required
def ticket_create(request):
    form = TicketCreateForm(
        request.POST or None,
        request.FILES or None,
        actor=request.user,
    )
    if request.method == "POST" and form.is_valid():
        cleaned = form.cleaned_data.copy()
        temporary_upload_ids = cleaned.pop("temporary_upload_ids", [])
        attachments = cleaned.pop("attachments", [])
        try:
            ticket = TicketService().create_ticket(
                request.user,
                cleaned_data=cleaned,
                attachments=attachments,
                temporary_upload_ids=temporary_upload_ids,
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(
                request,
                _("%(ticket_id)s created.") % {"ticket_id": ticket.display_id},
            )
            return redirect("ticket-detail", number=ticket.number)
    return render(request, "tickets/ticket_create.html", {"form": form})


def _ticket_detail_context(request, ticket, *, message_form=None, update_form=None):
    messages_qs = visible_messages(request.user, ticket).prefetch_related("attachments")
    events = ticket.events.select_related("actor")
    if not is_support(request.user):
        events = events.filter(visibility=TicketEvent.Visibility.PUBLIC)
    events = events.exclude(
        event_type__in=[
            TicketEvent.Type.PUBLIC_REPLY,
            TicketEvent.Type.INTERNAL_NOTE,
            TicketEvent.Type.ATTACHMENT,
        ]
    )
    timeline = [
        {"kind": "message", "date": item.created_at, "message": item}
        for item in messages_qs
    ] + [{"kind": "event", "date": item.created_at, "event": item} for item in events]
    timeline.sort(key=lambda item: item["date"])
    public_attachments = ticket.attachments.filter(
        message__isnull=True,
        visibility="public",
    )
    if is_support(request.user):
        public_attachments = ticket.attachments.filter(message__isnull=True)
    return {
        "ticket": ticket,
        "timeline": timeline,
        "ticket_attachments": public_attachments,
        "message_form": message_form or MessageForm(actor=request.user),
        "update_form": update_form
        or (
            TicketUpdateForm(instance=ticket, actor=request.user)
            if is_support(request.user)
            else None
        ),
    }


@login_required
def ticket_detail(request, number: int):
    ticket = get_visible_ticket_or_404(request.user, number)
    return render(
        request,
        "tickets/ticket_detail.html",
        _ticket_detail_context(request, ticket),
    )


@login_required
def ticket_update(request, number: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if not is_support(request.user):
        raise Http404("Ticket not found.")
    ticket = get_visible_ticket_or_404(request.user, number)
    form = TicketUpdateForm(
        request.POST,
        request.FILES,
        instance=ticket,
        actor=request.user,
    )
    if form.is_valid():
        cleaned = form.cleaned_data.copy()
        temporary_upload_ids = cleaned.pop("temporary_upload_ids", [])
        attachments = cleaned.pop("attachments", [])
        try:
            TicketService().update_ticket(
                request.user,
                ticket,
                cleaned_data=cleaned,
                attachments=attachments,
                temporary_upload_ids=temporary_upload_ids,
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, _("Ticket updated."))
            return redirect("ticket-detail", number=number)
    return render(
        request,
        "tickets/ticket_detail.html",
        _ticket_detail_context(request, ticket, update_form=form),
        status=400,
    )


@login_required
def ticket_message_create(request, number: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    ticket = get_visible_ticket_or_404(request.user, number)
    form = MessageForm(request.POST, request.FILES, actor=request.user)
    if form.is_valid():
        cleaned = form.cleaned_data
        try:
            TicketService().add_message(
                request.user,
                ticket,
                body=cleaned["body"],
                visibility=cleaned.get("visibility", "public"),
                attachments=cleaned.get("attachments", []),
                temporary_upload_ids=cleaned.get("temporary_upload_ids", []),
            )
        except (ValidationError, PermissionDenied) as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, _("Reply added."))
            return redirect(f"{reverse('ticket-detail', args=[number])}#conversation")
    return render(
        request,
        "tickets/ticket_detail.html",
        _ticket_detail_context(request, ticket, message_form=form),
        status=400,
    )


@login_required
def ticket_message_redact(request, number: int, message_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if not is_administrator(request.user):
        raise Http404("Message not found.")
    ticket = get_visible_ticket_or_404(request.user, number)
    message = get_object_or_404(ticket.messages, pk=message_id)
    TicketService().redact_message(request.user, message)
    messages.success(request, _("Message redacted and recorded in history."))
    return redirect("ticket-detail", number=number)


@login_required
def ticket_delete(request, number: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if not is_administrator(request.user):
        raise Http404("Ticket not found.")
    ticket = get_visible_ticket_or_404(request.user, number)
    if request.POST.get("confirmation") != ticket.display_id:
        return HttpResponseBadRequest("Explicit ticket confirmation is required.")
    display_id = ticket.display_id
    TicketService().delete_ticket(request.user, ticket)
    messages.success(
        request,
        _("%(ticket_id)s permanently deleted.") % {"ticket_id": display_id},
    )
    return redirect("ticket-list")


@login_required
def temporary_upload_create(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    uploaded_file = request.FILES.get("file")
    if uploaded_file is None:
        return JsonResponse({"error": "Choose an image."}, status=400)
    try:
        upload = TicketService().create_temporary_upload(request.user, uploaded_file)
    except ValidationError as exc:
        return JsonResponse({"error": "; ".join(exc.messages)}, status=400)
    return JsonResponse(
        {
            "id": upload.id,
            "url": reverse("temporary-upload-file", args=[upload.id]),
            "name": upload.original_name,
        },
        status=201,
    )


@login_required
def temporary_upload_file(request, upload_id: int):
    try:
        upload = TicketService().get_temporary_upload(request.user, upload_id)
    except PermissionDenied as exc:
        raise Http404("Upload not found.") from exc
    response = FileResponse(upload.file.open("rb"), content_type=upload.mime_type)
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
def attachment_file(request, attachment_id: int):
    attachment = get_object_or_404(
        TicketAttachment.objects.select_related("ticket"),
        pk=attachment_id,
    )
    if not can_view_attachment(request.user, attachment):
        raise Http404("Attachment not found.")
    response = FileResponse(
        attachment.file.open("rb"),
        as_attachment=not attachment.is_image,
        filename=attachment.original_name,
        content_type=attachment.mime_type,
    )
    response["X-Content-Type-Options"] = "nosniff"
    return response


@administrator_required
def administration_categories(request):
    form = CategoryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Category created."))
        return redirect("administration-categories")
    return render(
        request,
        "tickets/administration_categories.html",
        {"categories": Category.objects.all(), "form": form},
    )


@administrator_required
def administration_category_update(request, category_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    category = get_object_or_404(Category, pk=category_id)
    form = CategoryForm(request.POST, instance=category)
    if form.is_valid():
        form.save()
        messages.success(request, _("Category updated."))
    else:
        messages.error(request, _("Category update was invalid."))
    return redirect("administration-categories")
