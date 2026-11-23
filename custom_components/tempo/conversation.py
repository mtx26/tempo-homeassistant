"""Conversation agent forwarding user text to the Tempo backend (``POST /v1/interpret``).

The backend interprets the request (optionally with an LLM) and deterministic code validates
and applies it; questions are answered with ``action == "answer"``. This agent never invents
an answer: it speaks the backend ``summary`` or a clear error.
"""

from __future__ import annotations

from typing import Literal

from homeassistant.components import conversation
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import TempoAuthError, TempoError, TempoNotFoundError
from .const import LOGGER
from .coordinator import TempoConfigEntry, TempoCoordinator
from .entity import TempoEntity

PARALLEL_UPDATES = 1

MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "not_understood": "Sorry, Tempo did not understand this request.",
        "unavailable": "This Tempo backend does not support natural-language requests.",
        "auth": "The Tempo token was rejected; re-authenticate the integration.",
        "error": "Tempo could not be reached: {error}",
    },
    "fr": {
        "not_understood": "Désolé, Tempo n'a pas compris cette demande.",
        "unavailable": "Ce backend Tempo ne prend pas en charge les demandes en langage naturel.",
        "auth": "Le jeton Tempo a été refusé ; ré-authentifiez l'intégration.",
        "error": "Impossible de joindre Tempo : {error}",
    },
}


def _messages(language: str | None) -> dict[str, str]:
    return MESSAGES["fr"] if (language or "").lower().startswith("fr") else MESSAGES["en"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TempoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Tempo conversation agent."""
    async_add_entities([TempoConversationEntity(entry.runtime_data.coordinator)])


class TempoConversationEntity(TempoEntity, conversation.ConversationEntity):
    """Tempo assistant usable from Assist (text or voice)."""

    _attr_translation_key = "assistant"
    _attr_supports_streaming = False

    def __init__(self, coordinator: TempoCoordinator) -> None:
        """Initialize the agent."""
        super().__init__(coordinator, "assistant")

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """French and English (the backend answers in the user's language)."""
        return ["fr", "en"]

    @property
    def available(self) -> bool:
        """Stay usable even if the last poll failed: the request itself reports errors."""
        return True

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Send the text to /v1/interpret and speak the summary."""
        texts = _messages(user_input.language)
        try:
            result = await self.coordinator.client.interpret(user_input.text)
        except TempoNotFoundError:
            return self._error(user_input, chat_log, texts["unavailable"])
        except TempoAuthError:
            return self._error(user_input, chat_log, texts["auth"])
        except TempoError as err:
            LOGGER.warning("Tempo interpret request failed: %s", err)
            return self._error(user_input, chat_log, texts["error"].format(error=err))

        summary = str(result.get("summary") or "").strip()
        if not result.get("understood") and not summary:
            summary = texts["not_understood"]
        if result.get("applied"):
            await self.coordinator.async_request_refresh()
        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(agent_id=user_input.agent_id, content=summary)
        )
        return conversation.async_get_result_from_chat_log(user_input, chat_log)

    @staticmethod
    def _error(
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
        message: str,
    ) -> conversation.ConversationResult:
        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(agent_id=user_input.agent_id, content=message)
        )
        response = intent.IntentResponse(language=user_input.language)
        response.async_set_error(intent.IntentResponseErrorCode.UNKNOWN, message)
        return conversation.ConversationResult(
            response=response, conversation_id=chat_log.conversation_id
        )
