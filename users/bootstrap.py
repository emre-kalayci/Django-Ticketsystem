from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.db import OperationalError, ProgrammingError

logger = logging.getLogger(__name__)


def provision_default_users(**kwargs: Any) -> None:
    """Synchronize all accounts explicitly configured for the local demo."""
    provision_root_user()
    provision_demo_users()


def provision_root_user(**kwargs: Any) -> None:
    """Create/sync configured root user; tolerate pre-migration startup."""
    email = settings.ROOT_USER_EMAIL.strip()
    password = settings.ROOT_USER_PASSWORD
    if not email or not password:
        logger.warning(
            "Root user provisioning skipped: ROOT_USER_EMAIL and "
            "ROOT_USER_PASSWORD must both be configured."
        )
        return

    try:
        from users.models import User

        normalized_email = User.objects.normalize_email(email).lower()
        user = User.objects.filter(is_env_root=True).first()
        if user is None:
            user, _ = User.objects.get_or_create(
                email=normalized_email,
                defaults={
                    "first_name": "Demo",
                    "last_name": "Administrator",
                    "is_staff": True,
                    "is_superuser": True,
                    "is_active": True,
                    "is_env_root": True,
                    "role": User.Role.ADMINISTRATOR,
                },
            )
            user.is_env_root = True
        elif User.objects.filter(email=normalized_email).exclude(pk=user.pk).exists():
            logger.error(
                "Root user synchronization skipped: configured email belongs to "
                "another account."
            )
            return
        else:
            user.email = normalized_email
    except (OperationalError, ProgrammingError):
        return

    update_fields = [
        "email",
        "first_name",
        "last_name",
        "is_staff",
        "is_superuser",
        "is_active",
        "is_env_root",
        "role",
    ]
    if not user.check_password(password):
        user.set_password(password)
        update_fields.append("password")
    user.is_staff = True
    user.is_superuser = True
    user.is_active = True
    user.is_env_root = True
    user.role = User.Role.ADMINISTRATOR
    user.first_name = "Demo"
    user.last_name = "Administrator"
    user.save(update_fields=update_fields)


def provision_demo_users(**kwargs: Any) -> None:
    """Create or synchronize the optional support and requester demo users."""
    try:
        from users.models import User
    except (OperationalError, ProgrammingError):
        return

    configured_users = (
        (
            "Support Agent",
            settings.SUPPORT_USER_EMAIL.strip(),
            settings.SUPPORT_USER_PASSWORD,
            User.Role.SUPPORT_AGENT,
            "Demo",
            "Support",
        ),
        (
            "Requester",
            settings.REQUESTER_USER_EMAIL.strip(),
            settings.REQUESTER_USER_PASSWORD,
            User.Role.REQUESTER,
            "Demo",
            "Requester",
        ),
    )
    for label, email, password, role, first_name, last_name in configured_users:
        if not email and not password:
            continue
        if not email or not password:
            logger.warning(
                "%s provisioning skipped: email and password must both be configured.",
                label,
            )
            continue
        _provision_demo_user(
            label=label,
            email=email,
            password=password,
            role=role,
            first_name=first_name,
            last_name=last_name,
        )


def _provision_demo_user(
    *,
    label: str,
    email: str,
    password: str,
    role: str,
    first_name: str,
    last_name: str,
) -> None:
    from users.models import User

    normalized_email = User.objects.normalize_email(email).lower()
    try:
        user, created = User.objects.get_or_create(
            email=normalized_email,
            defaults={
                "first_name": first_name,
                "last_name": last_name,
                "role": role,
                "is_active": True,
            },
        )
    except (OperationalError, ProgrammingError):
        return
    if user.is_env_root:
        logger.error(
            "%s provisioning skipped: configured email belongs to the "
            "environment root.",
            label,
        )
        return

    update_fields = ["role", "is_active"]
    if created:
        update_fields.extend(["first_name", "last_name"])
    if not user.check_password(password):
        user.set_password(password)
        update_fields.append("password")
    user.role = role
    user.is_active = True
    user.save(update_fields=update_fields)
