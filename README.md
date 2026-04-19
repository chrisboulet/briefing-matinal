# Briefing Matinal

Pipeline de briefing matinal personnalisé — veille multi-domaine livrée 2×/jour sur Telegram via [hermes-agent](https://github.com/chrisboulet/hermes-agent).

> **État** : V1 assemblée et testée hors-ligne. 4 phases sur 7 mergées (`main`). 104 tests verts. Mode live xAI câblé, **non encore validé** sur API réelle. Voir [`PLAN.md`](./PLAN.md) pour l'état d'avancement détaillé.

## Aperçu

- 🌅 **2 briefings/jour** : 6h45 (matin, fenêtre 17h30→6h30) + 17h30 (soir, fenêtre 6h30→17h15) America/Toronto
- 📰 **Sources** : 15 comptes X surveillés + 8 recherches X thématiques + sites QC/CA (`journaldequebec.com`, `lesaffaires.com`, `lapresse.ca`)
- 🤖 **Pipeline** : xAI Grok (`grok-4-1-fast-latest`) via Responses API + tools server-side `x_search` / `web_search`
- 📱 **Format** : HTML standalone (~8 KB), inline CSS, mobile-first, dark/light auto, contraste AA
- 🔗 **Tracking clics** : short-links via redirector Tailscale (Phase 4)
- 💰 **Budget** : ~0.22-0.30 $/jour (~7-9 $/mois)

## Architecture

```
briefing-matinal/
├── PRD.md                       # Spec V2 (source de vérité)
├── PLAN.md                      # Plan 7 phases + état d'avancement
├── CLAUDE.md                    # Guide Claude Code
├── docs/
│   ├── xai-integration.md       # Référence opérationnelle xAI
│   └── preview/                 # Snapshots HTML pour visualisation
├── sources/
│   ├── comptes.json             # Config runtime
│   └── comptes.schema.json      # Validation JSON Schema
├── prompts/                     # Templates Jinja LLM versionnés
├── scripts/                     # Modules pipeline + CLI
├── templates/                   # Jinja HTML
├── tests/                       # 104 tests (pytest, mocks)
└── output/                      # Briefings générés (gitignored)
```

## Voir un briefing rendu

🔗 https://htmlpreview.github.io/?https://github.com/chrisboulet/briefing-matinal/blob/main/docs/preview/sample-matin.html

(Régénérable via `python -m scripts.build_briefing --moment matin --fixture tests/fixtures/sample_matin.json && cp output/2026-04-19-matin.html docs/preview/sample-matin.html`)

## Lancer un briefing

### Mode offline (fixture, gratuit)

```bash
python -m scripts.build_briefing --moment matin \
  --fixture tests/fixtures/sample_matin.json --dry-run
```

Sortie JSON sur stdout : `{"status": "ok", "items_count": 17, "size_bytes": 8155, ...}`. HTML écrit dans `output/YYYY-MM-DD-matin.html` si `--dry-run` est omis.

### Mode live (xAI, ~0.20 $/run)

```bash
export XAI_API_KEY="xai-..."
python -m scripts.build_briefing --moment matin --dry-run \
  2>&1 | jq 'select(.event=="xai_call")'
```

Voir [`docs/xai-integration.md`](./docs/xai-integration.md) pour la référence opérationnelle complète (endpoint, retry, coût, troubleshooting).

## Modification de la portée

| Quoi | Où |
|---|---|
| Ajouter/retirer un compte X | `sources/comptes.json` → `comptes_x` |
| Ajouter/retirer une recherche thématique | `sources/comptes.json` → `recherches_thematiques` (lier à un `section_id`) |
| Changer un seuil d'engagement | `sources/comptes.json` → `engagement_min` |
| Ajouter une section | `sources/comptes.json` → `sections` (le template itère dynamiquement) |
| Modifier le rendu HTML | `templates/briefing.html` + `templates/partials/_item.html` |
| Modifier les prompts LLM | `prompts/*.txt` + bumper `PROMPTS_VERSION` dans `scripts/build_briefing.py` |
| Modifier la logique de sourcing | `scripts/sourcing.py` |

## Développement

Prérequis : Python 3.11+, `pip` ≥ 23.

```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,redirector]"

# Tests + lint
pytest -q                     # 104 tests, 1.9s
ruff check .                  # All checks passed

# Validation config
python -c "import json, jsonschema; \
  jsonschema.validate(json.load(open('sources/comptes.json')), \
                      json.load(open('sources/comptes.schema.json'))); \
  print('config OK')"
```

## Contraintes dures

- **HTML standalone** : CSS inline, pas de CDN, pas de JS, polices système
- **Budget lisibilité 50 KB** (réel actuel 8.1 KB sur briefing plein)
- **Mobile-first**, contraste AA validé (≥7:1)
- **Latence < 3 min** par briefing
- **Output gitignored**

## Documentation

| Fichier | Rôle |
|---|---|
| [`PRD.md`](./PRD.md) | Spec fonctionnelle V2, log des décisions, modes d'erreur, data model |
| [`PLAN.md`](./PLAN.md) | 7 phases d'implémentation + état d'avancement |
| [`CLAUDE.md`](./CLAUDE.md) | Guide pour Claude Code (architecture, commandes, conventions) |
| [`docs/xai-integration.md`](./docs/xai-integration.md) | Référence opérationnelle xAI (endpoint, retry, coût) |

## Licence

Proprietary — Christian Boulet.
