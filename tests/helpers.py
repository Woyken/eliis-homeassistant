"""Shared config-entry builders for integration tests."""

from __future__ import annotations

from typing import Any

from custom_components.eliis.api.discovery import parse_init
from custom_components.eliis.const import DOMAIN
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .fixtures import INIT_PAYLOAD

ACCOUNT_ID = parse_init(INIT_PAYLOAD).account_id


def get_device(hass, entry, identifier: tuple[str, str]):
    """Look up a main device on both pre- and post-2026.10 APIs."""
    registry = dr.async_get(hass)
    if getter := getattr(registry, "async_get_device_by_identifier", None):
        return getter(identifier, entry.entry_id)
    return registry.async_get_device(identifiers={identifier})


def make_entry(options: dict[str, Any] | None = None) -> MockConfigEntry:
    """Build a loaded-config-entry stand-in with fictional selections."""
    return MockConfigEntry(
        domain=DOMAIN,
        version=1,
        unique_id=ACCOUNT_ID,
        title="Eliis (guardian)",
        data={
            "username": "guardian",
            "cookie": "eliis_csrf=session-cookie",
            "app_version": "2.69",
            "account_id": ACCOUNT_ID,
            "institutions": [{"id": "1001", "name": "Test Kindergarten"}],
            "children": [
                {
                    "id": "2002",
                    "name": "Test Child",
                    "institution_id": "1001",
                }
            ],
        },
        options=options or {},
    )
