# Intégration xAI Grok — Référence opérationnelle

> Document maintenu en synchronisation avec `scripts/xai_client.py` et `scripts/sourcing.py`. Toute déviation = bug à signaler.

## TL;DR

Le briefing utilise **xAI Grok via la Responses API** (`POST https://api.x.ai/v1/responses`) avec les outils server-side `x_search` et `web_search`. Le modèle Grok orchestre les appels aux outils, applique nos critères de qualité, et nous retourne une **liste JSON structurée** d'items prêts à dédupliquer/sélectionner localement.

| Quoi | Valeur |
|---|---|
| Endpoint | `POST https://api.x.ai/v1/responses` |
| Modèle défaut | `grok-4.5` (pin Hermes + repo, vérifié `GET /v1/models` 2026-07-17) |
| Auth | `Authorization: Bearer $XAI_API_KEY` |
| Tools | `x_search`, `web_search` (server-side, intégrés xAI) |
| Output format | JSON forcé par prompts + parsing défensif (pas de `response_format` sur Responses API) |
| Doc canonique | https://docs.x.ai/overview |

## Variables d'environnement

| Var | Requis | Exemple | Description |
|---|---|---|---|
| `XAI_API_KEY` | Oui en mode `live` | `xai-...` | Injectée par hermes-agent au runtime |
| `XAI_MODEL` | Non | `grok-4.5` | Override du modèle (défaut repo + pin Hermes) |
| `XAI_TIMEOUT_S` | Non | `30` | Timeout par appel HTTP (défaut 30s) |
| `XAI_MAX_RETRIES` | Non | `2` | Retries sur 5xx (défaut 2, total 3 essais) |

Mode `fixture` (Phase 1) : aucune variable xAI requise.

## Modèles disponibles

Aucun alias `-latest` exposé par xAI. Vérifié `GET /v1/models` le **2026-07-17** :

| Modèle | Usage V1 | Notes |
|---|---|---|
| `grok-4.5` | **Défaut** — sourcing + tools | ID live confirmé |
| `grok-4.3` | Fallback possible | ID live confirmé |
| `grok-4.20-0309-reasoning` | Escalade flagship | Coût plus élevé |
| `grok-4.20-0309-non-reasoning` | Flagship sans thinking | Coût plus élevé |

**Retirés / absents** (ne plus référencer comme défaut) : `grok-4-1-fast-non-reasoning`, `grok-4-1-fast-reasoning`, et autres `grok-4-*-fast-*` absents de l'API en 2026-07.

Toute modification du modèle doit :
1. Mettre à jour `DEFAULT_MODEL` dans `scripts/xai_client.py` OU exporter `XAI_MODEL`
2. Mettre à jour le pin `BRIEFING_XAI_MODEL` dans `~/.hermes/scripts/briefing_build.py` (prod cron)
3. Mettre à jour la section "Coût" si le pricing diffère

## Forme de la requête

```json
{
  "model": "grok-4.5",
  "input": [
    {"role": "system", "content": "<contenu de prompts/system.txt>"},
    {"role": "user",   "content": "<prompt rendu Jinja>"}
  ],
  "tools": [
    {
      "type": "x_search",
      "allowed_x_handles": ["karpathy", "AnthropicAI", "..."],
      "from_date": "2026-04-18",
      "to_date":   "2026-04-19"
    }
  ]
}
```

> **Important (2026-07)** : ne **pas** envoyer `response_format` / `json_schema` sur `/v1/responses` — l'API répond 400 (réservé chat completions / `text.format`). Le JSON est obtenu via les prompts + `_parse_response` défensif.

**Contraintes connues** (sources : `docs.x.ai`, blog xAI, GitHub openclaw/openclaw#26355) :

- `allowed_x_handles` : **max 10 handles par appel** → les comptes X sont splittés en batches (géré par `sourcing.py` via `_chunk` + `MAX_HANDLES_PER_CALL`)
- `allowed_x_handles` est mutuellement exclusif avec `excluded_x_handles`
- `web_search.allowed_domains` : **max 5 domaines par appel** (issue #31, confirmé live avec 400 "A maximum of 5 domains can be allowed") → splitté en batches via `MAX_DOMAINS_PER_CALL`
- Live Search API (legacy `search_parameters` sur `/chat/completions`) est **dépréciée 2026-01-12** (410 Gone) — on n'utilise QUE la Responses API

## Forme de la réponse

✅ Confirmée live (issue #15 + dogfood 2026-07) :

```json
{
  "id": "resp_xxx",
  "model": "grok-4.5",
  "output": [
    {"type": "web_search_call", "status": "completed", "action": {"type": "search", "query": "..."}},
    {"type": "message", "role": "assistant",
     "content": [{"type": "output_text", "text": "[{...}, {...}]"}]}
  ],
  "usage": {"input_tokens": 16000, "output_tokens": 800}
}
```

**Points à connaître (vs hypothèses initiales)** :

1. **`output_text` est souvent un array JSON nu** `[{...}, {...}]` au lieu du `{"items": [...], "warnings": [...]}` demandé par le prompt. Le client wrappe défensivement : si `parsed` est une list, transformée en `{"items": parsed, "warnings": []}`.
2. **Tool calls** : types observés `web_search_call` / `x_search_call` (et parfois alias historiques). Le fallback de comptage accepte plusieurs alias.
3. **`usage.tool_calls` n'existe pas** toujours dans la réponse — les tool calls sont à compter dans `output[]` via le fallback `_count_tool_calls_in_output()`.
4. **Latence** : variable selon modèle et volume de tools (souvent 10–40 s par appel en live 2026-07).

Le client extrait le `output_text` final (trois chemins tolérés, cf. `_extract_output_text`), parse le JSON, et normalise en `{items, warnings}`.

## Schéma des items retournés

```json
{
  "items": [
    {
      "title":          "string (langue d'origine préservée)",
      "summary":        "string (1-2 lignes français québécois)",
      "canonical_url":  "string (URL complète)",
      "source_type":    "x_account | x_search | web",
      "source_handle":  "string (@karpathy ou journaldequebec.com)",
      "published_at":   "string (ISO 8601 UTC)",
      "score":          "number (0.0-1.0)",
      "section_id":     "string (FK vers sections[].id)",
      "likes":          "integer (0 si web/inconnu)",
      "reposts":        "integer (0 si web/inconnu)"
    }
  ],
  "warnings": ["string (ex: 'aucun post dans la fenêtre pour @X')"]
}
```

## Modes d'erreur (matrice)

Implémentée dans `scripts/xai_client.py` et alignée sur PRD §Modes d'erreur.

| HTTP / Cas | Action | Exception levée |
|---|---|---|
| 200 + JSON valide | OK | — |
| 200 + JSON invalide | 1 retry (l'API peut hallucinatoire) | `XAIInvalidResponse` si persiste |
| 429 (rate limit) | Backoff 60s, 1 retry | `XAIRateLimited` si persiste |
| 5xx | Backoff 2s puis 8s, 2 retries | `XAIUnavailable` si persiste |
| Timeout > 30s | Compté comme 5xx | idem |
| 401 / 403 (auth) | Aucun retry | `XAIAuthError` immédiat |
| 4xx autre | Aucun retry | `XAIRequestError` immédiat |

Le caller (`sourcing.py`) catch les exceptions et **dégrade gracieusement** : un appel raté = section vide + warning dans le briefing, jamais d'erreur fatale.

## Coût et budget

Pricing (indicatif — vérifier le dashboard xAI pour `grok-4.5`) :
- Les tarifs tokens/tool fees varient par modèle ; le client log `cost_usd` par appel (stderr JSON).
- Dogfood live 2026-07 (~sources étendues + enrich) : de l’ordre de **~$0.4–1.0 / run** selon volume.
- Ancien ordre de grandeur V1 (moins de sources) : ~0.22–0.40 $/jour.

Le client log la consommation à chaque appel (stderr JSON). `build_briefing.py` agrège et abort avec exit code 4 si le budget tokens du briefing dépasse `XAI_MAX_TOKENS_PER_BRIEFING` (défaut 100 000).

## Versionnage des prompts

Les prompts vivent dans `prompts/*.txt`. La version est trackée via la constante `PROMPTS_VERSION` dans `scripts/build_briefing.py` (format `prompts-vX.Y`).

**Workflow lors d'un changement de prompt** :
1. Éditer le fichier `prompts/<nom>.txt`
2. Bumper `PROMPTS_VERSION` (mineur si tweak, majeur si refonte sémantique)
3. Le hash de la version est injecté en footer du HTML rendu (traçabilité)
4. Logger un commit message qui mentionne `prompts-vX.Y` pour faciliter le `git log --grep`

## Logs structurés

Chaque appel xAI émet une ligne JSON sur stderr :

```json
{"event":"xai_call","tool":"x_search","prompt":"search_accounts","tokens_in":850,"tokens_out":1240,"tool_calls":3,"duration_ms":4521,"cost_usd":0.0152,"status":"ok"}
```

Sur erreur :
```json
{"event":"xai_call","tool":"x_search","prompt":"search_accounts","status":"error","error_type":"XAIUnavailable","attempts":3}
```

Captable via `python ... 2>&1 | jq 'select(.event=="xai_call")'`.

## Comment tester en local sans clé

Tous les tests `pytest` utilisent `pytest-httpx` pour mocker l'API — **aucun appel réel**. Pour valider le mode live :

```bash
export XAI_API_KEY="xai-..."
export BRIEFING_ENV="dry-run"   # n'écrit pas le HTML, mais fait les vrais appels
python -m scripts.build_briefing --moment matin --dry-run
```

Le coût d'un test live ≈ 0.22 $.

## Points à valider sur premier appel réel

Marqués `# TODO(live):` dans le code. Liste à jour :

1. ✅ **Résolu (#15)** — `output_text` trouvé via `output[-1].content[-1].text`, et peut être une list ou un dict (parsing défensif).
2. ✅ **Résolu (#15)** — `usage.tool_calls` n'existe pas ; fallback `_count_tool_calls_in_output()` compte `output[type="custom_tool_call"]`.
3. Format des `tool_params` pour `web_search` (nom exact de `allowed_domains`) — à valider au premier call web réel.
4. Headers de rate limit (`X-RateLimit-*` ou autre) — pas encore observés.
5. Shape des items retournés par le LLM : est-ce que les champs `title/summary/canonical_url/section_id/...` sont respectés, ou est-ce que le modèle renvoie sa propre shape (`post_id/author/content/...`) malgré le schema strict ? — à surveiller sur le prochain test live, peut nécessiter un adaptateur dans `sourcing._to_item()`.

## Enrichissement 2e passe (issue #25)

Depuis `prompts-v1.1`, le pipeline exécute un **2e appel xAI par item sélectionné** pour produire un résumé substantiel (700-900 chars FR-QC) à partir du contenu web complet. Implémenté dans `scripts/enrichment.py` et hooké dans `scripts/build_briefing.build()` **après** `select_by_section` + `select_dont_miss`, **avant** la construction du `Briefing`.

### Pourquoi

La 1re passe (`source_briefing`) produit des `summary` courts à partir du `content` du post X (~150-300 chars, issue #23 les porte à 700-900 mais ce budget n'est atteignable que sur les sources web). Pour les items web, un 2e appel `web_search` restreint au domaine de l'URL permet d'extraire le body complet et de synthétiser un résumé journalistique complet au même budget.

### Hook point

```
build()
  ├── source_briefing()             ← 1re passe (multi-items via x_search/web_search)
  ├── dedupe + select_by_section    ← ~15-20 items survivants
  ├── select_dont_miss
  ├── enrich_selected()             ← 2e passe (web_search par item)  ← ICI
  └── Briefing(sections, dont_miss, …)
```

Les deux passes partagent le MÊME `XAIClient` (1 `httpx.Client` réutilisé) pour économiser les handshakes TLS.

### Kill switch

Exporter **`BRIEFING_ENRICH=0`** pour désactiver l'enrichissement sans toucher au code. Utile pour :
- Dégrader d'urgence si le budget xAI explose ;
- Régénérer un briefing rapidement en mode "à l'os" (1re passe seule) ;
- Tester les deux modes en parallèle.

Défaut : `BRIEFING_ENRICH=1` (actif). Ignoré en mode `--fixture` (pas de vrai client xAI disponible).

### Budget

| Métrique | Valeur |
|---|---|
| Appels additionnels | 1 par item enrichi (items X/Twitter skippés — redondants avec 1re passe) |
| Items typiques par briefing | ~10-15 (après dedup + quotas) dont ~5-8 web à enrichir |
| Tool fees | ~5-8 × 0.005 $ ≈ **+0.03-0.04 $/briefing** |
| Tokens (estim.) | ~2K input + ~1K output par item × 7 ≈ 14K in / 7K out ≈ **+0.007 $/briefing** |
| Latence | Parallélisé via `ThreadPoolExecutor(max_workers=4)`, deadline globale **30s** (`GLOBAL_DEADLINE_S`), per-item 20s (`DEFAULT_PER_ITEM_TIMEOUT_S`) |
| **Total delta** | **~+0.05-0.10 $/briefing**, **+30s** latence max |

Budget global révisé avec enrichissement actif : ~**0.15-0.25 $ par briefing × 2/jour ≈ 0.30-0.50 $/jour ≈ 9-15 $/mois**.

### Schema override pattern

L'enrichissement utilise une shape de réponse **single-item** (`{summary, warnings}`), différente du multi-items `{items: [...], warnings: [...]}` du sourcing. Pour permettre ça, `XAIClient.call()` accepte depuis issue #25 deux paramètres optionnels :

```python
client.call(
    system_prompt="",              # system inline dans enrich.txt
    user_prompt=user_prompt,
    tool="web_search",
    tool_params={"allowed_domains": [hostname]},
    prompt_label=f"enrich_{item.id[:8]}",
    response_schema=ENRICH_SCHEMA, # override json_schema.schema
    schema_name="enrich_item",     # override json_schema.name
)
```

Comportement :
- `response_schema=None` (défaut) → utilise `ITEMS_SCHEMA`, `_parse_response` exige la clé `items` (compat stricte Phase 3).
- `response_schema` fourni → utilise le schema custom, `_parse_response` **n'exige PAS** la clé `items` (le caller inspecte `parsed_output` selon sa propre shape). La normalisation `warnings → list[str]` reste appliquée à toutes les shapes.

Voir `scripts/enrichment.py:_ENRICH_RESPONSE_SCHEMA` pour le détail.

### Skip rules

- **Silencieux (pas de warning)** : items hébergés sur `x.com` ou `twitter.com` (tuple `ENRICH_X_HOSTS`) — la 1re passe a déjà produit un résumé à partir du `content` natif du post, un 2e appel serait redondant et la plupart du temps bloqué par x.com.
- **Avec warning** : items avec `canonical_url` vide ou sans hostname parseable.

### Dégradation gracieuse

Un échec per-item n'interrompt JAMAIS l'enrichissement global :
- `XAIError` (auth, rate limit, 5xx persistant, JSON invalide) → warning + item d'origine conservé ;
- Timeout per-item (`future.result(timeout=20s)`) → warning + original ;
- Deadline globale wall-clock dépassée (30s) → futures restants cancellés, originaux conservés avec warning ;
- Summary vide / whitespace → warning + original.

Les warnings sont agrégés dans `Briefing.warnings` (mais pas rendus dans le HTML — voir issue #20).

### Logs structurés

Chaque item enrichi émet une ligne JSON sur stderr, et un `enrichment_total` récapitulatif :

```json
{"event":"enrichment_call","prompt":"enrich_ab12cd34","status":"ok","item_id":"ab12cd34ef56","host":"lapresse.ca","summary_len":847,"tokens_in":1923,"tokens_out":412,"tool_calls":1,"cost_usd":0.0058,"duration_ms":4213}
{"event":"enrichment_total","enriched_count":6,"skipped_count":4,"failed_count":1,"warnings_count":1,"tokens_in":11532,"tokens_out":2418,"tool_calls":6,"cost_usd":0.0335}
```

Captable via `python ... 2>&1 | jq 'select(.event | startswith("enrichment"))'`.

## Références

- PRD §S1 (Sourcing), §S1.bis (Prompts), §Modes d'erreur, §Contraintes techniques
- PLAN Phase 3
- Issue #25 — Enrichissement 2e passe
- Doc xAI : https://docs.x.ai/overview
- Issue OpenClaw confirmant dépréciation Live Search : https://github.com/openclaw/openclaw/issues/26355
