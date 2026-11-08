"""Experimental institution-events calendar for Eliis.

Only explicitly non-recurring, representable events are exposed. Omissions
create a repair issue instead of a complete-looking calendar.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

import voluptuous as vol
from homeassistant.components.calendar import (
    CalendarEntity,
    CalendarEntityFeature,
    CalendarEvent,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .api.models import EventRecord, Institution
from .const import DOMAIN, PARTIAL_CALENDAR_ISSUE_ID
from .coordinator import EventsCoordinator
from .entity import (
    EliisEntity,
    institution_device_info,
    institution_scope,
    is_shared_owner,
)
from .runtime import EliisRuntimeData

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the institution events calendar."""
    runtime: EliisRuntimeData = entry.runtime_data
    entities = [
        EliisEventsCalendar(
            coordinator,
            runtime,
            institution,
            device_info=institution_device_info(runtime, institution),
        )
        for institution in runtime.institutions
        if is_shared_owner(hass, entry, institution_id=institution.id)
        and (coordinator := runtime.events_coordinators.get(institution.id)) is not None
    ]
    async_add_entities(entities)


def _build_calendar_event(record: EventRecord) -> CalendarEvent | None:
    """Build an HA event, returning None when HA rejects the interval."""
    try:
        return CalendarEvent(
            start=record.start,
            end=record.end,
            summary=record.summary,
            description=record.description,
            uid=record.uid,
        )
    except HomeAssistantError, ValueError, vol.Invalid:
        return None


def _event_bounds(record: EventRecord) -> tuple[datetime, datetime]:
    """Return local comparison bounds for an event record."""
    if record.all_day:
        assert isinstance(record.start, date)
        assert isinstance(record.end, date)
        return (
            dt_util.start_of_local_day(record.start),
            dt_util.start_of_local_day(record.end),
        )
    assert isinstance(record.start, datetime)
    assert isinstance(record.end, datetime)
    return record.start, record.end


class EliisEventsCalendar(EliisEntity, CalendarEntity):
    """Read-only institution event calendar."""

    _attr_translation_key = "institution_events"
    _attr_supported_features = CalendarEntityFeature(0)

    def __init__(
        self,
        coordinator: EventsCoordinator,
        runtime: EliisRuntimeData,
        institution: Institution,
        *,
        device_info: Any,
    ) -> None:
        """Initialize the calendar."""
        super().__init__(
            coordinator,
            unique_scope=institution_scope(institution.id),
            unique_key=f"events_{institution.id}",
            device_info=device_info,
        )
        self._institution = institution
        self._constructor_omissions = 0

    @property
    def available(self) -> bool:
        """Return whether the events module is usable."""
        return bool(
            self.coordinator.last_update_success and self.coordinator.data is not None
        )

    @property
    def event(self) -> CalendarEvent | None:
        """Return the current or next representable event."""
        data = self.coordinator.data
        if data is None:
            return None

        now = dt_util.now()
        current: CalendarEvent | None = None
        upcoming: CalendarEvent | None = None
        upcoming_start: datetime | None = None

        for record in data.events:
            calendar_event = _build_calendar_event(record)
            if calendar_event is None:
                self._constructor_omissions += 1
                self._async_report_constructor_omission()
                continue
            start, end = _event_bounds(record)
            if start <= now < end:
                current = calendar_event
                break
            if start > now and (upcoming_start is None or start < upcoming_start):
                upcoming = calendar_event
                upcoming_start = start

        return current or upcoming

    def _async_report_constructor_omission(self) -> None:
        """Create a repair issue when HA rejects an otherwise valid interval."""
        if self.hass is None:
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"{PARTIAL_CALENDAR_ISSUE_ID}_{self.coordinator.config_entry.entry_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="partial_calendar",
            translation_placeholders={"count": str(self._constructor_omissions)},
        )

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return representable events overlapping the requested range."""
        try:
            result = await self.coordinator.async_get_range(start_date, end_date)
        except ConfigEntryAuthFailed as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="calendar_unavailable",
            ) from err

        events: list[CalendarEvent] = []
        rejected = 0
        for record in result.events:
            calendar_event = _build_calendar_event(record)
            if calendar_event is None:
                rejected += 1
                continue
            events.append(calendar_event)

        omitted = result.omitted_count + rejected
        if omitted:
            await self.coordinator.async_sync_partial_issue(omitted)
        return events
