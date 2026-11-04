"""Setup, unload and entity tests for the Eliis integration."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from custom_components.eliis.const import DOMAIN
from custom_components.eliis.entity import child_name
from custom_components.eliis.util import opaque_id
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr

from .fakes import FakeEliisClient
from .helpers import ACCOUNT_ID, make_entry


@pytest.fixture(autouse=True)
def reset_fake_client():
    """Reset shared fake state between tests."""
    FakeEliisClient.reset()
    yield


@pytest.mark.asyncio
async def test_setup_creates_account_and_child_entities(hass) -> None:
    """A normal setup loads sensors for the account, institution and child."""
    entry = make_entry()
    entry.add_to_hass(hass)

    with patch("custom_components.eliis.runtime.EliisClient", FakeEliisClient):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED

    entity_ids = set(hass.states.async_entity_ids("sensor"))
    assert "sensor.eliis_unseen_notifications" in entity_ids
    assert "sensor.test_kindergarten_today_s_menu" in entity_ids
    assert "sensor.test_child_calendar_status_today" in entity_ids
    assert "sensor.test_child_diary_records_on_latest_returned_date" in entity_ids

    registry = dr.async_get(hass)
    child_device = registry.async_get_device(
        identifiers={(DOMAIN, f"child:{ACCOUNT_ID}:{opaque_id('2002')}")}
    )
    assert child_device is not None
    assert child_device.name == "Test Child"


@pytest.mark.asyncio
async def test_unload_entry_closes_client_and_removes_entities(hass) -> None:
    """Unloading releases entities, coordinators and the owned session."""
    entry = make_entry()
    entry.add_to_hass(hass)

    with patch("custom_components.eliis.runtime.EliisClient", FakeEliisClient):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    sensor_states = [
        hass.states.get(entity_id)
        for entity_id in hass.states.async_entity_ids("sensor")
    ]
    assert sensor_states
    assert all(
        state is not None and state.state == "unavailable" for state in sensor_states
    )


@pytest.mark.asyncio
async def test_events_calendar_is_opt_in(hass) -> None:
    """The experimental calendar only loads when enabled."""
    entry = make_entry(options={"enable_events": True})
    entry.add_to_hass(hass)

    with patch("custom_components.eliis.runtime.EliisClient", FakeEliisClient):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert "calendar.test_kindergarten_institution_events" in set(
        hass.states.async_entity_ids("calendar")
    )


@pytest.mark.asyncio
async def test_child_alias_is_applied(hass) -> None:
    """A configured alias renames the child device."""
    entry = make_entry(options={"child_aliases": {opaque_id("2002"): "Kiddo"}})
    entry.add_to_hass(hass)

    with patch("custom_components.eliis.runtime.EliisClient", FakeEliisClient):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    registry = dr.async_get(hass)
    child_device = registry.async_get_device(
        identifiers={(DOMAIN, f"child:{ACCOUNT_ID}:{opaque_id('2002')}")}
    )
    assert child_device is not None
    assert child_device.name == "Kiddo"


def test_child_name_without_alias(hass) -> None:
    """Missing or malformed aliases fall back to the source name."""
    from custom_components.eliis.api.models import Child

    child = Child("2002", "Test Child", "1001")
    assert child_name(make_entry(), child) == "Test Child"
    assert (
        child_name(make_entry(options={"child_aliases": {"2002": "  "}}), child)
        == "Test Child"
    )
    assert (
        child_name(
            make_entry(options={"child_aliases": {opaque_id("2002"): "Kiddo"}}),
            child,
        )
        == "Kiddo"
    )


@pytest.mark.asyncio
async def test_auth_failure_does_not_break_setup(hass) -> None:
    """An expired session loads the entry and requests reauthentication."""
    from custom_components.eliis.api.errors import EliisAuthError

    class FailingClient(FakeEliisClient):
        """Client whose session is rejected."""

        async def async_get_unseen_notification_count(self):
            raise EliisAuthError("expired")

        async def async_get_week_menu(self, institution_id, day):
            raise EliisAuthError("expired")

        async def async_get_child_calendar(self, institution_id, child_id):
            raise EliisAuthError("expired")

        async def async_get_guardian_feed(self, institution_id, child_id, cursor=None):
            raise EliisAuthError("expired")

    entry = make_entry()
    entry.add_to_hass(hass)

    with patch("custom_components.eliis.runtime.EliisClient", FailingClient):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert any(
        flow["context"].get("source") == "reauth"
        for flow in hass.config_entries.flow.async_progress()
    )
