"""Diagnostics redaction tests."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from custom_components.eliis.diagnostics import async_get_config_entry_diagnostics

from .fakes import FakeEliisClient
from .helpers import make_entry


@pytest.fixture(autouse=True)
def reset_fake_client():
    """Reset shared fake state between tests."""
    FakeEliisClient.reset()
    yield


@pytest.mark.asyncio
async def test_diagnostics_are_redacted_and_aggregate(hass) -> None:
    """Diagnostics expose module health but never credentials or content."""
    entry = make_entry()
    entry.add_to_hass(hass)

    with patch("custom_components.eliis.runtime.EliisClient", FakeEliisClient):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    payload = await async_get_config_entry_diagnostics(hass, entry)
    serialized = json.dumps(payload)

    assert payload["account"] == {"institution_count": 1, "child_count": 1}
    assert payload["options"]["enable_events"] is False
    assert payload["modules"]["account"]["notification_count"] == 3
    assert payload["modules"]["menu_0"]["day_count"] >= 0
    assert payload["modules"]["child_0"]["feed_ok"] is True

    assert "guardian" not in serialized
    assert "session-cookie" not in serialized
    assert "eliis_csrf" not in serialized
    assert "2002" not in serialized
    assert "1001" not in serialized


@pytest.mark.asyncio
async def test_diagnostics_for_unloaded_entry(hass) -> None:
    """An unloaded entry yields a minimal, safe payload."""
    entry = make_entry()
    entry.add_to_hass(hass)

    payload = await async_get_config_entry_diagnostics(hass, entry)
    assert payload == {"entry": {"state": "not_loaded"}}
