"""Unofficial read-only Eliis integration for Home Assistant."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_track_time_change

from .const import (
    CONF_ACCOUNT_ID,
    CONF_ENABLE_EVENTS,
    CONF_ENABLE_FEED,
    CONF_ENABLE_MENU,
    CONF_ENABLE_NOTIFICATIONS,
    DOMAIN,
)
from .coordinator import (
    AccountCoordinator,
    ChildCoordinator,
    EventsCoordinator,
    MenuCoordinator,
)
from .entity import async_ensure_devices, is_shared_owner
from .runtime import (
    EliisRuntimeData,
    build_client,
    children_from_entry,
    institutions_from_entry,
    scan_interval_from_entry,
    username_from_entry,
)
from .services import async_setup_services
from .util import opaque_id

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


def _option(entry: ConfigEntry, key: str, default: bool) -> bool:
    """Return a boolean option."""
    value = entry.options.get(key, default)
    return bool(value)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register integration-wide actions exactly once."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one Eliis account."""
    account_id = entry.data.get(CONF_ACCOUNT_ID)
    if not isinstance(account_id, str) or not account_id:
        _LOGGER.error("Eliis config entry is missing its account identifier")
        return False

    client = build_client(entry)
    institutions = institutions_from_entry(entry)
    children = children_from_entry(entry)
    scan_interval = scan_interval_from_entry(entry)

    enable_menu = _option(entry, CONF_ENABLE_MENU, True)
    enable_feed = _option(entry, CONF_ENABLE_FEED, True)
    enable_notifications = _option(entry, CONF_ENABLE_NOTIFICATIONS, True)
    enable_events = _option(entry, CONF_ENABLE_EVENTS, False)

    account_coordinator = AccountCoordinator(
        hass,
        entry,
        client,
        enable_notifications=enable_notifications,
    )
    menu_coordinators = {
        institution.id: MenuCoordinator(hass, entry, client, institution)
        for institution in institutions
        if enable_menu and is_shared_owner(hass, entry, institution_id=institution.id)
    }
    child_coordinators = {
        child.id: ChildCoordinator(hass, entry, client, child, enable_feed=enable_feed)
        for child in children
        if is_shared_owner(hass, entry, child_id=child.id)
    }
    events_coordinators = {
        institution.id: EventsCoordinator(hass, entry, client, institution)
        for institution in institutions
        if enable_events and is_shared_owner(hass, entry, institution_id=institution.id)
    }

    runtime = EliisRuntimeData(
        client=client,
        account_id=account_id,
        username=username_from_entry(entry),
        institutions=institutions,
        children=children,
        account_coordinator=account_coordinator,
        menu_coordinators=menu_coordinators,
        child_coordinators=child_coordinators,
        events_coordinators=events_coordinators,
        scan_interval=scan_interval,
        enable_menu=enable_menu,
        enable_feed=enable_feed,
        enable_notifications=enable_notifications,
        enable_events=enable_events,
    )
    entry.runtime_data = runtime

    # Optional modules fail independently. A first refresh records the failure
    # without preventing healthy entities from loading.
    await asyncio.gather(
        *(coordinator.async_refresh() for coordinator in runtime.coordinators)
    )

    platforms = list(PLATFORMS)
    if enable_events:
        platforms.append(Platform.CALENDAR)
    async_ensure_devices(hass, entry, runtime)
    await hass.config_entries.async_forward_entry_setups(entry, platforms)

    from homeassistant.core import callback

    @callback
    def _schedule_midnight_refresh(now: object) -> None:
        """Recompute date-sensitive modules at local midnight."""
        hass.async_create_task(_async_refresh_date_modules())

    async def _async_refresh_date_modules() -> None:
        for coordinator in (
            *runtime.menu_coordinators.values(),
            *runtime.child_coordinators.values(),
            *runtime.events_coordinators.values(),
        ):
            await coordinator.async_request_refresh()

    entry.async_on_unload(
        async_track_time_change(
            hass, _schedule_midnight_refresh, hour=0, minute=0, second=5
        )
    )
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    async def _async_close_client() -> None:
        """Close the account-scoped session when the entry unloads."""
        await client.async_close()

    entry.async_on_unload(_async_close_client)

    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry after option changes."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload one Eliis account."""
    runtime: EliisRuntimeData | None = getattr(entry, "runtime_data", None)
    platforms = list(PLATFORMS)
    if runtime is not None and runtime.enable_events:
        platforms.append(Platform.CALENDAR)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, platforms)

    if runtime is not None:
        from homeassistant.helpers import issue_registry as ir

        for coordinator in runtime.events_coordinators.values():
            coordinator.async_invalidate_cache()
        ir.async_delete_issue(hass, DOMAIN, f"partial_calendar_{entry.entry_id}")

    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove leftover integration issues and hand shared objects over."""
    from homeassistant.helpers import issue_registry as ir

    ir.async_delete_issue(hass, DOMAIN, f"partial_calendar_{entry.entry_id}")

    runtime: EliisRuntimeData | None = getattr(entry, "runtime_data", None)
    if runtime is not None:
        institutions = {item.id for item in runtime.institutions}
        children = {item.id for item in runtime.children}
    else:
        institutions = {item.id for item in institutions_from_entry(entry)}
        children = {item.id for item in children_from_entry(entry)}
    if not institutions and not children:
        return

    async def _async_reload_affected() -> None:
        """Let another account create the shared devices and entities."""
        for other in hass.config_entries.async_entries(DOMAIN):
            if other.entry_id == entry.entry_id:
                continue
            if other.state is not ConfigEntryState.LOADED:
                continue
            other_runtime: EliisRuntimeData | None = getattr(
                other, "runtime_data", None
            )
            if other_runtime is not None:
                other_institutions = {item.id for item in other_runtime.institutions}
                other_children = {item.id for item in other_runtime.children}
            else:
                other_institutions = {
                    item.id for item in institutions_from_entry(other)
                }
                other_children = {item.id for item in children_from_entry(other)}
            if (institutions & other_institutions) or (children & other_children):
                await hass.config_entries.async_reload(other.entry_id)

    hass.async_create_task(_async_reload_affected())


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate account-scoped devices to shared account-independent ones."""
    if entry.version > 2:
        return False
    if entry.version < 2:
        account_id = entry.data.get(CONF_ACCOUNT_ID)
        if isinstance(account_id, str) and account_id:
            registry = dr.async_get(hass)
            identifiers: list[tuple[str, str]] = [
                (DOMAIN, f"institution:{account_id}:{opaque_id(item.id)}")
                for item in institutions_from_entry(entry)
            ]
            identifiers.extend(
                (DOMAIN, f"child:{account_id}:{opaque_id(item.id)}")
                for item in children_from_entry(entry)
            )
            getter = getattr(registry, "async_get_device_by_identifier", None)
            for identifier in identifiers:
                device = (
                    getter(identifier, entry.entry_id)
                    if getter is not None
                    else registry.async_get_device(identifiers={identifier})
                )
                if device is not None:
                    registry.async_remove_device(device.id)
        hass.config_entries.async_update_entry(entry, version=2)
    return True


__all__ = ["async_setup", "async_setup_entry", "async_unload_entry"]
