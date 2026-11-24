"""Constants for the Tempo integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Final

DOMAIN: Final = "tempo"
LOGGER = logging.getLogger(__package__)

CONF_TRACKER_ENTITY_ID: Final = "tracker_entity_id"
CONF_NOTIFY_SERVICE: Final = "notify_service"
CONF_DASHBOARD: Final = "dashboard"
CONF_DASHBOARD_REQUIRE_ADMIN: Final = "dashboard_require_admin"
CONF_HOME_PLACE_ID: Final = "home_place_id"

DEFAULT_SCAN_INTERVAL: Final = 60
MIN_SCAN_INTERVAL: Final = 30
DEFAULT_TIMEOUT_SECONDS: Final = 15

# Coordinator schedule window around "now".
SCHEDULE_PAST: Final = timedelta(days=1)
SCHEDULE_FUTURE: Final = timedelta(days=14)
# Backend refuses /v1/schedule ranges longer than this.
MAX_SCHEDULE_RANGE: Final = timedelta(days=62)

# Position push throttling.
LOCATION_MIN_INTERVAL: Final = timedelta(seconds=60)
LOCATION_MIN_DISTANCE_M: Final = 100.0

# Default study effort when a to-do item is created without "<n> min" in its description.
DEFAULT_TASK_EFFORT_MINUTES: Final = 60

# Mobile app actionable notifications.
MOBILE_ACTION_EVENT: Final = "mobile_app_notification_action"
ACTION_APPROVE_PREFIX: Final = "TEMPO_APPROVE_"
ACTION_REJECT_PREFIX: Final = "TEMPO_REJECT_"

DEPARTURE_ALERT_STATUSES: Final = frozenset({"leave_now", "leave_earlier", "at_risk", "late"})
DEPARTURE_STATUSES: Final = [
    "on_time",
    "leave_now",
    "leave_earlier",
    "at_risk",
    "late",
    "impossible",
    "unknown_position",
    "unknown_routing",
    "none",
]
PLAN_STATUSES: Final = ["complete", "partial", "no_solution", "invalid", "none"]
