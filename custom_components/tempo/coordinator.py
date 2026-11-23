"""Data update coordinator polling the Tempo backend."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import TempoApiClient, TempoAuthError, TempoError
from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
    MAX_SCHEDULE_RANGE,
    SCHEDULE_FUTURE,
    SCHEDULE_PAST,
)

if TYPE_CHECKING:
    from .location import TempoLocationPusher
    from .notify_bridge import TempoNotificationBridge


@dataclass
class TempoData:
    """Snapshot of the backend state used by entities."""

    summary: dict[str, Any] = field(default_factory=dict)
    schedule: list[dict[str, Any]] = field(default_factory=list)
    tasks: list[dict[str, Any]] = field(default_factory=list)
    proposals: list[dict[str, Any]] = field(default_factory=list)
    notifications: list[dict[str, Any]] = field(default_factory=list)
    activities: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TempoRuntimeData:
    """Objects attached to a loaded config entry."""

    client: TempoApiClient
    coordinator: TempoCoordinator
    backend_version: str | None = None
    location: TempoLocationPusher | None = None
    bridge: TempoNotificationBridge | None = None


type TempoConfigEntry = ConfigEntry[TempoRuntimeData]


class TempoCoordinator(DataUpdateCoordinator[TempoData]):
    """Poll summary, schedule, tasks, proposals, unread notifications and activities."""

    config_entry: TempoConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: TempoConfigEntry, client: TempoApiClient
    ) -> None:
        """Initialize the coordinator."""
        interval = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
        )
        self.client = client

    async def _async_update_data(self) -> TempoData:
        now = dt_util.now()
        try:
            (
                summary,
                schedule,
                tasks,
                proposals,
                notifications,
                activities,
            ) = await asyncio.gather(
                self.client.summary(),
                self.client.schedule(now - SCHEDULE_PAST, now + SCHEDULE_FUTURE),
                self.client.tasks(),
                self.client.proposals(),
                self.client.notifications(unread=True),
                self.client.activities(),
            )
        except TempoAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except TempoError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        return TempoData(
            summary=summary or {},
            schedule=list(schedule or []),
            tasks=list(tasks or []),
            proposals=list(proposals or []),
            notifications=list(notifications or []),
            activities=list(activities or []),
        )

    async def async_fetch_schedule(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """Fetch an arbitrary range, split in chunks accepted by the backend."""
        items: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        cursor = start
        while cursor < end:
            chunk_end = min(cursor + MAX_SCHEDULE_RANGE, end)
            for item in await self.client.schedule(cursor, chunk_end):
                key = str(item.get("id"))
                if key not in items:
                    order.append(key)
                items[key] = item
            cursor = chunk_end
        return [items[key] for key in order]
