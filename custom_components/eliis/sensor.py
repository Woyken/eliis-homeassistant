"""Sensors for the Eliis integration."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, ClassVar

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .api.models import Child, Institution, MenuDay
from .coordinator import (
    AccountCoordinator,
    ChildCoordinator,
    EventsCoordinator,
    MenuCoordinator,
)
from .entity import (
    EliisEntity,
    account_device_info,
    child_device_info,
    institution_device_info,
)
from .runtime import EliisRuntimeData

MENU_AVAILABLE = "content_available"
MENU_EMPTY = "no_content_returned"
MULTIPLE_RECORDS = "multiple_records"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Eliis sensors."""
    runtime: EliisRuntimeData = entry.runtime_data
    entities: list[SensorEntity] = []

    if runtime.enable_notifications:
        entities.append(UnseenNotificationsSensor(runtime.account_coordinator, runtime))
    entities.append(
        ModuleLastSyncSensor(
            runtime.account_coordinator,
            runtime,
            unique_key="sync_account",
            device_info=account_device_info(runtime),
            translation_key="last_successful_sync",
        )
    )

    for institution in runtime.institutions:
        coordinator = runtime.menu_coordinators.get(institution.id)
        if coordinator is None:
            continue
        device_info = institution_device_info(runtime, institution)
        entities.extend(
            (
                MenuSensor(
                    coordinator,
                    runtime,
                    institution,
                    day_offset=0,
                    translation_key="menu_today",
                    device_info=device_info,
                ),
                MenuSensor(
                    coordinator,
                    runtime,
                    institution,
                    day_offset=1,
                    translation_key="menu_tomorrow",
                    device_info=device_info,
                ),
                ModuleLastSyncSensor(
                    coordinator,
                    runtime,
                    unique_key=f"sync_menu_{institution.id}",
                    device_info=device_info,
                    translation_key="last_successful_sync",
                ),
            )
        )

    for child in runtime.children:
        coordinator = runtime.child_coordinators.get(child.id)
        if coordinator is None:
            continue
        device_info = child_device_info(runtime, entry, child)
        entities.extend(
            (
                ChildStatusSensor(
                    coordinator,
                    runtime,
                    child,
                    device_info=device_info,
                ),
                ModuleLastSyncSensor(
                    coordinator,
                    runtime,
                    unique_key=f"sync_child_{child.id}",
                    device_info=device_info,
                    translation_key="last_successful_sync",
                ),
            )
        )
        if runtime.enable_feed:
            entities.extend(
                (
                    LatestDiaryDateSensor(
                        coordinator,
                        runtime,
                        child,
                        device_info=device_info,
                    ),
                    DiaryCountSensor(
                        coordinator,
                        runtime,
                        child,
                        device_info=device_info,
                    ),
                )
            )

    for institution in runtime.institutions:
        coordinator = runtime.events_coordinators.get(institution.id)
        if coordinator is None:
            continue
        entities.append(
            OmittedEventsSensor(
                coordinator,
                runtime,
                institution,
                device_info=institution_device_info(runtime, institution),
            )
        )

    async_add_entities(entities)


class UnseenNotificationsSensor(EliisEntity):
    """Current unseen notification count for the account."""

    _attr_translation_key = "unseen_notifications"
    _attr_icon = "mdi:bell-badge-outline"

    def __init__(
        self, coordinator: AccountCoordinator, runtime: EliisRuntimeData
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator,
            account_id=runtime.account_id,
            unique_key="unseen_notifications",
            device_info=account_device_info(runtime),
        )

    @property
    def native_value(self) -> int | None:
        """Return the count without inventing zero on failure."""
        if self.coordinator.data is None:
            return None
        if not self.coordinator.data.notifications_ok:
            return None
        return self.coordinator.data.notification_count


class MenuSensor(EliisEntity):
    """Today's or tomorrow's menu for one institution."""

    _attr_icon = "mdi:silverware-fork-knife"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options: ClassVar[list[str]] = [MENU_AVAILABLE, MENU_EMPTY]
    _unrecorded_attributes = frozenset({"meals"})

    def __init__(
        self,
        coordinator: MenuCoordinator,
        runtime: EliisRuntimeData,
        institution: Institution,
        *,
        day_offset: int,
        translation_key: str,
        device_info: Any,
    ) -> None:
        """Initialize the menu sensor."""
        super().__init__(
            coordinator,
            account_id=runtime.account_id,
            unique_key=f"{translation_key}_{institution.id}",
            device_info=device_info,
        )
        self._attr_translation_key = translation_key
        self._day_offset = day_offset

    @property
    def _target_date(self) -> date:
        return dt_util.now().date() + timedelta(days=self._day_offset)

    @property
    def _menu_day(self) -> MenuDay | None:
        data = self.coordinator.data
        if data is None:
            return None
        return data.days.get(self._target_date)

    @property
    def native_value(self) -> str | None:
        """Return a neutral content state, never a publication claim."""
        data = self.coordinator.data
        if data is None:
            return None
        menu_day = self._menu_day
        if menu_day is not None and menu_day.meals:
            return MENU_AVAILABLE
        if data.complete:
            return MENU_EMPTY
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return bounded, sanitized menu content."""
        attributes: dict[str, Any] = {"date": self._target_date.isoformat()}
        menu_day = self._menu_day
        if menu_day is None:
            attributes["meals"] = []
            attributes["content_truncated"] = False
            return attributes
        attributes["meals"] = [
            {"key": meal.key, "label": meal.label, "text": meal.text}
            for meal in menu_day.meals
        ]
        attributes["content_truncated"] = menu_day.truncated
        return attributes


class ChildStatusSensor(EliisEntity):
    """Neutral child-calendar status for the current local date."""

    _attr_icon = "mdi:calendar-account-outline"
    _attr_translation_key = "calendar_status_today"

    def __init__(
        self,
        coordinator: ChildCoordinator,
        runtime: EliisRuntimeData,
        child: Child,
        *,
        device_info: Any,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator,
            account_id=runtime.account_id,
            unique_key=f"calendar_status_{child.id}",
            device_info=device_info,
        )
        self._child = child

    @property
    def native_value(self) -> str | None:
        """Return the explicit source label, never a presence claim."""
        data = self.coordinator.data
        if data is None or not data.status_ok or data.day_status is None:
            return None
        labels = data.day_status.labels
        if not labels:
            return None
        if len(labels) == 1:
            return labels[0]
        return MULTIPLE_RECORDS

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the source date and course count only."""
        data = self.coordinator.data
        if data is None or data.day_status is None:
            return {}
        return {
            "date": data.day_status.date.isoformat(),
            "course_count": data.day_status.course_count,
        }


class LatestDiaryDateSensor(EliisEntity):
    """Newest fetched guardian-feed date with a valid diary record."""

    _attr_device_class = SensorDeviceClass.DATE
    _attr_icon = "mdi:calendar-text-outline"
    _attr_translation_key = "latest_diary_date"

    def __init__(
        self,
        coordinator: ChildCoordinator,
        runtime: EliisRuntimeData,
        child: Child,
        *,
        device_info: Any,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator,
            account_id=runtime.account_id,
            unique_key=f"latest_diary_date_{child.id}",
            device_info=device_info,
        )

    @property
    def native_value(self) -> date | None:
        """Return the newest fetched diary date, not a publication time."""
        data = self.coordinator.data
        if data is None or not data.feed_ok:
            return None
        return data.latest_diary_date


class DiaryCountSensor(EliisEntity):
    """Number of valid diary records on the latest fetched date."""

    _attr_icon = "mdi:book-open-variant"
    _attr_translation_key = "diary_records"

    def __init__(
        self,
        coordinator: ChildCoordinator,
        runtime: EliisRuntimeData,
        child: Child,
        *,
        device_info: Any,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator,
            account_id=runtime.account_id,
            unique_key=f"diary_records_{child.id}",
            device_info=device_info,
        )

    @property
    def native_value(self) -> int | None:
        """Return the count without inventing zero from malformed data."""
        data = self.coordinator.data
        if data is None or not data.feed_ok:
            return None
        return data.diary_count


class ModuleLastSyncSensor(EliisEntity):
    """Timestamp of the last successful module refresh."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_icon = "mdi:cloud-check-outline"

    def __init__(
        self,
        coordinator: Any,
        runtime: EliisRuntimeData,
        *,
        unique_key: str,
        device_info: Any,
        translation_key: str,
    ) -> None:
        """Initialize the diagnostic sensor."""
        super().__init__(
            coordinator,
            account_id=runtime.account_id,
            unique_key=unique_key,
            device_info=device_info,
        )
        self._attr_translation_key = translation_key

    @property
    def native_value(self) -> Any:
        """Return the source fetch time for the last successful update."""
        if self.coordinator.data is None or not self.coordinator.last_update_success:
            return None
        return getattr(self.coordinator.data, "fetched_at", None)


class OmittedEventsSensor(EliisEntity):
    """Number of events omitted because they cannot be represented."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_icon = "mdi:calendar-alert"
    _attr_translation_key = "omitted_events"

    def __init__(
        self,
        coordinator: EventsCoordinator,
        runtime: EliisRuntimeData,
        institution: Institution,
        *,
        device_info: Any,
    ) -> None:
        """Initialize the diagnostic sensor."""
        super().__init__(
            coordinator,
            account_id=runtime.account_id,
            unique_key=f"omitted_events_{institution.id}",
            device_info=device_info,
        )

    @property
    def native_value(self) -> int | None:
        """Return the omitted event count."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.omitted_count
