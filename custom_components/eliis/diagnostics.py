"""Redacted config-entry diagnostics for Eliis."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_COOKIE,
    CONF_USERNAME,
)
from .runtime import EliisRuntimeData, scan_interval_from_entry

_REDACT_KEYS = {
    CONF_COOKIE,
    CONF_USERNAME,
    "id",
    "ids",
    "name",
    "names",
    "email",
    "url",
    "urls",
    "child",
    "children",
    "institution",
    "institutions",
    "account_id",
    "unique_id",
}


def _module_diagnostics(runtime: EliisRuntimeData) -> dict[str, Any]:
    """Return aggregate module state without identifiers or content."""
    modules: dict[str, Any] = {}

    account = runtime.account_coordinator
    modules["account"] = {
        "last_update_success": account.last_update_success,
        "last_exception": type(account.last_exception).__name__
        if account.last_exception
        else None,
        "notification_count": (
            account.data.notification_count if account.data is not None else None
        ),
    }

    for index, coordinator in enumerate(runtime.menu_coordinators.values()):
        modules[f"menu_{index}"] = {
            "last_update_success": coordinator.last_update_success,
            "last_exception": type(coordinator.last_exception).__name__
            if coordinator.last_exception
            else None,
            "day_count": len(coordinator.data.days)
            if coordinator.data is not None
            else None,
            "complete": coordinator.data.complete
            if coordinator.data is not None
            else None,
            "malformed": coordinator.data.malformed
            if coordinator.data is not None
            else None,
        }

    for index, coordinator in enumerate(runtime.child_coordinators.values()):
        modules[f"child_{index}"] = {
            "last_update_success": coordinator.last_update_success,
            "last_exception": type(coordinator.last_exception).__name__
            if coordinator.last_exception
            else None,
            "status_ok": coordinator.data.status_ok
            if coordinator.data is not None
            else None,
            "feed_ok": coordinator.data.feed_ok
            if coordinator.data is not None
            else None,
            "has_latest_diary": bool(
                coordinator.data.latest_diary_date
                if coordinator.data is not None
                else None
            ),
            "diary_count": (
                coordinator.data.diary_count if coordinator.data is not None else None
            ),
        }

    for index, coordinator in enumerate(runtime.events_coordinators.values()):
        modules[f"events_{index}"] = {
            "last_update_success": coordinator.last_update_success,
            "last_exception": type(coordinator.last_exception).__name__
            if coordinator.last_exception
            else None,
            "event_count": len(coordinator.data.events)
            if coordinator.data is not None
            else None,
            "omitted_count": coordinator.data.omitted_count
            if coordinator.data is not None
            else None,
            "complete": coordinator.data.complete
            if coordinator.data is not None
            else None,
        }

    return modules


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return allowlisted diagnostics for one config entry."""
    runtime: EliisRuntimeData | None = getattr(entry, "runtime_data", None)
    if runtime is None:
        return {"entry": {"state": "not_loaded"}}

    payload: dict[str, Any] = {
        "entry": {
            "version": entry.version,
            "state": entry.state.value,
            "has_username": bool(runtime.username),
            "has_session": bool(runtime.client.cookie),
        },
        "client": {"app_version": runtime.client.app_version},
        "options": {
            "scan_interval_minutes": scan_interval_from_entry(entry).total_seconds()
            / 60,
            "enable_menu": runtime.enable_menu,
            "enable_feed": runtime.enable_feed,
            "enable_notifications": runtime.enable_notifications,
            "enable_events": runtime.enable_events,
        },
        "account": {
            "institution_count": len(runtime.institutions),
            "child_count": len(runtime.children),
        },
        "modules": _module_diagnostics(runtime),
    }
    return async_redact_data(payload, _REDACT_KEYS)
