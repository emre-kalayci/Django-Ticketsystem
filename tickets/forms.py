from __future__ import annotations

import json

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from tickets.models import Category, Ticket, TicketMessage
from tickets.richtext import rich_text_is_empty, sanitize_rich_text
from users.models import User


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    widget = MultipleFileInput

    def clean(self, data, initial=None):
        if not data:
            return []
        return [forms.FileField.clean(self, item, initial) for item in data]


class RichTextMixin:
    def clean_rich_field(self, field_name: str, *, required: bool = True) -> str:
        cleaned = sanitize_rich_text(self.cleaned_data.get(field_name, ""))
        if required and rich_text_is_empty(cleaned):
            raise ValidationError(_("Enter content before saving."))
        return cleaned

    def clean_upload_ids(self) -> list[int]:
        raw = self.cleaned_data.get("temporary_upload_ids", "") or "[]"
        try:
            values = json.loads(raw)
            if not isinstance(values, list):
                raise TypeError
            return [int(value) for value in values]
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValidationError(_("Uploaded image references are invalid.")) from exc


class TicketCreateForm(RichTextMixin, forms.ModelForm):
    description = forms.CharField(widget=forms.HiddenInput())
    temporary_upload_ids = forms.CharField(required=False, widget=forms.HiddenInput())
    attachments = MultipleFileField(required=False)
    requester = forms.ModelChoiceField(queryset=User.objects.none(), required=False)

    class Meta:
        model = Ticket
        fields = ["subject", "description", "requester", "category", "priority"]

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        self.fields["category"].queryset = Category.objects.filter(is_active=True)
        if actor.is_support:
            self.fields["requester"].queryset = User.objects.filter(
                is_active=True,
                role=User.Role.REQUESTER,
            )
            self.fields["requester"].label_from_instance = lambda user: (
                f"{user.full_name} · {user.email}"
            )
            self.fields["requester"].required = True
        else:
            self.fields.pop("requester")

    def clean_description(self):
        return self.clean_rich_field("description")

    def clean_temporary_upload_ids(self):
        return self.clean_upload_ids()


class TicketUpdateForm(RichTextMixin, forms.ModelForm):
    description = forms.CharField(widget=forms.HiddenInput())
    temporary_upload_ids = forms.CharField(required=False, widget=forms.HiddenInput())
    attachments = MultipleFileField(required=False)

    class Meta:
        model = Ticket
        fields = [
            "subject",
            "description",
            "requester",
            "assignee",
            "category",
            "priority",
            "status",
            "resolution_summary",
        ]

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        self.fields["category"].queryset = Category.objects.filter(
            is_active=True
        ) | Category.objects.filter(pk=self.instance.category_id)
        self.fields["assignee"].queryset = User.objects.filter(
            is_active=True,
            role__in=[User.Role.SUPPORT_AGENT, User.Role.ADMINISTRATOR],
        )
        self.fields["assignee"].label_from_instance = lambda user: user.full_name
        if not actor.is_administrator:
            self.fields.pop("requester")
        else:
            self.fields["requester"].queryset = User.objects.filter(is_active=True)
            self.fields["requester"].label_from_instance = lambda user: (
                f"{user.full_name} · {user.email}"
            )
        for field_name in (
            "status",
            "category",
            "requester",
            "assignee",
            "priority",
            "resolution_summary",
        ):
            if field_name in self.fields:
                self.fields[field_name].widget.attrs["form"] = "ticket-update-form"

    def clean_description(self):
        return self.clean_rich_field("description")

    def clean_temporary_upload_ids(self):
        return self.clean_upload_ids()

    def clean(self):
        cleaned = super().clean()
        has_public_resolution_reply = bool(
            self.instance.pk
            and self.instance.messages.filter(
                visibility=TicketMessage.Visibility.PUBLIC,
                author__role__in=[
                    User.Role.SUPPORT_AGENT,
                    User.Role.ADMINISTRATOR,
                ],
            ).exists()
        )
        if (
            cleaned.get("status") == Ticket.Status.RESOLVED
            and not (cleaned.get("resolution_summary") or "").strip()
            and not has_public_resolution_reply
        ):
            self.add_error(
                "resolution_summary",
                _("Add a resolution summary before resolving this ticket."),
            )
        return cleaned


class MessageForm(RichTextMixin, forms.ModelForm):
    body = forms.CharField(widget=forms.HiddenInput())
    temporary_upload_ids = forms.CharField(required=False, widget=forms.HiddenInput())
    attachments = MultipleFileField(required=False)

    class Meta:
        model = TicketMessage
        fields = ["visibility", "body"]

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        if not actor.is_support:
            self.fields.pop("visibility")

    def clean_body(self):
        return self.clean_rich_field("body")

    def clean_temporary_upload_ids(self):
        return self.clean_upload_ids()


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name", "is_active"]
