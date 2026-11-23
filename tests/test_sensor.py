"""Sensor tests."""

from __future__ import annotations

from copy import deepcopy

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from .conftest import SUMMARY, mock_backend, setup_entry


async def test_sensor_states(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Sensors expose the summary."""
    next_event = hass.states.get("sensor.tempo_next_event")
    assert next_event is not None
    assert next_event.state == "Algèbre"
    assert next_event.attributes["type"] == "class"
    assert next_event.attributes["location"] == "Campus Plaine"
    assert next_event.attributes["start"] == "2026-11-23T10:00:00+01:00"

    departure = hass.states.get("sensor.tempo_next_departure")
    assert departure is not None
    # leave_at from departure_check wins over the planned travel departure.
    assert departure.state == "2026-11-23T08:10:00+00:00"
    assert departure.attributes["origin"] == "Home"
    assert departure.attributes["destination"] == "Campus Plaine"
    assert departure.attributes["realtime"] is True
    assert departure.attributes["status"] == "routed"
    assert len(departure.attributes["legs"]) == 2

    status = hass.states.get("sensor.tempo_departure_status")
    assert status is not None
    assert status.state == "leave_earlier"
    assert status.attributes["reason"] == "Bus 71 delayed"

    remaining = hass.states.get("sensor.tempo_remaining_study")
    assert remaining is not None
    assert remaining.state == "600"
    assert remaining.attributes["unit_of_measurement"] == "min"
    assert hass.states.get("sensor.tempo_study_today").state == "90"  # type: ignore[union-attr]

    plan = hass.states.get("sensor.tempo_plan_status")
    assert plan is not None
    assert plan.state == "partial"
    assert plan.attributes["unplanned_minutes"] == 30
    assert plan.attributes["missed_blocks"] == 2

    proposals = hass.states.get("sensor.tempo_pending_approvals")
    assert proposals is not None
    assert proposals.state == "1"
    assert proposals.attributes["proposals"][0]["id"] == "prop-1"
    assert hass.states.get("sensor.tempo_unread_notifications").state == "1"  # type: ignore[union-attr]

    issues = hass.states.get("sensor.tempo_planning_issues")
    assert issues is not None
    assert issues.state == "1"
    assert issues.attributes["issues"] == SUMMARY["issues"]


async def test_sensor_empty_summary(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """Without events or departure check, sensors report unknown / none."""
    summary = deepcopy(SUMMARY)
    summary.update(
        next_event=None,
        next_departure=None,
        departure_check=None,
        plan_status="none",
        issues=[],
    )
    mock_backend(aioclient_mock, summary=summary, notifications=[], proposals=[])
    await setup_entry(hass, mock_config_entry)
    assert hass.states.get("sensor.tempo_next_event").state == "unknown"  # type: ignore[union-attr]
    assert hass.states.get("sensor.tempo_next_departure").state == "unknown"  # type: ignore[union-attr]
    assert hass.states.get("sensor.tempo_departure_status").state == "none"  # type: ignore[union-attr]
    assert hass.states.get("sensor.tempo_plan_status").state == "none"  # type: ignore[union-attr]
    assert hass.states.get("sensor.tempo_planning_issues").state == "0"  # type: ignore[union-attr]


async def test_departure_falls_back_to_travel(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """Without departure check, the planned travel departure is used."""
    summary = deepcopy(SUMMARY)
    summary["departure_check"] = None
    mock_backend(aioclient_mock, summary=summary, notifications=[])
    await setup_entry(hass, mock_config_entry)
    assert hass.states.get("sensor.tempo_next_departure").state == "2026-11-23T08:20:00+00:00"  # type: ignore[union-attr]
