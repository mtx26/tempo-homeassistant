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
the options flow); the choice is persisted in the config entry options. The panel is visible
to every user by default; the ``dashboard_require_admin`` option restricts it to admins.

Safety rules of the generated cards:

* every backend-provided string rendered by a markdown card goes through the ``esc`` Jinja
  macro (Markdown/HTML special characters are backslash-escaped) so a calendar title, a note
  or a STIB message cannot inject links, images or HTML; identifiers are shown as inline
  code with backticks removed;
* proposals are decided with per-proposal actions carrying an explicit ``proposal_id`` and a
  confirmation dialog naming the proposal. Lovelace confirmation texts are static, so the
  "oldest proposal" buttons (whose target can change after the page was loaded) are not
  placed on the dashboard. The view is regenerated on every load and ``lovelace_updated`` is
  fired when the pending proposals change so open dashboards can be refreshed.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components import frontend
from homeassistant.components.lovelace.const import LOVELACE_DATA, MODE_YAML
from homeassistant.components.lovelace.dashboard import LovelaceConfig
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_LOVELACE_UPDATED
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.json import json_bytes, json_fragment

from .const import DOMAIN
from .coordinator import TempoConfigEntry, pending_proposals

DASHBOARD_URL_PATH = "tempo-planner"
DASHBOARD_ICON = "mdi:calendar-clock"
MAX_PROPOSAL_CARDS = 10

# Jinja macro backslash-escaping Markdown/HTML special characters (CommonMark escapes).
ESC_MACRO = (
    r"{%- macro esc(s) -%}"
    r"{{ ('' if s is none else s) | string"
    r" | regex_replace('([\\\\`*_\\[\\]()!<>#|~{}])', '\\\\\\1') }}"
    r"{%- endmacro -%}"
)

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
    "settings": ("sensor", "settings"),
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
        "approvals_help": (
            "Each button below decides the proposal it names, after a confirmation. "
            "Reload the page if the list changed."
        ),
        "approve": "Approve",
        "reject": "Reject",
        "confirm_approve": "Approve the proposal “{title}”?",
        "confirm_reject": "Reject the proposal “{title}”?",
        "status": "Plan status",
        "issues": "Issues and warnings",
        "no_issue": "No issue.",
        "missed": "Missed study sessions",
        "config": "Configuration",
        "config_help": (
            "Everything is configured from Home Assistant by calling Tempo actions in "
            "**Developer tools → Actions** (form mode, no YAML), or by asking the Tempo "
            "assistant.\n\n"
            "1. **Places**: `tempo.set_place` (`place_id` such as `home`, name, latitude, "
            "longitude, optional aliases used in calendars).\n"
            "2. **Home**: `tempo.set_home` (`place_id`), or *Settings → Devices & services → "
            "Tempo → Configure*.\n"
            "3. **Study availability**: `tempo.set_availability` (weekly windows: weekday "
            "0 = Monday, start, end, place).\n"
            "4. **Stays** (for example a weekend with family): `tempo.add_stay` (place, "
            "start, end); `tempo.clear_stays` removes them. Tempo never switches home on "
            "its own.\n"
            "5. **Horizon, buffer, study preferences**: `tempo.set_settings`."
        ),
        "current": "Current settings",
        "home": "Home",
        "horizon": "Horizon",
        "buffer": "Buffer",
        "days": "days",
        "stays": "Declared stays",
        "no_stay": "No declared stay.",
        "places": "Places",
        "not_set": "not set",
        "open_actions": "Open actions",
        "clear_stays": "Clear stays",
        "confirm_clear_stays": "Remove every declared stay?",
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
            "Chaque bouton ci-dessous décide la proposition qu'il nomme, après confirmation. "
            "Rechargez la page si la liste a changé."
        ),
        "approve": "Approuver",
        "reject": "Refuser",
        "confirm_approve": "Approuver la proposition « {title} » ?",
        "confirm_reject": "Refuser la proposition « {title} » ?",
        "status": "État du planning",
        "issues": "Problèmes et avertissements",
        "no_issue": "Aucun problème.",
        "missed": "Séances d'étude manquées",
        "config": "Configuration",
        "config_help": (
            "Tout se configure depuis Home Assistant en appelant les actions Tempo dans "
            "**Outils de développement → Actions** (mode formulaire, sans YAML), ou en "
            "demandant à l'assistant Tempo.\n\n"
            "1. **Lieux** : `tempo.set_place` (`place_id` comme `home`, nom, latitude, "
            "longitude, alias éventuels utilisés dans les calendriers).\n"
            "2. **Domicile** : `tempo.set_home` (`place_id`), ou *Paramètres → Appareils et "
            "services → Tempo → Configurer*.\n"
            "3. **Disponibilités d'étude** : `tempo.set_availability` (créneaux "
            "hebdomadaires : weekday 0 = lundi, début, fin, lieu).\n"
            "4. **Séjours** (par exemple un week-end en famille) : `tempo.add_stay` (lieu, "
            "début, fin) ; `tempo.clear_stays` les efface. Tempo ne change jamais de "
            "domicile tout seul.\n"
            "5. **Horizon, marge, préférences d'étude** : `tempo.set_settings`."
        ),
        "current": "Réglages actuels",
        "home": "Domicile",
        "horizon": "Horizon",
        "buffer": "Marge",
        "days": "jours",
        "stays": "Séjours déclarés",
        "no_stay": "Aucun séjour déclaré.",
        "places": "Lieux",
        "not_set": "non défini",
        "open_actions": "Ouvrir les actions",
        "clear_stays": "Effacer les séjours",
        "confirm_clear_stays": "Supprimer tous les séjours déclarés ?",
    },
}


def _texts(language: str | None) -> dict[str, str]:
    return TEXTS["fr"] if (language or "").lower().startswith("fr") else TEXTS["en"]


def _attr(entity_id: str, name: str) -> str:
    """Jinja expression reading an attribute."""
    return f"state_attr('{entity_id}', '{name}')"


def _code(expr: str) -> str:
    """Render an identifier as inline code (backticks removed so it cannot break out)."""
    return "`{{ (" + expr + ") | string | replace('`', '') }}`"


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


def _plain(value: Any, limit: int = 120) -> str:
    """Single-line plain text for card names and confirmation dialogs (not Markdown)."""
    return " ".join(str(value or "").split())[:limit]


def _with_entry(data: dict[str, Any], entry_id: str | None) -> dict[str, Any]:
    return {**data, "config_entry_id": entry_id} if entry_id else data


def _decide_card(
    proposal: dict[str, Any], approve: bool, t: dict[str, str], entry_id: str | None
) -> dict[str, Any]:
    """A button deciding one explicit proposal after a confirmation naming it."""
    proposal_id = str(proposal.get("id"))
    title = _plain(proposal.get("title")) or proposal_id
    key = "approve" if approve else "reject"
    return {
        "type": "button",
        "name": f"{t[key]} — {title}",
        "icon": "mdi:check-circle" if approve else "mdi:close-circle",
        "show_state": False,
        "tap_action": {
            "action": "perform-action",
            "perform_action": f"{DOMAIN}.{key}_proposal",
            "data": _with_entry({"proposal_id": proposal_id}, entry_id),
            "confirmation": {"text": t[f"confirm_{key}"].format(title=title)},
        },
    }


def _departure_markdown(dep: str, status: str | None, t: dict[str, str]) -> str:
    text = (
        ESC_MACRO
        + f"{{% set d = states('{dep}') %}}"
        + "{% if d not in ['unknown', 'unavailable', ''] %}"
        + f"**{t['leave_at']} "
        + "{{ as_timestamp(d) | timestamp_custom('%H:%M') }}**"
        + f" — {{{{ esc({_attr(dep, 'origin')} or '?') }}}} → "
        + f"{{{{ esc({_attr(dep, 'destination')} or '?') }}}}\n"
        + f"{{% for leg in {_attr(dep, 'legs')} or [] %}}\n"
        + "- {{ esc(leg.mode) }} {{ esc(leg.line or '') }}: "
        + "{{ esc(leg['from']) }} → {{ esc(leg.to) }}"
        + "{% if leg.realtime %} ⏱{% endif %}"
        + "{% endfor %}\n"
    )
    if status:
        text += (
            f"{{% if {_attr(status, 'reason')} %}}\n\n"
            f"_{{{{ esc({_attr(status, 'reason')}) }}}}_{{% endif %}}"
            f"{{% for w in {_attr(status, 'warnings')} or [] %}}\n"
            "- {{ esc(w) }}{% endfor %}"
        )
    return text + f"{{% else %}}{t['no_departure']}{{% endif %}}"


def _settings_markdown(settings: str, t: dict[str, str]) -> str:
    def attr(name: str) -> str:
        return _attr(settings, name)

    return (
        ESC_MACRO
        + f"{{% set buffer = {attr('buffer_minutes')} %}}"
        + f"- {t['home']} : {{{{ esc({attr('home_place_name')} or {attr('home_place_id')}"
        + f" or '{t['not_set']}') }}}}\n"
        + f"- {t['horizon']} : {{{{ esc({attr('horizon_days')} or '?') }}}} {t['days']}\n"
        + f"- {t['buffer']} : "
        + "{{ esc('?' if buffer is none else buffer) }} min\n"
        + f"- {t['places']} : "
        + f"{{% for p in {attr('places')} or [] %}}{{{{ esc(p.name) }}}} ("
        + _code("p.id")
        + "){% if not loop.last %}, {% endif %}"
        + f"{{% else %}}{t['not_set']}{{% endfor %}}\n\n"
        + f"**{t['stays']}**\n"
        + f"{{% for s in {attr('stays')} or [] %}}\n"
        + "- {{ esc(s.place_name or s.place_id) }} : "
        + "{{ esc(s.start) }} → {{ esc(s.end) }}"
        + f"{{% else %}}\n{t['no_stay']}{{% endfor %}}"
    )


def build_dashboard_config(
    entities: dict[str, str],
    language: str | None,
    proposals: list[dict[str, Any]] | None = None,
    entry_id: str | None = None,
) -> dict[str, Any]:
    """Return the Lovelace configuration of the Tempo dashboard.

    ``entities`` maps keys of :data:`ENTITY_KEYS` to entity ids; cards whose entities are
    missing are left out. ``proposals`` are the pending proposals (oldest first) for which
    per-proposal approve/reject buttons are generated. Only core cards are used so the
    dashboard works on phones.
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
        cards.append({"type": "markdown", "content": _departure_markdown(dep, status, t)})
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
                            ESC_MACRO
                            + f"{{% for a in {_attr(conf, 'activities')} or [] %}}\n"
                            + "- **{{ esc(a.title) }}** ({{ esc(a.kind) }}"
                            + "{% if a.preferred_day %}, {{ esc(a.preferred_day) }}{% endif %})"
                            + " — "
                            + _code("a.id")
                            + "\n{% endfor %}\n\n"
                            + t["to_confirm_help"]
                        ),
                    }
                ],
            )
        )
    section(t["to_confirm"], "mdi:run-fast", cards)

    # Approvals: explicit proposal ids, each action confirmed with the proposal title.
    cards = []
    if pend := entities.get("pending_proposals"):
        cards.append(_tile(pend))
        inner: list[dict[str, Any]] = [
            {
                "type": "markdown",
                "content": (
                    ESC_MACRO
                    + f"{{% for p in {_attr(pend, 'proposals')} or [] %}}\n"
                    + "{{ loop.index }}. **{{ esc(p.title) }}** — "
                    + _code("p.id")
                    + "\n{% endfor %}\n\n"
                    + t["approvals_help"]
                ),
            }
        ]
        for proposal in (proposals or [])[:MAX_PROPOSAL_CARDS]:
            inner.append(
                {
                    "type": "grid",
                    "columns": 2,
                    "square": False,
                    "cards": [
                        _decide_card(proposal, True, t, entry_id),
                        _decide_card(proposal, False, t, entry_id),
                    ],
                }
            )
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
        content = (
            ESC_MACRO
            + f"{{% set items = ({_attr(issues, 'issues')} or [])"
            + f" + ({_attr(issues, 'warnings')} or []) %}}"
            + "{% for i in items %}\n- {{ esc(i) }}{% endfor %}"
        )
        if plan:
            content += (
                f"{{% if ({_attr(plan, 'missed_blocks')} or 0) > 0 %}}\n"
                f"- {t['missed']} : {{{{ {_attr(plan, 'missed_blocks')} | int }}}}"
                "{% endif %}"
            )
        content += f"{{% if not items %}}{t['no_issue']}{{% endif %}}"
        cards.append({"type": "markdown", "title": t["issues"], "content": content})
    section(t["status"], "mdi:calendar-check", cards)

    # Configuration: places, home, availability and stays, from the UI only.
    cards = [{"type": "markdown", "content": t["config_help"]}]
    if settings := entities.get("settings"):
        cards.append(_tile(settings))
        cards.append(
            {
                "type": "markdown",
                "title": t["current"],
                "content": _settings_markdown(settings, t),
            }
        )
    cards.append(
        {
            "type": "grid",
            "columns": 2,
            "square": False,
            "cards": [
                {
                    "type": "button",
                    "name": t["open_actions"],
                    "icon": "mdi:cog-play",
                    "show_state": False,
                    "tap_action": {
                        "action": "navigate",
                        "navigation_path": "/developer-tools/action",
                    },
                },
                {
                    "type": "button",
                    "name": t["clear_stays"],
                    "icon": "mdi:home-remove",
                    "show_state": False,
                    "tap_action": {
                        "action": "perform-action",
                        "perform_action": f"{DOMAIN}.clear_stays",
                        "data": _with_entry({}, entry_id),
                        "confirmation": {"text": t["confirm_clear_stays"]},
                    },
                },
            ],
        }
    )
    section(t["config"], "mdi:cog", cards)

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


def _entry_proposals(hass: HomeAssistant, entry_id: str) -> list[dict[str, Any]]:
    entry: TempoConfigEntry | None = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.state is not ConfigEntryState.LOADED:
        return []
    return pending_proposals(entry.runtime_data.coordinator.data)


class TempoDashboard(LovelaceConfig):
    """Read-only dashboard generated from the entity registry on every load."""

    def __init__(self, hass: HomeAssistant, entry_id: str, require_admin: bool = False) -> None:
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
                "require_admin": require_admin,
            },
        )
        self.entry_id = entry_id

    @property
    def mode(self) -> str:
        """Generated dashboards behave like YAML ones: not editable in the UI."""
        return MODE_YAML

    def _build(self) -> dict[str, Any]:
        return build_dashboard_config(
            async_resolve_entities(self.hass, self.entry_id),
            self.hass.config.language,
            _entry_proposals(self.hass, self.entry_id),
            self.entry_id,
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
def async_register_dashboard(
    hass: HomeAssistant, entry_id: str, *, require_admin: bool = False
) -> None:
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
    data.dashboards[DASHBOARD_URL_PATH] = TempoDashboard(hass, entry_id, require_admin)
    frontend.async_register_built_in_panel(
        hass,
        "lovelace",
        sidebar_title="Tempo",
        sidebar_icon=DASHBOARD_ICON,
        frontend_url_path=DASHBOARD_URL_PATH,
        config={"mode": MODE_YAML},
        require_admin=require_admin,
        update=update,
    )


@callback
def async_unregister_dashboard(hass: HomeAssistant, entry_id: str) -> None:
    """Remove the Tempo dashboard if it belongs to ``entry_id``."""
    if not is_installed(hass, entry_id):
        return
    hass.data[LOVELACE_DATA].dashboards.pop(DASHBOARD_URL_PATH, None)
    frontend.async_remove_panel(hass, DASHBOARD_URL_PATH, warn_if_unknown=False)


@callback
def async_track_proposals(hass: HomeAssistant, entry: TempoConfigEntry) -> CALLBACK_TYPE:
    """Fire ``lovelace_updated`` when the pending proposals shown by the dashboard change."""

    def snapshot() -> list[tuple[str, str]]:
        return [
            (str(p.get("id")), str(p.get("title") or ""))
            for p in pending_proposals(entry.runtime_data.coordinator.data)
        ]

    shown = snapshot()

    @callback
    def _updated() -> None:
        current = snapshot()
        if current == shown:
            return
        shown[:] = current
        if is_installed(hass, entry.entry_id):
            hass.bus.async_fire(EVENT_LOVELACE_UPDATED, {"url_path": DASHBOARD_URL_PATH})

    return entry.runtime_data.coordinator.async_add_listener(_updated)
