"""Data coordinators for the Eliis integration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, NoReturn, TypeVar

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    EliisAuthError,
    EliisClient,
    EliisConnectionError,
    EliisError,
    EliisPermissionError,
    EliisRateLimitError,
    EliisResponseError,
    EliisSchemaError,
)
from .api.models import (
    AccountData,
    Child,
    ChildData,
    EventsData,
    EventsResult,
    Institution,
    MenuData,
)
from .api.parsers import (
    latest_feed_summary,
    parse_child_day,
    parse_events,
    parse_feed,
    parse_menu,
    parse_notification_count,
)
from .const import (
    CALENDAR_CACHE_TTL,
    CALENDAR_HORIZON,
    CALENDAR_MAX_SLICES,
    CALENDAR_SLICE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EVENTS_INTERVAL,
    MENU_INTERVAL,
    MODULE_EVENTS,
    PARTIAL_CALENDAR_ISSUE_ID,
)
from .util import utcnow

_LOGGER = logging.getLogger(__name__)

_T = TypeVar("_T")

EVENT_FALLBACK_SUMMARY = "Eliis event"


def _raise_update_failure(err: EliisError) -> NoReturn:
    """Map a client error to an isolated coordinator failure.

    Only a confirmed authentication rejection is entry-wide. Permission,
    schema and transport failures stay inside the affected optional module.
    """
    if isinstance(err, EliisAuthError):
        raise ConfigEntryAuthFailed("Eliis session is no longer valid") from err
    if isinstance(err, EliisRateLimitError):
        raise UpdateFailed("Eliis rate limited the request") from err
    if isinstance(err, EliisConnectionError):
        raise UpdateFailed("Could not reach the Eliis service") from err
    if isinstance(err, EliisPermissionError):
        raise UpdateFailed("Eliis denied access to this resource") from err
    if isinstance(err, (EliisSchemaError, EliisResponseError)):
        raise UpdateFailed("Eliis returned an unsupported response") from err
    raise UpdateFailed("Eliis request failed") from err


class BaseEliisCoordinator(DataUpdateCoordinator[_T]):
    """Base coordinator with a stable, non-private name."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        *,
        name: str,
        update_interval: timedelta,
        client: EliisClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {name}",
            update_interval=update_interval,
            always_update=False,
        )
        self.client = client
        self.config_entry = entry


class AccountCoordinator(BaseEliisCoordinator[AccountData]):
    """Poll account-level summaries."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: EliisClient
    ) -> None:
        """Initialize the account coordinator."""
        super().__init__(
            hass,
            entry,
            name="account",
            update_interval=DEFAULT_SCAN_INTERVAL,
            client=client,
        )

    async def _async_update_data(self) -> AccountData:
        """Fetch the unseen notification count."""
        try:
            payload = await self.client.async_get_unseen_notification_count()
        except EliisError as err:
            _raise_update_failure(err)

        parsed = parse_notification_count(payload)
        if parsed.malformed or parsed.count is None:
            raise UpdateFailed("Eliis returned an invalid notification count")
        return AccountData(
            notification_count=parsed.count,
            notifications_ok=True,
            fetched_at=utcnow(),
        )


class MenuCoordinator(BaseEliisCoordinator[MenuData]):
    """Poll one institution's weekly menu."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: EliisClient,
        institution: Institution,
    ) -> None:
        """Initialize the menu coordinator."""
        super().__init__(
            hass,
            entry,
            name="menu",
            update_interval=MENU_INTERVAL,
            client=client,
        )
        self.institution = institution

    async def _async_update_data(self) -> MenuData:
        """Fetch the weeks needed for today and tomorrow."""
        today = dt_util.now().date()
        tomorrow = today + timedelta(days=1)
        anchors = sorted(
            {
                today - timedelta(days=today.weekday()),
                tomorrow - timedelta(days=tomorrow.weekday()),
            }
        )

        days: dict[date, Any] = {}
        malformed = False
        failures = 0

        for anchor in anchors:
            try:
                payload = await self.client.async_get_week_menu(
                    self.institution.id, anchor.isoformat()
                )
            except EliisAuthError as err:
                raise ConfigEntryAuthFailed("Eliis session is no longer valid") from err
            except EliisError as err:
                failures += 1
                _LOGGER.debug(
                    "Menu week fetch failed for one anchor (%s)",
                    type(err).__name__,
                )
                continue

            try:
                result = parse_menu(payload)
            except EliisSchemaError as err:
                failures += 1
                _LOGGER.debug("Menu week parse failed (%s)", type(err).__name__)
                continue
            malformed = malformed or result.malformed
            for day in result.days:
                days.setdefault(day.date, day)

        if failures == len(anchors):
            raise UpdateFailed("Could not fetch the Eliis weekly menu")

        return MenuData(
            days=days,
            complete=failures == 0 and not malformed,
            malformed=malformed,
            fetched_at=utcnow(),
        )

    def day_for(self, target: date) -> Any:
        """Return today's/tomorrow's explicit menu entry."""
        if self.data is None:
            return None
        return self.data.days.get(target)


class ChildCoordinator(BaseEliisCoordinator[ChildData]):
    """Poll one child's calendar and guardian-feed summaries."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: EliisClient,
        child: Child,
    ) -> None:
        """Initialize the child coordinator."""
        super().__init__(
            hass,
            entry,
            name="child",
            update_interval=DEFAULT_SCAN_INTERVAL,
            client=client,
        )
        self.child = child
        if child.institution_id is None:
            raise ValueError("Child coordinator requires a verified institution")

    async def _async_update_data(self) -> ChildData:
        """Fetch calendar status and the first feed page."""
        institution_id = self.child.institution_id
        assert institution_id is not None
        results = await asyncio.gather(
            self.client.async_get_child_calendar(institution_id, self.child.id),
            self.client.async_get_guardian_feed(institution_id, self.child.id),
            return_exceptions=True,
        )
        calendar_result, feed_result = results

        for result in results:
            if isinstance(result, EliisAuthError):
                raise ConfigEntryAuthFailed(
                    "Eliis session is no longer valid"
                ) from result

        failures = [result for result in results if isinstance(result, BaseException)]
        if len(failures) == len(results):
            first = failures[0]
            if isinstance(first, EliisError):
                _raise_update_failure(first)
            raise UpdateFailed("Could not fetch Eliis child data")

        data = ChildData(fetched_at=utcnow())
        today = dt_util.now().date()

        if not isinstance(calendar_result, BaseException):
            try:
                data.day_status = parse_child_day(calendar_result, today)
                data.status_ok = True
            except EliisSchemaError:
                data.status_ok = False

        if not isinstance(feed_result, BaseException):
            try:
                page = parse_feed(feed_result, fetched_at=data.fetched_at)
            except EliisSchemaError:
                data.feed_ok = False
            else:
                latest, count = latest_feed_summary(page)
                data.feed_ok = not page.malformed
                data.latest_diary_date = latest
                data.diary_count = count

        return data


@dataclass(slots=True)
class _RangeSlice:
    """A cached bounded event range."""

    result: EventsResult
    stored_at: datetime


class EventsCoordinator(BaseEliisCoordinator[EventsData]):
    """Fetch and cache bounded institution event ranges."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: EliisClient,
        institution: Institution,
    ) -> None:
        """Initialize the events coordinator."""
        super().__init__(
            hass,
            entry,
            name="events",
            update_interval=EVENTS_INTERVAL,
            client=client,
        )
        self.institution = institution
        self._cache: dict[tuple[date, date], _RangeSlice] = {}
        self._fetch_lock = asyncio.Lock()

    async def _async_update_data(self) -> EventsData:
        """Prefetch the default calendar horizon."""
        now = utcnow()
        result = await self.async_get_range(now, now + CALENDAR_HORIZON, force=True)
        await self.async_sync_partial_issue(result.omitted_count)
        return EventsData(
            events=result.events,
            omitted_count=result.omitted_count,
            complete=result.complete,
            fetched_at=result.fetched_at,
        )

    async def async_sync_partial_issue(self, omitted_count: int) -> None:
        """Create or clear the partial-calendar repair issue."""
        from homeassistant.helpers import issue_registry as ir

        issue_id = f"{PARTIAL_CALENDAR_ISSUE_ID}_{self.config_entry.entry_id}"
        if omitted_count:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="partial_calendar",
                translation_placeholders={"count": str(omitted_count)},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    def async_invalidate_cache(self) -> None:
        """Drop all cached slices."""
        self._cache.clear()

    async def async_get_range(
        self, start: datetime, end: datetime, *, force: bool = False
    ) -> EventsResult:
        """Return supported events overlapping a requested range.

        The range is split into at most three source-date windows. If the
        requested span needs more, the query fails instead of returning a
        complete-looking partial answer.
        """
        if end <= start:
            return EventsResult()

        start_date = dt_util.as_local(start).date()
        end_date = dt_util.as_local(end).date()
        windows: list[tuple[date, date]] = []
        cursor = start_date
        while cursor <= end_date:
            window_end = min(cursor + CALENDAR_SLICE - timedelta(days=1), end_date)
            windows.append((cursor, window_end))
            cursor = window_end + timedelta(days=1)

        if len(windows) > CALENDAR_MAX_SLICES:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="range_too_large",
                translation_placeholders={"max_days": str(CALENDAR_MAX_SLICES)},
            )

        results: list[EventsResult] = []
        for window_start, window_end in windows:
            results.append(
                await self._async_get_slice(window_start, window_end, force=force)
            )

        merged: list[Any] = []
        seen: set[tuple[Any, ...]] = set()
        omitted = 0
        malformed = False
        fetched_at = None

        start_utc = start.astimezone(UTC)
        end_utc = end.astimezone(UTC)
        start_local = start_date
        end_local = end_date

        for result in results:
            omitted += result.omitted_count
            malformed = malformed or result.malformed
            if result.fetched_at is not None and (
                fetched_at is None or result.fetched_at < fetched_at
            ):
                fetched_at = result.fetched_at
            for event in result.events:
                if event.all_day:
                    if not isinstance(event.start, date):
                        continue
                    if not isinstance(event.end, date):
                        continue
                    if not (event.end > start_local and event.start < end_local):
                        continue
                else:
                    if not isinstance(event.start, datetime):
                        continue
                    if not isinstance(event.end, datetime):
                        continue
                    if not (event.end > start_utc and event.start < end_utc):
                        continue
                key = (
                    event.uid,
                    event.summary,
                    event.start,
                    event.end,
                    event.description,
                )
                if key in seen:
                    continue
                seen.add(key)
                merged.append(event)

        merged.sort(
            key=lambda event: (
                event.start
                if isinstance(event.start, datetime)
                else dt_util.start_of_local_day(event.start),
                event.summary,
            )
        )
        return EventsResult(
            events=tuple(merged),
            omitted_count=omitted,
            malformed=malformed,
            fetched_at=fetched_at or utcnow(),
        )

    async def _async_get_slice(
        self, start: date, end: date, *, force: bool
    ) -> EventsResult:
        """Return one cached or freshly fetched source-date window."""
        key = (start, end)
        now = utcnow()
        cached = self._cache.get(key)
        if (
            not force
            and cached is not None
            and now - cached.stored_at < CALENDAR_CACHE_TTL
        ):
            return cached.result

        async with self._fetch_lock:
            cached = self._cache.get(key)
            if (
                not force
                and cached is not None
                and utcnow() - cached.stored_at < CALENDAR_CACHE_TTL
            ):
                return cached.result
            try:
                payload = await self.client.async_get_institution_events(
                    self.institution.id, start.isoformat(), end.isoformat()
                )
            except EliisAuthError as err:
                raise ConfigEntryAuthFailed("Eliis session is no longer valid") from err
            except EliisPermissionError as err:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="permission_denied",
                ) from err
            except EliisError as err:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="cannot_fetch_events",
                ) from err

            try:
                result = parse_events(
                    payload,
                    institution_id=self.institution.id,
                    fallback_summary=EVENT_FALLBACK_SUMMARY,
                )
            except EliisSchemaError as err:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="cannot_fetch_events",
                ) from err
            self._cache[key] = _RangeSlice(result=result, stored_at=utcnow())
            return result


def module_coordinators(
    account: AccountCoordinator,
    menus: dict[str, MenuCoordinator],
    children: dict[str, ChildCoordinator],
    events: dict[str, EventsCoordinator],
) -> list[DataUpdateCoordinator[Any]]:
    """Return every coordinator for refresh actions and unload handling."""
    result: list[DataUpdateCoordinator[Any]] = [account]
    result.extend(menus.values())
    result.extend(children.values())
    result.extend(events.values())
    return result


__all__ = [
    "MODULE_EVENTS",
    "AccountCoordinator",
    "ChildCoordinator",
    "EventsCoordinator",
    "MenuCoordinator",
    "module_coordinators",
]
