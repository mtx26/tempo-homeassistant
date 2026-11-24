"""Surface Tempo notifications, proposals and departure alerts in Home Assistant.

Notifications are informative only: they carry no action button. Approving or refusing a
proposal, or changing anything, is done by talking to the Tempo assistant.

* every new unread backend notification becomes a persistent notification
  (``tempo_<id>``), is pushed to the optional notify service, and is then acknowledged in the
  backend so it is not shown twice;
* pending proposals are pushed once to the optional notify service, with a hint to answer
  the assistant;
* departure alerts are raised once per (event, status).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .api import TempoError
from .const import CONF_NOTIFY_SERVICE, DEPARTURE_ALERT_STATUSES, DOMAIN, LOGGER
from .coordinator import TempoConfigEntry, TempoCoordinator

PROPOSAL_HINT = "Répondez à Tempo, par exemple « accepte la proposition » ou « refuse »."

DEPARTURE_TEXT = {
    "leave_now": "Partez maintenant",
    "leave_earlier": "Partez plus tôt que prévu",
    "at_risk": "Départ à risque",
    "late": "Vous êtes en retard",
}


class TempoNotificationBridge:
    """Bridge between backend notifications/proposals and HA notifications."""

    def __init__(
        self, hass: HomeAssistant, entry: TempoConfigEntry, coordinator: TempoCoordinator
    ) -> None:
        """Initialize the bridge."""
        self._hass = hass
        self._entry = entry
        self._coordinator = coordinator
        self._surfaced: set[str] = set()
        self._notified_proposals: set[str] = set()
        self._departure_alerts: set[tuple[str, str]] = set()
        self._unsubs: list[Callable[[], None]] = []

    @property
    def notify_service(self) -> str | None:
        """Return the configured notify service (``notify.xxx``), if any."""
        value = self._entry.options.get(CONF_NOTIFY_SERVICE)
        return value if isinstance(value, str) and value.startswith("notify.") else None

    @callback
    def async_start(self) -> None:
        """Listen to coordinator updates; process current data."""
        self._unsubs.append(self._coordinator.async_add_listener(self._async_process))
        self._async_process()

    @callback
    def async_stop(self) -> None:
        """Remove listeners."""
        while self._unsubs:
            self._unsubs.pop()()

    @callback
    def _async_process(self) -> None:
        data = self._coordinator.data
        if data is None:
            return
        for notification in data.notifications:
            self._surface_notification(notification)
        for proposal in data.proposals:
            if proposal.get("status", "pending") == "pending":
                self._notify_proposal(proposal)
        check = (data.summary or {}).get("departure_check")
        if isinstance(check, dict):
            travel = (data.summary or {}).get("next_departure")
            self._departure_alert(check, travel if isinstance(travel, dict) else None)

    def _surface_notification(self, notification: dict[str, Any]) -> None:
        if notification.get("acknowledged") or notification.get("id") is None:
            return
        notification_id = str(notification["id"])
        if notification_id not in self._surfaced:
            persistent_notification.async_create(
                self._hass,
                str(notification.get("message") or ""),
                title=str(notification.get("title") or "Tempo"),
                notification_id=f"tempo_{notification_id}",
            )
            self._surfaced.add(notification_id)
            if (service := self.notify_service) is not None:
                self._call_notify(
                    service,
                    str(notification.get("title") or "Tempo"),
                    str(notification.get("message") or ""),
                    {"tag": f"tempo_{notification_id}"},
                )
        # Acknowledge (again if a previous attempt failed: it is still listed as unread).
        self._hass.async_create_task(self._async_ack(notification_id), eager_start=True)

    async def _async_ack(self, notification_id: str) -> None:
        try:
            await self._coordinator.client.ack_notification(notification_id)
        except TempoError as err:
            LOGGER.warning("Could not acknowledge Tempo notification %s: %s", notification_id, err)

    def _notify_proposal(self, proposal: dict[str, Any]) -> None:
        if (service := self.notify_service) is None or proposal.get("id") is None:
            return
        proposal_id = str(proposal["id"])
        if proposal_id in self._notified_proposals:
            return
        self._notified_proposals.add(proposal_id)
        title = f"Tempo : {proposal.get('title') or 'modification à approuver'}"
        self._call_notify(service, title, PROPOSAL_HINT, {"tag": f"tempo_proposal_{proposal_id}"})

    def _departure_alert(self, check: dict[str, Any], travel: dict[str, Any] | None) -> None:
        status = check.get("status")
        if status not in DEPARTURE_ALERT_STATUSES:
            return
        event_key = str((travel or {}).get("id") or check.get("event_title") or "")
        key = (event_key, str(status))
        if key in self._departure_alerts:
            return
        self._departure_alerts.add(key)
        title = f"Tempo : {DEPARTURE_TEXT[str(status)]}"
        parts = []
        if check.get("event_title"):
            parts.append(str(check["event_title"]))
        if check.get("leave_at"):
            parts.append(f"départ {check['leave_at']}")
        if check.get("minutes_late"):
            parts.append(f"retard estimé {check['minutes_late']} min")
        if check.get("reason"):
            parts.append(str(check["reason"]))
        message = " — ".join(parts) or title
        persistent_notification.async_create(
            self._hass, message, title=title, notification_id=f"{DOMAIN}_departure"
        )
        if (service := self.notify_service) is not None:
            self._call_notify(service, title, message, {"tag": f"{DOMAIN}_departure"})

    def _call_notify(self, service: str, title: str, message: str, data: dict[str, Any]) -> None:
        domain, _, name = service.partition(".")
        self._hass.async_create_task(
            self._async_call_notify(domain, name, title, message, data), eager_start=True
        )

    async def _async_call_notify(
        self, domain: str, name: str, title: str, message: str, data: dict[str, Any]
    ) -> None:
        try:
            await self._hass.services.async_call(
                domain,
                name,
                {"title": title, "message": message, "data": data},
                blocking=True,
            )
        except HomeAssistantError as err:
            LOGGER.warning("Could not call %s.%s: %s", domain, name, err)
