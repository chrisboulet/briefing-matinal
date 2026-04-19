# Plan d'implémentation — Briefing Matinal V1

> Ce document détaille l'ordre et la structure d'implémentation de la V1 du pipeline, tel que spécifié dans `PRD.md` v2. Il est destiné à être relu avant chaque commit pour s'assurer qu'on reste dans le scope.

## 🚦 État d'avancement (mis à jour 2026-04-19)

| Phase | Statut | PR | SHA | Tests cumulés |
|---|---|---|---|---|
| 0 — Scaffolding | ✅ Mergé | [#2](https://github.com/chrisboulet/briefing-matinal/pull/2) | `de9b8e2` | 0 |
| 1 — Pipeline offline | ✅ Mergé | [#3](https://github.com/chrisboulet/briefing-matinal/pull/3) | `05f8f2b` | 30 |
| 2 — Template HTML prod | ✅ Mergé | [#4](https://github.com/chrisboulet/briefing-matinal/pull/4) | `b0ca7f1` | 45 |
| 3 — Intégration xAI | ✅ Mergé | [#6](https://github.com/chrisboulet/briefing-matinal/pull/6) | `09e4c8a` | **104** |
| 4 — Redirector Tailscale | ❌ Skipped ([#13](https://github.com/chrisboulet/briefing-matinal/issues/13)) | — | — | — |
| 5 — Intégration hermes-agent | ⏳ À faire | — | — | — |
| 6 — Tests étendus | ⏳ À faire | — | — | — |
| 7 — Mise en service V1 | ⏳ À faire | — | — | — |

**PR bonus** : [#5](https://github.com/chrisboulet/briefing-matinal/pull/5) — `docs/preview/sample-matin.html` (snapshot HTML pour visualisation navigateur).

**Métriques actuelles** :
- Tests : **104/104 verts** en 1.9s (`pytest -q`)
- Lint : `ruff check .` → All checks passed
- Briefing complet (17 items via fixture) : **8.1 KB** (6× sous budget 50 KB)
- Mode live xAI **câblé mais non encore validé** sur API réelle (requiert `XAI_API_KEY`)

**Prochaines étapes** : Phase 4 skippée (issue #13). Chemin critique : **Phase 5** (intégration hermes-agent) → **Phase 6** (tests étendus, au fil) → **Phase 7** (go-live, une fois mode live validé sur premier appel réel).

---

## Contexte

- **PRD source** : `PRD.md` (v2, commit `5a61521`)
- **Stack** : Python 3.11+, Jinja2, httpx (xAI). FastAPI/SQLite étaient prévus pour Phase 4 (redirector) mais skippés via [#13](https://github.com/chrisboulet/briefing-matinal/issues/13).
- **Exécution** : hermes-agent orchestre, ce repo fournit scripts + config
- **Budget V1** : ~0.35-0.40 $/jour, latence < 3 min par briefing

## Principes directeurs

1. **Offline-first** — toute la logique (ranking, dédup, rendu, contrat stdout) doit tourner sans appel API, via fixtures. On branche xAI en phase 3, pas avant.
2. **Chaque phase se termine par un artefact testable** — jamais "à moitié fini" entre deux phases.
3. **Le `--dry-run` est disponible dès la phase 1** — on ne spamme jamais le vrai Telegram/NAS pendant le dev.
4. **Pas d'optimisation prématurée** — V1 vise la correctness, pas la perf. On profile seulement si on frôle les 3 min de budget latence.
5. **Commits atomiques par phase** — chaque phase = 1 à 3 commits max, pas de PR monstre.

## Chemin critique

```
Phase 0 (scaffolding)
    ↓
Phase 1 (pipeline offline avec fixtures)  ← Milestone 1 : HTML généré sans API
    ↓
Phase 2 (template Jinja2 final)           ← Milestone 2 : briefing visuellement OK
    ↓
Phase 3 (intégration xAI)                 ← Milestone 3 : briefing avec vraies données
    ↓
Phase 5 (intégration hermes-agent)        ← Milestone 4 : contrat script↔harness OK
    ↓
Phase 6 (tests & validation)              ← Milestone 5 : suite de tests verte
    ↓
Phase 7 (mise en service V1)              ← Milestone 6 : prod, cron actif
```

Phases 1 et 2 peuvent se chevaucher si besoin. Tout le reste est séquentiel. Phase 4 (redirector) skippée via [issue #13](https://github.com/chrisboulet/briefing-matinal/issues/13) — N=1 lecteur, pas de tracking en V1.

---

## Phase 0 — Scaffolding ✅ Mergé via PR #2 (`de9b8e2`)

**Objectif** : poser la structure du repo et les dépendances sans écrire de logique métier.

### Livrables

| Fichier | Contenu |
|---|---|
| `pyproject.toml` | Projet Python avec deps pinnées, `ruff` + `pytest` en dev |
| `.python-version` | `3.11` (pour pyenv/asdf) |
| `.env.example` | Template des env vars (`XAI_API_KEY=`, `BRIEFING_ENV=`) |
| `.gitignore` (update) | Ajouter `*.db`, `.venv/`, `.pytest_cache/`, `.ruff_cache/` |
| `scripts/` | Dossier vide avec `__init__.py` |
| `templates/` | Dossier vide |
| `prompts/` | Dossier vide |
| `tests/fixtures/` | Dossier vide |
| `sources/comptes.schema.json` | JSON Schema draft 2020-12 valide `comptes.json` |
| `README.md` (update) | Ajout section "Développement" (install, run, test) |

### Dépendances Python pinnées

```toml
[project]
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27,<1.0",
    "jinja2>=3.1,<4.0",
    "pydantic>=2.7,<3.0",
    "python-slugify>=8.0,<9.0",  # pour normalisation titres dans dédup
]

[project.optional-dependencies]
redirector = [
    "fastapi>=0.110,<1.0",
    "uvicorn[standard]>=0.29,<1.0",
]
dev = [
    "pytest>=8.0,<9.0",
    "pytest-httpx>=0.30,<1.0",  # mock xAI
    "ruff>=0.4,<1.0",
    "mypy>=1.10,<2.0",
]
```

### Acceptance criteria

- [ ] `python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"` passe sans erreur (`[redirector]` extra dormant, optionnel)
- [ ] `pytest` tourne (collecte 0 tests, c'est normal)
- [ ] `ruff check .` passe (pas encore de code)
- [ ] `python -c "import json, jsonschema; jsonschema.validate(json.load(open('sources/comptes.json')), json.load(open('sources/comptes.schema.json')))"` passe

### Commits suggérés

1. `chore: bootstrap python project (pyproject, venv, gitignore)`
2. `chore: scaffold directories (scripts, templates, prompts, tests)`
3. `feat(config): add JSON schema for comptes.json + schema_version`

---

## Phase 1 — Pipeline offline avec fixtures ✅ Mergé via PR #3 (`05f8f2b`)

**Objectif** : produire un HTML de briefing complet à partir de fixtures JSON, sans aucun appel externe. **C'est le milestone le plus important de V1** — si ça marche ici, le reste est du branchement.

### Livrables

| Fichier | Rôle |
|---|---|
| `scripts/models.py` | Dataclasses `Item`, `Briefing` (voir PRD §Data model) |
| `scripts/config.py` | Chargement + validation de `comptes.json` contre le schema |
| `scripts/window.py` | Calcul des fenêtres temporelles matin/soir (pure function) |
| `scripts/dedup.py` | Logique de dédup (URL canonique + hash titre) |
| `scripts/select.py` | Sélection `max_items` par section + sélection "À ne pas manquer" |
| `scripts/render.py` | Wrapper Jinja2 autour de `templates/briefing.html` |
| `scripts/build_briefing.py` | Entrée CLI avec `argparse`, orchestrateur |
| `tests/fixtures/x_search_sample.json` | ~30 posts X fake répartis sur tous les comptes/thèmes |
| `tests/fixtures/web_search_sample.json` | ~15 résultats web fake |
| `templates/briefing.html` | **Placeholder minimal** (phase 2 polira) |

### Logique du CLI

```bash
python -m scripts.build_briefing \
    --moment matin \
    --fixture tests/fixtures/x_search_sample.json \
    --fixture tests/fixtures/web_search_sample.json \
    --dry-run
```

Comportement :
1. Valide `sources/comptes.json`
2. Charge les fixtures comme si c'étaient des retours xAI
3. Applique filtres qualité (engagement min, exclure replies, etc.)
4. Dédup
5. Sélectionne `max_items` par section + 1 item "À ne pas manquer"
6. Rend le HTML via Jinja2 (template placeholder OK à ce stade)
7. Écrit `output/YYYY-MM-DD-matin.html`
8. Print JSON stdout : `{"status": "ok", "path": "...", "briefing_id": "...", "items_count": N}`
9. `--dry-run` = n'écrit PAS sur disque, print le HTML tronqué sur stderr pour inspection

### Acceptance criteria

- [ ] Le CLI produit un HTML non vide
- [ ] Le JSON stdout est parseable et contient tous les champs attendus
- [ ] La dédup marche : fixture avec 2 items même URL → 1 item, `alt_sources` peuplé
- [ ] La sélection respecte `max_items` par section
- [ ] Idempotence : 2 runs consécutifs avec mêmes fixtures produisent exactement le même HTML (bit-à-bit)
- [ ] Latence < 1s pour la phase offline (tout sauf rendu Jinja)

### Risques / gotchas

- **Timezone** : toute la logique de fenêtre temporelle doit utiliser `zoneinfo.ZoneInfo("America/Toronto")`, pas UTC
- **Dédup cross-sections** : un item Tesla peut matcher aussi Business — règle PRD §S1 : le placer dans la plus spécifique
- **Ordre stable** : pour garantir l'idempotence, trier par `(score DESC, published_at DESC, id ASC)` partout

### Commits suggérés

1. `feat(models): add Item and Briefing dataclasses`
2. `feat(config): load and validate comptes.json`
3. `feat(window): compute morning/evening time windows`
4. `feat(dedup): canonical URL + title-hash dedup`
5. `feat(select): section quota + hero section selection`
6. `feat(render): minimal Jinja2 rendering pipeline`
7. `feat(cli): build_briefing entry point with --dry-run and --fixture`
8. `test(fixtures): sample X + web search fixtures for offline runs`

---

## Phase 2 — Template HTML final ✅ Mergé via PR #4 (`b0ca7f1`)

**Objectif** : transformer le placeholder Phase 1 en template production-ready : mobile-first, dark/light auto, accessibilité AA, budget lisibilité ~50 KB.

### Livrables

> **Note post-merge (PR #4)** : `_section.html` et `_hero.html` initialement prévus ont été **consolidés dans une seule macro `_item.html`** (paramètre `hero=bool`) + des boucles Jinja dans `briefing.html`. Même DRY, moins de fichiers à maintenir.

| Fichier | Rôle |
|---|---|
| `templates/briefing.html` | Template Jinja2 final |
| `templates/partials/_item.html` | Macro `render_item(item, hero=False)` — réutilisée pour sections et dont_miss |
| `scripts/render.py` (update) | Ajout validation taille + regex anti-CDN |
| `tests/test_render.py` | 15 tests structuraux sur le HTML produit |

### Design constraints (rappel PRD §S2)

- **Inline CSS** dans un `<style>` unique en `<head>` (pas d'attributs `style=""` éparpillés, maintenabilité)
- **Polices système** : `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif`
- **Auto dark/light** via `@media (prefers-color-scheme: dark)`
- **Contraste AA** : ≥ 4.5:1 sur texte normal, ≥ 3:1 sur large text
- **Font-size ≥ 15px** sur mobile
- **Emoji natifs** (pas d'images/SVG)
- **Pas de JS**, pas de CDN, pas de Google Fonts
- **Pas d'images** dans le briefing V1 (source : PRD non-goals clarifié)

### Structure visuelle du template

```
<header>
  ☀️ BRIEFING MATIN — vendredi 19 avril 2026
</header>

<section class="standard" id="ai-tech">
  🤖 AI / Tech
  [items via macro _item.html]
</section>
... (tesla, spacex, sante, politique, business)

<section class="dont-miss">
  📌 À NE PAS MANQUER
  [1 item "mis en valeur", avec indication de durée si vidéo]
</section>

<footer>
  ⚙️ briefing_id · config_hash · git_commit · prompts_version
</footer>
```

### Validation au rendu

Dans `render.py`, après rendu Jinja, vérifier :

1. **Taille** : `len(html.encode("utf-8")) < 50_000` → sinon warning stderr (pas bloquant)
2. **HTML valide** : parse avec `html.parser` stdlib, pas d'erreur
3. **Pas de CDN résiduel** : regex `https?://.*\.(googleapis|cloudflare|jsdelivr)` → si match, fail build

### Acceptance criteria

- [ ] Rendu visuel cohérent dans iOS Telegram (dark + light) — test manuel en phase 7
- [ ] Taille < 50 KB pour un briefing "plein" (16 items max)
- [ ] Golden test : HTML rendu avec fixtures stables est bit-à-bit identique entre runs
- [ ] Contraste validé par outil tiers (ex. `axe-core` CLI ou vérif manuelle des couleurs hex)
- [ ] Template lisible en désactivant CSS (structure sémantique propre : `<header>`, `<section>`, `<article>`)

### Commits suggérés

1. `feat(templates): base layout with dark/light auto and inline CSS`
2. `feat(templates): item macro with source attribution and multi-source tag`
3. `feat(templates): hero sections (60s + dont-miss)`
4. `feat(render): size and CDN validation hooks`
5. `test(render): golden tests against fixture briefing`

---

## Phase 3 — Intégration xAI Grok Responses API ✅ Mergé via PR #6 (`09e4c8a`)

**Objectif** : remplacer les fixtures par de vrais appels à l'API xAI. On garde le mode fixture pour les tests et le dev offline.

**Notes post-merge** :
- Délégation à 2 subagents pour qualité indépendante (reviewer + tests writer)
- 3 CRITICAL + 7 MAJOR identifiés et fixés AVANT que les tests soient écrits (format:uri/date-time incompat strict json_schema, 429 retry off-by-one, backoff cap, tool_params validation, etc.)
- 59 tests via mocks (pytest-httpx + StubClient) — aucun appel réel
- `# TODO(live):` marqueurs aux endroits où la doc xAI accessible ne fige pas la forme exacte (extraction `output_text`, nom champ `tool_calls`, params `web_search`)
- **À valider** sur premier appel réel avec `XAI_API_KEY`

### Livrables

> **Note post-merge (PR #6)** : écart volontaire vs plan initial, documenté ici pour traçabilité.
>
> - `scripts/errors.py` prévu → **fusionné dans `scripts/xai_client.py`** (hiérarchie `XAIError` + sous-classes exposées au même niveau que le client, pas d'intérêt à un fichier séparé).
> - `prompts/rank.txt`, `prompts/brief_60s.txt`, `prompts/dont_miss.txt` prévus → **logique déplacée en Python pur** dans `scripts/select.py` (`select_by_section`, `select_sixty_seconds`, `select_dont_miss`). Pas besoin d'un 2e appel LLM : le 1er appel LLM renvoie déjà un `score` par item, et le ranking local est déterministe + idempotent + gratuit.
> - Prompts renommés : `x_search_accounts.txt → search_accounts.txt`, `x_search_theme.txt → search_theme.txt`, `web_search.txt → search_web.txt` (préfixe du tool implicite, nom plus court).
> - **Ajouts** non prévus : `docs/xai-integration.md` (référence opérationnelle), `tests/test_sourcing.py` (22 tests du orchestrateur, en plus des 37 de `test_xai_client.py`).

| Fichier | Rôle |
|---|---|
| `scripts/xai_client.py` | Wrapper `httpx` + hiérarchie d'exceptions + retry/backoff |
| `scripts/sourcing.py` | Orchestrateur (batchs accounts + N thèmes + 1 web) |
| `scripts/build_briefing.py` (update) | Switch `--fixture` (offline) vs live mode via `XAI_API_KEY` |
| `prompts/system.txt` | Prompt système commun à tous les appels |
| `prompts/search_accounts.txt` | Template Jinja prompt comptes X |
| `prompts/search_theme.txt` | Template Jinja prompt thématique X |
| `prompts/search_web.txt` | Template Jinja prompt web |
| `docs/xai-integration.md` | Référence opérationnelle (endpoint, retry, coût, TODO live) |
| `tests/test_xai_client.py` | 37 tests via `pytest-httpx` mocks |
| `tests/test_sourcing.py` | 22 tests via `StubClient` |

### Contrat `xai_client.py`

```python
class XAIClient:
    def __init__(self, api_key: str, model: str = "grok-4-1-fast-non-reasoning"):
        ...

    def x_search(
        self,
        prompt: str,
        allowed_handles: list[str] | None = None,   # max 10
        from_date: date | None = None,
        to_date: date | None = None,
        timeout: float = 30.0,
    ) -> XAIResponse:
        """Un appel à /v1/responses avec tool x_search."""
        ...

    def web_search(
        self,
        prompt: str,
        allowed_domains: list[str] | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        timeout: float = 30.0,
    ) -> XAIResponse:
        ...
```

Chaque appel :
- Log structuré JSON sur stderr : `{"event": "xai_call", "tool": "x_search", "tokens_in": N, "tokens_out": M, "duration_ms": D, "cost_usd": C}`
- Retry : 2 tentatives avec backoff `2s, 8s` sur 5xx / timeout
- Sur 429 : backoff 60s + 1 retry
- Sur échec persistant : lève `XAIUnavailable(tool, reason)` — le caller choisit de dégrader

### Gestion des 15 comptes X

`allowed_x_handles` est limité à 10/call. Stratégie :
- Split des 15 comptes en 2 appels (par exemple : 8 AI+Tesla+SpaceX, puis 7 Santé+Business+Politique)
- Le grouping est configurable dans `comptes.json` via un champ optionnel `comptes_x_groups` (si absent : split équitable auto)

### Mode fixture vs mode live

Le CLI `build_briefing.py` détecte :
- `--fixture <path>` → mode offline, bypass `XAIClient`
- Aucune fixture + `XAI_API_KEY` présent → mode live
- Aucune fixture + pas de clé → erreur explicite, exit 2

### Acceptance criteria

- [ ] Un briefing "live" complet réussit en < 3 min wall-clock
- [ ] Coût mesuré d'un briefing réel ≈ 0.15-0.20 $ (tokens + tool fees)
- [ ] Les prompts sont versionnés (`prompts-v1.0`) et le hash est injecté dans le HTML
- [ ] En cas de panne xAI simulée (mock 503) : le briefing continue avec un warning en header, aucune erreur fatale
- [ ] Les logs structurés sont parseables par `jq`

### Commits suggérés

1. `feat(xai): httpx client for Responses API with retry/backoff`
2. `feat(prompts): versioned prompts with include directive support`
3. `feat(sourcing): parallel orchestration of x_search + web_search`
4. `feat(errors): graceful degradation on tool unavailability`
5. `test(xai): mock-based unit tests (pytest-httpx)`
6. `feat(cli): switch between fixture and live modes`

---

## Phase 4 — Redirector + tracking (Tailscale) ❌ Skipped (issue #13)

**Statut** : **skippée** via [issue #13](https://github.com/chrisboulet/briefing-matinal/issues/13) avant toute implémentation.

**Rationale** : le briefing est livré à N=1 lecteur (Chris). Le coût d'un service web (FastAPI + SQLite + cert Tailscale + systemd + purge mensuelle) pour tracker les clics d'un seul utilisateur ne se justifie pas. Substitution : self-report hebdo de Chris (cf. Phase 7 monitoring + PRD §Succès mesurable).

**Ce qui était prévu** (voir `git log PLAN.md` pour la spec détaillée avant skip) :
- `scripts/redirector.py` (FastAPI : `/b/:short_id`, `/stats`, `/healthz`)
- `scripts/shortlinks.py` + `scripts/migrations/001_init.sql`
- `scripts/redirector.service` (systemd unit) + HTTPS Tailscale
- Tables SQLite `short_links` + `clicks`, purge 12 mois

**Ce qui est préservé pour éventuelle reprise si N > 1** :
- Extra `[redirector]` dormant dans `pyproject.toml` (`fastapi`, `uvicorn[standard]`)
- Champ `Item.short_url: str = ""` dans `scripts/models.py`
- Template Jinja `{{ item.short_url or item.canonical_url }}` dans `templates/partials/_item.html` (tombe sur `canonical_url` aujourd'hui)
- Initialisation `short_url=""` dans `scripts/sourcing.py` à la conversion LLM→Item

---

## Phase 5 — Intégration hermes-agent ⏳ À faire

**Objectif** : brancher le pipeline sur le cron hermes-agent, valider le contrat stdout JSON, activer le fallback NAS.

### Livrables

| Fichier | Rôle |
|---|---|
| `scripts/build_briefing.py` (update) | Respect strict du contrat JSON stdout + exit codes |
| `scripts/hermes_contract.md` | Doc du contrat script↔hermes (exit codes, stdout, stderr, env vars) |
| `docs/deployment.md` | Runbook de déploiement côté hermes-agent (cron, secrets, NAS mount) |

### Contrat script → hermes-agent

**Env vars attendues** :
- `XAI_API_KEY` — requis en mode live
- `BRIEFING_ENV` — `production` | `staging` | `dry-run`
- `TELEGRAM_CHAT_ID` — passé à hermes uniquement, pas au script
- `NAS_PATH` — ex. `/mnt/nas/commun/briefing-matinal`

**Exit codes** :
| Code | Sens | Action hermes |
|---|---|---|
| 0 | Succès, HTML écrit | Lire stdout JSON, envoyer Telegram |
| 2 | Config invalide (schema fail, no API key) | Alerter Chris, pas de retry |
| 3 | NAS indisponible ET Telegram aussi | Email inline du HTML |
| 4 | Budget tokens dépassé | Alerter + pas de retry ce cycle |
| ≠0 autre | Erreur inattendue | 1 retry dans 5 min, sinon alerter |

**Stdout JSON (succès)** :
```json
{
  "status": "ok",
  "path": "output/2026-04-19-matin.html",
  "briefing_id": "2026-04-19-matin",
  "items_count": 14,
  "warnings": ["x_search theme=Politique US returned 0 results"]
}
```

### Acceptance criteria

- [ ] Hermes peut lancer le script, parser le JSON, envoyer le HTML, sans intervention
- [ ] Mode `dry-run` ne spamme pas Telegram (testé explicitement)
- [ ] Fallback NAS testé : débrancher Telegram temporairement, vérifier dépôt NAS
- [ ] Latence mesurée sur la machine hermes-agent < 3 min (briefing moyen)

### Commits suggérés

1. `feat(contract): strict stdout JSON + exit codes`
2. `docs(deploy): hermes-agent runbook and env vars`
3. `feat(fallback): NAS deposit on Telegram failure`

---

## Phase 6 — Tests & validation ⏳ À faire (déjà 104 tests verts via Phases 1-3)

**Objectif** : suite de tests verte, couvrant les cas tordus + un test end-to-end avec fixtures.

> Mise à jour 2026-04-19 : la majorité des tests prévus pour cette phase ont déjà été écrits au fil des phases 1-3 (`test_dedup`, `test_select`, `test_window`, `test_render`, `test_e2e_offline`, `test_xai_client`, `test_sourcing`). Phase 6 se réduit à l'ajout des tests "pièges" résiduels (DST switch, schema invalid, fixtures additionnelles edge cases).

### Livrables

| Fichier | Rôle |
|---|---|
| `tests/test_config.py` | Validation schema `comptes.json` |
| `tests/test_window.py` | Fenêtres temporelles (matin, soir, DST edge) |
| `tests/test_dedup.py` | Dédup URL + titre, cross-section |
| `tests/test_select.py` | Quotas, ordre stable, empty section |
| `tests/test_render.py` | Golden tests + taille + pas de CDN |
| `tests/test_e2e_offline.py` | Pipeline complet avec fixtures → HTML → JSON stdout |
| `tests/conftest.py` | Fixtures pytest partagées |

### Couverture ciblée

- **Logique métier** (dedup, select, window) : **100 %** de lignes
- **I/O** (xai_client, render) : ≥ 80 % (le reste c'est happy path trivial)

### Tests "pièges" à ne pas oublier

- Briefing avec 0 item (journée calme) → message fallback, pas d'erreur
- 2 items strictement identiques dans la même fenêtre (repost exact) → 1 seul item
- `max_items=0` pour une section → section skippée du rendu, pas vide
- URL avec paramètres trackers (`?utm_source=...`) → canonicalisation les retire
- DST switch mars/novembre → fenêtre ne saute pas une heure
- Emoji dans un titre → pas d'échappement cassé

### Acceptance criteria

- [ ] `pytest` passe, 0 skip non justifié
- [ ] `ruff check` + `mypy` propres
- [ ] Test e2e offline tourne en < 5s

---

## Phase 7 — Mise en service V1 ⏳ À faire

**Objectif** : premier envoi réel à Chris, monitoring actif 2 semaines.

### Checklist de go-live

- [ ] Cron hermes-agent programmé : 6h44:45 et 17h29:45 America/Toronto
- [ ] `XAI_API_KEY` injectée par hermes-agent au runtime
- [ ] Chat Telegram de prod configuré dans hermes-agent
- [ ] Chat Telegram de test configuré (pour `BRIEFING_ENV=staging`)
- [ ] NAS monté et accessible en écriture par hermes-agent
- [ ] Premier `--dry-run` validé visuellement par Chris (HTML ouvert dans navigateur)
- [ ] Premier run `staging` validé sur chat de test
- [ ] Premier run `production` lancé manuellement hors cycle cron pour observer
- [ ] Cron activé

### Monitoring 2 premières semaines

- Vérif quotidienne des logs hermes-agent (erreurs, latence, coût)
- Chris tient un `FEEDBACK.md` dans le repo : ajustements proposés, items trouvés non pertinents, comptes/thèmes à changer
- Coût réel vs budget : si > 0.50 $/jour, investigate (trop de tool calls ? modèle trop gros ?)
- Self-report Chris : ≥ 1 item cliqué/jour en moyenne après semaine 2 (tracking direct skippé, voir issue #13)

### Décisions à prendre en fin de période 2 semaines

- Rester sur `grok-4-1-fast-non-reasoning` ou escalader (1) `-reasoning` variant, (2) `grok-4.20-0309-reasoning` ?
- Ajuster `max_items` par section selon feedback Chris ?
- Ajouter/retirer des comptes ou thèmes ?
- Passer à la V1.5 (features type breaking news, fallback b64) ?

---

## Dépendances inter-phases

```
Phase 0 → Phase 1 → Phase 2 → Phase 3 → Phase 5 → Phase 6 → Phase 7
```

- **Phase 4 skippée** via [issue #13](https://github.com/chrisboulet/briefing-matinal/issues/13) (N=1, pas de tracking en V1)
- **Phase 5 dépend de Phase 3** — il faut le mode live xAI pour câbler le contrat hermes-agent
- **Phase 6 peut commencer pendant Phase 2** pour les tests unitaires, mais le e2e attend Phase 3+

## Questions ouvertes à trancher en cours de route

1. **Prompt language** : prompts LLM en FR ou EN ? EN probablement plus efficace pour Grok, mais outputs en FR-QC requis. → Décision : **prompts en EN, instruction explicite "respond in Quebec French"** dans le system prompt. À valider au premier vrai run.
2. **Gestion des citations X anglophones** : garder tel quel (bilinguisme assumé) ou reformuler en FR ? → Décision PRD : **garder tel quel**. Jamais de traduction automatique.
3. **"À ne pas manquer" quand les sources sont pauvres** : que faire si aucun candidat clair ? → Proposition : omettre la section ce briefing-là, mettre une note discrète en footer. À trancher après premiers runs.
4. **Retry sur le cron** : si run de 6h45 échoue, hermes-agent réessaye à 6h50 ? 7h00 ? Jamais ? → Proposition : **1 retry à +5 min, sinon NAS fallback + alerte**. À confirmer avec Chris.

## Estimation globale (ordre de grandeur)

| Phase | Charge | Parallélisable |
|---|---|---|
| 0 — Scaffolding | 1-2 h | non |
| 1 — Pipeline offline | 4-6 h | non |
| 2 — Template HTML | 3-5 h | oui (avec 1) |
| 3 — Intégration xAI | 4-6 h | non |
| 4 — Redirector | ❌ Skipped (#13) | — |
| 5 — Intégration hermes | 2-3 h | non |
| 6 — Tests | 3-5 h | partiellement (au fil) |
| 7 — Mise en service | 1-2 h actif + 2 semaines de monitoring | — |

**Total dev actif** : ~18-26 h étalées sur 1-2 semaines réelles, monitoring 2 semaines post-lancement.

---

## Critères de "V1 terminée"

- [ ] Chris reçoit 2 briefings/jour à 6h45 et 17h30 pendant 7 jours consécutifs sans intervention manuelle
- [ ] Coût moyen < 0.50 $/jour
- [ ] Au moins 1 clic tracké par briefing en moyenne (semaine 2)
- [ ] Chris rapporte qu'il ouvre le briefing avant de scroller X le matin
- [ ] Aucun échec non récupéré (Telegram OU NAS livre toujours)
