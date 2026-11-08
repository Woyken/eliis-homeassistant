"""Shared entity and device helpers."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import (
    DeviceEntry,
    DeviceEntryType,
    DeviceInfo,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api.models import Child, Institution
from .const import CONF_CHILD_ALIASES, DOMAIN
from .runtime import children_from_entry, institutions_from_entry
from .util import opaque_id


def account_identifier(account_id: str) -> tuple[str, str]:
    """Return the registry identifier for one account device."""
    return (DOMAIN, f"account:{account_id}")


def institution_identifier(institution_id: str) -> tuple[str, str]:
    """Return the account-independent registry identifier for an institution."""
    return (DOMAIN, f"institution:{opaque_id(institution_id)}")


def child_identifier(child_id: str) -> tuple[str, str]:
    """Return the account-independent registry identifier for a child."""
    return (DOMAIN, f"child:{opaque_id(child_id)}")


def account_scope(account_id: str) -> str:
    """Return the unique-ID scope for account-level entities.

    The raw account ID is kept for this scope so existing account entities keep
    their unique IDs after upgrading to shared institution/child devices.
    """
    return account_id


def institution_scope(institution_id: str) -> str:
    """Return the shared unique-ID scope for an institution."""
    return f"institution:{opaque_id(institution_id)}"


def child_scope(child_id: str) -> str:
    """Return the shared unique-ID scope for a child."""
    return f"child:{opaque_id(child_id)}"


def account_device_info(runtime: Any) -> DeviceInfo:
    """Return the logical account device."""
    return DeviceInfo(
        identifiers={account_identifier(runtime.account_id)},
        name="Eliis",
        manufacturer="Eliis",
        entry_type=DeviceEntryType.SERVICE,
    )


def institution_device_info(runtime: Any, institution: Institution) -> DeviceInfo:
    """Return the logical institution device."""
    return DeviceInfo(
        identifiers={institution_identifier(institution.id)},
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
        identifiers={child_identifier(child.id)},
        name=child_name(entry, child),
        manufacturer="Eliis",
        entry_type=DeviceEntryType.SERVICE,
    )


def _selections(
    entry: ConfigEntry,
) -> tuple[tuple[Institution, ...], tuple[Child, ...]]:
    """Return the stored institutions and children selected by an entry."""
    return institutions_from_entry(entry), children_from_entry(entry)


def shared_owner_entry(
    hass: HomeAssistant,
    *,
    institution_id: str | None = None,
    child_id: str | None = None,
) -> ConfigEntry | None:
    """Return the deterministic owner of a shared institution or child.

    Home Assistant devices belong to exactly one config entry. The oldest entry
    that can see the real institution or child therefore owns its device and
    entities. Other entries reuse that owner instead of creating duplicate
    devices or `_2` entities.
    """
    candidates: list[ConfigEntry] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.domain != DOMAIN:
            continue
        institutions, children = _selections(entry)
        if institution_id is not None:
            visible = any(item.id == institution_id for item in institutions)
        else:
            visible = any(item.id == child_id for item in children)
        if visible:
            candidates.append(entry)
    if not candidates:
        return None
    return min(candidates, key=lambda candidate: candidate.entry_id)


def shared_owner_entry_id(
    hass: HomeAssistant,
    *,
    institution_id: str | None = None,
    child_id: str | None = None,
) -> str | None:
    """Return the config entry ID of the deterministic shared owner."""
    owner = shared_owner_entry(hass, institution_id=institution_id, child_id=child_id)
    return owner.entry_id if owner is not None else None


def is_shared_owner(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    institution_id: str | None = None,
    child_id: str | None = None,
) -> bool:
    """Return whether this entry should create a shared device or entity."""
    return (
        shared_owner_entry_id(hass, institution_id=institution_id, child_id=child_id)
        == entry.entry_id
    )


def _device_by_identifier(
    registry: dr.DeviceRegistry,
    identifier: tuple[str, str],
    entry_id: str,
) -> DeviceEntry | None:
    """Look up a device owned by a specific config entry."""
    getter = getattr(registry, "async_get_device_by_identifier", None)
    if getter is not None:
        return getter(identifier, entry_id)
    return registry.async_get_device(identifiers={identifier})


def _async_relink_children(
    hass: HomeAssistant,
    registry: dr.DeviceRegistry,
    institution_id: str,
    parent_device_id: str,
) -> None:
    """Point owned child devices at a newly available shared institution."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        _, children = _selections(entry)
        for child in children:
            if child.institution_id != institution_id:
                continue
            if not is_shared_owner(hass, entry, child_id=child.id):
                continue
            device = _device_by_identifier(
                registry, child_identifier(child.id), entry.entry_id
            )
            if device is not None and device.via_device_id != parent_device_id:
                registry.async_update_device(device.id, via_device_id=parent_device_id)


def async_ensure_devices(hass: HomeAssistant, entry: ConfigEntry, runtime: Any) -> None:
    """Create the account device and every device this entry owns.

    Shared institutions and children are created only by their deterministic
    owner. A secondary account can still place its own child under the shared
    institution device through `via_device_id`.
    """
    registry = dr.async_get(hass)
    account = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        **account_device_info(runtime),
    )

    for institution in runtime.institutions:
        if not is_shared_owner(hass, entry, institution_id=institution.id):
            continue
        device = registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            **institution_device_info(runtime, institution),
        )
        if device.via_device_id != account.id:
            registry.async_update_device(device.id, via_device_id=account.id)
        _async_relink_children(hass, registry, institution.id, device.id)

    for child in runtime.children:
        if not is_shared_owner(hass, entry, child_id=child.id):
            continue

        parent_device_id = account.id
        if child.institution_id is not None:
            owner_id = shared_owner_entry_id(hass, institution_id=child.institution_id)
            if owner_id is not None:
                parent = _device_by_identifier(
                    registry,
                    institution_identifier(child.institution_id),
                    owner_id,
                )
                if parent is not None:
                    parent_device_id = parent.id

        device = registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            **child_device_info(runtime, entry, child),
        )
        if device.via_device_id != parent_device_id:
            registry.async_update_device(device.id, via_device_id=parent_device_id)


class EliisEntity(CoordinatorEntity[Any]):
    """Base entity with an object-scoped unique ID."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: Any,
        *,
        unique_scope: str,
        unique_key: str,
        device_info: DeviceInfo,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._attr_unique_id = opaque_id("eliis", unique_scope, unique_key)
        self._attr_device_info = device_info
