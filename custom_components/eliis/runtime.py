"""Typed runtime data for a loaded Eliis config entry."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry

from .api import EliisClient
from .api.models import Child, Institution
from .const import (
    CONF_APP_VERSION,
    CONF_CHILDREN,
    CONF_COOKIE,
    CONF_INSTITUTIONS,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    DEFAULT_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .coordinator import (
    AccountCoordinator,
    ChildCoordinator,
    EventsCoordinator,
    MenuCoordinator,
)
from .util import safe_version


@dataclass(slots=True)
class EliisRuntimeData:
    """Objects owned by one loaded config entry."""

    client: EliisClient
    account_id: str
    username: str
    institutions: tuple[Institution, ...]
    children: tuple[Child, ...]
    account_coordinator: AccountCoordinator
    menu_coordinators: dict[str, MenuCoordinator] = field(default_factory=dict)
    child_coordinators: dict[str, ChildCoordinator] = field(default_factory=dict)
    events_coordinators: dict[str, EventsCoordinator] = field(default_factory=dict)
    scan_interval: timedelta = DEFAULT_SCAN_INTERVAL
    enable_menu: bool = True
    enable_feed: bool = True
    enable_notifications: bool = True
    enable_events: bool = False

    @property
    def coordinators(self) -> list[Any]:
        """Return every module coordinator for this entry."""
        result: list[Any] = [self.account_coordinator]
        result.extend(self.menu_coordinators.values())
        result.extend(self.child_coordinators.values())
        result.extend(self.events_coordinators.values())
        return result


def institutions_from_entry(entry: ConfigEntry) -> tuple[Institution, ...]:
    """Read stored institution selections."""
    raw = entry.data.get(CONF_INSTITUTIONS)
    if not isinstance(raw, list):
        return ()
    result: list[Institution] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        institution_id = item.get("id")
        if not isinstance(institution_id, str) or not institution_id:
            continue
        name = item.get("name")
        result.append(
            Institution(institution_id, name if isinstance(name, str) else None)
        )
    return tuple(result)


def children_from_entry(entry: ConfigEntry) -> tuple[Child, ...]:
    """Read stored child selections with verified institution pairs."""
    raw = entry.data.get(CONF_CHILDREN)
    if not isinstance(raw, list):
        return ()
    result: list[Child] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        child_id = item.get("id")
        institution_id = item.get("institution_id")
        if not isinstance(child_id, str) or not child_id:
            continue
        if not isinstance(institution_id, str) or not institution_id:
            continue
        name = item.get("name")
        result.append(
            Child(
                child_id,
                name if isinstance(name, str) else None,
                institution_id,
            )
        )
    return tuple(result)


def scan_interval_from_entry(entry: ConfigEntry) -> timedelta:
    """Return a bounded polling interval from options."""
    raw = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL.total_seconds())
    try:
        minutes = float(raw)
    except TypeError, ValueError:
        return DEFAULT_SCAN_INTERVAL
    interval = timedelta(minutes=minutes)
    if interval < MIN_SCAN_INTERVAL:
        return MIN_SCAN_INTERVAL
    return interval


def build_client(entry: ConfigEntry) -> EliisClient:
    """Build the account-scoped client from stored session data."""
    cookie = entry.data.get(CONF_COOKIE)
    return EliisClient(
        cookie=cookie if isinstance(cookie, str) else None,
        app_version=safe_version(
            entry.data.get(CONF_APP_VERSION)
            if isinstance(entry.data.get(CONF_APP_VERSION), str)
            else None
        ),
    )


def username_from_entry(entry: ConfigEntry) -> str:
    """Return the stored login name used for reauthentication."""
    value = entry.data.get(CONF_USERNAME)
    return value if isinstance(value, str) else ""
