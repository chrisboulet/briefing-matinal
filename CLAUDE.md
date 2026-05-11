# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Règles comportementales (12 règles BST-grade — non-négociables)

> Source : Karpathy (règles 1-4) + Mnilax (règles 5-12, mai 2026). Taux d'erreur documenté : 41% → 3%.
> Ces règles sont **advisory** (~76% compliance). Ne pas dépasser 200 lignes dans ce fichier.

1. **Think Before Coding** — Formuler les hypothèses explicitement avant d'écrire. Jamais d'interprétation silencieuse. Pousser en retour si une approche plus simple existe.
2. **Simplicity First** — Code minimum qui résout le problème. Zéro feature spéculative. Test mental : un dev senior dirait-il que c'est trop compliqué ?
3. **Surgical Changes** — Toucher uniquement ce qui est demandé. Ne pas "améliorer" le code adjacent. Matcher le style existant.
4. **Goal-Driven Execution** — Reformuler toute demande vague en critères vérifiables. Pour multi-étapes : plan + checkpoints avant d'agir.
5. **Use the Model Only for Judgment Calls** — LLM pour classification/rédaction/extraction. PAS pour routing/retry/status codes/transforms déterministes.
6. **Token Budgets Are Not Advisory** — Budget tâche : 4k tokens, session : 30k. Quand on approche : résumer et repartir. Signaler le dépassement plutôt que continuer silencieusement.
7. **Surface Conflicts, Don't Average Them** — Si deux patterns contradictoires coexistent : choisir le plus récent/testé, expliquer, signaler l'autre. Le code qui "satisfait les deux" est le pire.
8. **Read Before You Write** — Avant d'ajouter du code : lire les exports du fichier, le caller immédiat, les utilitaires partagés. "Looks orthogonal to me" = phrase la plus dangereuse.
9. **Tests Verify Intent, Not Just Behavior** — Chaque test encode POURQUOI le comportement importe. Un test qui passerait même si la logique business était cassée est inutile.
10. **Checkpoint After Every Significant Step** — Après chaque étape multi-étapes : résumer état actuel, ce qui est vérifié, ce qui reste. Ne pas continuer depuis un état indescriptible.
11. **Match Conventions, Even If You Disagree** — Respecter les conventions du codebase même sous-optimales. Signaler, ne pas unilatéralement changer le style.
12. **Don't Resume from Unknown State** — Au début d'une tâche reprise : relire les fichiers pertinents. Si état ambigu → demander confirmation avant d'agir. Jamais en aveugle.

En cas de conflit avec les contraintes dures ci-dessous, les contraintes projet gagnent.

---

## État du projet (avril 2026)

Pipeline V1 **assemblé et testé hors-ligne**. 4 phases du PLAN mergées sur `main` (PR #2, #3, #4, #6). 104 tests verts (`pytest -q`). Mode live xAI câblé mais **non encore validé contre l'API réelle** — requiert `XAI_API_KEY` côté hermes-agent.

| Phase | Statut | Livrables |
|---|---|---|
| 0 — Scaffolding | ✅ PR #2 | `pyproject.toml`, JSON schema, dirs |
| 1 — Pipeline offline | ✅ PR #3 | `models`, `config`, `window`, `dedup`, `select`, `render`, `build_briefing`, fixture, 30 tests |
| 2 — Template HTML prod | ✅ PR #4 | `templates/briefing.html` AA + dark/light, macro `_item.html`, 15 tests |
| 3 — Intégration xAI | ✅ PR #6 | `xai_client`, `sourcing`, 4 prompts versionnés, `docs/xai-integration.md`, 59 tests |
| 4 — Redirector Tailscale | ❌ Skipped ([#13](https://github.com/chrisboulet/briefing-matinal/issues/13)) | N=1 lecteur, pas de tracking en V1 |
| 5 — Intégration hermes-agent | ⏳ | contrat stdout JSON figé, fallback NAS, runbook |
| 6 — Tests étendus | ⏳ | DST edge cases, schema invalid, fixtures additionnelles |
| 7 — Mise en service V1 | ⏳ | cron, premier envoi, monitoring 2 semaines |

## Architecture actuelle

```
briefing-matinal/
├── PRD.md                          # Spec V2 (source de vérité)
├── PLAN.md                         # Plan d'impl 7 phases (état d'avancement)
├── docs/
│   ├── xai-integration.md          # Référence opérationnelle xAI
│   └── preview/sample-matin.html   # Snapshot HTML pour visualisation
├── sources/
│   ├── comptes.json                # Config runtime (15 comptes, 8 recherches, 6 sections)
│   └── comptes.schema.json         # JSON Schema draft 2020-12
├── prompts/                        # Templates Jinja LLM (versionnés via PROMPTS_VERSION)
│   ├── system.txt
│   ├── search_accounts.txt
│   ├── search_theme.txt
│   └── search_web.txt
├── scripts/
│   ├── models.py                   # Item, Briefing dataclasses
│   ├── config.py                   # load_config + schema validation
│   ├── window.py                   # Fenêtres matin/soir America/Toronto
│   ├── dedup.py                    # canonical_url + title hash
│   ├── select.py                   # quotas par section + dont_miss
│   ├── render.py                   # Jinja2 + validation taille/CDN
│   ├── xai_client.py               # httpx wrapper Responses API + retry
│   ├── sourcing.py                 # orchestrateur appels xAI
│   ├── fixture_loader.py           # mode offline (Phase 1)
│   └── build_briefing.py           # CLI (switch fixture/live)
├── templates/
│   ├── briefing.html               # Layout Jinja AA-compliant
│   └── partials/_item.html         # Macro item réutilisée
├── tests/                          # 104 tests, 1.9s
└── output/                         # Briefings générés (gitignored)
```

**Flux exécution** :

`comptes.json` → `build_briefing.py --moment {matin|soir}` → soit `fixture_loader` (offline) soit `sourcing.source_briefing(XAIClient)` (live, appels parallèles `x_search`/`web_search` via Responses API) → filtres engagement + fenêtre → `dedup` → `select` (quotas par section + dont_miss) → `render` Jinja → HTML standalone dans `output/YYYY-MM-DD-{matin|soir}.html` + JSON stdout pour hermes-agent.

## Commandes courantes

```bash
# Setup initial
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Tests + lint
pytest -q                            # 104 tests, 1.9s
ruff check .                         # All checks passed

# Mode offline (fixture, gratuit)
python -m scripts.build_briefing --moment matin \
  --fixture tests/fixtures/sample_matin.json --dry-run

# Mode live (requiert XAI_API_KEY)
export XAI_API_KEY="xai-..."
python -m scripts.build_briefing --moment matin --dry-run \
  2>&1 | jq 'select(.event=="xai_call")'

# Valider la config contre le schema
python -c "import json, jsonschema; \
  jsonschema.validate(json.load(open('sources/comptes.json')), \
                      json.load(open('sources/comptes.schema.json'))); \
  print('config OK')"

# Régénérer le snapshot preview
python -m scripts.build_briefing --moment matin \
  --fixture tests/fixtures/sample_matin.json && \
  cp output/2026-04-19-matin.html docs/preview/sample-matin.html
```

## Décisions V1 figées (voir PRD §Décisions V1)

| # | Décision | Valeur |
|---|---|---|
| D1 | Cadence | 2x/jour, 6h45 + 17h30 America/Toronto |
| D2 | Weekends | Identique semaine |
| D3 | Budget lecture | Triage pur ≤ 5 min |
| D4 | Politique | 1 section unifiée, 3 items max |
| D5 | "À ne pas manquer" | Dans les 2 briefings |
| D6 | Tracking | ❌ Aucun tracking V1 ([issue #13](https://github.com/chrisboulet/briefing-matinal/issues/13)) — self-report à la place |
| D7 | Langue | FR-QC structure, bilingue toléré sans traduction |

## Contraintes dures (non négociables)

- **HTML standalone** : CSS inline (`<style>` unique), pas de CDN, pas de JS, polices système.
- **Budget lisibilité ~50 KB** (réel actuel : 8.1 KB sur fixture pleine — 6× sous budget).
- **Mobile-first**, contraste AA validé (≥7:1 partout en réalité).
- **Auto dark/light** via `prefers-color-scheme`.
- **Latence < 3 min** pour la génération complète (Phase 1 offline tourne en <1s).
- **Budget xAI** ~0.22-0.30 $/jour (cf. `docs/xai-integration.md`).
- **Output gitignored** (`output/`), comme `.env`, `*.db`, `.venv/`, caches.

## Stack technique

- **Python 3.11+** (alias `datetime.UTC`, `zoneinfo` stdlib)
- **httpx** sync pour xAI Responses API
- **jinja2** StrictUndefined + autoescape
- **pydantic** pour validation (contrat I/O xAI)
- **jsonschema** draft 2020-12 pour `comptes.json`
- **fastapi + uvicorn** (dormant dans `[redirector]` extras — Phase 4 skippée #13, hooks préservés pour reprise éventuelle si N > 1)
- **pytest + pytest-httpx** pour mocks API
- **ruff** line-length 110, target py311 (configuré dans `pyproject.toml`)
- **mypy** strict (configuré, pas encore lancé en CI)

## Conventions

- **Branches** : `claude/<phase-ou-feature>-<slug>` (ex. `claude/phase-3-xai`, `claude/docs-update`)
- **PRs** : 1 PR par phase, squash-merge sur `main`
- **Commits atomiques** au sein d'une PR (chore/feat/test/docs scopés)
- **PROMPTS_VERSION** bumpée manuellement à chaque modification sémantique des prompts (injectée en footer HTML pour traçabilité)
- **Config hash** SHA-256 de `comptes.json` injecté en footer HTML
- **TODO(live):** marqueurs aux endroits où la doc xAI accessible ne fige pas la forme exacte — à valider au premier appel réel

## Non-goals explicites (voir PRD §Non-goals)

Pas de posting X, pas de résumé vidéo YouTube, pas d'analyse de sentiment, pas de dashboard web, pas de multi-utilisateur, pas de scraping HTML direct, pas d'alertes breaking news hors cycle (v2+), pas de traduction automatique. Ne pas fusionner avec le skill `bst-veille-hebdo`.

## Références

- **PRD.md** — spec fonctionnelle V2, log des décisions V1, modes d'erreur, data model
- **PLAN.md** — découpage 7 phases avec acceptance criteria
- **docs/xai-integration.md** — référence opérationnelle xAI (endpoint, schema, retry, cost)
- **sources/comptes.json** + **comptes.schema.json** — config runtime + validation
- **docs/preview/sample-matin.html** — snapshot HTML rendu (visualisation via `htmlpreview.github.io`)
