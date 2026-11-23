# Tempo — Home Assistant integration

Custom integration (HACS layout) for **Tempo**, a self-hosted personal planner. Home Assistant is
Tempo's only user interface; the planning backend is a separate private service reached over the
internal network with a bearer token. Version `0.1.0`, tested against Home Assistant `2026.2.3`.

Licence: to be chosen by the owner.

## Français

### Installation
- **HACS (après publication du dépôt)** : HACS → Intégrations → menu ⋮ → *Dépôts personnalisés* →
  `https://github.com/mtx26/tempo-homeassistant`, catégorie *Intégration*, puis installer *Tempo*
  et redémarrer Home Assistant.
- **Manuelle** : copier `custom_components/tempo` dans le dossier `config/custom_components/` de
  Home Assistant, puis redémarrer.

Ensuite : Paramètres → Appareils et services → *Ajouter une intégration* → **Tempo** :
1. URL interne du backend (ex. `http://tempo-api:8000`) et jeton de service (validés via
   `/v1/status`) ;
2. traceur `device_tracker` du téléphone (enregistré aussi dans les réglages du backend).

Options : traceur, intervalle d'interrogation (60 s par défaut, minimum 30 s) et service de
notification facultatif (ex. `notify.mobile_app_telephone`) pour les approbations et alertes de
départ.

### Fonctionnalités
- **Calendrier `calendar.tempo`** : cours, études, entraînements, courses et trajets (étapes,
  temps réel, statut impossible).
- **Liste de tâches `todo.tempo_tasks`** (identifiant pouvant varier selon la langue) : tâches d'étude non archivées. Créer un
  élément crée une tâche : la date d'échéance est **obligatoire** (date cible), l'effort est lu
  dans la description (« 120 min », sinon **60 min**) et la cible est **souple par défaut**
  (stricte si la description contient `hard`/`strict`). Cocher ou supprimer un élément l'archive.
- **Capteurs** : prochain événement, prochain départ (heure de départ conseillée), statut du
  départ, étude restante, étude du jour, statut du planning, approbations en attente,
  notifications non lues, problèmes de planification.
- **Position** : chaque changement du traceur avec latitude/longitude **et** `gps_accuracy` est
  envoyé à `/v1/location` (au plus une fois par minute sauf déplacement > 100 m). Aucune
  coordonnée n'est inventée : sans précision GPS, rien n'est envoyé.
- **Notifications** : chaque notification non lue du backend devient une notification
  persistante (`tempo_<id>`) puis est acquittée. Si un service `notify` est configuré, les
  propositions en attente sont envoyées avec les actions *Approuver* / *Refuser*
  (`TEMPO_APPROVE_<id>` / `TEMPO_REJECT_<id>`) et les alertes de départ une fois par
  (événement, statut).
- **Actions** : `tempo.add_task`, `replan`, `approve_proposal`, `reject_proposal`, `lock_block`,
  `unlock_block`, `complete_block`, `add_training`, `add_race`, `set_place`, `set_availability`,
  `acknowledge_notification`, `interpret` (renvoie une réponse). Pas d'agent de conversation :
  `tempo.interpret` suffit pour les demandes en langage naturel (erreur claire si le backend ne
  le prend pas en charge).

## English (short)
Install through HACS as a custom repository (once published) or copy `custom_components/tempo`
into `config/custom_components/`. Add the **Tempo** integration with the internal backend URL,
the service token and your phone `device_tracker`. It provides one calendar, one to-do list
(new items need a due date; effort from "N min" in the description, default 60 min; soft target
by default), status sensors, position push (real GPS only, throttled), persistent and actionable
mobile notifications, and the `tempo.*` actions listed above. No conversation agent is provided;
use `tempo.interpret`.

## Development
```bash
uv sync                       # Python 3.13.2+ (see .python-version)
uv run ruff check . && uv run ruff format --check .
uv run mypy                   # strict, custom_components/tempo
uv run pytest                 # Linux or WSL only: Home Assistant does not import on Windows
```
Tests use `pytest-homeassistant-custom-component` with the backend HTTP API mocked.

CI (`.github/workflows/validate.yml`: hassfest, HACS action, lint/types/tests) only runs once the
repository is published on GitHub; it has not run yet.

## Before publication (owner decisions)
- Choose a licence; add `brand/icon.png`, GitHub description and topics, first release
  (required by the HACS action).
