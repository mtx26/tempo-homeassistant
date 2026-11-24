"""Setup, unload and coordinator error handling."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tempo.const import DOMAIN

from .conftest import BASE, TOKEN, calls, mock_backend, poll, setup_entry


async def test_setup_and_unload(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, init_integration: MockConfigEntry
) -> None:
    """The entry loads, polls every endpoint with the token and unloads cleanly."""
    assert init_integration.state is ConfigEntryState.LOADED
    for path in ("/v1/summary", "/v1/schedule", "/v1/tasks", "/v1/proposals"):
        found = calls(aioclient_mock, "GET", path)
        assert found, path
        assert found[0][3]["Authorization"] == f"Bearer {TOKEN}"
    notif = calls(aioclient_mock, "GET", "/v1/notifications")
    assert notif[0][1].query["unread"] == "true"
    schedule = calls(aioclient_mock, "GET", "/v1/schedule")[0][1]
    assert schedule.query["start"].startswith("2026-11-22T08:00:00")
    assert schedule.query["end"].startswith("2026-12-07T08:00:00")

    # async_entries_for_config_entry exists on every supported HA version (async_get_device
    # is deprecated since 2026.10).
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), init_integration.entry_id)
    assert len(devices) == 1
    device = devices[0]
    assert (DOMAIN, init_integration.entry_id) in device.identifiers
    assert device.name == "Tempo"
    assert device.sw_version == "0.6.0"
    assert device.entry_type is dr.DeviceEntryType.SERVICE

    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    assert init_integration.state is ConfigEntryState.NOT_LOADED
    # No business service actions: everything goes through the conversation.
    assert hass.services.async_services_for_domain(DOMAIN) == {}


async def test_setup_auth_failed_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """A rejected token at setup triggers the reauth flow."""
    aioclient_mock.get(f"{BASE}/v1/status", status=401)
    await hass.config.async_set_time_zone("Europe/Brussels")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == SOURCE_REAUTH for flow in flows)


async def test_setup_not_ready(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """An unreachable backend makes the entry retry later."""
    aioclient_mock.get(f"{BASE}/v1/status", status=502)
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_coordinator_auth_failure_triggers_reauth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    init_integration: MockConfigEntry,
) -> None:
    """A 401 during polling starts reauth."""
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/v1/summary", status=401)
    mock_backend(aioclient_mock)
    await poll(hass)
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == SOURCE_REAUTH for flow in flows)


async def test_coordinator_update_failed(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    init_integration: MockConfigEntry,
) -> None:
    """A server error makes entities unavailable until the backend recovers."""
    assert hass.states.get("sensor.tempo_plan_status").state == "partial"
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/v1/summary", status=500)
    mock_backend(aioclient_mock)
    await poll(hass)
    assert hass.states.get("sensor.tempo_plan_status").state == STATE_UNAVAILABLE

    aioclient_mock.clear_requests()
    mock_backend(aioclient_mock)
    await poll(hass)
    assert hass.states.get("sensor.tempo_plan_status").state == "partial"


async def test_setup_without_tracker(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An entry without tracker still loads (no position push)."""
    mock_backend(aioclient_mock)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=BASE,
        data={"url": BASE, "api_token": TOKEN, "verify_ssl": True},
    )
    await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.location is None
