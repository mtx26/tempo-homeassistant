"""Conversation agent tests (backend mocked)."""

from __future__ import annotations

import pytest
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import intent
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from .conftest import BASE, calls

AGENT = "conversation.tempo_assistant"


async def _converse(hass: HomeAssistant, text: str, language: str = "fr") -> intent.IntentResponse:
    result = await conversation.async_converse(
        hass, text, None, Context(), language=language, agent_id=AGENT
    )
    assert result.conversation_id
    return result.response


async def test_agent_registered(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """The agent exists and supports French and English."""
    assert hass.states.get(AGENT) is not None
    agent = conversation.async_get_agent(hass, AGENT)
    assert agent is not None
    assert agent.supported_languages == ["fr", "en"]


async def test_answer_question(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, init_integration: MockConfigEntry
) -> None:
    """A question is forwarded and the backend summary is spoken; no refresh."""
    aioclient_mock.post(
        f"{BASE}/v1/conversation",
        json={"reply": "Prochain départ à 09:10 depuis Home.", "used_llm": True, "changed": False},
    )
    before = len(calls(aioclient_mock, "POST", "/v1/operations/get_summary"))
    response = await _converse(hass, "prochain départ ?")
    assert response.response_type is intent.IntentResponseType.ACTION_DONE
    assert response.speech["plain"]["speech"] == "Prochain départ à 09:10 depuis Home."
    sent = calls(aioclient_mock, "POST", "/v1/conversation")[-1][2]
    assert sent["text"] == "prochain départ ?"
    assert sent["conversation_id"]  # follow-ups keep their context
    await hass.async_block_till_done()
    assert len(calls(aioclient_mock, "POST", "/v1/operations/get_summary")) == before


async def test_applied_refreshes(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, init_integration: MockConfigEntry
) -> None:
    """An applied action refreshes the coordinator."""
    aioclient_mock.post(
        f"{BASE}/v1/conversation",
        json={"reply": "Task added.", "used_llm": True, "changed": True},
    )
    before = len(calls(aioclient_mock, "POST", "/v1/operations/get_summary"))
    response = await _converse(hass, "add 2 h of maths before Friday", language="en")
    assert response.speech["plain"]["speech"] == "Task added."
    await hass.async_block_till_done()
    assert len(calls(aioclient_mock, "POST", "/v1/operations/get_summary")) > before


async def test_not_understood(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, init_integration: MockConfigEntry
) -> None:
    """An empty reply gets a clear sentence instead of silence."""
    aioclient_mock.post(
        f"{BASE}/v1/conversation",
        json={"reply": "", "used_llm": False, "changed": False},
    )
    response = await _converse(hass, "blabla")
    assert response.speech["plain"]["speech"] == "Désolé, Tempo n'a pas compris cette demande."


@pytest.mark.parametrize(
    ("status", "language", "expected"),
    [
        (404, "fr", "Ce backend Tempo ne prend pas en charge"),
        (401, "en", "The Tempo token was rejected"),
        (500, "en", "Tempo could not answer right now"),
        (503, "fr", "Tempo ne peut pas répondre pour le moment"),
    ],
)
async def test_backend_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    init_integration: MockConfigEntry,
    status: int,
    language: str,
    expected: str,
) -> None:
    """Backend failures produce an error response, never an invented answer."""
    aioclient_mock.post(f"{BASE}/v1/conversation", status=status)
    response = await _converse(hass, "x", language=language)
    assert response.response_type is intent.IntentResponseType.ERROR
    assert response.speech["plain"]["speech"].startswith(expected)


async def test_error_details_not_spoken(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    init_integration: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Backend error details and internal URLs are logged, never spoken."""
    aioclient_mock.post(
        f"{BASE}/v1/conversation", status=422, json={"detail": f"boom at {BASE}/internal"}
    )
    response = await _converse(hass, "x", language="en")
    speech = response.speech["plain"]["speech"]
    assert response.response_type is intent.IntentResponseType.ERROR
    assert "tempo-api" not in speech
    assert "422" not in speech
    assert "boom" not in speech
    assert "boom" in caplog.text
