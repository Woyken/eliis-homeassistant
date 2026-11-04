"""Admin-only read actions for the Eliis integration."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.util import dt as dt_util

from .api import EliisAuthError, EliisClient, EliisError
from .api.models import Child, DiaryRecord, Institution, MenuDay
from .api.parsers import (
    parse_feed,
    parse_menu,
    parse_status_history,
)
from .const import (
    DOMAIN,
    MAX_ACTION_DAYS,
    MAX_ACTION_ITEMS,
    MAX_ACTION_REQUESTS,
    MODULE_EVENTS,
    MODULE_FEED,
    MODULE_MENU,
    MODULE_NOTIFICATIONS,
    MODULE_STATUS,
)
from .runtime import EliisRuntimeData
from .util import opaque_id, utcnow

_LOGGER = logging.getLogger(__name__)

_MENU_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Optional("start_date"): cv.date,
        vol.Optional("end_date"): cv.date,
    }
)
_FEED_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Optional("pages", default=1): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=MAX_ACTION_REQUESTS)
        ),
    }
)
_STATUS_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("date"): cv.date,
    }
)
_REFRESH_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): cv.string,
        vol.Optional("module"): vol.In(
            (
                MODULE_MENU,
                MODULE_FEED,
                MODULE_STATUS,
                MODULE_NOTIFICATIONS,
                MODULE_EVENTS,
            )
        ),
    }
)


def async_setup_services(hass: HomeAssistant) -> None:
    """Register global admin-only actions once per HA instance."""
    if hass.services.has_service(DOMAIN, "get_menu"):
        return

    async_register_admin_service(
        hass,
        DOMAIN,
        "get_menu",
        async_get_menu,
        schema=_MENU_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        "get_feed_summary",
        async_get_feed_summary,
        schema=_FEED_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        "get_status_history",
        async_get_status_history,
        schema=_STATUS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        "refresh",
        async_refresh,
        schema=_REFRESH_SCHEMA,
        supports_response=SupportsResponse.NONE,
    )


def _loaded_runtime(hass: HomeAssistant, entry_id: str) -> EliisRuntimeData:
    """Return runtime data for a loaded Eliis entry."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="entry_not_found"
        )
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="entry_not_loaded"
        )
    runtime = getattr(entry, "runtime_data", None)
    if runtime is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="entry_not_loaded"
        )
    return runtime


def _device_target(
    hass: HomeAssistant, device_id: str
) -> tuple[EliisRuntimeData, Institution | Child]:
    """Resolve a device to an account-scoped institution or child."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="device_not_found"
        )

    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is not ConfigEntryState.LOADED:
            continue
        runtime = getattr(entry, "runtime_data", None)
        if runtime is None:
            continue
        for institution in runtime.institutions:
            identifier = (
                DOMAIN,
                f"institution:{runtime.account_id}:{opaque_id(institution.id)}",
            )
            if identifier in device.identifiers:
                return runtime, institution
        for child in runtime.children:
            identifier = (
                DOMAIN,
                f"child:{runtime.account_id}:{opaque_id(child.id)}",
            )
            if identifier in device.identifiers:
                return runtime, child

    raise ServiceValidationError(
        translation_domain=DOMAIN, translation_key="device_not_found"
    )


def _require_institution(
    hass: HomeAssistant, device_id: str
) -> tuple[EliisRuntimeData, Institution]:
    runtime, target = _device_target(hass, device_id)
    if not isinstance(target, Institution):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="institution_device_required"
        )
    return runtime, target


def _require_child(
    hass: HomeAssistant, device_id: str
) -> tuple[EliisRuntimeData, Child]:
    runtime, target = _device_target(hass, device_id)
    if not isinstance(target, Child):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="child_device_required"
        )
    return runtime, target


def _start_reauth(hass: HomeAssistant, runtime: EliisRuntimeData) -> None:
    """Start one reauthentication flow after a confirmed auth rejection."""
    runtime.account_coordinator.config_entry.async_start_reauth(hass)


async def _fetch_week(
    client: EliisClient, institution_id: str, anchor: date
) -> tuple[Any, Any]:
    """Fetch and parse one menu week."""
    payload = await client.async_get_week_menu(institution_id, anchor.isoformat())
    return parse_menu(payload), utcnow()


async def async_get_menu(call: ServiceCall) -> dict[str, Any]:
    """Return normalized weekly-menu days for an institution device."""
    runtime, institution = _require_institution(call.hass, call.data["device_id"])
    start = call.data.get("start_date") or dt_util.now().date()
    end = call.data.get("end_date") or start
    if end < start:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="invalid_date_range"
        )
    if (end - start).days + 1 > MAX_ACTION_DAYS:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="range_too_large",
            translation_placeholders={"max_days": str(MAX_ACTION_DAYS)},
        )

    anchors: list[date] = []
    cursor = start
    while cursor <= end:
        anchor = cursor - timedelta(days=cursor.weekday())
        if anchor not in anchors:
            anchors.append(anchor)
        cursor = anchor + timedelta(days=7)
    if len(anchors) > MAX_ACTION_REQUESTS:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="range_too_large",
            translation_placeholders={"max_days": str(MAX_ACTION_DAYS)},
        )

    days: dict[date, MenuDay] = {}
    oldest_fetch = None
    failures = 0
    malformed = False

    for anchor in anchors:
        try:
            result, fetched_at = await _fetch_week(
                runtime.client, institution.id, anchor
            )
        except EliisAuthError as err:
            _start_reauth(call.hass, runtime)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except EliisError as err:
            failures += 1
            _LOGGER.debug("Menu action week failed (%s)", type(err).__name__)
            continue
        malformed = malformed or result.malformed
        if oldest_fetch is None or fetched_at < oldest_fetch:
            oldest_fetch = fetched_at
        for day in result.days:
            days.setdefault(day.date, day)

    if failures == len(anchors):
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="cannot_fetch_menu"
        )

    output_days: list[dict[str, Any]] = []
    truncated = False
    cursor = start
    while cursor <= end:
        day = days.get(cursor)
        if day is None:
            if failures == 0 and not malformed:
                output_days.append(
                    {
                        "date": cursor.isoformat(),
                        "meals": [],
                        "content_truncated": False,
                    }
                )
        else:
            truncated = truncated or day.truncated
            output_days.append(
                {
                    "date": day.date.isoformat(),
                    "meals": [
                        {
                            "key": meal.key,
                            "label": meal.label,
                            "text": meal.text,
                        }
                        for meal in day.meals
                    ],
                    "content_truncated": day.truncated,
                }
            )
        cursor += timedelta(days=1)

    return {
        "days": output_days[:MAX_ACTION_ITEMS],
        "fetched_at": (oldest_fetch or utcnow()).isoformat(),
        "coverage": {
            "complete": failures == 0 and not malformed and not truncated,
            "truncated": truncated or failures > 0,
        },
    }


async def async_get_feed_summary(call: ServiceCall) -> dict[str, Any]:
    """Return bounded guardian-feed date/count summaries."""
    runtime, child = _require_child(call.hass, call.data["device_id"])
    assert child.institution_id is not None
    pages = int(call.data.get("pages", 1))

    cursor: str | None = None
    seen_cursors: set[str] = set()
    grouped: dict[date, list[DiaryRecord]] = {}
    seen_ids: set[str] = set()
    oldest_fetch = None
    malformed = False
    truncated = False
    fetched_pages = 0

    for page_number in range(pages):
        try:
            payload = await runtime.client.async_get_guardian_feed(
                child.institution_id, child.id, cursor
            )
        except EliisAuthError as err:
            _start_reauth(call.hass, runtime)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except EliisError as err:
            if fetched_pages == 0:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="cannot_fetch_feed",
                ) from err
            truncated = True
            break

        fetched_at = utcnow()
        if oldest_fetch is None or fetched_at < oldest_fetch:
            oldest_fetch = fetched_at
        try:
            page = parse_feed(payload, fetched_at=fetched_at)
        except EliisError as err:
            malformed = True
            if fetched_pages == 0:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="cannot_fetch_feed",
                ) from err
            break
        fetched_pages += 1
        malformed = malformed or page.malformed

        for record in page.diary_records:
            if record.id in seen_ids:
                continue
            seen_ids.add(record.id)
            grouped.setdefault(record.date, []).append(record)
            if len(seen_ids) >= MAX_ACTION_ITEMS:
                truncated = True
                break
        if truncated:
            break

        if page.next_date is None:
            cursor = None
            break
        if page.next_date == cursor or page.next_date in seen_cursors:
            malformed = True
            break
        seen_cursors.add(page.next_date)
        cursor = page.next_date
        if page_number + 1 >= pages and page.next_date is not None:
            truncated = True

    groups = [
        {
            "date": group_date.isoformat(),
            "diary_count": len(records),
            "courses": sorted(
                {record.course for record in records if record.course is not None}
            ),
            "statuses": sorted(
                {record.status for record in records if record.status is not None}
            ),
        }
        for group_date, records in sorted(
            grouped.items(), key=lambda item: item[0], reverse=True
        )
    ]

    return {
        "groups": groups,
        "fetched_at": (oldest_fetch or utcnow()).isoformat(),
        "coverage": {
            "complete": cursor is None and not malformed and not truncated,
            "truncated": truncated,
        },
    }


async def async_get_status_history(call: ServiceCall) -> dict[str, Any]:
    """Return status-log records for one explicit date."""
    runtime, child = _require_child(call.hass, call.data["device_id"])
    assert child.institution_id is not None
    target: date = call.data["date"]

    try:
        payload = await runtime.client.async_get_status_log(
            child.institution_id, child.id, target.isoformat()
        )
    except EliisAuthError as err:
        _start_reauth(call.hass, runtime)
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="auth_failed"
        ) from err
    except EliisError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="cannot_fetch_status"
        ) from err

    try:
        history = parse_status_history(payload)
    except EliisError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="cannot_fetch_status"
        ) from err
    records = [
        {
            "label": record.label,
            "created_at": record.created_at.isoformat()
            if record.created_at is not None
            else None,
            "total_time": record.total_time,
        }
        for record in history.records[:MAX_ACTION_ITEMS]
    ]
    return {
        "date": target.isoformat(),
        "records": records,
        "fetched_at": utcnow().isoformat(),
        "coverage": {
            "complete": not history.malformed,
            "truncated": len(history.records) > MAX_ACTION_ITEMS,
        },
    }


async def async_refresh(call: ServiceCall) -> None:
    """Request a coalesced local refresh for one entry or module."""
    runtime = _loaded_runtime(call.hass, call.data["config_entry_id"])
    module = call.data.get("module")

    if module in (None, MODULE_MENU):
        coordinators = list(runtime.menu_coordinators.values())
    elif module in (MODULE_FEED, MODULE_STATUS):
        coordinators = list(runtime.child_coordinators.values())
    elif module == MODULE_NOTIFICATIONS:
        coordinators = (
            [runtime.account_coordinator] if runtime.enable_notifications else []
        )
    elif module == MODULE_EVENTS:
        coordinators = list(runtime.events_coordinators.values())
    else:
        coordinators = []

    if module is None:
        coordinators = list(runtime.coordinators)

    if not coordinators:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="module_not_enabled"
        )

    await asyncio.gather(
        *(coordinator.async_request_refresh() for coordinator in coordinators)
    )
