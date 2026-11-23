"""Integration-managed "Tempo" dashboard (sidebar panel), built only from core cards.

Why this design (verified against the installed Home Assistant sources, 2026.2 and 2026.10):

* Home Assistant has no public API for an integration to create a *storage* dashboard: the
  ``DashboardsCollection`` instance is local to ``lovelace.async_setup`` and only reachable
  through admin websocket commands. Instantiating a second collection would overwrite
  ``.storage/lovelace_dashboards`` behind the back of the real one.
* The Lovelace websocket API resolves dashboards through ``hass.data[LOVELACE_DATA]
  .dashboards[url_path]`` (a ``LovelaceConfig``) and the frontend shows any panel registered
  with ``frontend.async_register_built_in_panel(hass, "lovelace", ...)``. This is the same
  mechanism used for YAML dashboards, so we register a read-only ``LovelaceConfig`` whose
  configuration is generated on every load from the entity registry. It therefore always
  points at the current entity ids, needs no YAML and writes nothing to ``.storage``.

The dashboard is only registered when the user asked for it (``tempo.install_dashboard`` or
the options flow); the choice is persisted in the config entry options.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components import frontend
from homeassistant.components.lovelace.const import LOVELACE_DATA, MODE_YAML
from homeassistant.components.lovelace.dashboard import LovelaceConfig
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.json import json_bytes, json_fragment

from .const import DOMAIN

DASHBOARD_URL_PATH = "tempo-planner"
DASHBOARD_ICON = "mdi:calendar-clock"

# (entity domain, unique-id key) of every entity the dashboard may show.
ENTITY_KEYS: dict[str, tuple[str, str]] = {
    "calendar": ("calendar", "calendar"),
    "tasks": ("todo", "tasks"),
    "next_event": ("sensor", "next_event"),
    "next_departure": ("sensor", "next_departure"),
    "departure_status": ("sensor", "departure_status"),
    "remaining_study": ("sensor", "remaining_study"),
    "today_study": ("sensor", "today_study"),
    "plan_status": ("sensor", "plan_status"),
    "pending_proposals": ("sensor", "pending_proposals"),
    "unread_notifications": ("sensor", "unread_notifications"),
    "trainings_to_confirm": ("sensor", "trainings_to_confirm"),
    "issues": ("sensor", "issues"),
    "replan": ("button", "replan"),
    "approve_oldest_proposal": ("button", "approve_oldest_proposal"),
    "reject_oldest_proposal": ("button", "reject_oldest_proposal"),
}

TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "title": "Tempo",
        "today": "Agenda",
        "departure": "Next departure",
        "leave_at": "Leave at",
        "no_departure": "No planned departure.",
        "tasks": "Study tasks",
        "to_confirm": "Trainings to confirm",
        "to_confirm_help": (
            "Confirm with the action **tempo.confirm_activity** "
            "(Developer tools → Actions, or ask the Tempo assistant): "
            "`activity_id`, `duration_minutes`, and `start` for a race."
        ),
        "approvals": "Approvals",
        "approvals_help": "The buttons below decide the **first** proposal of this list.",
        "status": "Plan status",
        "issues": "Issues and warnings",
        "no_issue": "No issue.",
        "missed": "Missed study sessions",
    },
    "fr": {
        "title": "Tempo",
        "today": "Agenda",
        "departure": "Prochain départ",
        "leave_at": "Partir à",
        "no_departure": "Aucun départ prévu.",
        "tasks": "Tâches d'étude",
        "to_confirm": "Entraînements à confirmer",
        "to_confirm_help": (
            "Confirmez avec l'action **tempo.confirm_activity** "
            "(Outils de développement → Actions, ou demandez à l'assistant Tempo) : "
            "`activity_id`, `duration_minutes`, et `start` pour une course."
        ),
        "approvals": "Approbations",
        "approvals_help": (
            "Les boutons ci-dessous décident la **première** proposition de la liste."
        ),
        "status": "État du planning",
        "issues": "Problèmes et avertissements",
        "no_issue": "Aucun problème.",
        "missed": "Séances d'étude manquées",
    },
}


def _texts(language: str | None) -> dict[str, str]:
    return TEXTS["fr"] if (language or "").lower().startswith("fr") else TEXTS["en"]


def _tile(entity_id: str, **extra: Any) -> dict[str, Any]:
    return {"type": "tile", "entity": entity_id, **extra}


def _press(entity_id: str) -> dict[str, Any]:
    """A tile that presses a button entity when tapped."""
    return _tile(
        entity_id,
        tap_action={
            "action": "perform-action",
            "perform_action": "button.press",
            "target": {"entity_id": entity_id},
        },
    )


def _hidden_when_zero(entity_id: str, cards: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "conditional",
        "conditions": [
            {"condition": "state", "entity": entity_id, "state_not": "0"},
            {"condition": "state", "entity": entity_id, "state_not": "unavailable"},
            {"condition": "state", "entity": entity_id, "state_not": "unknown"},
        ],
        "card": {"type": "vertical-stack", "cards": cards},
    }


def _heading(text: str, icon: str) -> dict[str, Any]:
    return {"type": "heading", "heading": text, "icon": icon}


def build_dashboard_config(entities: dict[str, str], language: str | None) -> dict[str, Any]:
    """Return the Lovelace configuration of the Tempo dashboard.

    ``entities`` maps keys of :data:`ENTITY_KEYS` to entity ids; cards whose entities are
    missing are left out. Only core cards are used so the dashboard works on phones.
    """
    t = _texts(language)
    sections: list[dict[str, Any]] = []

    def section(title: str, icon: str, cards: list[dict[str, Any]]) -> None:
        if cards:
            sections.append({"type": "grid", "cards": [_heading(title, icon), *cards]})

    # Agenda.
    cards: list[dict[str, Any]] = []
    if cal := entities.get("calendar"):
        cards.append({"type": "calendar", "entities": [cal], "initial_view": "listWeek"})
    if ev := entities.get("next_event"):
        cards.append(_tile(ev))
    section(t["today"], "mdi:calendar-today", cards)

    # Next departure.
    cards = []
    dep = entities.get("next_departure")
    status = entities.get("departure_status")
    if dep:
        cards.append(_tile(dep))
    if status:
        cards.append(_tile(status))
    if dep:
        cards.append(
            {
                "type": "markdown",
                "content": (
                    f"{{% set d = states('{dep}') %}}"
                    f"{{% if d not in ['unknown', 'unavailable', ''] %}}"
                    f"**{t['leave_at']} {{{{ as_timestamp(d) | timestamp_custom('%H:%M') }}}}**"
                    f" — {{{{ state_attr('{dep}', 'origin') or '?' }}}} → "
                    f"{{{{ state_attr('{dep}', 'destination') or '?' }}}}\n"
                    f"{{% for leg in state_attr('{dep}', 'legs') or [] %}}\n"
                    f"- {{{{ leg.mode }}}} {{{{ leg.line or '' }}}}: "
                    f"{{{{ leg['from'] }}}} → {{{{ leg.to }}}}"
                    f"{{% if leg.realtime %}} ⏱{{% endif %}}"
                    f"{{% endfor %}}\n"
                    + (
                        f"{{% if state_attr('{status}', 'reason') %}}\n\n"
                        f"_{{{{ state_attr('{status}', 'reason') }}}}_{{% endif %}}"
                        if status
                        else ""
                    )
                    + f"{{% else %}}{t['no_departure']}{{% endif %}}"
                ),
            }
        )
    section(t["departure"], "mdi:train-bus", cards)

    # Tasks.
    cards = []
    if tasks := entities.get("tasks"):
        cards.append({"type": "todo-list", "entity": tasks})
    section(t["tasks"], "mdi:clipboard-list", cards)

    # Trainings to confirm.
    cards = []
    if conf := entities.get("trainings_to_confirm"):
        cards.append(_tile(conf))
        cards.append(
            _hidden_when_zero(
                conf,
                [
                    {
                        "type": "markdown",
                        "content": (
                            f"{{% for a in state_attr('{conf}', 'activities') or [] %}}\n"
                            "- **{{ a.title }}** ({{ a.kind }}"
                            "{% if a.preferred_day %}, {{ a.preferred_day }}{% endif %})"
                            " — `{{ a.id }}`\n"
                            "{% endfor %}\n\n" + t["to_confirm_help"]
                        ),
                    }
                ],
            )
        )
    section(t["to_confirm"], "mdi:run-fast", cards)

    # Approvals.
    cards = []
    if pend := entities.get("pending_proposals"):
        cards.append(_tile(pend))
        inner: list[dict[str, Any]] = [
            {
                "type": "markdown",
                "content": (
                    f"{{% for p in state_attr('{pend}', 'proposals') or [] %}}\n"
                    "{{ loop.index }}. **{{ p.title }}** — `{{ p.id }}`\n"
                    "{% endfor %}\n\n" + t["approvals_help"]
                ),
            }
        ]
        buttons = [
            _press(entities[key])
            for key in ("approve_oldest_proposal", "reject_oldest_proposal")
            if key in entities
        ]
        if buttons:
            inner.append({"type": "grid", "columns": 2, "square": False, "cards": buttons})
        cards.append(_hidden_when_zero(pend, inner))
    section(t["approvals"], "mdi:account-check", cards)

    # Plan status, notifications and issues.
    cards = [
        _tile(entities[key])
        for key in (
            "plan_status",
            "remaining_study",
            "today_study",
            "unread_notifications",
            "issues",
        )
        if key in entities
    ]
    if replan := entities.get("replan"):
        cards.append(_press(replan))
    if issues := entities.get("issues"):
        plan = entities.get("plan_status")
        cards.append(
            {
                "type": "markdown",
                "title": t["issues"],
                "content": (
                    f"{{% set items = (state_attr('{issues}', 'issues') or [])"
                    f" + (state_attr('{issues}', 'warnings') or []) %}}"
                    "{% for i in items %}\n- {{ i }}{% endfor %}"
                    + (
                        f"{{% if (state_attr('{plan}', 'missed_blocks') or 0) > 0 %}}\n"
                        f"- {t['missed']} : {{{{ state_attr('{plan}', 'missed_blocks') }}}}"
                        "{% endif %}"
                        if plan
                        else ""
                    )
                    + f"{{% if not items %}}{t['no_issue']}{{% endif %}}"
                ),
            }
        )
    section(t["status"], "mdi:calendar-check", cards)

    return {
        "title": t["title"],
        "views": [
            {
                "title": t["title"],
                "path": "tempo",
                "icon": DASHBOARD_ICON,
                "type": "sections",
                "max_columns": 3,
                "sections": sections,
            }
        ],
    }


@callback
def async_resolve_entities(hass: HomeAssistant, entry_id: str) -> dict[str, str]:
    """Map dashboard keys to the current entity ids of a config entry."""
    registry = er.async_get(hass)
    resolved: dict[str, str] = {}
    for key, (domain, unique_key) in ENTITY_KEYS.items():
        entity_id = registry.async_get_entity_id(domain, DOMAIN, f"{entry_id}_{unique_key}")
        if entity_id is not None:
            resolved[key] = entity_id
    return resolved


class TempoDashboard(LovelaceConfig):
    """Read-only dashboard generated from the entity registry on every load."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the dashboard."""
        super().__init__(
            hass,
            DASHBOARD_URL_PATH,
            {
                "id": DASHBOARD_URL_PATH.replace("-", "_"),
                "mode": MODE_YAML,
                "title": "Tempo",
                "icon": DASHBOARD_ICON,
                "show_in_sidebar": True,
                "require_admin": False,
            },
        )
        self.entry_id = entry_id

    @property
    def mode(self) -> str:
        """Generated dashboards behave like YAML ones: not editable in the UI."""
        return MODE_YAML

    def _build(self) -> dict[str, Any]:
        return build_dashboard_config(
            async_resolve_entities(self.hass, self.entry_id), self.hass.config.language
        )

    async def async_get_info(self) -> dict[str, Any]:
        """Return the config info."""
        return {"mode": self.mode, "views": len(self._build()["views"])}

    async def async_load(self, force: bool) -> dict[str, Any]:
        """Return the generated config."""
        return self._build()

    async def async_json(self, force: bool) -> json_fragment:
        """Return the generated config as JSON."""
        return json_fragment(json_bytes(self._build()))


def is_installed(hass: HomeAssistant, entry_id: str | None = None) -> bool:
    """Return True if the Tempo dashboard is registered (for ``entry_id`` if given)."""
    data = hass.data.get(LOVELACE_DATA)
    if data is None:
        return False
    dashboard = data.dashboards.get(DASHBOARD_URL_PATH)
    if not isinstance(dashboard, TempoDashboard):
        return False
    return entry_id is None or dashboard.entry_id == entry_id


@callback
def async_register_dashboard(hass: HomeAssistant, entry_id: str) -> None:
    """Register (or re-point) the Tempo sidebar dashboard."""
    data = hass.data.get(LOVELACE_DATA)
    if data is None:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="dashboard_unavailable")
    existing = data.dashboards.get(DASHBOARD_URL_PATH)
    if existing is not None and not isinstance(existing, TempoDashboard):
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="dashboard_url_taken",
            translation_placeholders={"url": DASHBOARD_URL_PATH},
        )
    update = existing is not None
    if not update and DASHBOARD_URL_PATH in hass.data.get(frontend.DATA_PANELS, {}):
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="dashboard_url_taken",
            translation_placeholders={"url": DASHBOARD_URL_PATH},
        )
    data.dashboards[DASHBOARD_URL_PATH] = TempoDashboard(hass, entry_id)
    frontend.async_register_built_in_panel(
        hass,
        "lovelace",
        sidebar_title="Tempo",
        sidebar_icon=DASHBOARD_ICON,
        frontend_url_path=DASHBOARD_URL_PATH,
        config={"mode": MODE_YAML},
        require_admin=False,
        update=update,
    )


@callback
def async_unregister_dashboard(hass: HomeAssistant, entry_id: str) -> None:
    """Remove the Tempo dashboard if it belongs to ``entry_id``."""
    if not is_installed(hass, entry_id):
        return
    hass.data[LOVELACE_DATA].dashboards.pop(DASHBOARD_URL_PATH, None)
    frontend.async_remove_panel(hass, DASHBOARD_URL_PATH, warn_if_unknown=False)
