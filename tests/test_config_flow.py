"""Config, reauth, reconfigure and options flow tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from custom_components.eliis.api.errors import EliisAuthError, EliisConnectionError
from custom_components.eliis.const import DOMAIN
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .fakes import FakeEliisClient
from .fixtures import INIT_PAYLOAD


@pytest.fixture(autouse=True)
def reset_fake_client():
    """Reset shared fake state between tests."""
    FakeEliisClient.reset()
    yield


async def _start_user_flow(hass):
    """Start the initial setup flow."""
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


@pytest.mark.asyncio
async def test_user_flow_creates_entry(hass) -> None:
    """A valid login discovers the account and creates one entry."""
    result = await _start_user_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    with patch("custom_components.eliis.config_flow.EliisClient", FakeEliisClient):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"username": "guardian", "password": "secret"},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.unique_id == entry.data["account_id"]
    assert entry.options["enable_events"] is False
    assert entry.options["enable_menu"] is True
    assert len(entry.data["children"]) == 2


@pytest.mark.asyncio
async def test_user_flow_two_factor(hass) -> None:
    """A challenge requires the code step before the entry is created."""
    FakeEliisClient.login_requires_2fa = True

    with patch("custom_components.eliis.config_flow.EliisClient", FakeEliisClient):
        result = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"username": "guardian", "password": "secret"},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "two_factor"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "123456"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_user_flow_rejects_bad_credentials(hass) -> None:
    """Authentication and transport errors stay on the form."""
    FakeEliisClient.login_error = EliisAuthError("rejected")

    with patch("custom_components.eliis.config_flow.EliisClient", FakeEliisClient):
        result = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"username": "guardian", "password": "wrong"},
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    FakeEliisClient.login_error = EliisConnectionError("offline")
    with patch("custom_components.eliis.config_flow.EliisClient", FakeEliisClient):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"username": "guardian", "password": "wrong"},
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.asyncio
async def test_user_flow_duplicate_account_aborts(hass) -> None:
    """The same account cannot be configured twice."""
    with patch("custom_components.eliis.config_flow.EliisClient", FakeEliisClient):
        result = await _start_user_flow(hass)
        first = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"username": "guardian", "password": "secret"},
        )
        assert first["type"] is FlowResultType.CREATE_ENTRY

        result = await _start_user_flow(hass)
        second = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"username": "guardian", "password": "secret"},
        )
    assert second["type"] is FlowResultType.ABORT
    assert second["reason"] == "already_configured"


@pytest.mark.asyncio
async def test_reauth_updates_only_session_data(hass) -> None:
    """Reauthentication keeps selections and refreshes the cookie."""
    from custom_components.eliis.api.discovery import parse_init

    account_id = parse_init(INIT_PAYLOAD).account_id
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=account_id,
        data={
            "username": "guardian",
            "cookie": "eliis_csrf=old",
            "app_version": "2.69",
            "account_id": account_id,
            "institutions": [{"id": "1001", "name": "Test Kindergarten"}],
            "children": [
                {
                    "id": "2002",
                    "name": "Test Child",
                    "institution_id": "1001",
                }
            ],
        },
    )
    entry.add_to_hass(hass)

    with patch("custom_components.eliis.config_flow.EliisClient", FakeEliisClient):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": entry.entry_id,
                "unique_id": entry.unique_id,
            },
        )
        assert result["step_id"] == "reauth_confirm"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"username": "guardian", "password": "secret"},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["cookie"] == "eliis_csrf=session-cookie"
    assert entry.data["children"][0]["id"] == "2002"


@pytest.mark.asyncio
async def test_reauth_rejects_wrong_account(hass) -> None:
    """A different account cannot take over an existing entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="not-this-account",
        data={
            "username": "guardian",
            "cookie": "eliis_csrf=old",
            "app_version": "2.69",
            "account_id": "not-this-account",
            "institutions": [{"id": "1001"}],
            "children": [],
        },
    )
    entry.add_to_hass(hass)

    with patch("custom_components.eliis.config_flow.EliisClient", FakeEliisClient):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": entry.entry_id,
                "unique_id": entry.unique_id,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"username": "guardian", "password": "secret"},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"


@pytest.mark.asyncio
async def test_options_flow_updates_and_hashes_aliases(hass) -> None:
    """Options are stored and child aliases use opaque keys."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="account",
        data={
            "username": "guardian",
            "cookie": "eliis_csrf=old",
            "app_version": "2.69",
            "account_id": "account",
            "institutions": [{"id": "1001"}],
            "children": [
                {
                    "id": "2002",
                    "name": "Test Child",
                    "institution_id": "1001",
                }
            ],
        },
        options={},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "scan_interval": 30,
            "enable_menu": True,
            "enable_feed": False,
            "enable_notifications": True,
            "enable_events": False,
            "alias_1": "Kiddo",
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["scan_interval"] == 30
    assert entry.options["enable_feed"] is False
    assert list(entry.options["child_aliases"].values()) == ["Kiddo"]
    assert "2002" not in entry.options["child_aliases"]


@pytest.mark.asyncio
async def test_flow_closes_client_on_discovery_failure(hass) -> None:
    """A discovery failure does not leave the flow client open."""
    FakeEliisClient.init_error = EliisConnectionError("offline")

    with patch("custom_components.eliis.config_flow.EliisClient", FakeEliisClient):
        result = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"username": "guardian", "password": "secret"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


def test_fixture_account_is_stable() -> None:
    """Sanity check the shared fake discovery payload."""
    assert INIT_PAYLOAD["kindergartens"][0]["id"] == 1001
