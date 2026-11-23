"""Push the configured phone tracker position to the Tempo backend.

Only real coordinates reported by Home Assistant are sent; nothing is ever inferred.
Pushes are throttled to one per minute unless the phone moved more than 100 m.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from homeassistant.const import ATTR_GPS_ACCURACY, ATTR_LATITUDE, ATTR_LONGITUDE
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util
from homeassistant.util.location import distance

from .api import TempoApiClient, TempoError
from .const import LOCATION_MIN_DISTANCE_M, LOCATION_MIN_INTERVAL, LOGGER


class TempoLocationPusher:
    """Forward state changes of one device_tracker entity to POST /v1/location."""

    def __init__(self, hass: HomeAssistant, client: TempoApiClient, entity_id: str) -> None:
        """Initialize the pusher."""
        self._hass = hass
        self._client = client
        self.entity_id = entity_id
        self._last_sent_at: datetime | None = None
        self._last_position: tuple[float, float] | None = None
        self._unsub: Callable[[], None] | None = None

    @callback
    def async_start(self) -> None:
        """Start listening and push the current state if it carries a position."""
        self._unsub = async_track_state_change_event(
            self._hass, [self.entity_id], self._async_state_changed
        )
        if (state := self._hass.states.get(self.entity_id)) is not None:
            self._async_handle_state(state)

    @callback
    def async_stop(self) -> None:
        """Stop listening."""
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    @callback
    def _async_state_changed(self, event: Event[EventStateChangedData]) -> None:
        if (state := event.data["new_state"]) is not None:
            self._async_handle_state(state)

    @callback
    def _async_handle_state(self, state: State) -> None:
        latitude = state.attributes.get(ATTR_LATITUDE)
        longitude = state.attributes.get(ATTR_LONGITUDE)
        if not isinstance(latitude, int | float) or not isinstance(longitude, int | float):
            LOGGER.debug("%s has no coordinates; position not pushed", self.entity_id)
            return
        accuracy = state.attributes.get(ATTR_GPS_ACCURACY)
        if not isinstance(accuracy, int | float) or accuracy < 0:
            LOGGER.info(
                "%s has no gps_accuracy attribute; position not pushed to Tempo", self.entity_id
            )
            return
        now = dt_util.utcnow()
        position = (float(latitude), float(longitude))
        if self._last_sent_at is not None and self._last_position is not None:
            moved = distance(*self._last_position, *position) or 0.0
            if (
                now - self._last_sent_at < LOCATION_MIN_INTERVAL
                and moved <= LOCATION_MIN_DISTANCE_M
            ):
                return
        self._last_sent_at = now
        self._last_position = position
        payload = {
            "entity_id": self.entity_id,
            "latitude": position[0],
            "longitude": position[1],
            "accuracy_m": float(accuracy),
            "observed_at": state.last_updated.isoformat(),
        }
        self._hass.async_create_task(
            self._async_push(payload), name="tempo_location_push", eager_start=True
        )

    async def _async_push(self, payload: dict[str, object]) -> None:
        try:
            await self._client.post_location(payload)
        except TempoError as err:
            LOGGER.warning("Could not push position to Tempo: %s", err)
            # Allow the next state change to retry immediately.
            self._last_sent_at = None
