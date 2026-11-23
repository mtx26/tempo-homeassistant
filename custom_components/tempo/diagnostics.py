"""Diagnostics for Tempo (token and coordinates redacted)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_TOKEN
from homeassistant.core import HomeAssistant

from .coordinator import TempoConfigEntry

TO_REDACT = {CONF_API_TOKEN, "latitude", "longitude", "token", "authorization"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TempoConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime = entry.runtime_data
    coordinator = runtime.coordinator
    data = coordinator.data
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "backend_version": runtime.backend_version,
        "last_update_success": coordinator.last_update_success,
        "counts": {
            "schedule": len(data.schedule),
            "tasks": len(data.tasks),
            "proposals": len(data.proposals),
            "unread_notifications": len(data.notifications),
            "activities": len(data.activities),
        },
        "data": async_redact_data(asdict(data), TO_REDACT),
    }
