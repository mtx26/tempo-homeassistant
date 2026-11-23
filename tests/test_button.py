"""Button tests: replan and decide the oldest pending proposal."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from .conftest import BASE, PROPOSALS, calls, mock_backend, setup_entry

NEWER = {
    "id": "prop-0",
    "kind": "move_fixed",
    "title": "Plus récente",
    "status": "pending",
    "created_at": "2026-11-23T07:30:00+01:00",
}


async def _press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call("button", "press", {"entity_id": entity_id}, blocking=True)


async def test_replan_button(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, init_integration: MockConfigEntry
) -> None:
    """The replan button calls POST /v1/replan."""
    aioclient_mock.post(f"{BASE}/v1/replan", status=202, json={})
    await _press(hass, "button.tempo_replan")
    assert calls(aioclient_mock, "POST", "/v1/replan")


@pytest.mark.parametrize(
    ("entity_id", "action"),
    [
        ("button.tempo_approve_oldest_proposal", "approve"),
        ("button.tempo_reject_oldest_proposal", "reject"),
    ],
)
async def test_oldest_proposal_buttons(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    entity_id: str,
    action: str,
) -> None:
    """The oldest pending proposal (by created_at) is decided and shown as attribute."""
    mock_backend(aioclient_mock, proposals=[NEWER, *PROPOSALS])
    await setup_entry(hass, mock_config_entry)
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes["proposal_id"] == "prop-1"
    assert state.attributes["pending"] == 2
    aioclient_mock.post(f"{BASE}/v1/proposals/prop-1/{action}", json={})
    await _press(hass, entity_id)
    assert calls(aioclient_mock, "POST", f"/v1/proposals/prop-1/{action}")
    assert not calls(aioclient_mock, "POST", f"/v1/proposals/prop-0/{action}")


async def test_no_pending_proposal(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """Pressing with nothing pending fails clearly and calls nothing."""
    mock_backend(aioclient_mock, proposals=[])
    await setup_entry(hass, mock_config_entry)
    with pytest.raises(ServiceValidationError) as err:
        await _press(hass, "button.tempo_approve_oldest_proposal")
    assert err.value.translation_key == "no_pending_proposal"


@pytest.mark.parametrize(("status", "key"), [(409, "conflict"), (500, "backend_error")])
async def test_button_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    init_integration: MockConfigEntry,
    status: int,
    key: str,
) -> None:
    """Backend errors are translated."""
    aioclient_mock.post(f"{BASE}/v1/proposals/prop-1/approve", status=status, json={})
    with pytest.raises(HomeAssistantError) as err:
        await _press(hass, "button.tempo_approve_oldest_proposal")
    assert err.value.translation_key == key
