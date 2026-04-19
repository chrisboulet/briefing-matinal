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
- Changer la logique de sourcing → éditer `scripts/build-briefing.py`
