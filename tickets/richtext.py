from __future__ import annotations

import re
from html import unescape

import bleach
from django.utils.html import strip_tags

LOCAL_IMAGE_URL = re.compile(
    r"^/(?:uploads/\d+/file|attachments/\d+/file)/?$",
)

ALLOWED_TAGS = {
    "p",
    "br",
    "h2",
    "h3",
    "strong",
    "em",
    "a",
    "ul",
    "ol",
    "li",
    "blockquote",
    "img",
}


def allowed_attribute(tag: str, name: str, value: str) -> bool:
    if tag == "a":
        return name in {"href", "title"}
    if tag == "img":
        return name in {"alt", "title"} or (
            name == "src" and bool(LOCAL_IMAGE_URL.fullmatch(value))
        )
    if tag == "ul":
        return name == "data-type" and value == "taskList"
    if tag == "li":
        return name == "data-checked" and value in {"true", "false"}
    return False


def sanitize_rich_text(value: str) -> str:
    return bleach.clean(
        value or "",
        tags=ALLOWED_TAGS,
        attributes=allowed_attribute,
        protocols={"http", "https", "mailto"},
        strip=True,
        strip_comments=True,
    ).strip()


def rich_text_is_empty(value: str) -> bool:
    text = unescape(strip_tags(value or "")).replace("\xa0", " ").strip()
    return not text and "<img" not in (value or "").lower()
