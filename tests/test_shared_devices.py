"""Shared institution and child device tests across multiple accounts."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from custom_components.eliis.const import DOMAIN
from custom_components.eliis.entity import (
    child_identifier,
    institution_identifier,
)
from custom_components.eliis.util import opaque_id
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .fakes import FakeEliisClient
from .helpers import get_device, make_entry


@pytest.fixture(autouse=True)
def reset_fake_client():
    """Reset shared fake state between tests."""
    FakeEliisClient.reset()
    yield


async def _setup(hass, *entries):
    """Add and load the given entries in order."""
    for entry in entries:
        entry.add_to_hass(hass)
    with patch("custom_components.eliis.runtime.EliisClient", FakeEliisClient):
        for entry in entries:
            if entry.state is ConfigEntryState.NOT_LOADED:
                assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


def _owner(*entries):
    """Return the deterministic shared owner used by the integration."""
    return min(entries, key=lambda entry: entry.entry_id)


def _sensor_ids(hass, fragment: str) -> list[str]:
    """Return sensor entity IDs containing a fragment."""
    return sorted(
        entity_id
        for entity_id in hass.states.async_entity_ids("sensor")
        if fragment in entity_id
    )


@pytest.mark.asyncio
async def test_shared_institution_with_different_children(hass) -> None:
    """Two accounts share one institution while keeping their own children."""
    entry_a = make_entry(
        account_id="account-a",
        username="parent-a",
        children=[{"id": "2002", "name": "Alice", "institution_id": "1001"}],
    )
    entry_b = make_entry(
        account_id="account-b",
        username="parent-b",
        children=[{"id": "3003", "name": "Bob", "institution_id": "1001"}],
    )
    await _setup(hass, entry_a, entry_b)

    registry = dr.async_get(hass)
    owner = _owner(entry_a, entry_b)
    institution = get_device(hass, owner, institution_identifier("1001"))
    assert institution is not None
    assert institution.config_entries == {owner.entry_id}

    alice = get_device(hass, entry_a, child_identifier("2002"))
    bob = get_device(hass, entry_b, child_identifier("3003"))
    assert alice is not None
    assert bob is not None
    assert alice.via_device_id == institution.id
    assert bob.via_device_id == institution.id

    shared_institutions = [
        device
        for device in registry.devices.values()
        if institution_identifier("1001") in device.identifiers
    ]
    assert len(shared_institutions) == 1

    assert _sensor_ids(hass, "test_kindergarten") == [
        "sensor.test_kindergarten_today_s_menu",
        "sensor.test_kindergarten_tomorrow_s_menu",
    ]
    assert _sensor_ids(hass, "alice_calendar_status_today") == [
        "sensor.alice_calendar_status_today"
    ]
    assert _sensor_ids(hass, "bob_calendar_status_today") == [
        "sensor.bob_calendar_status_today"
    ]

    # Account notifications are genuinely per account and remain separate.
    assert len(_sensor_ids(hass, "eliis_unseen_notifications")) == 2


@pytest.mark.asyncio
async def test_shared_child_has_one_device_and_one_entity_set(hass) -> None:
    """Two accounts seeing the same child share one device and entity set."""
    child = {"id": "2002", "name": "Alex", "institution_id": "1001"}
    entry_a = make_entry(
        account_id="account-a",
        username="parent-a",
        children=[dict(child)],
    )
    entry_b = make_entry(
        account_id="account-b",
        username="parent-b",
        children=[dict(child)],
    )
    await _setup(hass, entry_a, entry_b)

    registry = dr.async_get(hass)
    child_devices = [
        device
        for device in registry.devices.values()
        if child_identifier("2002") in device.identifiers
    ]
    assert len(child_devices) == 1
    owner = _owner(entry_a, entry_b)
    child_device = get_device(hass, owner, child_identifier("2002"))
    assert child_device is not None
    assert child_device.config_entries == {owner.entry_id}

    entities = [
        entity
        for entity in er.async_get(hass).entities.values()
        if entity.device_id == child_device.id
    ]
    assert entities
    assert {entity.config_entry_id for entity in entities} == {owner.entry_id}
    assert sorted(entity.entity_id for entity in entities) == [
        "sensor.alex_calendar_status_today",
        "sensor.alex_diary_records_on_latest_returned_date",
        "sensor.alex_last_successful_sync",
        "sensor.alex_latest_returned_diary_date",
    ]
    assert all(not entity.entity_id.endswith("_2") for entity in entities)


@pytest.mark.asyncio
async def test_services_use_another_account_when_owner_unloads(hass) -> None:
    """A shared device stays usable through another loaded account."""
    shared_child = {"id": "2002", "name": "Alex", "institution_id": "1001"}
    entry_a = make_entry(
        account_id="account-a",
        username="parent-a",
        children=[dict(shared_child)],
    )
    entry_b = make_entry(
        account_id="account-b",
        username="parent-b",
        children=[dict(shared_child)],
    )
    await _setup(hass, entry_a, entry_b)

    owner = _owner(entry_a, entry_b)
    other = entry_b if owner is entry_a else entry_a
    institution = get_device(hass, owner, institution_identifier("1001"))
    child = get_device(hass, owner, child_identifier("2002"))
    assert institution is not None
    assert child is not None

    assert await hass.config_entries.async_unload(owner.entry_id)
    await hass.async_block_till_done()
    assert owner.state is ConfigEntryState.NOT_LOADED
    assert other.state is ConfigEntryState.LOADED

    FakeEliisClient.menu_requests = []
    menu = await hass.services.async_call(
        DOMAIN,
        "get_menu",
        {"device_id": institution.id},
        blocking=True,
        return_response=True,
    )
    assert menu["coverage"]["complete"] is True
    assert FakeEliisClient.menu_requests

    feed = await hass.services.async_call(
        DOMAIN,
        "get_feed_summary",
        {"device_id": child.id},
        blocking=True,
        return_response=True,
    )
    assert feed["groups"][0]["date"] == "2026-11-03"


@pytest.mark.asyncio
async def test_removing_owner_hands_shared_devices_to_another_account(hass) -> None:
    """Removing the owner lets the next account recreate shared devices."""
    shared_child = {"id": "2002", "name": "Alex", "institution_id": "1001"}
    entry_a = make_entry(
        account_id="account-a",
        username="parent-a",
        children=[dict(shared_child)],
    )
    entry_b = make_entry(
        account_id="account-b",
        username="parent-b",
        children=[dict(shared_child)],
    )
    await _setup(hass, entry_a, entry_b)

    owner = _owner(entry_a, entry_b)
    other = entry_b if owner is entry_a else entry_a

    await hass.config_entries.async_remove(owner.entry_id)
    await hass.async_block_till_done()
    await hass.async_block_till_done()

    assert other.state is ConfigEntryState.LOADED
    institution = get_device(hass, other, institution_identifier("1001"))
    child = get_device(hass, other, child_identifier("2002"))
    assert institution is not None
    assert child is not None
    assert institution.config_entries == {other.entry_id}
    assert child.config_entries == {other.entry_id}

    entity_ids = [
        entity.entity_id
        for entity in er.async_get(hass).entities.values()
        if entity.device_id == child.id
    ]
    assert "sensor.alex_calendar_status_today" in entity_ids
    assert all(not entity_id.endswith("_2") for entity_id in entity_ids)


@pytest.mark.asyncio
async def test_version_one_devices_are_migrated(hass) -> None:
    """Old account-scoped devices are removed before shared setup."""
    entry = make_entry(version=1)
    entry.add_to_hass(hass)

    registry = dr.async_get(hass)
    old_identifier = (
        DOMAIN,
        f"institution:{entry.data['account_id']}:{opaque_id('1001')}",
    )
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={old_identifier},
        name="Old Institution",
    )

    with patch("custom_components.eliis.runtime.EliisClient", FakeEliisClient):
        if entry.state is ConfigEntryState.NOT_LOADED:
            assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.version == 2
    assert get_device(hass, entry, old_identifier) is None
    assert get_device(hass, entry, institution_identifier("1001")) is not None
