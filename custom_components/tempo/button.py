"""Buttons used by the Tempo dashboard: replan and decide the oldest pending proposal.

Proposal ids are dynamic, so the dashboard cannot hard-code them. The two proposal buttons
always act on the *oldest* pending proposal, whose id and title are exposed as attributes
(and shown first in the dashboard) so the user sees exactly what is approved or rejected.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import TempoConflictError, TempoError
from .const import DOMAIN
from .coordinator import TempoConfigEntry, TempoCoordinator, TempoData
from .entity import TempoEntity

PARALLEL_UPDATES = 1

BUTTON_REPLAN = "replan"
BUTTON_APPROVE_OLDEST = "approve_oldest_proposal"
BUTTON_REJECT_OLDEST = "reject_oldest_proposal"

BUTTONS: tuple[ButtonEntityDescription, ...] = (
    ButtonEntityDescription(key=BUTTON_REPLAN, translation_key=BUTTON_REPLAN),
    ButtonEntityDescription(key=BUTTON_APPROVE_OLDEST, translation_key=BUTTON_APPROVE_OLDEST),
    ButtonEntityDescription(key=BUTTON_REJECT_OLDEST, translation_key=BUTTON_REJECT_OLDEST),
)


def pending_proposals(data: TempoData | None) -> list[dict[str, Any]]:
    """Return pending proposals, oldest first (ISO timestamps sort chronologically)."""
    if data is None:
        return []
    pending = [
        p
        for p in data.proposals
        if p.get("status", "pending") == "pending" and p.get("id") is not None
    ]
    return sorted(pending, key=lambda p: str(p.get("created_at") or ""))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TempoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Tempo buttons."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(TempoButton(coordinator, description) for description in BUTTONS)


class TempoButton(TempoEntity, ButtonEntity):
    """A Tempo action button."""

    def __init__(self, coordinator: TempoCoordinator, description: ButtonEntityDescription) -> None:
        """Initialize the button."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the proposal the button would decide."""
        if self.entity_description.key == BUTTON_REPLAN:
            return None
        pending = pending_proposals(self.coordinator.data)
        oldest = pending[0] if pending else {}
        return {
            "proposal_id": oldest.get("id"),
            "title": oldest.get("title"),
            "details": oldest.get("details"),
            "pending": len(pending),
        }

    async def async_press(self) -> None:
        """Handle the button press."""
        client = self.coordinator.client
        try:
            if self.entity_description.key == BUTTON_REPLAN:
                await client.replan()
            else:
                pending = pending_proposals(self.coordinator.data)
                if not pending:
                    raise ServiceValidationError(
                        translation_domain=DOMAIN, translation_key="no_pending_proposal"
                    )
                approve = self.entity_description.key == BUTTON_APPROVE_OLDEST
                await client.decide_proposal(str(pending[0]["id"]), approve)
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
