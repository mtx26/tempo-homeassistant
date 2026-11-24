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

from .conftest import SUMMARY, calls, mock_backend, poll, setup_entry


async def test_persistent_notification_once_and_ack(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    init_integration: MockConfigEntry,
) -> None:
    """A backend notification is shown once and acknowledged."""
    notifications = persistent_notification._async_get_or_create_notifications(hass)
    assert "tempo_notif-1" in notifications
    assert notifications["tempo_notif-1"]["title"] == "Cours modifié"
    acks = calls(aioclient_mock, "POST", "/v1/operations/acknowledge_notification")
    assert [a[2] for a in acks] == [{"id": "notif-1"}]

    # User dismisses it; the backend still lists it (ack lost): not shown again, ack retried.
    persistent_notification.async_dismiss(hass, "tempo_notif-1")
    await poll(hass)
    assert "tempo_notif-1" not in notifications
    acks = calls(aioclient_mock, "POST", "/v1/operations/acknowledge_notification")
    assert len(acks) == 2


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


async def test_proposal_pushed_once_without_actions(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Pending proposals are pushed once, informative only: no action button, and a mobile
    action event never decides anything (approval happens in conversation)."""
    notify_calls = async_mock_service(hass, "notify", "mobile_app_phone")
    entry = MockConfigEntry(
        domain=mock_config_entry.domain,
        unique_id=mock_config_entry.unique_id,
        data=dict(mock_config_entry.data),
        options={CONF_NOTIFY_SERVICE: "notify.mobile_app_phone"},
    )
    mock_backend(aioclient_mock)
    await setup_entry(hass, entry)

    assert all("actions" not in c.data["data"] for c in notify_calls)
    proposal_calls = [c for c in notify_calls if "proposal" in c.data["data"].get("tag", "")]
    assert len(proposal_calls) == 1
    assert "Répondez à Tempo" in proposal_calls[0].data["message"]

    await poll(hass)
    assert len([c for c in notify_calls if "proposal" in c.data["data"].get("tag", "")]) == 1

    hass.bus.async_fire("mobile_app_notification_action", {"action": "TEMPO_APPROVE_prop-1"})
    await hass.async_block_till_done()
    assert calls(aioclient_mock, "POST", "/v1/proposals/prop-1/approve") == []


async def test_no_notify_service_no_push(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """Without notify service, only persistent notifications are created."""
    notify_calls = async_mock_service(hass, "notify", "mobile_app_phone")
    mock_backend(aioclient_mock)
    await setup_entry(hass, mock_config_entry)
    assert notify_calls == []
