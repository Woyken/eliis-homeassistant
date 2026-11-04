"""Fake Eliis clients with fictional, deterministic payloads."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, ClassVar

from custom_components.eliis.api.errors import EliisAuthError
from custom_components.eliis.api.models import LoginResult

from .fixtures import (
    CHILD_CALENDAR_PAYLOAD,
    CHILD_ID,
    EVENTS_PAYLOAD,
    FEED_PAYLOAD,
    INIT_PAYLOAD,
    INSTITUTION_ID,
    STATUS_LOG_PAYLOAD,
)


def _menu_payload(today: date) -> dict[str, Any]:
    """Return a weekly menu anchored to the real local date."""
    return {
        "days": [
            {
                "date": today.isoformat(),
                "breakfast": "Porridge",
                "lunch": "Soup",
                "supper": None,
                "dinner": None,
            },
            {
                "date": (today + timedelta(days=1)).isoformat(),
                "breakfast": "Bread",
                "lunch": "Pasta",
                "supper": "Fruit",
                "dinner": None,
            },
        ]
    }


class FakeEliisClient:
    """In-memory client used by config-flow and setup tests."""

    app_version = "2.69"

    login_requires_2fa = False
    login_error: Exception | None = None
    init_error: Exception | None = None
    init_payload: Any = INIT_PAYLOAD
    account_id: str | None = None
    probe_allowed: ClassVar[set[tuple[str, str]]] = set()
    menu_requests: ClassVar[list[tuple[str, str]]] = []
    events_requests: ClassVar[list[tuple[str, str]]] = []
    status_requests: ClassVar[list[tuple[str, str, str]]] = []
    feed_requests: ClassVar[list[tuple[str, str, str | None]]] = []
    calls: ClassVar[list[str]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the fake client."""
        self.cookie: str | None = kwargs.get("cookie")
        self.closed = False

    @classmethod
    def reset(cls) -> None:
        """Reset mutable class-level test state."""
        cls.login_requires_2fa = False
        cls.login_error = None
        cls.init_error = None
        cls.init_payload = INIT_PAYLOAD
        cls.account_id = None
        cls.probe_allowed = set()
        cls.menu_requests = []
        cls.events_requests = []
        cls.status_requests = []
        cls.feed_requests = []
        cls.calls = []

    async def async_close(self) -> None:
        """Close the fake client."""
        self.closed = True

    async def async_login(self, username: str, password: str) -> LoginResult:
        """Return a configured login outcome."""
        self.calls.append("login")
        if self.login_error is not None:
            raise self.login_error
        if self.login_requires_2fa:
            return LoginResult(None, True, "challenge-token")
        self.cookie = "eliis_csrf=session-cookie"
        return LoginResult(self.cookie, False, None)

    async def async_login_2fa(self, code: str, login_identifier: str) -> LoginResult:
        """Complete the two-factor challenge."""
        self.calls.append("login_2fa")
        if code != "123456":
            raise EliisAuthError("rejected")
        self.cookie = "eliis_csrf=session-cookie"
        return LoginResult(self.cookie, False, None)

    async def async_get_init(self) -> Any:
        """Return the discovery payload."""
        self.calls.append("init")
        if self.init_error is not None:
            raise self.init_error
        return self.init_payload

    async def async_probe_child(self, institution_id: str, child_id: str) -> bool:
        """Return the configured probe result."""
        return (institution_id, child_id) in self.probe_allowed

    async def async_get_week_menu(self, institution_id: str, day: str) -> Any:
        """Return a menu anchored to the requested week."""
        self.menu_requests.append((institution_id, day))
        return _menu_payload(date.fromisoformat(day))

    async def async_get_child_calendar(self, institution_id: str, child_id: str) -> Any:
        """Return the fixture child calendar."""
        return CHILD_CALENDAR_PAYLOAD

    async def async_get_guardian_feed(
        self,
        institution_id: str,
        child_id: str,
        cursor: str | None = None,
    ) -> Any:
        """Return the fixture guardian feed."""
        self.feed_requests.append((institution_id, child_id, cursor))
        return FEED_PAYLOAD

    async def async_get_status_log(
        self, institution_id: str, child_id: str, day: str
    ) -> Any:
        """Return the fixture status log."""
        self.status_requests.append((institution_id, child_id, day))
        return STATUS_LOG_PAYLOAD

    async def async_get_unseen_notification_count(self) -> Any:
        """Return a fixed unseen count."""
        return {"notifications_count": 3}

    async def async_get_institution_events(
        self, institution_id: str, date_from: str, date_to: str
    ) -> Any:
        """Return the fixture event list."""
        self.events_requests.append((institution_id, date_from, date_to))
        return EVENTS_PAYLOAD


__all__ = ["CHILD_ID", "INSTITUTION_ID", "FakeEliisClient"]
