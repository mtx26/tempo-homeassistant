# Tempo — Home Assistant integration

Custom integration (HACS layout) for **Tempo**, a self-hosted personal planner managed by
natural-language conversation. The planning backend is a separate private service reached over
the internal network with a bearer token. Version `0.3.0`. Product and architecture:
`ARCHITECTURE.md` of the Tempo backend repository.

Licence: to be chosen by the owner.

## Ce que fait l'intégration / What it does

| Élément | Rôle |
| --- | --- |
| Agent de conversation **Tempo** (Assist, texte ou voix) | **Unique interface de gestion** : créer, déplacer, annuler, verrouiller, déclarer le travail fait, accepter/refuser une proposition, régler lieux, disponibilités et rappels… Le texte part vers `POST /v1/conversation` avec l'identifiant de conversation Assist (les références « déplace-le » gardent leur contexte) ; la réponse prononcée est celle du backend, jamais inventée. |
| Calendrier `calendar.tempo` | Lecture seule : cours, événements, entraînements, compétitions, séances d'étude, trajets. |
| Liste `todo.tempo_tasks` | Lecture seule : travail restant (tâches et volumes d'étude). |
| Capteurs | Prochain événement, prochain départ, état du départ, étude du jour, travail restant, état du plan, points à vérifier, propositions en attente, entraînements à confirmer. |
| Position | Pousse la position GPS réelle du `device_tracker` configuré (throttling 60 s / 100 m). |
| Notifications | Chaque notification du backend (alertes et rappels de départ compris) devient une notification persistante, relayée au service `notify` facultatif, puis est acquittée côté backend (remise une seule fois). **Informatives uniquement** (aucun bouton d'action) : on répond à Tempo en langage naturel. |

Aucun service métier, bouton, formulaire ou tableau de bord de modification : tout passe par la
conversation. Seule la configuration technique (URL, jeton, traceur, intervalle, service de
notification, modèle d'IA, adresse publique de l'agenda) se fait dans les écrans
d'intégration.

## Compatibilité / Compatibility

| Home Assistant | Python | Résultat / Result |
| --- | --- | --- |
| 2026.2.3 | 3.13 | suite de tests complète OK (+ mypy strict, Ruff) |
| 2026.11.2 (conteneur local) | — | recette réelle avec le backend de développement (`scripts/ha_e2e.py` du dépôt backend) |

`hacs.json` déclare le minimum `2026.2.0`. Les tests automatisés simulent le backend ; un test
de contrat du dépôt backend exécute le vrai client de cette intégration contre le vrai backend.
**Installation HACS réelle non testée** (le dépôt n'est pas publié). La conversation attend
jusqu'à 150 s la réponse du backend (budget de 60 s de l'assistant plus une replanification).

## Installation
- **HACS (une fois le dépôt publié)** : HACS → ⋮ → *Dépôts personnalisés* →
  `https://github.com/mtx26/tempo-homeassistant`, catégorie *Intégration*, installer *Tempo*,
  redémarrer Home Assistant.
- **Manuelle** : copier `custom_components/tempo` dans `config/custom_components/`, redémarrer.

## Configuration
Paramètres → Appareils et services → *Ajouter une intégration* → **Tempo** :
1. URL interne du backend (ex. `http://tempo-api:8000`) et jeton de service ;
2. `device_tracker` du téléphone (source de la position réelle) ;
3. réglages : modèle d'IA (liste des modèles Gemini du projet Google Cloud, testé par le backend
   avant d'être retenu ; absent si le backend n'a pas d'IA), service `notify` (liste des services
   existants).

Options (modifiables à tout moment) : modèle d'IA, traceur, intervalle d'interrogation (60 s,
minimum 30 s), service `notify` facultatif et adresse publique de l'agenda (l'URL d'abonnement
du téléphone s'affiche alors en haut du formulaire). Les résidences se disent à l'assistant.

Puis, dans Assist, choisir l'agent **Tempo** et parler normalement, par exemple :
« Prévois 15 heures de physique avant vendredi », « Dentiste jeudi à 14 h au centre-ville »,
« Demain je pars de chez papa », « Rappelle-moi deux fois avant de partir au cours »,
« Qu'est-ce qui a changé cette nuit ? », « Accepte la proposition ».

## Development
```bash
uv sync                       # Python 3.13.2+
uv run ruff check . && uv run ruff format --check .
uv run mypy                   # strict
uv run pytest                 # Linux or WSL only: Home Assistant does not import on Windows
```
`tests/test_metadata.py` covers what can be checked locally from hassfest/HACS (manifest,
translation parity, absence of business services/buttons, brand sizes). The CI workflow
(`.github/workflows/validate.yml`) only runs once the repository is published.

## Before publication (owner decisions)
- Licence, GitHub repository visibility/description, first release.
