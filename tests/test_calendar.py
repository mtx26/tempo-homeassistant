"""Calendar entity tests."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from homeassistant.components.calendar import DOMAIN as CALENDAR_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tempo.calendar import describe_item, item_to_event

from .conftest import BASE, SCHEDULE, calls

ENTITY = "calendar.tempo"
TZ = ZoneInfo("Europe/Brussels")


async def test_state_is_next_event(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """The calendar state reflects the next upcoming item (travel first here)."""
    state = hass.states.get(ENTITY)
    assert state is not None
    assert state.state == "off"
    assert state.attributes["message"] == "Home → Campus Plaine"
    assert state.attributes["start_time"] == "2026-11-23 09:20:00"
    assert "BUS 71 (STIB)" in state.attributes["description"]


async def _get_events(hass: HomeAssistant, start: datetime, end: datetime) -> list[dict[str, Any]]:
    response = await hass.services.async_call(
        CALENDAR_DOMAIN,
        "get_events",
        {"start_date_time": start, "end_date_time": end},
        target={"entity_id": ENTITY},
        blocking=True,
        return_response=True,
    )
    assert response is not None
    return list(response[ENTITY]["events"])  # type: ignore[index, call-overload]


async def test_get_events_mapping(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, init_integration: MockConfigEntry
) -> None:
    """Every item type becomes an event with uid, location and description."""
    aioclient_mock.mock_calls.clear()
    events = await _get_events(
        hass, datetime(2026, 11, 23, tzinfo=TZ), datetime(2026, 11, 24, tzinfo=TZ)
    )
    assert [e["summary"] for e in events] == [
        "Home → Campus Plaine",
        "Algèbre",
        "Étude : Analyse",
        "Footing",
    ]
    assert events[1]["location"] == "Campus Plaine"
    assert "Horaire fixe" in events[1]["description"]
    assert "Verrouillé" in events[2]["description"]
    assert "État : planifié" in events[2]["description"]
    assert "Notes : Zone 2" in events[3]["description"]
    assert "Déplaçable" in events[3]["description"]
    fetched = calls(aioclient_mock, "POST", "/v1/operations/get_schedule")
    assert len(fetched) == 1
    assert fetched[0][2]["start"] == "2026-11-23T00:00:00+01:00"


async def test_get_events_chunked(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, init_integration: MockConfigEntry
) -> None:
    """Ranges longer than 62 days are split into backend-sized chunks."""
    aioclient_mock.mock_calls.clear()
    await _get_events(hass, datetime(2026, 9, 1, tzinfo=TZ), datetime(2027, 1, 1, tzinfo=TZ))
    fetched = calls(aioclient_mock, "POST", "/v1/operations/get_schedule")
    assert len(fetched) == 2
    first_start = datetime.fromisoformat(fetched[0][2]["start"])
    first_end = datetime.fromisoformat(fetched[0][2]["end"])
    second_start = datetime.fromisoformat(fetched[1][2]["start"])
    assert (first_end - first_start).days == 62
    assert second_start == first_end


async def test_get_events_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, init_integration: MockConfigEntry
) -> None:
    """Backend failures surface as HomeAssistantError."""
    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{BASE}/v1/operations/get_schedule", status=500)
    with pytest.raises(HomeAssistantError):
        await _get_events(
            hass, datetime(2026, 11, 23, tzinfo=TZ), datetime(2026, 11, 24, tzinfo=TZ)
        )


def test_item_mapping_helpers() -> None:
    """Malformed items are skipped; impossible travel is explicit."""
    assert item_to_event({"id": "x", "title": "t", "start": "bad", "end": "bad"}) is None
    assert (
        item_to_event(
            {"id": "x", "title": "t", "start": "2026-11-23T10:00:00", "end": "2026-11-23T11:00:00"}
        )
        is None
    ), "naive timestamps are rejected"
    text = describe_item({"type": "travel", "status": "impossible", "fixed": False})
    assert "trajet impossible" in text
    event = item_to_event(SCHEDULE[0])
    assert event is not None
    assert event.uid == "travel-1"
