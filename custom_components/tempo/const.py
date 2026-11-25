"""Constants for the Tempo integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Final

DOMAIN: Final = "tempo"
LOGGER = logging.getLogger(__package__)

CONF_TRACKER_ENTITY_ID: Final = "tracker_entity_id"
CONF_NOTIFY_SERVICE: Final = "notify_service"
CONF_HOME_FROM_ZONE: Final = "home_from_zone"

DEFAULT_SCAN_INTERVAL: Final = 60
MIN_SCAN_INTERVAL: Final = 30
DEFAULT_TIMEOUT_SECONDS: Final = 15
CONVERSATION_TIMEOUT_SECONDS: Final = 150
"""The backend assistant stops starting model calls after 60 s; one re-plan and the
final answer still fit: the user hears the real outcome, never a client timeout."""

# Coordinator schedule window around "now".
SCHEDULE_PAST: Final = timedelta(days=1)
SCHEDULE_FUTURE: Final = timedelta(days=14)
# Backend refuses /v1/schedule ranges longer than this.
MAX_SCHEDULE_RANGE: Final = timedelta(days=62)

# Position push throttling.
LOCATION_MIN_INTERVAL: Final = timedelta(seconds=60)
LOCATION_MIN_DISTANCE_M: Final = 100.0

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
