"""Calendar entity exposing the Tempo plan (classes, study, training, races, travel)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import TempoError
from .const import DOMAIN, LOGGER
from .coordinator import TempoConfigEntry, TempoCoordinator
from .entity import TempoEntity, parse_dt

PARALLEL_UPDATES = 0

TYPE_LABELS = {
    "class": "Cours",
    "study": "Étude",
    "training": "Entraînement",
    "race": "Course",
    "travel": "Trajet",
}

STUDY_STATE_LABELS = {
    "planned": "planifié",
    "missed": "manqué",
    "done": "fait",
    "promoted": "promu",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TempoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Tempo calendar."""
    async_add_entities([TempoCalendarEntity(entry.runtime_data.coordinator)])


def _hhmm(value: Any) -> str:
    parsed = parse_dt(value)
    return dt_util.as_local(parsed).strftime("%H:%M") if parsed else "?"


def describe_legs(legs: list[dict[str, Any]]) -> list[str]:
    """Return one human-readable line per travel leg."""
    lines: list[str] = []
    for leg in legs:
        mode = str(leg.get("mode") or "?")
        line = f" {leg['line']}" if leg.get("line") else ""
        agency = f" ({leg['agency']})" if leg.get("agency") else ""
        lines.append(
            f"{_hhmm(leg.get('start'))}-{_hhmm(leg.get('end'))} {mode}{line}{agency}: "
            f"{leg.get('from') or '?'} → {leg.get('to') or '?'}"
        )
    return lines


def describe_item(item: dict[str, Any]) -> str:
    """Build the calendar description of a schedule item."""
    kind = str(item.get("type", ""))
    lines = [f"Type : {TYPE_LABELS.get(kind, kind)}"]
    lines.append("Horaire fixe" if item.get("fixed") else "Déplaçable par Tempo")
    if item.get("locked"):
        lines.append("Verrouillé")
    if item.get("pending_move"):
        lines.append("Déplacement proposé : en attente de votre accord")
    if kind == "study" and item.get("state"):
        state = str(item["state"])
        lines.append(f"État : {STUDY_STATE_LABELS.get(state, state)}")
    if item.get("location"):
        lines.append(f"Lieu : {item['location']}")
    if kind == "travel" and (published := item.get("travel_details")):
        lines.append(str(published))
    elif kind == "travel":
        lines.append(f"Statut du trajet : {item.get('status') or 'unknown'}")
        if item.get("status") == "impossible":
            lines.append("Aucun itinéraire trouvé : trajet impossible")
        elif item.get("status") == "stale":
            lines.append("Itinéraire précédent conservé, non revalidé (routage indisponible)")
        if item.get("stale_snapshot"):
            lines.append("Horaires et quais à vérifier (prévisions du planning périmées).")
        legs = item.get("legs") or []
        if legs:
            lines.append("Étapes :")
            lines.extend(f"- {leg}" for leg in describe_legs(legs))
    if item.get("block_id"):
        lines.append(f"Bloc : {item['block_id']}")
    if item.get("notes"):
        lines.append(f"Notes : {item['notes']}")
    return "\n".join(lines)


def item_to_event(item: dict[str, Any]) -> CalendarEvent | None:
    """Map a backend schedule item to a calendar event (None if malformed)."""
    start = parse_dt(item.get("start"))
    end = parse_dt(item.get("end"))
    if start is None or end is None or end <= start:
        LOGGER.debug("Skipping malformed schedule item %s", item.get("id"))
        return None
    return CalendarEvent(
        start=start,
        end=end,
        summary=str(item.get("title") or TYPE_LABELS.get(str(item.get("type")), "Tempo")),
        description=describe_item(item),
        location=item.get("location") or None,
        uid=str(item["id"]) if item.get("id") is not None else None,
    )


class TempoCalendarEntity(TempoEntity, CalendarEntity):
    """The Tempo plan as a single calendar."""

    _attr_name = None

    def __init__(self, coordinator: TempoCoordinator) -> None:
        """Initialize the calendar."""
        super().__init__(coordinator, "calendar")

    @property
    def event(self) -> CalendarEvent | None:
        """Return the current event or, if none, the next upcoming one."""
        now = dt_util.utcnow()
        best: CalendarEvent | None = None
        for item in self.coordinator.data.schedule:
            event = item_to_event(item)
            if event is None or event.end_datetime_local <= now:
                continue
            if best is None or event.start_datetime_local < best.start_datetime_local:
                best = event
        return best

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return events in the requested range, fetched from the backend."""
        try:
            items = await self.coordinator.async_fetch_schedule(start_date, end_date)
        except TempoError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="schedule_fetch_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        events = [event for item in items if (event := item_to_event(item)) is not None]
        return [e for e in events if e.end_datetime_local > start_date and e.start < end_date]
