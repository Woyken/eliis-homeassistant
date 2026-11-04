"""Shared config-entry builders for integration tests."""

from __future__ import annotations

from typing import Any

from custom_components.eliis.api.discovery import parse_init
from custom_components.eliis.const import DOMAIN
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .fixtures import INIT_PAYLOAD

ACCOUNT_ID = parse_init(INIT_PAYLOAD).account_id


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
