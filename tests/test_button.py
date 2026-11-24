"""Button tests: replan and decide the oldest pending proposal."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

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
    assert state.attributes["created_at"] == PROPOSALS[0]["created_at"]
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


async def test_refuses_when_oldest_changed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry
) -> None:
    """If the backend's oldest pending proposal is not the one shown, nothing is decided."""
    mock_backend(aioclient_mock)
    await setup_entry(hass, mock_config_entry)
    state = hass.states.get("button.tempo_approve_oldest_proposal")
    assert state is not None
    assert state.attributes["proposal_id"] == "prop-1"

    # prop-1 was decided elsewhere; an older proposal is now first in the backend.
    older = {**NEWER, "id": "prop-old", "created_at": "2026-11-22T07:00:00+01:00"}
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/v1/proposals", json=[older])
    mock_backend(aioclient_mock)
    with pytest.raises(ServiceValidationError) as err:
        await _press(hass, "button.tempo_approve_oldest_proposal")
    assert err.value.translation_key == "proposal_changed"
    assert err.value.translation_placeholders == {"title": PROPOSALS[0]["title"]}
    assert not [c for c in aioclient_mock.mock_calls if c[0] == "POST"]


async def test_double_press_decides_only_once(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    frozen_now: FrozenDateTimeFactory,
) -> None:
    """A double tap approves prop-1 only, not the next proposal that appeared right after."""
    pending = [deepcopy(NEWER), deepcopy(PROPOSALS[0])]

    async def proposals(*_: Any) -> AiohttpClientMockResponse:
        return AiohttpClientMockResponse("GET", URL(f"{BASE}/v1/proposals"), json=pending)

    async def approve(*_: Any) -> AiohttpClientMockResponse:
        pending[:] = [p for p in pending if p["id"] != "prop-1"]
        return AiohttpClientMockResponse("POST", URL(f"{BASE}/x"), json={})

    aioclient_mock.get(f"{BASE}/v1/proposals", side_effect=proposals)
    aioclient_mock.post(f"{BASE}/v1/proposals/prop-1/approve", side_effect=approve)
    aioclient_mock.post(f"{BASE}/v1/proposals/prop-0/approve", json={})
    mock_backend(aioclient_mock)
    await setup_entry(hass, mock_config_entry)
    frozen_now.tick(10)

    entity_id = "button.tempo_approve_oldest_proposal"
    await _press(hass, entity_id)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["proposal_id"] == "prop-0"  # type: ignore[union-attr]

    with pytest.raises(ServiceValidationError) as err:
        await _press(hass, entity_id)
    assert err.value.translation_key == "proposal_just_changed"
    assert not calls(aioclient_mock, "POST", "/v1/proposals/prop-0/approve")

    # Once it has been visible for a moment, the new proposal can be decided.
    frozen_now.tick(5)
    await _press(hass, entity_id)
    assert len(calls(aioclient_mock, "POST", "/v1/proposals/prop-0/approve")) == 1
    assert len(calls(aioclient_mock, "POST", "/v1/proposals/prop-1/approve")) == 1
