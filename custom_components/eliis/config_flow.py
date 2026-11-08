"""Config, reauth, reconfigure and options flows for Eliis."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv

from .api import (
    EliisAuthError,
    EliisClient,
    EliisConnectionError,
    EliisError,
    EliisPermissionError,
    EliisRateLimitError,
    EliisResponseError,
    EliisSchemaError,
    EliisUnsupportedError,
)
from .api.discovery import pair_children, parse_init
from .api.models import Discovery, LoginResult
from .const import (
    CONF_ACCOUNT_ID,
    CONF_APP_VERSION,
    CONF_CHILD_ALIASES,
    CONF_CHILDREN,
    CONF_COOKIE,
    CONF_ENABLE_EVENTS,
    CONF_ENABLE_FEED,
    CONF_ENABLE_MENU,
    CONF_ENABLE_NOTIFICATIONS,
    CONF_INSTITUTIONS,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)
from .runtime import (
    children_from_entry,
    institutions_from_entry,
    scan_interval_from_entry,
)
from .util import opaque_id

_LOGGER = logging.getLogger(__name__)

_CODE_SCHEMA = vol.Schema({vol.Required("code"): str})


def _credentials_schema(username: str = "") -> vol.Schema:
    """Return the username/password schema."""
    return vol.Schema(
        {
            vol.Required(CONF_USERNAME, default=username): str,
            vol.Required(CONF_PASSWORD): str,
        }
    )


def _error_key(err: EliisError) -> str:
    """Map a client error to a translated form error."""
    if isinstance(err, EliisAuthError):
        return "invalid_auth"
    if isinstance(err, EliisConnectionError):
        return "cannot_connect"
    if isinstance(err, EliisRateLimitError):
        return "rate_limited"
    if isinstance(err, EliisPermissionError):
        return "permission_denied"
    if isinstance(err, EliisUnsupportedError):
        return "two_factor_unsupported"
    if isinstance(err, (EliisSchemaError, EliisResponseError)):
        return "invalid_response"
    return "unknown"


def _entry_payload(
    discovery: Discovery, login: LoginResult, username: str, app_version: str
) -> dict[str, Any]:
    """Build the minimum config-entry data."""
    return {
        CONF_USERNAME: username,
        CONF_COOKIE: login.cookie or "",
        CONF_APP_VERSION: app_version,
        CONF_ACCOUNT_ID: discovery.account_id,
        CONF_INSTITUTIONS: [
            {"id": item.id, "name": item.name} for item in discovery.institutions
        ],
        CONF_CHILDREN: [
            {
                "id": item.id,
                "name": item.name,
                "institution_id": item.institution_id,
            }
            for item in discovery.children
            if item.institution_id is not None
        ],
    }


def _default_options() -> dict[str, Any]:
    """Return explicit option defaults."""
    return {
        CONF_SCAN_INTERVAL: int(DEFAULT_SCAN_INTERVAL.total_seconds() / 60),
        CONF_ENABLE_MENU: True,
        CONF_ENABLE_FEED: True,
        CONF_ENABLE_NOTIFICATIONS: True,
        CONF_ENABLE_EVENTS: False,
        CONF_CHILD_ALIASES: {},
    }


class EliisConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle Eliis account setup and reauthentication."""

    VERSION = 2

    def __init__(self) -> None:
        """Initialize the flow."""
        self._client: EliisClient | None = None
        self._username = ""
        self._login_identifier: str | None = None
        self._reauth_entry: ConfigEntry | None = None
        self._reconfigure_entry: ConfigEntry | None = None

    async def _async_close_client(self) -> None:
        """Close a flow-owned client."""
        if self._client is not None:
            await self._client.async_close()
            self._client = None

    async def _async_login(self, username: str, password: str) -> LoginResult:
        """Start a login with a fresh account-scoped client."""
        await self._async_close_client()
        self._client = EliisClient()
        return await self._client.async_login(username, password)

    async def _async_discover(
        self,
        expected_account_id: str | None = None,
        *,
        pair: bool = True,
    ) -> Discovery:
        """Discover the account and verify child/institution pairs."""
        assert self._client is not None
        payload = await self._client.async_get_init()
        discovery = parse_init(payload, fallback_seed=self._username)
        if (
            expected_account_id is not None
            and discovery.account_id != expected_account_id
        ):
            # Entries created before the login-name fallback used only the
            # visible institution set. Keep accepting that identity during
            # reauth and reconfigure.
            legacy = parse_init(payload)
            if legacy.account_id == expected_account_id:
                discovery = legacy
        if not pair:
            return discovery
        paired = await pair_children(self._client, discovery)
        return Discovery(
            institutions=discovery.institutions,
            children=paired,
            account_id=discovery.account_id,
            malformed=discovery.malformed,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle initial setup."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            try:
                login = await self._async_login(
                    self._username, user_input[CONF_PASSWORD]
                )
            except EliisError as err:
                errors["base"] = _error_key(err)
            else:
                if login.requires_2fa:
                    self._login_identifier = login.login_identifier
                    return await self.async_step_two_factor()
                return await self._async_finish_setup(login)

        return self.async_show_form(
            step_id="user",
            data_schema=_credentials_schema(self._username),
            errors=errors,
        )

    async def async_step_two_factor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a supported two-factor challenge."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if self._client is None or self._login_identifier is None:
                return self.async_abort(reason="two_factor_expired")
            try:
                login = await self._client.async_login_2fa(
                    user_input["code"], self._login_identifier
                )
            except EliisError as err:
                errors["base"] = _error_key(err)
            else:
                return await self._async_finish_setup(login)

        return self.async_show_form(
            step_id="two_factor", data_schema=_CODE_SCHEMA, errors=errors
        )

    async def _async_finish_setup(self, login: LoginResult) -> ConfigFlowResult:
        """Discover the account and create the entry."""
        assert self._client is not None
        try:
            discovery = await self._async_discover()
        except EliisError as err:
            await self._async_close_client()
            return self.async_show_form(
                step_id="user",
                data_schema=_credentials_schema(self._username),
                errors={"base": _error_key(err)},
            )

        if not discovery.institutions:
            await self._async_close_client()
            return self.async_show_form(
                step_id="user",
                data_schema=_credentials_schema(self._username),
                errors={"base": "no_institutions"},
            )

        await self.async_set_unique_id(discovery.account_id)
        self._abort_if_unique_id_configured()

        data = _entry_payload(
            discovery, login, self._username, self._client.app_version
        )
        title = f"Eliis ({self._username})" if self._username else "Eliis"
        await self._async_close_client()
        return self.async_create_entry(
            title=title, data=data, options=_default_options()
        )

    # Reauthentication ---------------------------------------------------

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication for an existing entry."""
        entry_id = self.context.get("entry_id")
        entry = self.hass.config_entries.async_get_entry(entry_id) if entry_id else None
        if entry is None:
            return self.async_abort(reason="reauth_failed")
        self._reauth_entry = entry
        self._username = str(entry.data.get(CONF_USERNAME, ""))
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Verify credentials before restoring the session."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            try:
                login = await self._async_login(
                    self._username, user_input[CONF_PASSWORD]
                )
            except EliisError as err:
                errors["base"] = _error_key(err)
            else:
                if login.requires_2fa:
                    self._login_identifier = login.login_identifier
                    return await self.async_step_reauth_2fa()
                return await self._async_finish_reauth(login)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_credentials_schema(self._username),
            errors=errors,
        )

    async def async_step_reauth_2fa(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Complete a reauthentication challenge."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if self._client is None or self._login_identifier is None:
                return self.async_abort(reason="two_factor_expired")
            try:
                login = await self._client.async_login_2fa(
                    user_input["code"], self._login_identifier
                )
            except EliisError as err:
                errors["base"] = _error_key(err)
            else:
                return await self._async_finish_reauth(login)

        return self.async_show_form(
            step_id="reauth_2fa", data_schema=_CODE_SCHEMA, errors=errors
        )

    async def _async_finish_reauth(self, login: LoginResult) -> ConfigFlowResult:
        """Verify identity and replace only session data."""
        entry = self._reauth_entry
        assert entry is not None
        assert self._client is not None
        try:
            discovery = await self._async_discover(entry.unique_id, pair=False)
        except EliisError as err:
            await self._async_close_client()
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=_credentials_schema(self._username),
                errors={"base": _error_key(err)},
            )

        if discovery.account_id != entry.unique_id:
            await self._async_close_client()
            return self.async_abort(reason="wrong_account")

        data = dict(entry.data)
        data[CONF_USERNAME] = self._username
        data[CONF_COOKIE] = login.cookie or ""
        data[CONF_APP_VERSION] = self._client.app_version
        await self._async_close_client()
        return self.async_update_reload_and_abort(entry, data=data)

    # Reconfigure --------------------------------------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reauthenticate and refresh selection on an existing entry."""
        entry_id = self.context.get("entry_id")
        entry = self.hass.config_entries.async_get_entry(entry_id) if entry_id else None
        if entry is None:
            return self.async_abort(reason="reconfigure_failed")
        self._reconfigure_entry = entry
        self._username = str(entry.data.get(CONF_USERNAME, ""))

        errors: dict[str, str] = {}
        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            try:
                login = await self._async_login(
                    self._username, user_input[CONF_PASSWORD]
                )
            except EliisError as err:
                errors["base"] = _error_key(err)
            else:
                if login.requires_2fa:
                    self._login_identifier = login.login_identifier
                    return await self.async_step_reconfigure_2fa()
                return await self._async_finish_reconfigure(login)

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_credentials_schema(self._username),
            errors=errors,
        )

    async def async_step_reconfigure_2fa(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Complete a reconfigure challenge."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if self._client is None or self._login_identifier is None:
                return self.async_abort(reason="two_factor_expired")
            try:
                login = await self._client.async_login_2fa(
                    user_input["code"], self._login_identifier
                )
            except EliisError as err:
                errors["base"] = _error_key(err)
            else:
                return await self._async_finish_reconfigure(login)

        return self.async_show_form(
            step_id="reconfigure_2fa", data_schema=_CODE_SCHEMA, errors=errors
        )

    async def _async_finish_reconfigure(self, login: LoginResult) -> ConfigFlowResult:
        """Refresh session and keep only still-visible selections."""
        entry = self._reconfigure_entry
        assert entry is not None
        assert self._client is not None
        try:
            discovery = await self._async_discover(entry.unique_id)
        except EliisError as err:
            await self._async_close_client()
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=_credentials_schema(self._username),
                errors={"base": _error_key(err)},
            )

        if discovery.account_id != entry.unique_id:
            await self._async_close_client()
            return self.async_abort(reason="wrong_account")

        stored_institutions = institutions_from_entry(entry)
        stored_children = children_from_entry(entry)
        visible_institutions = {item.id for item in discovery.institutions}
        visible_children = {item.id for item in discovery.children}

        data = dict(entry.data)
        data[CONF_USERNAME] = self._username
        data[CONF_COOKIE] = login.cookie or ""
        data[CONF_APP_VERSION] = self._client.app_version
        data[CONF_INSTITUTIONS] = [
            {"id": item.id, "name": item.name}
            for item in stored_institutions
            if item.id in visible_institutions
        ]
        data[CONF_CHILDREN] = [
            {
                "id": item.id,
                "name": item.name,
                "institution_id": item.institution_id,
            }
            for item in stored_children
            if item.id in visible_children
        ]
        await self._async_close_client()
        return self.async_update_reload_and_abort(entry, data=data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> EliisOptionsFlow:
        """Return the options flow handler."""
        return EliisOptionsFlow()


class EliisOptionsFlow(OptionsFlow):
    """Configure modules, polling and display aliases."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage integration options."""
        entry = self.config_entry
        children = children_from_entry(entry)
        current_aliases = entry.options.get(CONF_CHILD_ALIASES, {})
        if not isinstance(current_aliases, dict):
            current_aliases = {}

        if user_input is not None:
            aliases: dict[str, str] = {}
            for index, child in enumerate(children, start=1):
                alias = user_input.pop(f"alias_{index}", None)
                if isinstance(alias, str) and alias.strip():
                    aliases[opaque_id(child.id)] = alias.strip()
            user_input[CONF_CHILD_ALIASES] = aliases
            return self.async_create_entry(title="", data=user_input)

        schema: dict[Any, Any] = {
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=int(scan_interval_from_entry(entry).total_seconds() / 60),
            ): vol.All(
                vol.Coerce(int),
                vol.Range(
                    min=int(MIN_SCAN_INTERVAL.total_seconds() / 60),
                    max=24 * 60,
                ),
            ),
            vol.Optional(
                CONF_ENABLE_MENU,
                default=bool(entry.options.get(CONF_ENABLE_MENU, True)),
            ): cv.boolean,
            vol.Optional(
                CONF_ENABLE_FEED,
                default=bool(entry.options.get(CONF_ENABLE_FEED, True)),
            ): cv.boolean,
            vol.Optional(
                CONF_ENABLE_NOTIFICATIONS,
                default=bool(entry.options.get(CONF_ENABLE_NOTIFICATIONS, True)),
            ): cv.boolean,
            vol.Optional(
                CONF_ENABLE_EVENTS,
                default=bool(entry.options.get(CONF_ENABLE_EVENTS, False)),
            ): cv.boolean,
        }
        for index, child in enumerate(children, start=1):
            field = f"alias_{index}"
            schema[
                vol.Optional(
                    field,
                    default=current_aliases.get(opaque_id(child.id), ""),
                )
            ] = str

        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema))
