"""Shared entity helpers for Tempo."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import TempoConfigEntry, TempoCoordinator


def parse_dt(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp from the backend; naive values are rejected."""
    if not isinstance(value, str) or not value:
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None or parsed.tzinfo is None:
        return None
    return parsed


def device_info(entry: TempoConfigEntry) -> DeviceInfo:
    """Return the single service device representing the Tempo backend."""
    version = entry.runtime_data.backend_version if hasattr(entry, "runtime_data") else None
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Tempo",
        manufacturer="Tempo",
        model="Tempo planner backend",
        entry_type=DeviceEntryType.SERVICE,
        sw_version=version,
    )


class TempoEntity(CoordinatorEntity[TempoCoordinator]):
    """Base class for Tempo entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: TempoCoordinator, key: str) -> None:
        """Initialize the entity with a stable unique id."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = device_info(entry)
