"""Config, reauth and options flow tests."""

from __future__ import annotations

import aiohttp
import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_API_TOKEN, CONF_SCAN_INTERVAL, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tempo.const import (
    CONF_HOME_FROM_ZONE,
    CONF_LLM_MODEL,
    CONF_NOTIFY_SERVICE,
    CONF_TRACKER_ENTITY_ID,
    DOMAIN,
)

from .conftest import BASE, LLM_STATE, TOKEN, TRACKER, calls, mock_backend

USER_INPUT = {CONF_URL: "HTTP://Tempo-API:8000/", CONF_API_TOKEN: TOKEN, CONF_VERIFY_SSL: True}


async def _start(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return str(result["flow_id"])


async def test_full_flow(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """URL + token are validated, then the tracker is stored and pushed to the backend."""
    mock_backend(aioclient_mock)
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "tracker"

    status_calls = calls(aioclient_mock, "GET", "/v1/status")
    assert status_calls[0][3]["Authorization"] == f"Bearer {TOKEN}"

    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_TRACKER_ENTITY_ID: TRACKER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "settings"
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_LLM_MODEL: "gemini-3.5-flash-lite", CONF_HOME_FROM_ZONE: False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Tempo"
    assert not calls(aioclient_mock, "PUT", "/v1/llm")  # unchanged model: nothing sent
    assert result["data"] == {
        CONF_URL: BASE,
        CONF_API_TOKEN: TOKEN,
        CONF_VERIFY_SSL: True,
        CONF_TRACKER_ENTITY_ID: TRACKER,
    }
    assert result["result"].unique_id == BASE
    patches = calls(aioclient_mock, "POST", "/v1/operations/update_settings")
    assert patches[0][2] == {"tracker_entity_id": TRACKER}


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"status": 401}, "invalid_auth"),
        ({"status": 403}, "invalid_auth"),
        ({"status": 429}, "rate_limited"),
        ({"status": 503}, "cannot_connect"),
        ({"exc": aiohttp.ClientConnectionError()}, "cannot_connect"),
        ({"exc": TimeoutError()}, "cannot_connect"),
        ({"status": 418}, "unknown"),
    ],
)
async def test_user_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    kwargs: dict[str, object],
    error: str,
) -> None:
    """Errors are shown and the flow can recover."""
    aioclient_mock.get(f"{BASE}/v1/status", **kwargs)  # type: ignore[arg-type]
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    aioclient_mock.clear_requests()
    mock_backend(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)
    assert result["step_id"] == "tracker"


async def test_invalid_url(hass: HomeAssistant) -> None:
    """A non-http URL is rejected without network access."""
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {**USER_INPUT, CONF_URL: "ftp://tempo"}
    )
    assert result["errors"] == {CONF_URL: "invalid_url"}


async def test_tracker_step_error(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A backend failure while storing the tracker is reported."""
    aioclient_mock.get(f"{BASE}/v1/status", json={"version": "1", "timezone": "UTC"})
    aioclient_mock.post(f"{BASE}/v1/operations/update_settings", status=500)
    flow_id = await _start(hass)
    await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_TRACKER_ENTITY_ID: TRACKER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_duplicate(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """The same normalized URL cannot be configured twice."""
    mock_config_entry.add_to_hass(hass)
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert aioclient_mock.call_count == 0


async def test_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """Reauth replaces the token after validation."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    aioclient_mock.get(f"{BASE}/v1/status", status=401)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "bad"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    aioclient_mock.clear_requests()
    mock_backend(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "new-token"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_API_TOKEN] == "new-token"
    await hass.async_block_till_done()


async def test_options_flow(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, init_integration: MockConfigEntry
) -> None:
    """Options change the tracker (pushed to the backend), interval and notify service."""
    async_mock_service(hass, "notify", "mobile_app_phone")
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_TRACKER_ENTITY_ID: "device_tracker.other",
            CONF_SCAN_INTERVAL: 120,
            CONF_NOTIFY_SERVICE: "notify.unknown",
        },
    )
    assert result["errors"] == {CONF_NOTIFY_SERVICE: "invalid_notify_service"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_TRACKER_ENTITY_ID: "device_tracker.other",
            CONF_SCAN_INTERVAL: 120,
            CONF_NOTIFY_SERVICE: "notify.mobile_app_phone",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert init_integration.options == {
        CONF_TRACKER_ENTITY_ID: "device_tracker.other",
        CONF_SCAN_INTERVAL: 120,
        CONF_NOTIFY_SERVICE: "notify.mobile_app_phone",
    }
    assert calls(aioclient_mock, "POST", "/v1/operations/update_settings")[-1][2] == {
        "tracker_entity_id": "device_tracker.other"
    }
    # Reloaded with the new interval.
    coordinator = init_integration.runtime_data.coordinator
    assert coordinator.update_interval is not None
    assert coordinator.update_interval.total_seconds() == 120


async def test_options_home_from_zone(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, init_integration: MockConfigEntry
) -> None:
    """The HA home zone is registered as the Tempo place "home" (technical setup only)."""
    hass.config.latitude, hass.config.longitude = 50.45, 3.95
    aioclient_mock.post(f"{BASE}/v1/operations/upsert_place", json={"ok": True})
    aioclient_mock.post(f"{BASE}/v1/operations/update_settings", json={"ok": True})
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_TRACKER_ENTITY_ID: TRACKER,
            CONF_SCAN_INTERVAL: 60,
            CONF_HOME_FROM_ZONE: True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    place = calls(aioclient_mock, "POST", "/v1/operations/upsert_place")[-1][2]
    assert place["place_id"] == "home"
    assert (place["latitude"], place["longitude"]) == (50.45, 3.95)
    settings = calls(aioclient_mock, "POST", "/v1/operations/update_settings")[-1][2]
    assert settings == {"home_place_id": "home"}
    await hass.async_block_till_done()


async def _to_settings(hass: HomeAssistant) -> str:
    flow_id = await _start(hass)
    await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_TRACKER_ENTITY_ID: TRACKER}
    )
    assert result["step_id"] == "settings"
    return flow_id


async def test_first_setup_chooses_model_notify_and_home(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The first configuration sets the AI model, notify service and home zone at once."""
    mock_backend(aioclient_mock)
    aioclient_mock.post(f"{BASE}/v1/operations/upsert_place", json={"ok": True, "result": {}})
    async_mock_service(hass, "notify", "mobile_app_phone")
    flow_id = await _to_settings(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id,
        {
            CONF_LLM_MODEL: "gemini-3.5-flash",
            CONF_NOTIFY_SERVICE: "notify.mobile_app_phone",
            CONF_HOME_FROM_ZONE: True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {CONF_NOTIFY_SERVICE: "notify.mobile_app_phone"}
    assert calls(aioclient_mock, "PUT", "/v1/llm")[-1][2] == {"model": "gemini-3.5-flash"}
    assert calls(aioclient_mock, "POST", "/v1/operations/upsert_place")


async def test_inaccessible_model_is_reported_on_the_field(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_backend(aioclient_mock)
    flow_id = await _to_settings(hass)
    aioclient_mock.clear_requests()
    aioclient_mock.put(f"{BASE}/v1/llm", status=422, json={"detail": "modèle inaccessible"})
    mock_backend(aioclient_mock)  # the first registered answer for a URL wins
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_LLM_MODEL: "gemini-3.5-flash", CONF_HOME_FROM_ZONE: False}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_LLM_MODEL: "model_unavailable"}


async def test_backend_without_llm_hides_the_model_field(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_backend(aioclient_mock)
    flow_id = await _start(hass)
    await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)
    aioclient_mock.clear_requests()
    aioclient_mock.get(
        f"{BASE}/v1/llm", json={"enabled": False, "model": None, "default": "x", "models": []}
    )
    mock_backend(aioclient_mock)  # the first registered answer for a URL wins
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_TRACKER_ENTITY_ID: TRACKER}
    )
    assert CONF_LLM_MODEL not in result["data_schema"].schema
    result = await hass.config_entries.flow.async_configure(flow_id, {CONF_HOME_FROM_ZONE: False})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_options_change_the_model(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, init_integration: MockConfigEntry
) -> None:
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    assert CONF_LLM_MODEL in result["data_schema"].schema
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_LLM_MODEL: "gemini-3.5-flash",
            CONF_TRACKER_ENTITY_ID: TRACKER,
            CONF_SCAN_INTERVAL: 60,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert calls(aioclient_mock, "PUT", "/v1/llm")[-1][2] == {"model": "gemini-3.5-flash"}
    assert LLM_STATE["model"] == "gemini-3.5-flash-lite"
    await hass.async_block_till_done()
