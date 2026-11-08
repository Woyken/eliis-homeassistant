"""Admin-only action tests."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from custom_components.eliis.const import DOMAIN
from custom_components.eliis.util import opaque_id
from homeassistant.auth.const import GROUP_ID_USER
from homeassistant.core import Context
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
    Unauthorized,
)
from homeassistant.util import dt as dt_util

from .fakes import FakeEliisClient
from .helpers import get_device, make_entry


@pytest.fixture(autouse=True)
def reset_fake_client():
    """Reset shared fake state between tests."""
    FakeEliisClient.reset()
    yield


async def _setup(hass):
    """Set up one entry and return the device ids."""
    entry = make_entry()
    entry.add_to_hass(hass)
    with patch("custom_components.eliis.runtime.EliisClient", FakeEliisClient):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    institution = get_device(
        hass,
        entry,
        (DOMAIN, f"institution:{opaque_id('1001')}"),
    )
    child = get_device(hass, entry, (DOMAIN, f"child:{opaque_id('2002')}"))
    assert institution is not None
    assert child is not None
    return entry, institution.id, child.id


@pytest.mark.asyncio
async def test_get_menu_returns_bounded_days(hass) -> None:
    """The menu action returns only explicit returned days."""
    _, institution_id, _ = await _setup(hass)
    today = dt_util.now().date()

    response = await hass.services.async_call(
        DOMAIN,
        "get_menu",
        {"device_id": institution_id, "start_date": today, "end_date": today},
        blocking=True,
        return_response=True,
    )
    assert response["days"][0]["date"] == today.isoformat()
    assert response["coverage"]["complete"] is True
    assert FakeEliisClient.menu_requests


@pytest.mark.asyncio
async def test_get_menu_rejects_large_range(hass) -> None:
    """A request beyond the bounded window fails before any HTTP call."""
    _, institution_id, _ = await _setup(hass)
    today = dt_util.now().date()

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "get_menu",
            {
                "device_id": institution_id,
                "start_date": today,
                "end_date": today + timedelta(days=60),
            },
            blocking=True,
            return_response=True,
        )


@pytest.mark.asyncio
async def test_get_menu_requires_institution_device(hass) -> None:
    """A child device cannot be used for the menu action."""
    _, _, child_id = await _setup(hass)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "get_menu",
            {"device_id": child_id},
            blocking=True,
            return_response=True,
        )


@pytest.mark.asyncio
async def test_get_feed_summary(hass) -> None:
    """The feed action returns date/count summaries without text."""
    _, _, child_id = await _setup(hass)

    response = await hass.services.async_call(
        DOMAIN,
        "get_feed_summary",
        {"device_id": child_id},
        blocking=True,
        return_response=True,
    )
    assert response["groups"][0]["date"] == "2026-11-03"
    assert response["groups"][0]["diary_count"] == 2
    assert response["coverage"]["truncated"] is True


@pytest.mark.asyncio
async def test_get_status_history(hass) -> None:
    """Status history requires an explicit date and returns bounded records."""
    _, _, child_id = await _setup(hass)

    response = await hass.services.async_call(
        DOMAIN,
        "get_status_history",
        {"device_id": child_id, "date": date(2026, 11, 3)},
        blocking=True,
        return_response=True,
    )
    assert response["date"] == "2026-11-03"
    assert [record["label"] for record in response["records"]] == [
        "Present",
        "Left",
    ]
    assert FakeEliisClient.status_requests == [("1001", "2002", "2026-11-03")]


@pytest.mark.asyncio
async def test_refresh_module(hass) -> None:
    """Refreshing a disabled module is rejected instead of silently ignored."""
    entry, _, _ = await _setup(hass)

    await hass.services.async_call(
        DOMAIN,
        "refresh",
        {"config_entry_id": entry.entry_id, "module": "menu"},
        blocking=True,
    )

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "refresh",
            {"config_entry_id": entry.entry_id, "module": "events"},
            blocking=True,
        )


@pytest.mark.asyncio
async def test_unknown_entry_is_rejected(hass) -> None:
    """A missing config entry cannot be refreshed."""
    await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "refresh",
            {"config_entry_id": "01MISSING"},
            blocking=True,
        )


@pytest.mark.asyncio
async def test_non_admin_user_is_denied(hass) -> None:
    """The read actions are admin-only."""
    _, institution_id, _ = await _setup(hass)
    await hass.auth.async_create_user("owner")
    user = await hass.auth.async_create_user("regular", group_ids=[GROUP_ID_USER])
    assert user.is_admin is False

    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN,
            "get_menu",
            {"device_id": institution_id},
            blocking=True,
            return_response=True,
            context=Context(user_id=user.id),
        )


@pytest.mark.asyncio
async def test_auth_failure_starts_reauth(hass) -> None:
    """A rejected session during an action requests reauthentication."""
    from custom_components.eliis.api.errors import EliisAuthError

    _, institution_id, _ = await _setup(hass)

    async def _raise(*args, **kwargs):
        raise EliisAuthError("expired")

    with (
        patch.object(FakeEliisClient, "async_get_week_menu", _raise),
        pytest.raises(HomeAssistantError),
    ):
        await hass.services.async_call(
            DOMAIN,
            "get_menu",
            {"device_id": institution_id},
            blocking=True,
            return_response=True,
        )

    assert any(
        flow["context"].get("source") == "reauth"
        for flow in hass.config_entries.flow.async_progress()
    )
