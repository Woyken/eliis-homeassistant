"""Shared entity and device helpers."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api.models import Child, Institution
from .const import CONF_CHILD_ALIASES, DOMAIN
from .util import opaque_id


def account_device_info(runtime: Any) -> DeviceInfo:
    """Return the logical account device."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"account:{runtime.account_id}")},
        name="Eliis",
        manufacturer="Eliis",
        entry_type=DeviceEntryType.SERVICE,
    )


def institution_device_info(runtime: Any, institution: Institution) -> DeviceInfo:
    """Return the logical institution device."""
    return DeviceInfo(
        identifiers={
            (DOMAIN, f"institution:{runtime.account_id}:{opaque_id(institution.id)}")
        },
        name=institution.name or "Eliis institution",
        manufacturer="Eliis",
        entry_type=DeviceEntryType.SERVICE,
    )


def child_name(entry: ConfigEntry, child: Child) -> str:
    """Return the display alias for a child, if configured."""
    aliases = entry.options.get(CONF_CHILD_ALIASES)
    if isinstance(aliases, dict):
        alias = aliases.get(opaque_id(child.id)) or aliases.get(child.id)
        if isinstance(alias, str) and alias.strip():
            return alias.strip()
    return child.name or "Eliis child"


def child_device_info(runtime: Any, entry: ConfigEntry, child: Child) -> DeviceInfo:
    """Return the logical child device."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"child:{runtime.account_id}:{opaque_id(child.id)}")},
        name=child_name(entry, child),
        manufacturer="Eliis",
        entry_type=DeviceEntryType.SERVICE,
    )


def async_ensure_devices(hass: HomeAssistant, entry: ConfigEntry, runtime: Any) -> None:
    """Create the account device tree and link each child to its parent."""
    registry = dr.async_get(hass)
    account = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        **account_device_info(runtime),
    )

    institutions: dict[str, Any] = {}
    for institution in runtime.institutions:
        device = registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            **institution_device_info(runtime, institution),
        )
        if device.via_device_id != account.id:
            registry.async_update_device(device.id, via_device_id=account.id)
        institutions[institution.id] = device

    for child in runtime.children:
        parent = institutions.get(child.institution_id) or account
        device = registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            **child_device_info(runtime, entry, child),
        )
        if device.via_device_id != parent.id:
            registry.async_update_device(device.id, via_device_id=parent.id)


class EliisEntity(CoordinatorEntity[Any]):
    """Base entity with account-scoped unique IDs."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: Any,
        *,
        account_id: str,
        unique_key: str,
        device_info: DeviceInfo,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._attr_unique_id = opaque_id("eliis", account_id, unique_key)
        self._attr_device_info = device_info
