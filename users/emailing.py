from __future__ import annotations

import json
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.mail import send_mail


def send_email(subject: str, body: str, recipients: list[str]) -> None:
    """Send separate messages so recipients never learn each other's addresses."""
    for recipient in dict.fromkeys(recipients):
        if settings.RESEND_API_KEY:
            _send_with_resend(subject, body, recipient)
        else:
            send_mail(
                subject,
                body,
                settings.DEFAULT_FROM_EMAIL,
                [recipient],
                fail_silently=False,
            )


def _send_with_resend(subject: str, body: str, recipient: str) -> None:
    payload = json.dumps(
        {
            "from": settings.RESEND_FROM_EMAIL,
            "to": [recipient],
            "subject": subject,
            "text": body,
        }
    ).encode()
    request = Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {settings.RESEND_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "Support-Portal/1.0",
        },
        method="POST",
    )
    with urlopen(request, timeout=15) as response:  # noqa: S310 - fixed HTTPS host
        if not 200 <= response.status < 300:
            raise RuntimeError(f"Resend returned HTTP {response.status}.")
