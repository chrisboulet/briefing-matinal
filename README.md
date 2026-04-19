# Briefing Matinal

Pipeline de briefing matinal personnalisé — veille multi-domaine livrée via Telegram.

## Architecture

```
briefing-matinal/
├── PRD.md                      # Product Requirements Document
├── sources/
│   └── comptes.json            # Comptes X, recherches, sources web
├── templates/                  # Templates HTML (à venir)
├── scripts/                    # Scripts de build (à venir)
└── output/                     # Briefings générés (gitignored)
```

## Usage

Ce repo est utilisé par Hermes Agent via le skill `briefing-matinal`. Le pipeline :
1. Lit `sources/comptes.json` pour la config
2. Interroge xAI Grok + web_search
3. Compile le HTML via template
4. Livre sur Telegram à 6h45 AM

## Modification

- Ajouter/retirer un compte X → éditer `sources/comptes.json`
- Changer le template → éditer `templates/briefing.html`
- Changer la logique de sourcing → éditer `scripts/build_briefing.py`

## Développement

Prérequis : Python 3.11+, `pip` ≥ 23.

```bash
# Setup
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,redirector]"

# Vérifier que tout est en place
ruff check .
pytest

# Valider la config contre le schema
python -c "import json, jsonschema; \
  jsonschema.validate(json.load(open('sources/comptes.json')), \
                      json.load(open('sources/comptes.schema.json'))); \
  print('config OK')"

# Lancer un briefing en dry-run avec fixtures (à venir Phase 1)
python -m scripts.build_briefing --moment matin --dry-run
```

Voir [`PRD.md`](./PRD.md) pour les specs et [`PLAN.md`](./PLAN.md) pour l'ordre d'implémentation.
