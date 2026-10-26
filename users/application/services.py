from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.exceptions import ValidationError
from django.db import OperationalError, transaction
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.utils.translation import gettext as _

from users.emailing import send_email
from users.models import UserInvitation


def validate_avatar(avatar) -> str:
    if avatar.size > 2 * 1024 * 1024:
        raise ValidationError(_("Avatar must be 2 MB or smaller."))
    header = avatar.read(16)
    avatar.seek(0)
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if header.startswith(b"\xff\xd8\xff"):
        return "jpg"
    raise ValidationError(_("Avatar must be a PNG or JPEG image."))


class UserService:
    def __init__(self) -> None:
        self.user_model = get_user_model()

    def update_profile(self, user, cleaned_data: dict):
        avatar = cleaned_data.pop("avatar", None)
        remove_avatar = cleaned_data.pop("remove_avatar", False)
        password = cleaned_data.pop("new_password", "")
        for field in ("first_name", "last_name", "email", "language"):
            if field in cleaned_data:
                setattr(user, field, cleaned_data[field])
        if remove_avatar:
            if user.avatar:
                user.avatar.delete(save=False)
            user.avatar = None
        elif avatar and avatar != user.avatar:
            extension = validate_avatar(avatar)
            avatar.name = f"{uuid.uuid4().hex}.{extension}"
            if user.avatar:
                user.avatar.delete(save=False)
            user.avatar = avatar
        if password:
            validate_password(password, user=user)
            user.set_password(password)
        user.save()
        return user

    def update_user_access(
        self,
        actor,
        *,
        target_id: int,
        role: str,
        is_active: bool,
    ):
        for attempt in range(8):
            try:
                return self._update_user_access_once(
                    actor,
                    target_id=target_id,
                    role=role,
                    is_active=is_active,
                )
            except OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 7:
                    raise
                time.sleep(min(0.04 * (attempt + 1), 0.25))
        raise RuntimeError("User access update retry loop exhausted.")

    @transaction.atomic
    def _update_user_access_once(
        self,
        actor,
        *,
        target_id: int,
        role: str,
        is_active: bool,
    ):
        from tickets.models import Ticket, TicketEvent

        target = self.user_model.objects.select_for_update().get(pk=target_id)
        active_administrators = list(
            self.user_model.objects.select_for_update().filter(
                role=self.user_model.Role.ADMINISTRATOR,
                is_active=True,
            )
        )
        if target.is_env_root and (
            not is_active or role != self.user_model.Role.ADMINISTRATOR
        ):
            raise ValidationError(
                _("Environment root cannot be demoted or deactivated.")
            )
        removes_administrator = target.is_administrator and (
            not is_active or role != self.user_model.Role.ADMINISTRATOR
        )
        if removes_administrator and not any(
            administrator.pk != target.pk for administrator in active_administrators
        ):
            raise ValidationError(_("At least one active Administrator is required."))

        removes_support_access = target.is_support and (
            not is_active or role == self.user_model.Role.REQUESTER
        )
        target.role = role
        target.is_active = is_active
        target.save(update_fields=["role", "is_active"])

        if removes_support_access:
            assigned_tickets = list(
                Ticket.objects.select_for_update().filter(assignee=target)
            )
            for ticket in assigned_tickets:
                ticket.assignee = None
                ticket.save(update_fields=["assignee", "updated_at"])
                TicketEvent.objects.create(
                    ticket=ticket,
                    actor=actor,
                    event_type=TicketEvent.Type.ASSIGNED,
                    visibility=TicketEvent.Visibility.INTERNAL,
                    data={
                        "field": "assignee_id",
                        "old": target.pk,
                        "new": None,
                        "reason": "support access removed",
                    },
                )
        return target

    def request_password_reset(self, email: str) -> None:
        user = self.user_model.objects.filter(
            email__iexact=email,
            is_active=True,
        ).first()
        if user is None:
            return
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = PasswordResetTokenGenerator().make_token(user)
        url = f"{settings.SITE_URL}/reset-password/{uid}/{token}/"
        send_email(
            "Reset your support portal password",
            f"Use this link within 24 hours:\n\n{url}",
            [user.email],
        )

    def password_reset_user(self, uid: str, token: str):
        try:
            user_id = force_str(urlsafe_base64_decode(uid))
            user = self.user_model.objects.get(pk=user_id, is_active=True)
        except (
            TypeError,
            ValueError,
            OverflowError,
            self.user_model.DoesNotExist,
        ):
            return None
        if not PasswordResetTokenGenerator().check_token(user, token):
            return None
        return user

    def set_reset_password(self, user, password: str) -> None:
        validate_password(password, user=user)
        user.set_password(password)
        user.save(update_fields=["password"])

    @transaction.atomic
    def invite(self, actor, *, email: str, role: str) -> str:
        normalized = self.user_model.objects.normalize_email(email).lower()
        raw_token = secrets.token_urlsafe(32)
        invitation = (
            UserInvitation.objects.select_for_update().filter(email=normalized).first()
        )
        if invitation is None:
            invitation = UserInvitation(email=normalized)
        invitation.role = role
        invitation.invited_by = actor
        invitation.token_hash = self._token_hash(raw_token)
        invitation.expires_at = timezone.now() + timedelta(days=7)
        invitation.revoked_at = None
        invitation.accepted_at = None
        invitation.save()
        self._send_invitation(invitation, raw_token)
        return raw_token

    @transaction.atomic
    def accept_existing(self, invitation: UserInvitation, user) -> None:
        invitation = UserInvitation.objects.select_for_update().get(pk=invitation.pk)
        if not invitation.is_pending or user.email.lower() != invitation.email.lower():
            raise ValidationError(
                _("This invitation does not match the signed-in account.")
            )
        user.role = invitation.role
        user.save(update_fields=["role"])
        invitation.accepted_at = timezone.now()
        invitation.save(update_fields=["accepted_at"])

    def get_invitation(self, raw_token: str) -> UserInvitation | None:
        invitation = UserInvitation.objects.filter(
            token_hash=self._token_hash(raw_token)
        ).first()
        if invitation is None or not invitation.is_pending:
            return None
        return invitation

    @transaction.atomic
    def signup(self, invitation: UserInvitation, cleaned_data: dict):
        invitation = UserInvitation.objects.select_for_update().get(pk=invitation.pk)
        if not invitation.is_pending:
            raise ValidationError(_("This invitation is no longer valid."))
        if self.user_model.objects.filter(email__iexact=invitation.email).exists():
            raise ValidationError(_("An account with this email already exists."))
        user = self.user_model.objects.create_user(
            email=invitation.email,
            first_name=cleaned_data["first_name"],
            last_name=cleaned_data["last_name"],
            password=cleaned_data["password"],
            role=invitation.role,
        )
        invitation.accepted_at = timezone.now()
        invitation.save(update_fields=["accepted_at"])
        return user

    @transaction.atomic
    def resend(self, actor, invitation: UserInvitation) -> str:
        raw_token = secrets.token_urlsafe(32)
        invitation.invited_by = actor
        invitation.token_hash = self._token_hash(raw_token)
        invitation.expires_at = timezone.now() + timedelta(days=7)
        invitation.revoked_at = None
        invitation.accepted_at = None
        invitation.save()
        self._send_invitation(invitation, raw_token)
        return raw_token

    @staticmethod
    def revoke(invitation: UserInvitation) -> None:
        invitation.revoked_at = timezone.now()
        invitation.save(update_fields=["revoked_at"])

    @staticmethod
    def _token_hash(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode()).hexdigest()

    @staticmethod
    def _send_invitation(invitation: UserInvitation, raw_token: str) -> None:
        url = f"{settings.SITE_URL}/invite/{raw_token}/"
        send_email(
            "You are invited to the support portal",
            f"Create your account within 7 days:\n\n{url}",
            [invitation.email],
        )
