"""Defensive parsers for Eliis payloads.

The upstream API has many partial, sample-only shapes. These parsers accept
only fields with evidence, distinguish missing/null/malformed values and never
invent presence, publication, cancellation or recurrence semantics.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from typing import Any

from ..const import MAX_ACTION_ITEMS, MAX_MENU_MEALS, MAX_MENU_TEXT_CHARS
from ..util import (
    collapse_whitespace,
    combine_date_time,
    opaque_id,
    parse_date,
    parse_datetime,
    scalar_id,
    scalar_text,
    utcnow,
)
from .errors import EliisSchemaError
from .models import (
    ChildDayStatus,
    DiaryRecord,
    EventRecord,
    EventsResult,
    FeedGroup,
    FeedPage,
    MealEntry,
    MenuDay,
    MenuResult,
    NotificationCount,
    StatusHistory,
    StatusRecord,
)

MEAL_KEYS: tuple[tuple[str, str], ...] = (
    ("breakfast", "Breakfast"),
    ("lunch", "Lunch"),
    ("supper", "Supper"),
    ("dinner", "Dinner"),
)

_BLOCK_TAGS = {
    "br",
    "div",
    "li",
    "p",
    "tr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
}


class _PlainTextExtractor(HTMLParser):
    """Convert benign HTML to plain text without rendering anything."""

    def __init__(self) -> None:
        """Initialize the extractor."""
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_data(self, data: str) -> None:
        """Collect text nodes outside script and style blocks."""
        if self._skip_depth:
            return
        self._parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Add readable breaks for block tags."""
        if tag in {"script", "style"}:
            self._skip_depth += 1
            return
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        """Add readable breaks for block tags."""
        if tag in {"script", "style"}:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def text(self) -> str:
        """Return sanitized plain text."""
        return "".join(self._parts)


def sanitize_text(value: Any, *, max_length: int = 2_000) -> str | None:
    """Return safe plain text or ``None`` when no text is present.

    Markup is stripped rather than passed to a dashboard. Line breaks are kept
    so menus and descriptions stay readable. The result is always bounded.
    """
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    text = str(value)
    if "<" in text or "&" in text:
        parser = _PlainTextExtractor()
        try:
            parser.feed(text)
            parser.close()
        except Exception:
            parser = _PlainTextExtractor()
        text = parser.text()
    text = collapse_whitespace(text)
    if not text:
        return None
    if len(text) > max_length:
        text = text[:max_length].rstrip()
    return text


def parse_notification_count(payload: Any) -> NotificationCount:
    """Parse an unseen notification count without coercing invalid values."""
    if not isinstance(payload, dict):
        return NotificationCount(None, malformed=True)
    value = payload.get("notifications_count")
    if isinstance(value, bool) or value is None:
        return NotificationCount(None, malformed=True)
    if isinstance(value, int):
        return NotificationCount(value if value >= 0 else None, value < 0)
    if isinstance(value, float):
        if value.is_integer() and value >= 0:
            return NotificationCount(int(value), False)
        return NotificationCount(None, malformed=True)
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return NotificationCount(int(text), False)
    return NotificationCount(None, malformed=True)


def _parse_meals(day_item: dict[str, Any]) -> tuple[tuple[MealEntry, ...], bool, bool]:
    """Return meals, malformed-leaf flag and truncation flag."""
    meals: list[MealEntry] = []
    malformed = False
    truncated = False
    total_chars = 0

    for key, label in MEAL_KEYS:
        if key not in day_item:
            continue
        value = day_item[key]
        if value is None:
            continue
        if not isinstance(value, str):
            malformed = True
            continue
        text = sanitize_text(value, max_length=MAX_MENU_TEXT_CHARS)
        if text is None:
            continue

        remaining = MAX_MENU_TEXT_CHARS - total_chars
        if remaining <= 0:
            truncated = True
            break
        if len(text) > remaining:
            text = text[:remaining].rstrip()
            truncated = True
        if not text:
            break

        meals.append(MealEntry(key=key, label=label, text=text))
        total_chars += len(text)
        if len(meals) >= MAX_MENU_MEALS:
            truncated = True
            break

    return tuple(meals), malformed, truncated


def parse_menu(payload: Any) -> MenuResult:
    """Parse a weekly menu payload into bounded source-date entries."""
    if not isinstance(payload, dict):
        raise EliisSchemaError("Menu response was not an object")

    raw_days = payload.get("days")
    if not isinstance(raw_days, list):
        return MenuResult((), malformed=True)

    by_date: dict[date, MenuDay] = {}
    malformed = False

    for item in raw_days:
        if not isinstance(item, dict):
            malformed = True
            continue
        day = parse_date(item.get("date"))
        if day is None:
            malformed = True
            continue
        meals, meal_malformed, truncated = _parse_meals(item)
        malformed = malformed or meal_malformed
        existing = by_date.get(day)
        if existing is None:
            by_date[day] = MenuDay(day, meals, truncated)
        else:
            merged = (*existing.meals, *meals)[:MAX_MENU_MEALS]
            by_date[day] = MenuDay(
                day,
                merged,
                existing.truncated
                or truncated
                or len(existing.meals) + len(meals) > MAX_MENU_MEALS,
            )

    return MenuResult(
        tuple(sorted(by_date.values(), key=lambda entry: entry.date)),
        malformed=malformed,
    )


def menu_day_for_date(result: MenuResult, target: date) -> MenuDay | None:
    """Return the explicit entry for a date, never a default or inferred day."""
    for day in result.days:
        if day.date == target:
            return day
    return None


def _diary_status(item: dict[str, Any]) -> str | None:
    for key in ("status", "diary_status"):
        status = item.get(key)
        if isinstance(status, dict):
            name = sanitize_text(status.get("name"), max_length=120)
            if name:
                return name
    return None


def parse_feed(payload: Any, *, fetched_at: datetime | None = None) -> FeedPage:
    """Parse one guardian-feed page.

    Grouping is preserved. Only records with an opaque identifier are
    considered valid diary records; evaluations-only groups do not become
    diary entries.
    """
    if not isinstance(payload, dict):
        raise EliisSchemaError("Guardian feed response was not an object")

    raw_groups = payload.get("data")
    if not isinstance(raw_groups, list):
        return FeedPage((), None, malformed=True, fetched_at=fetched_at)

    groups: list[FeedGroup] = []
    malformed = False

    for group_item in raw_groups:
        if not isinstance(group_item, dict):
            malformed = True
            continue
        group_date = parse_date(group_item.get("date"))
        raw_diaries = group_item.get("diaries")
        if group_date is None or not isinstance(raw_diaries, list):
            malformed = True
            continue

        diaries: list[DiaryRecord] = []
        for item in raw_diaries:
            if not isinstance(item, dict):
                malformed = True
                continue
            diary_id = scalar_id(item.get("id"))
            if diary_id is None:
                malformed = True
                continue
            diaries.append(
                DiaryRecord(
                    id=diary_id,
                    date=group_date,
                    course=scalar_text(item.get("course")),
                    status=_diary_status(item),
                    total_time=scalar_text(item.get("total_time")),
                )
            )
        groups.append(FeedGroup(group_date, tuple(diaries)))

    next_date = scalar_text(payload.get("next_date"))
    return FeedPage(
        tuple(groups),
        next_date,
        malformed=malformed,
        fetched_at=fetched_at,
    )


def latest_feed_summary(page: FeedPage) -> tuple[date | None, int | None]:
    """Return the newest fetched diary date and its valid record count.

    A malformed page yields ``(None, None)`` rather than a fabricated zero.
    """
    if page.malformed:
        return None, None

    by_id: dict[str, DiaryRecord] = {}
    for record in page.diary_records:
        by_id.setdefault(record.id, record)
    if not by_id:
        return None, 0

    latest = max(record.date for record in by_id.values())
    count = sum(1 for record in by_id.values() if record.date == latest)
    return latest, count


def parse_child_day(payload: Any, target: date) -> ChildDayStatus | None:
    """Return the explicit child-calendar status for one date.

    Default status and weekday rules are ignored. Multiple courses with
    different labels are reported as a multi-label record instead of picking
    one arbitrarily.
    """
    if not isinstance(payload, dict):
        raise EliisSchemaError("Child calendar response was not an object")

    raw_days = payload.get("days")
    if not isinstance(raw_days, list):
        return None

    labels: list[str] = []
    course_count = 0
    for item in raw_days:
        if not isinstance(item, dict):
            continue
        if parse_date(item.get("date")) != target:
            continue
        entry = item.get("entry")
        if not isinstance(entry, dict):
            continue
        course_count += 1
        status = entry.get("diary_status")
        if not isinstance(status, dict):
            continue
        label = sanitize_text(status.get("name"), max_length=120)
        if label and label not in labels:
            labels.append(label)

    if not labels:
        return None
    return ChildDayStatus(target, tuple(labels), course_count)


def parse_status_history(payload: Any) -> StatusHistory:
    """Parse status-log records from the tolerant known envelope."""
    if isinstance(payload, list):
        raw_records = payload
    elif isinstance(payload, dict):
        raw_records = None
        for key in ("logs", "data", "status_log", "status_logs"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                raw_records = candidate
                break
        if raw_records is None:
            return StatusHistory((), malformed=True)
    else:
        raise EliisSchemaError("Status log response had an unsupported shape")

    records: list[StatusRecord] = []
    malformed = False
    for item in raw_records[:MAX_ACTION_ITEMS]:
        if not isinstance(item, dict):
            malformed = True
            continue
        label = None
        for key in ("diary_status", "status"):
            status = item.get(key)
            if isinstance(status, dict):
                label = sanitize_text(status.get("name"), max_length=120)
                if label:
                    break
        if label is None:
            label = sanitize_text(item.get("name") or item.get("label"), max_length=120)
        created_at = None
        for key in ("created_at", "datetime", "date", "time"):
            created_at = parse_datetime(item.get(key))
            if created_at is not None:
                break
        total_time = None
        for key in ("total_time", "hours"):
            total_time = scalar_text(item.get(key))
            if total_time is not None:
                break
        if label is None and created_at is None:
            malformed = True
            continue
        records.append(
            StatusRecord(
                label=label or "unknown",
                created_at=created_at,
                total_time=total_time,
            )
        )

    if len(raw_records) > MAX_ACTION_ITEMS:
        malformed = True
    return StatusHistory(tuple(records), malformed=malformed)


def _event_is_representable(item: dict[str, Any]) -> bool:
    """Return whether an event is explicitly non-recurring."""
    if item.get("is_recurring") is not False:
        return False
    if item.get("repeat_start"):
        return False
    return not item.get("rrule")


def _event_all_day(item: dict[str, Any]) -> tuple[bool, bool]:
    """Return (all_day, classified)."""
    if "start_time" in item and item["start_time"] is None:
        return True, True
    event_type = item.get("event_type")
    if type(event_type) is int and event_type == 0:
        return True, True
    if "start_time" not in item:
        return False, False
    return False, True


def parse_events(
    payload: Any,
    *,
    institution_id: str,
    fallback_summary: str,
    fetched_at: datetime | None = None,
) -> EventsResult:
    """Normalize institution events with a conservative partial policy.

    Recurring, ambiguous, cancellation-sensitive or unclassifiable records are
    omitted and counted. Callers surface the omission instead of presenting a
    complete-looking calendar.
    """
    if not isinstance(payload, list):
        raise EliisSchemaError("Events response was not an array")

    events: list[EventRecord] = []
    omitted = 0
    scope = opaque_id(institution_id)

    for item in payload:
        if not isinstance(item, dict):
            omitted += 1
            continue
        if not _event_is_representable(item):
            omitted += 1
            continue

        all_day, classified = _event_all_day(item)
        if not classified:
            omitted += 1
            continue

        start_date = parse_date(item.get("start_date"))
        summary = sanitize_text(item.get("title"), max_length=300) or fallback_summary

        if all_day:
            if start_date is None:
                omitted += 1
                continue
            if "end_date" not in item:
                end_date = start_date + timedelta(days=1)
            else:
                raw_end = item.get("end_date")
                parsed_end = parse_date(raw_end)
                if parsed_end is None:
                    omitted += 1
                    continue
                # Upstream all-day end dates are treated as inclusive under an
                # app-export-compatible policy.
                end_date = parsed_end + timedelta(days=1)
            if end_date <= start_date:
                omitted += 1
                continue
            start: datetime | date = start_date
            end: datetime | date = end_date
        else:
            start = combine_date_time(start_date, item.get("start_time"))
            end_day = parse_date(item.get("end_date")) or start_date
            end = combine_date_time(end_day, item.get("end_time"))
            if (
                start is None
                or end is None
                or not isinstance(start, datetime)
                or not isinstance(end, datetime)
            ):
                omitted += 1
                continue
            if end <= start:
                omitted += 1
                continue

        raw_uid = scalar_id(item.get("unique_id")) or scalar_id(item.get("id"))
        uid = f"{scope}:{raw_uid}" if raw_uid is not None else None
        description = sanitize_text(item.get("description"), max_length=1_000)
        events.append(
            EventRecord(
                summary=summary,
                start=start,
                end=end,
                all_day=all_day,
                uid=uid,
                description=description,
            )
        )

    return EventsResult(
        events=tuple(events),
        omitted_count=omitted,
        malformed=omitted > 0,
        fetched_at=fetched_at or utcnow(),
    )
