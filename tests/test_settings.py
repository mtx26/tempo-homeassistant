"""Settings from Home Assistant: home, horizon/buffer/preferences, stays, options flow."""

from __future__ import annotations

from typing import Any

import pytest
import voluptuous as vol
from homeassistant.auth.models import User
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import Context, HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tempo.const import (
    CONF_DASHBOARD,
    CONF_DASHBOARD_REQUIRE_ADMIN,
    CONF_HOME_PLACE_ID,
    CONF_TRACKER_ENTITY_ID,
    DOMAIN,
)

from .conftest import BASE, SETTINGS, TRACKER, calls, mock_backend, setup_entry


def _patches(aioclient_mock: AiohttpClientMocker) -> list[Any]:
    return [call[2] for call in calls(aioclient_mock, "PATCH", "/v1/settings")]


async def test_set_home(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, init_integration: MockConfigEntry
) -> None:
    """tempo.set_home stores an existing place as home_place_id."""
    await hass.services.async_call(DOMAIN, "set_home", {"place_id": "campus"}, blocking=True)
    assert _patches(aioclient_mock)[-1] == {"home_place_id": "campus"}


async def test_set_home_unknown_place(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, init_integration: MockConfigEntry
) -> None:
    """A place not created with tempo.set_place is refused before any PATCH."""
    before = len(_patches(aioclient_mock))
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "set_home", {"place_id": "nowhere"}, blocking=True)
    assert err.value.translation_key == "unknown_place"
    assert len(_patches(aioclient_mock)) == before


async def test_set_settings_merges_study_preferences(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, init_integration: MockConfigEntry
) -> None:
    """Only given fields change; study preferences are merged with the stored object."""
    await hass.services.async_call(
        DOMAIN,
        "set_settings",
        {"horizon_days": 21, "buffer_minutes": 15, "max_daily_study_minutes": 300},
        blocking=True,
    )
    assert _patches(aioclient_mock)[-1] == {
        "horizon_days": 21,
        "buffer_minutes": 15,
        "study_preferences": {"preferred_block_minutes": 90, "max_daily_study_minutes": 300},
    }
    await hass.services.async_call(DOMAIN, "set_settings", {"horizon_days": 7}, blocking=True)
    assert _patches(aioclient_mock)[-1] == {"horizon_days": 7}


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"horizon_days": 0},
        {"horizon_days": 43},
        {"buffer_minutes": 121},
        {"granularity_minutes": 20},
        {"max_blocks_per_subject_per_day": 5},
        {"min_block_minutes": 10},
    ],
)
async def test_set_settings_validation(
    hass: HomeAssistant, init_integration: MockConfigEntry, data: dict[str, Any]
) -> None:
    """Out-of-range values (backend limits) and empty calls are rejected locally."""
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(DOMAIN, "set_settings", data, blocking=True)


async def test_add_and_clear_stays(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, init_integration: MockConfigEntry
) -> None:
    """add_stay appends to the declared stays (naive times use the HA zone); clear empties."""
    await hass.services.async_call(
        DOMAIN,
        "add_stay",
        {"place_id": "parents", "start": "2027-01-08T18:00:00", "end": "2027-01-10T20:00:00"},
        blocking=True,
    )
    assert _patches(aioclient_mock)[-1] == {
        "stays": [
            *SETTINGS["stays"],
            {
                "place_id": "parents",
                "start": "2027-01-08T18:00:00+01:00",
                "end": "2027-01-10T20:00:00+01:00",
            },
        ]
    }
    await hass.services.async_call(DOMAIN, "clear_stays", {}, blocking=True)
    assert _patches(aioclient_mock)[-1] == {"stays": []}


@pytest.mark.parametrize(
    ("data", "key"),
    [
        (
            {"place_id": "parents", "start": "2027-01-10T20:00:00", "end": "2027-01-08T18:00:00"},
            "invalid_window",
        ),
        (
            {"place_id": "moon", "start": "2027-01-08T18:00:00", "end": "2027-01-10T20:00:00"},
            "unknown_place",
        ),
    ],
)
async def test_add_stay_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    init_integration: MockConfigEntry,
    data: dict[str, Any],
    key: str,
) -> None:
    """Invalid stays are refused before any PATCH."""
    before = len(_patches(aioclient_mock))
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "add_stay", data, blocking=True)
    assert err.value.translation_key == key
    assert len(_patches(aioclient_mock)) == before


async def test_settings_sensor(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """The diagnostic settings sensor exposes home, horizon, buffer, stays and places."""
    state = hass.states.get("sensor.tempo_settings")
    assert state is not None
    assert state.state == "Kot"
    assert state.attributes["home_place_id"] == "home"
    assert state.attributes["horizon_days"] == 14
    assert state.attributes["buffer_minutes"] == 10
    assert state.attributes["stays"][0]["place_name"] == "Parents"
    assert {"id": "campus", "name": "Campus Plaine"} in state.attributes["places"]


@pytest.mark.parametrize("service", ["install_dashboard", "remove_dashboard"])
async def test_dashboard_services_admin_only(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_read_only_user: User,
    service: str,
) -> None:
    """Non-admin users cannot install or remove the dashboard."""
    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN, service, {}, blocking=True, context=Context(user_id=hass_read_only_user.id)
        )


async def test_options_flow_home_place(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, init_integration: MockConfigEntry
) -> None:
    """The options flow lists existing places, PATCHes a new home and stores the admin flag."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    assert result["type"] is FlowResultType.FORM
    schema = result["data_schema"].schema
    home_key = next(k for k in schema if k == CONF_HOME_PLACE_ID)
    assert home_key.description == {"suggested_value": "home"}
    options = schema[home_key].config["options"]
    assert [o["value"] for o in options] == ["campus", "home", "parents"]

    base = {CONF_TRACKER_ENTITY_ID: TRACKER, CONF_SCAN_INTERVAL: 60}
    # Unknown places are rejected by the selector itself.
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {**base, CONF_HOME_PLACE_ID: "moon"}
        )

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {**base, CONF_HOME_PLACE_ID: "parents", CONF_DASHBOARD_REQUIRE_ADMIN: True},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert _patches(aioclient_mock)[-1] == {"home_place_id": "parents"}
    assert init_integration.options[CONF_DASHBOARD_REQUIRE_ADMIN] is True
    assert init_integration.options[CONF_DASHBOARD] is False
    assert CONF_HOME_PLACE_ID not in init_integration.options


async def test_options_flow_unchanged_home_not_patched(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, init_integration: MockConfigEntry
) -> None:
    """Keeping the same home (and tracker) sends no PATCH."""
    before = len(_patches(aioclient_mock))
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_TRACKER_ENTITY_ID: TRACKER, CONF_SCAN_INTERVAL: 60, CONF_HOME_PLACE_ID: "home"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert len(_patches(aioclient_mock)) == before


async def test_options_flow_without_places(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """Without any place yet, the home field is hidden (create places with set_place)."""
    mock_backend(aioclient_mock, places=[], settings={**SETTINGS, "home_place_id": None})
    await setup_entry(hass, mock_config_entry)
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert CONF_HOME_PLACE_ID not in result["data_schema"].schema
    state = hass.states.get("sensor.tempo_settings")
    assert state is not None
    assert state.state == "unknown"


async def test_options_flow_backend_down_hides_home(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, init_integration: MockConfigEntry
) -> None:
    """If places cannot be read, the form still opens without the home field."""
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/v1/places", status=500)  # first registered mock wins
    mock_backend(aioclient_mock)
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert CONF_HOME_PLACE_ID not in result["data_schema"].schema
