"""Notification bridge tests."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.auth.models import User
from homeassistant.components import persistent_notification
from homeassistant.core import Context, HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tempo.const import CONF_NOTIFY_SERVICE
from custom_components.tempo.notify_bridge import SENT_PROPOSAL_TTL

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

    # The id is consumed by the first decision: a second tap is ignored; so are unrelated
    # actions.
    hass.bus.async_fire("mobile_app_notification_action", {"action": "TEMPO_REJECT_prop-1"})
    hass.bus.async_fire("mobile_app_notification_action", {"action": "OTHER"})
    await hass.async_block_till_done()
    assert calls(aioclient_mock, "POST", "/v1/proposals/prop-1/reject") == []
    assert len(calls(aioclient_mock, "POST", "/v1/proposals/prop-1/approve")) == 1


async def test_no_notify_service_no_push(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """Without notify service, only persistent notifications are created."""
    notify_calls = async_mock_service(hass, "notify", "mobile_app_phone")
    mock_backend(aioclient_mock)
    await setup_entry(hass, mock_config_entry)
    assert notify_calls == []


async def _setup_with_notify(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    base: MockConfigEntry,
    approve_status: int = 200,
) -> MockConfigEntry:
    async_mock_service(hass, "notify", "mobile_app_phone")
    entry = MockConfigEntry(
        domain=base.domain,
        unique_id=base.unique_id,
        data=dict(base.data),
        options={CONF_NOTIFY_SERVICE: "notify.mobile_app_phone"},
    )
    mock_backend(aioclient_mock)
    aioclient_mock.post(f"{BASE}/v1/proposals/prop-1/approve", status=approve_status, json={})
    aioclient_mock.post(f"{BASE}/v1/proposals/other/approve", json={})
    await setup_entry(hass, entry)
    return entry


async def test_action_for_unsent_proposal_ignored(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """Only ids Tempo sent in a notification are accepted."""
    await _setup_with_notify(hass, aioclient_mock, mock_config_entry)
    hass.bus.async_fire("mobile_app_notification_action", {"action": "TEMPO_APPROVE_other"})
    await hass.async_block_till_done()
    assert calls(aioclient_mock, "POST", "/v1/proposals/other/approve") == []


async def test_sent_ids_expire(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    frozen_now: FrozenDateTimeFactory,
) -> None:
    """A sent id is only accepted for a limited time."""
    await _setup_with_notify(hass, aioclient_mock, mock_config_entry)
    frozen_now.tick(SENT_PROPOSAL_TTL + timedelta(minutes=1))
    hass.bus.async_fire("mobile_app_notification_action", {"action": "TEMPO_APPROVE_prop-1"})
    await hass.async_block_till_done()
    assert calls(aioclient_mock, "POST", "/v1/proposals/prop-1/approve") == []


async def test_system_user_refused_household_user_accepted(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    hass_read_only_user: User,
) -> None:
    """A system user's action is refused; any real household user (not only admins) works."""
    await _setup_with_notify(hass, aioclient_mock, mock_config_entry)
    system = await hass.auth.async_create_system_user("robot")
    hass.bus.async_fire(
        "mobile_app_notification_action",
        {"action": "TEMPO_APPROVE_prop-1"},
        context=Context(user_id=system.id),
    )
    await hass.async_block_till_done()
    assert calls(aioclient_mock, "POST", "/v1/proposals/prop-1/approve") == []

    hass.bus.async_fire(
        "mobile_app_notification_action",
        {"action": "TEMPO_APPROVE_prop-1"},
        context=Context(user_id=hass_read_only_user.id),
    )
    await hass.async_block_till_done()
    assert len(calls(aioclient_mock, "POST", "/v1/proposals/prop-1/approve")) == 1


async def test_failed_decision_can_be_retried(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """A backend failure keeps the id so the notification action can be retried."""
    await _setup_with_notify(hass, aioclient_mock, mock_config_entry, approve_status=503)
    for _ in range(2):
        hass.bus.async_fire("mobile_app_notification_action", {"action": "TEMPO_APPROVE_prop-1"})
        await hass.async_block_till_done()
    assert len(calls(aioclient_mock, "POST", "/v1/proposals/prop-1/approve")) == 2
