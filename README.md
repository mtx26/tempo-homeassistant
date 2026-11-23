# Tempo — Home Assistant integration

Custom integration (HACS layout) for **Tempo**, a self-hosted personal planner. Home Assistant is
Tempo's only user interface; the planning backend is a separate private service reached over the
internal network with a bearer token. Version `0.2.0`.

Licence: to be chosen by the owner.

## Compatibilité / Compatibility

| Home Assistant | Python | Résultat / Result |
| --- | --- | --- |
| 2026.2.3 | 3.13 | suite de tests complète OK / full test suite passes (+ mypy strict, Ruff) |
| 2026.10.4 | 3.14 | suite de tests complète OK / full test suite passes |

`hacs.json` déclare le minimum `2026.2.0`. Seules les versions ci-dessus ont été testées
(tests automatisés avec backend simulé ; pas encore d'essai sur une instance réelle).
Only the versions above were tested (automated tests with a mocked backend; not yet tried on a
real instance).

## Français

### Installation
- **HACS (une fois le dépôt publié)** : HACS → menu ⋮ → *Dépôts personnalisés* →
  `https://github.com/mtx26/tempo-homeassistant`, catégorie *Intégration*, installer *Tempo*,
  puis redémarrer Home Assistant.
- **Manuelle** : copier `custom_components/tempo` dans `config/custom_components/`, puis
  redémarrer.

### Configuration
Paramètres → Appareils et services → *Ajouter une intégration* → **Tempo** :
1. URL interne du backend (ex. `http://tempo-api:8000`) et jeton de service (vérifiés via
   `/v1/status`) ;
2. traceur `device_tracker` du téléphone (aussi enregistré dans les réglages du backend).

Options : traceur, intervalle d'interrogation (60 s par défaut, minimum 30 s), service de
notification facultatif (ex. `notify.mobile_app_telephone`) et **tableau de bord Tempo**.

### Tableau de bord (sans YAML)
Deux façons de l'activer :
- Options de l'intégration → cocher *Afficher le tableau de bord Tempo* ;
- ou action `tempo.install_dashboard` (et `tempo.remove_dashboard` pour le retirer).

Un tableau de bord **Tempo** (`/tempo-planner`) apparaît dans la barre latérale, y compris sur
l'application mobile. Il n'utilise que des cartes intégrées (vue *sections*, calendrier, liste
de tâches, tuiles, markdown, conditionnelles) : agenda, prochain départ (heure de départ, statut,
étapes, temps réel), tâches d'étude, entraînements à confirmer, approbations avec boutons
*Approuver / Refuser la plus ancienne proposition*, état du planning, problèmes, séances
manquées et bouton *Replanifier*. Le choix est conservé après redémarrage.

Pourquoi ce mécanisme : Home Assistant n'offre pas d'API publique permettant à une intégration
de créer un tableau de bord en mode *storage* (la collection des tableaux de bord n'est
accessible qu'aux commandes websocket d'administration ; en créer une seconde écraserait
`.storage/lovelace_dashboards`). L'intégration enregistre donc un panneau Lovelace en lecture
seule, comme un tableau de bord YAML, dont la configuration est générée à chaque ouverture à
partir du registre des entités : il suit les renommages d'entités, n'écrit rien dans `.storage`
et disparaît quand l'intégration est déchargée. Il n'est pas modifiable dans l'interface ; pour
le personnaliser, créez votre propre tableau de bord avec les mêmes entités. Ce mécanisme
s'appuie sur des structures internes de Lovelace (`LOVELACE_DATA`, `LovelaceConfig`) vérifiées
sur 2026.2.3 et 2026.10.4 ; il pourrait devoir être adapté à une future version.

### Entités
- **Calendrier `calendar.tempo`** : cours, études (état planifié / manqué / fait / promu),
  entraînements, courses et trajets (étapes, temps réel, trajet impossible).
- **Liste de tâches** (`todo.tempo_*`) : tâches d'étude ; une date d'échéance est obligatoire,
  l'effort est lu dans la description (« 120 min », sinon 60 min), cible souple par défaut.
- **Capteurs** : prochain événement, prochain départ, statut du départ, étude restante, étude du
  jour, état du planning (`missed_blocks` en attribut), approbations en attente, notifications
  non lues, problèmes de planification, **entraînements à confirmer** (nombre + liste `id`,
  `title`, `preferred_day`, `kind`).
- **Boutons** : *Replanifier*, *Approuver / Refuser la plus ancienne proposition* (l'id et le
  titre de la proposition visée sont en attributs et affichés en premier dans le tableau de bord).
- **Agent de conversation** `conversation.tempo_assistant` (voir ci-dessous).

### Actions
`tempo.add_task`, `replan`, `approve_proposal`, `reject_proposal`, `lock_block`,
`unlock_block`, `complete_block`, `add_training`, `add_race`, **`confirm_activity`**
(`activity_id`, `duration_minutes`, `start` obligatoire pour une course), `set_place`,
`set_availability`, `acknowledge_notification`, `interpret` (renvoie `understood`, `action`,
`summary`, `applied`, `used_llm`), **`install_dashboard`**, **`remove_dashboard`**.

Exemple de confirmation d'un entraînement :
```yaml
action: tempo.confirm_activity
data:
  activity_id: act-2          # voir l'attribut du capteur « Entraînements à confirmer »
  duration_minutes: 60
```

### Assistant conversationnel
Paramètres → Assistants vocaux → créer ou modifier un assistant → *Agent de conversation* :
**Tempo Assistant** (langues : français, anglais). Le texte est transmis tel quel à
`/v1/interpret` ; la réponse affichée ou prononcée est le `summary` du backend (y compris pour
les questions, ex. « qu'est-ce que j'ai demain ? », « prochain départ ? »). Si une action est
appliquée, les entités sont rafraîchies. Les erreurs (backend injoignable, jeton refusé,
fonction absente) sont signalées clairement ; aucune réponse n'est inventée.

### Notifications et position
Inchangé depuis 0.1.0 : notifications persistantes pour les notifications du backend,
notifications mobiles actionnables pour les propositions si un service `notify` est configuré,
alertes de départ, et envoi de la position réelle du traceur (avec `gps_accuracy`, limité à une
fois par minute sauf déplacement > 100 m).

### Images de marque
`custom_components/tempo/brand/` contient une icône originale (`icon.png`, `icon@2x.png` et
variantes `dark_`), générée par `scripts/make_brand.py`. Home Assistant 2026.10.4 sert ces
images localement pour les intégrations personnalisées (vérifié dans le code installé) ; sur
2026.2.3 ce mécanisme n'existe pas et l'icône générique est affichée. La documentation HACS
demande un dossier `brand` contenant au moins `icon.png`. Aucune demande n'a été faite au dépôt
`home-assistant/brands`.

## English

**Install**: HACS custom repository (once published) or copy `custom_components/tempo` into
`config/custom_components/`, restart, then add the **Tempo** integration (internal backend URL,
service token, phone `device_tracker`).

**Dashboard**: enable *Show the Tempo dashboard in the sidebar* in the integration options or
call `tempo.install_dashboard` (`tempo.remove_dashboard` removes it). A read-only, phone-friendly
**Tempo** dashboard (`/tempo-planner`, core cards only) shows the agenda, next departure, tasks,
trainings to confirm, approvals with approve/reject-oldest buttons, plan status, issues and a
replan button. Home Assistant has no public API for an integration to create a storage-mode
dashboard, so the integration registers a generated, YAML-like Lovelace panel rebuilt from the
entity registry on every load (relies on Lovelace internals verified on 2026.2.3 and 2026.10.4).

**Entities**: calendar, to-do list, sensors (including *Trainings to confirm*), buttons
(*Replan*, *Approve/Reject oldest proposal*) and the conversation agent.

**Actions**: as listed in the French section, including `tempo.confirm_activity`
(`activity_id`, `duration_minutes`, `start` required for a race), `install_dashboard` and
`remove_dashboard`.

**Conversation agent**: Settings → Voice assistants → choose **Tempo Assistant** (French and
English). Text is forwarded to `/v1/interpret` and the backend `summary` is spoken or shown;
entities are refreshed when the request was applied; backend errors are reported, never
invented.

**Brand**: original icons in `custom_components/tempo/brand/` are served locally by Home
Assistant 2026.10.4 (not by 2026.2.3). Nothing was submitted to `home-assistant/brands`.

## Development
```bash
uv sync                       # Python 3.13.2+ (see .python-version)
uv run ruff check . && uv run ruff format --check .
uv run mypy                   # strict, custom_components/tempo
uv run pytest                 # Linux or WSL only: Home Assistant does not import on Windows
python scripts/make_brand.py  # regenerate brand images (needs Pillow)
```
Tests use `pytest-homeassistant-custom-component` with the backend HTTP API mocked. The
`tests/test_metadata.py` checks replace what can be checked locally from hassfest/HACS
(manifest keys and order, translations parity fr/en/strings, services/icons, brand sizes).

CI (`.github/workflows/validate.yml`: hassfest, HACS action, lint/types/tests) only runs once the
repository is published on GitHub; it has not run yet.

## Before publication (owner decisions)
- Choose a licence, GitHub description and topics, first release (HACS action requirements).
