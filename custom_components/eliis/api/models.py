"""Normalized domain models for the read-only Eliis integration.

Every field is optional unless the parser can prove it. The models keep
"missing", "explicit null" and "malformed" distinguishable by carrying
validity flags rather than substituting defaults.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class LoginResult:
    """Outcome of a password login attempt."""

    cookie: str | None
    requires_2fa: bool
    login_identifier: str | None = None


@dataclass(frozen=True, slots=True)
class Institution:
    """An institution visible to the signed-in account."""

    id: str
    name: str | None = None


@dataclass(frozen=True, slots=True)
class Child:
    """A child visible to the signed-in account."""

    id: str
    name: str | None = None
    institution_id: str | None = None


@dataclass(frozen=True, slots=True)
class Discovery:
    """Account discovery result."""

    institutions: tuple[Institution, ...]
    children: tuple[Child, ...]
    account_id: str
    malformed: bool = False


@dataclass(frozen=True, slots=True)
class MealEntry:
    """A single, bounded menu text entry."""

    key: str
    label: str
    text: str


@dataclass(frozen=True, slots=True)
class MenuDay:
    """Meals returned for one source date."""

    date: date
    meals: tuple[MealEntry, ...]
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class MenuResult:
    """Parsed weekly-menu payload."""

    days: tuple[MenuDay, ...]
    malformed: bool = False


@dataclass(frozen=True, slots=True)
class DiaryRecord:
    """A single diary record from the guardian feed."""

    id: str
    date: date
    course: str | None = None
    status: str | None = None
    total_time: str | None = None


@dataclass(frozen=True, slots=True)
class FeedGroup:
    """A date group in the guardian feed."""

    date: date
    diaries: tuple[DiaryRecord, ...]


@dataclass(frozen=True, slots=True)
class FeedPage:
    """One bounded guardian-feed page."""

    groups: tuple[FeedGroup, ...]
    next_date: str | None
    malformed: bool = False
    fetched_at: datetime | None = None

    @property
    def diary_records(self) -> tuple[DiaryRecord, ...]:
        """Return all diary records in this page."""
        return tuple(record for group in self.groups for record in group.diaries)


@dataclass(frozen=True, slots=True)
class ChildDayStatus:
    """Neutral status label for an explicit child-calendar day record."""

    date: date
    labels: tuple[str, ...]
    course_count: int


@dataclass(frozen=True, slots=True)
class StatusRecord:
    """A status-log record with an optional reported duration."""

    label: str
    created_at: datetime | None = None
    total_time: str | None = None


@dataclass(frozen=True, slots=True)
class StatusHistory:
    """Parsed status-log payload."""

    records: tuple[StatusRecord, ...]
    malformed: bool = False


@dataclass(frozen=True, slots=True)
class EventRecord:
    """A normalized, non-recurring institution event."""

    summary: str
    start: datetime | date
    end: datetime | date
    all_day: bool
    uid: str | None = None
    description: str | None = None


@dataclass(frozen=True, slots=True)
class EventsResult:
    """Events from one bounded source range."""

    events: tuple[EventRecord, ...] = ()
    omitted_count: int = 0
    malformed: bool = False
    fetched_at: datetime | None = None

    @property
    def complete(self) -> bool:
        """Return whether every returned record was representable."""
        return self.omitted_count == 0 and not self.malformed


@dataclass(frozen=True, slots=True)
class NotificationCount:
    """Unseen notification count result."""

    count: int | None
    malformed: bool = False


@dataclass(slots=True)
class AccountData:
    """Account-level coordinator data."""

    notification_count: int | None
    notifications_ok: bool
    fetched_at: datetime


@dataclass(slots=True)
class MenuData:
    """Institution menu coordinator data."""

    days: dict[date, MenuDay] = field(default_factory=dict)
    complete: bool = False
    malformed: bool = False
    fetched_at: datetime | None = None


@dataclass(slots=True)
class ChildData:
    """Child calendar/feed coordinator data."""

    day_status: ChildDayStatus | None = None
    status_ok: bool = False
    latest_diary_date: date | None = None
    diary_count: int | None = None
    feed_ok: bool = False
    fetched_at: datetime | None = None


@dataclass(slots=True)
class EventsData:
    """Institution events coordinator data."""

    events: tuple[EventRecord, ...] = ()
    omitted_count: int = 0
    complete: bool = False
    fetched_at: datetime | None = None
