"""Position push tests."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from .conftest import BASE, TRACKER, calls, mock_backend, setup_entry

HOME = {"latitude": 50.8466, "longitude": 4.3528, "gps_accuracy": 12}


def _pushes(aioclient_mock: AiohttpClientMocker) -> list[dict[str, object]]:
    return [call[2] for call in calls(aioclient_mock, "POST", "/v1/location")]


async def test_initial_state_and_change_are_pushed(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    frozen_now: FrozenDateTimeFactory,
) -> None:
    """The current position is sent at startup and real changes are forwarded."""
    hass.states.async_set(TRACKER, "home", HOME)
    mock_backend(aioclient_mock)
    await setup_entry(hass, mock_config_entry)
    pushes = _pushes(aioclient_mock)
    assert pushes == [
        {
            "entity_id": TRACKER,
            "latitude": 50.8466,
            "longitude": 4.3528,
            "accuracy_m": 12.0,
            "observed_at": "2026-11-23T07:00:00+00:00",
        }
    ]

    # Small move within 60 s: throttled.
    frozen_now.tick(timedelta(seconds=10))
    hass.states.async_set(TRACKER, "home", {**HOME, "latitude": 50.8467})
    await hass.async_block_till_done()
    assert len(_pushes(aioclient_mock)) == 1

    # Move > 100 m within 60 s: pushed immediately.
    frozen_now.tick(timedelta(seconds=10))
    hass.states.async_set(TRACKER, "not_home", {**HOME, "latitude": 50.8500})
    await hass.async_block_till_done()
    assert len(_pushes(aioclient_mock)) == 2

    # Small move after 60 s: pushed.
    frozen_now.tick(timedelta(seconds=61))
    hass.states.async_set(TRACKER, "not_home", {**HOME, "latitude": 50.8501})
    await hass.async_block_till_done()
    pushes = _pushes(aioclient_mock)
    assert len(pushes) == 3
    assert pushes[-1]["observed_at"] == "2026-11-23T07:01:21+00:00"


async def test_missing_accuracy_or_coordinates_not_pushed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """No coordinates are invented: incomplete states are skipped."""
    mock_backend(aioclient_mock)
    await setup_entry(hass, mock_config_entry)
    hass.states.async_set(TRACKER, "home", {"latitude": 50.0, "longitude": 4.0})
    await hass.async_block_till_done()
    hass.states.async_set(TRACKER, "unknown", {})
    await hass.async_block_till_done()
    hass.states.async_set("device_tracker.other", "home", HOME)
    await hass.async_block_till_done()
    assert _pushes(aioclient_mock) == []


async def test_push_failure_is_retried(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    frozen_now: FrozenDateTimeFactory,
) -> None:
    """A failed push does not throttle the next state change."""
    aioclient_mock.post(f"{BASE}/v1/location", status=500)
    mock_backend(aioclient_mock)
    await setup_entry(hass, mock_config_entry)
    hass.states.async_set(TRACKER, "home", HOME)
    await hass.async_block_till_done()
    frozen_now.tick(timedelta(seconds=5))
    hass.states.async_set(TRACKER, "home", {**HOME, "gps_accuracy": 10})
    await hass.async_block_till_done()
    assert len(_pushes(aioclient_mock)) == 2
