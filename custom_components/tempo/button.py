"""Buttons used by the Tempo dashboard: replan and decide the oldest pending proposal.

Proposal ids are dynamic, so these buttons act on the *oldest* pending proposal (ordered by
``created_at``, like the sensor attributes). To avoid deciding something the user did not
see, each button remembers the proposal id it exposed in its attributes at the last
coordinator update. On press, the backend list is fetched again and the press is refused if
the oldest pending proposal is no longer that one (already decided, new proposal...). A
press is also refused when the shown proposal changed less than
:data:`MIN_SHOWN_SECONDS` ago, so a double tap cannot approve the first proposal and then the
next one that the user never saw. The generated dashboard uses per-proposal actions with an explicit
``proposal_id`` instead of these buttons.
"""

from __future__ import annotations

import time
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import TempoConflictError, TempoError
from .const import DOMAIN
from .coordinator import TempoConfigEntry, TempoCoordinator, TempoData, pending_proposals
from .entity import TempoEntity

PARALLEL_UPDATES = 1
MIN_SHOWN_SECONDS = 3.0

BUTTON_REPLAN = "replan"
BUTTON_APPROVE_OLDEST = "approve_oldest_proposal"
BUTTON_REJECT_OLDEST = "reject_oldest_proposal"

BUTTONS: tuple[ButtonEntityDescription, ...] = (
    ButtonEntityDescription(key=BUTTON_REPLAN, translation_key=BUTTON_REPLAN),
    ButtonEntityDescription(key=BUTTON_APPROVE_OLDEST, translation_key=BUTTON_APPROVE_OLDEST),
    ButtonEntityDescription(key=BUTTON_REJECT_OLDEST, translation_key=BUTTON_REJECT_OLDEST),
)

__all__ = ["BUTTONS", "TempoButton", "pending_proposals"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TempoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Tempo buttons."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(TempoButton(coordinator, description) for description in BUTTONS)


def _oldest(data: TempoData | None) -> dict[str, Any]:
    pending = pending_proposals(data)
    return pending[0] if pending else {}


class TempoButton(TempoEntity, ButtonEntity):
    """A Tempo action button."""

    def __init__(self, coordinator: TempoCoordinator, description: ButtonEntityDescription) -> None:
        """Initialize the button."""
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._shown: dict[str, Any] = _oldest(coordinator.data)
        # The first state is not a "change" the user might have missed.
        self._shown_since = float("-inf")

    @property
    def _decides(self) -> bool:
        return self.entity_description.key != BUTTON_REPLAN

    @callback
    def _handle_coordinator_update(self) -> None:
        """Remember the proposal exposed in the attributes written now."""
        shown = _oldest(self.coordinator.data)
        if shown.get("id") != self._shown.get("id"):
            self._shown_since = time.monotonic()
        self._shown = shown
        super()._handle_coordinator_update()

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the proposal the button would decide."""
        if not self._decides:
            return None
        return {
            "proposal_id": self._shown.get("id"),
            "title": self._shown.get("title"),
            "details": self._shown.get("details"),
            "created_at": self._shown.get("created_at"),
            "pending": len(pending_proposals(self.coordinator.data)),
        }

    async def async_press(self) -> None:
        """Handle the button press."""
        client = self.coordinator.client
        try:
            if not self._decides:
                await client.replan()
            else:
                await self._async_decide_shown()
        except TempoConflictError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="conflict",
                translation_placeholders={"error": str(err)},
            ) from err
        except TempoError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="backend_error",
                translation_placeholders={"error": str(err)},
            ) from err
        await self.coordinator.async_request_refresh()

    async def _async_decide_shown(self) -> None:
        shown_id = self._shown.get("id")
        if shown_id is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_pending_proposal"
            )
        if time.monotonic() - self._shown_since < MIN_SHOWN_SECONDS:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="proposal_just_changed",
                translation_placeholders={"title": str(self._shown.get("title") or shown_id)},
            )
        current = _oldest(TempoData(proposals=list(await self.coordinator.client.proposals())))
        if str(current.get("id")) != str(shown_id):
            title = str(self._shown.get("title") or shown_id)
            await self.coordinator.async_request_refresh()
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="proposal_changed",
                translation_placeholders={"title": title},
            )
        approve = self.entity_description.key == BUTTON_APPROVE_OLDEST
        await self.coordinator.client.decide_proposal(str(shown_id), approve)
