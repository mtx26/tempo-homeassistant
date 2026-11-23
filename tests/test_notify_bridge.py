"""Notification bridge tests."""

from __future__ import annotations

from copy import deepcopy

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tempo.const import CONF_NOTIFY_SERVICE

from .conftest import BASE, SUMMARY, calls, mock_backend, poll, setup_entry


async def test_persistent_notification_once_and_ack(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    init_integration: MockConfigEntry,
) -> None:
    """A backend notification is shown once and acknowledged."""
    notifications = persistent_notification._async_get_or_create_notifications(hass)
    assert "tempo_notif-1" in notifications
    assert notifications["tempo_notif-1"]["title"] == "Cours modifié"
    assert len(calls(aioclient_mock, "POST", "/v1/notifications/notif-1/ack")) == 1

    # User dismisses it; the backend still lists it (ack lost): not shown again, ack retried.
    persistent_notification.async_dismiss(hass, "tempo_notif-1")
    await poll(hass)
    assert "tempo_notif-1" not in notifications
    assert len(calls(aioclient_mock, "POST", "/v1/notifications/notif-1/ack")) == 2


async def test_departure_alert_once_per_status(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    init_integration: MockConfigEntry,
) -> None:
    """Departure alerts are raised once per (event, status)."""
    notifications = persistent_notification._async_get_or_create_notifications(hass)
    assert "Partez plus tôt" in notifications["tempo_departure"]["title"]
    persistent_notification.async_dismiss(hass, "tempo_departure")
    await poll(hass)
    assert "tempo_departure" not in notifications

    summary = deepcopy(SUMMARY)
    summary["departure_check"]["status"] = "leave_now"
    aioclient_mock.clear_requests()
    mock_backend(aioclient_mock, summary=summary)
    await poll(hass)
    assert "Partez maintenant" in notifications["tempo_departure"]["title"]


async def test_actionable_proposal_and_approval(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Pending proposals are pushed once with actions; the action event approves."""
    notify_calls = async_mock_service(hass, "notify", "mobile_app_phone")
    entry = MockConfigEntry(
        domain=mock_config_entry.domain,
        unique_id=mock_config_entry.unique_id,
        data=dict(mock_config_entry.data),
        options={CONF_NOTIFY_SERVICE: "notify.mobile_app_phone"},
    )
    mock_backend(aioclient_mock)
    aioclient_mock.post(f"{BASE}/v1/proposals/prop-1/approve", json={"id": "prop-1"})
    aioclient_mock.post(f"{BASE}/v1/proposals/prop-1/reject", status=409, json={"detail": "x"})
    await setup_entry(hass, entry)

    proposal_calls = [c for c in notify_calls if "actions" in c.data["data"]]
    assert len(proposal_calls) == 1
    actions = proposal_calls[0].data["data"]["actions"]
    assert [a["action"] for a in actions] == ["TEMPO_APPROVE_prop-1", "TEMPO_REJECT_prop-1"]
    departure_calls = [c for c in notify_calls if "actions" not in c.data["data"]]
    assert len(departure_calls) == 1

    await poll(hass)
    assert len([c for c in notify_calls if "actions" in c.data["data"]]) == 1

    hass.bus.async_fire("mobile_app_notification_action", {"action": "TEMPO_APPROVE_prop-1"})
    await hass.async_block_till_done()
    assert len(calls(aioclient_mock, "POST", "/v1/proposals/prop-1/approve")) == 1

    # Already decided (409) is tolerated; unrelated actions are ignored.
    hass.bus.async_fire("mobile_app_notification_action", {"action": "TEMPO_REJECT_prop-1"})
    hass.bus.async_fire("mobile_app_notification_action", {"action": "OTHER"})
    await hass.async_block_till_done()
    assert len(calls(aioclient_mock, "POST", "/v1/proposals/prop-1/reject")) == 1


async def test_no_notify_service_no_push(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """Without notify service, only persistent notifications are created."""
    notify_calls = async_mock_service(hass, "notify", "mobile_app_phone")
    mock_backend(aioclient_mock)
    await setup_entry(hass, mock_config_entry)
    assert notify_calls == []
