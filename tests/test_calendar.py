"""Calendar range, repair and end-to-end event tests."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from custom_components.eliis.api.models import Institution
from custom_components.eliis.const import DOMAIN, PARTIAL_CALENDAR_ISSUE_ID
from custom_components.eliis.coordinator import EventsCoordinator
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir

from .fakes import FakeEliisClient
from .helpers import make_entry


@pytest.fixture(autouse=True)
def reset_fake_client():
    """Reset shared fake state between tests."""
    FakeEliisClient.reset()
    yield


@pytest.fixture(autouse=True)
def utc_timezone():
    """Use UTC so source-date windows are deterministic."""
    from zoneinfo import ZoneInfo

    from homeassistant.util import dt as dt_util

    previous = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(ZoneInfo("UTC"))
    yield
    dt_util.set_default_time_zone(previous)


@pytest.mark.asyncio
async def test_range_is_split_merged_and_cached(hass) -> None:
    """One bounded window is fetched once and cached."""
    entry = make_entry()
    coordinator = EventsCoordinator(hass, entry, FakeEliisClient(), Institution("1001"))
    start = datetime(2026, 11, 1, tzinfo=UTC)
    end = datetime(2026, 11, 10, tzinfo=UTC)

    result = await coordinator.async_get_range(start, end)
    assert [event.summary for event in result.events] == [
        "Autumn fair",
        "All day",
    ]
    assert result.omitted_count == 2
    assert result.complete is False
    assert FakeEliisClient.events_requests == [("1001", "2026-11-01", "2026-11-10")]

    cached = await coordinator.async_get_range(start, end)
    assert cached.events == result.events
    assert len(FakeEliisClient.events_requests) == 1


@pytest.mark.asyncio
async def test_range_beyond_slice_limit_fails(hass) -> None:
    """A range needing more than three windows is refused."""
    entry = make_entry()
    coordinator = EventsCoordinator(hass, entry, FakeEliisClient(), Institution("1001"))

    with pytest.raises(HomeAssistantError):
        await coordinator.async_get_range(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 6, 1, tzinfo=UTC),
        )
    assert FakeEliisClient.events_requests == []


@pytest.mark.asyncio
async def test_partial_calendar_repair_issue(hass) -> None:
    """Omitted events create one transparent repair issue."""
    entry = make_entry(options={"enable_events": True})
    entry.add_to_hass(hass)

    with patch("custom_components.eliis.runtime.EliisClient", FakeEliisClient):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"{PARTIAL_CALENDAR_ISSUE_ID}_{entry.entry_id}"
    )
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.WARNING
    assert int(issue.translation_placeholders["count"]) >= 2


@pytest.mark.asyncio
async def test_calendar_service_returns_representable_events(hass) -> None:
    """The exposed calendar returns only supported events for the range."""
    entry = make_entry(options={"enable_events": True})
    entry.add_to_hass(hass)

    with patch("custom_components.eliis.runtime.EliisClient", FakeEliisClient):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    response = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": "calendar.test_kindergarten_institution_events",
            "start_date_time": "2026-11-01T00:00:00+00:00",
            "end_date_time": "2026-11-10T00:00:00+00:00",
        },
        blocking=True,
        return_response=True,
    )

    events = response["calendar.test_kindergarten_institution_events"]["events"]
    assert [event["summary"] for event in events] == ["Autumn fair", "All day"]
    assert events[0]["start"] == "2026-11-04T10:00:00+00:00"
    assert events[1]["start"] == "2026-11-05"
    assert events[1]["end"] == "2026-11-06"
