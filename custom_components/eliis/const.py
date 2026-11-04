"""Constants for the Eliis integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "eliis"

API_BASE_URL: Final = "https://api.eliis.eu"
API_APP_VERSION: Final = "2.69"

# Transport limits from the integration plan.
REQUEST_TIMEOUT: Final = 30
MAX_RESPONSE_BYTES: Final = 8 * 1024 * 1024
MAX_CONCURRENT_REQUESTS: Final = 2

# Polling defaults. These are integration choices, not upstream rate limits.
DEFAULT_SCAN_INTERVAL: Final = timedelta(minutes=15)
MIN_SCAN_INTERVAL: Final = timedelta(minutes=5)
MENU_INTERVAL: Final = timedelta(hours=6)
EVENTS_INTERVAL: Final = timedelta(hours=1)
DISCOVERY_INTERVAL: Final = timedelta(hours=24)

# Action limits from the integration plan.
MAX_ACTION_DAYS: Final = 31
MAX_ACTION_REQUESTS: Final = 3
MAX_ACTION_ITEMS: Final = 100
MAX_ACTION_TEXT_CHARS: Final = 32_000

# Menu content limits.
MAX_MENU_MEALS: Final = 8
MAX_MENU_TEXT_CHARS: Final = 8_000

# Calendar limits.
CALENDAR_HORIZON: Final = timedelta(days=31)
CALENDAR_SLICE: Final = timedelta(days=31)
CALENDAR_CACHE_TTL: Final = timedelta(minutes=60)
CALENDAR_MAX_SLICES: Final = 3

CONF_ACCOUNT_ID: Final = "account_id"
CONF_APP_VERSION: Final = "app_version"
CONF_CHILD_ALIASES: Final = "child_aliases"
CONF_CHILDREN: Final = "children"
CONF_COOKIE: Final = "cookie"
CONF_ENABLE_EVENTS: Final = "enable_events"
CONF_ENABLE_FEED: Final = "enable_feed"
CONF_ENABLE_MENU: Final = "enable_menu"
CONF_ENABLE_NOTIFICATIONS: Final = "enable_notifications"
CONF_INSTITUTIONS: Final = "institutions"
CONF_PAIRS: Final = "pairs"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_USERNAME: Final = "username"

MODULE_ACCOUNT: Final = "account"
MODULE_MENU: Final = "menu"
MODULE_CHILD: Final = "child"
MODULE_EVENTS: Final = "events"
MODULE_STATUS: Final = "status"
MODULE_FEED: Final = "feed"
MODULE_NOTIFICATIONS: Final = "notifications"

MODULES: Final = (
    MODULE_MENU,
    MODULE_FEED,
    MODULE_STATUS,
    MODULE_NOTIFICATIONS,
    MODULE_EVENTS,
)

PARTIAL_CALENDAR_ISSUE_ID: Final = "partial_calendar"
