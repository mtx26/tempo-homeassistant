"""Diagnostics tests."""

from __future__ import annotations

import json

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tempo.diagnostics import async_get_config_entry_diagnostics

from .conftest import TOKEN


async def test_diagnostics_redacts_token(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The token never appears in diagnostics."""
    diag = await async_get_config_entry_diagnostics(hass, init_integration)
    assert diag["entry"]["data"]["api_token"] == "**REDACTED**"
    assert diag["backend_version"] == "0.6.0"
    assert diag["counts"] == {
        "schedule": 4,
        "tasks": 3,
        "proposals": 1,
        "unread_notifications": 1,
    }
    assert TOKEN not in json.dumps(diag, default=str)
