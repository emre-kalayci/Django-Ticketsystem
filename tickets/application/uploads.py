from __future__ import annotations

from pathlib import Path

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

ALLOWED_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "text/plain",
}
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


def validate_uploaded_file(uploaded_file) -> tuple[str, str]:
    """Validate actual file signatures/content, never client MIME alone."""
    if uploaded_file.size > MAX_ATTACHMENT_BYTES:
        raise ValidationError(_("File must be 10 MB or smaller."))
    if uploaded_file.size == 0:
        raise ValidationError(_("File must not be empty."))

    start = uploaded_file.read(8192)
    uploaded_file.seek(0)
    mime_type = detect_mime_type(start, uploaded_file)
    if mime_type not in ALLOWED_MIME_TYPES:
        raise ValidationError(
            _("Only PDF, PNG, JPEG, and UTF-8 text files are allowed.")
        )
    return mime_type, Path(uploaded_file.name).name[:255]


def detect_mime_type(start: bytes, uploaded_file) -> str | None:
    if start.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if start.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if start.startswith(b"%PDF-"):
        return "application/pdf"
    try:
        uploaded_file.seek(0)
        text = uploaded_file.read().decode("utf-8")
        if "\x00" in text:
            return None
        disallowed_controls = sum(
            1 for character in text if ord(character) < 32 and character not in "\n\r\t"
        )
        if text and disallowed_controls / len(text) > 0.01:
            return None
    except UnicodeDecodeError:
        return None
    finally:
        uploaded_file.seek(0)
    return "text/plain"
