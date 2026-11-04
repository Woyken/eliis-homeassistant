"""Small shared helpers for the Eliis integration."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, date, datetime
from typing import Any

from homeassistant.util import dt as dt_util

_WHITESPACE_RE = re.compile(r"[ \t\f\v]+")
_VERSION_RE = re.compile(r"[0-9]+(?:\.[0-9]+)*")


def opaque_id(*parts: object) -> str:
    """Return a stable, opaque identifier for registry use.

    Registry and unique IDs must not expose names, e-mail addresses or raw
    upstream identifiers. Hashing keeps them stable without making personal
    data part of an entity ID.
    """
    joined = "\x1f".join(str(part) for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:24]


def safe_version(value: str | None) -> str:
    """Return a conservative version string for headers and diagnostics."""
    if value and _VERSION_RE.fullmatch(value):
        return value
    return "2.69"


def scalar_text(value: Any) -> str | None:
    """Return a non-empty scalar as text without inventing structure."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (str, int, float)):
        text = str(value).strip()
        return text or None
    return None


def scalar_id(value: Any) -> str | None:
    """Return an opaque identifier as text, refusing containers and booleans."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (str, int)):
        text = str(value).strip()
        return text or None
    return None


def parse_date(value: Any) -> date | None:
    """Parse a source calendar date without inventing a timezone."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if "T" in text:
        text = text.split("T", 1)[0]
    elif " " in text:
        text = text.split(" ", 1)[0]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def parse_datetime(value: Any) -> datetime | None:
    """Parse a source timestamp and return an aware UTC datetime.

    Eliis' observed ordinary-event times are UTC client values. A value
    without an offset is therefore interpreted as UTC rather than as the HA
    machine's local zone. Never substitute "now" or midnight for unknown input.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    normalized = text.replace(" ", "T", 1)
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def combine_date_time(day: date | None, value: Any) -> datetime | None:
    """Combine a source date with a source time value as a UTC datetime."""
    parsed = parse_datetime(value)
    if parsed is not None:
        return parsed
    if day is None or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            clock = datetime.strptime(text, fmt).time()
        except ValueError:
            continue
        return datetime.combine(day, clock, tzinfo=UTC)
    return None


def collapse_whitespace(text: str) -> str:
    """Collapse horizontal whitespace while preserving line breaks."""
    lines = [_WHITESPACE_RE.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def as_local_date(value: datetime) -> date:
    """Return the HA-local date for an aware datetime."""
    return dt_util.as_local(value).date()


def utcnow() -> datetime:
    """Return the current UTC time."""
    return datetime.now(UTC)
