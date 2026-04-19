# Intégration xAI Grok — Référence opérationnelle

> Document maintenu en synchronisation avec `scripts/xai_client.py` et `scripts/sourcing.py`. Toute déviation = bug à signaler.

## TL;DR

Le briefing utilise **xAI Grok via la Responses API** (`POST https://api.x.ai/v1/responses`) avec les outils server-side `x_search` et `web_search`. Le modèle Grok orchestre les appels aux outils, applique nos critères de qualité, et nous retourne une **liste JSON structurée** d'items prêts à dédupliquer/sélectionner localement.

| Quoi | Valeur |
|---|---|
| Endpoint | `POST https://api.x.ai/v1/responses` |
| Modèle V1 | `grok-4-1-fast-non-reasoning` (escalade possible vers `-reasoning` puis `grok-4.20-0309-reasoning` si qualité insuffisante) |
| Auth | `Authorization: Bearer ${XAI_API_KEY}` |
| Tools | `x_search`, `web_search` (server-side, intégrés xAI) |
| Output format | `response_format: json_schema` (forçage JSON valide) |
| Doc canonique | https://docs.x.ai/overview |

## Variables d'environnement

| Var | Requis | Exemple | Description |
|---|---|---|---|
| `XAI_API_KEY` | Oui en mode `live` | `xai-...` | Injectée par hermes-agent au runtime |
| `XAI_MODEL` | Non | `grok-4-1-fast-non-reasoning` | Override du modèle (voir liste complète ci-dessous) |
| `XAI_TIMEOUT_S` | Non | `30` | Timeout par appel HTTP (défaut 30s) |
| `XAI_MAX_RETRIES` | Non | `2` | Retries sur 5xx (défaut 2, total 3 essais) |

Mode `fixture` (Phase 1) : aucune variable xAI requise.

## Modèles disponibles

Aucun alias `-latest` exposé par xAI (vérifié `GET /v1/models` le 2026-04-19). Liste exhaustive :

| Modèle | Usage V1 | Coût indicatif |
|---|---|---|
| `grok-4-1-fast-non-reasoning` | **Défaut** — extraction + ranking structuré | 0.20 / 0.50 $ per M input/output |
| `grok-4-1-fast-reasoning` | Escalade 1 si qualité insuffisante (+ thinking tokens) | idem input, output gonflé |
| `grok-4-fast-non-reasoning` | Variante plus petite, pas notre cible | cheaper |
| `grok-4-fast-reasoning` | idem + thinking | cheaper input, output gonflé |
| `grok-4.20-0309-non-reasoning` | Flagship sans thinking | 3.00 / 15.00 $ per M |
| `grok-4.20-0309-reasoning` | Escalade 2 — flagship complet | idem + thinking tokens |

Toute modification du modèle doit :
1. Mettre à jour `DEFAULT_MODEL` dans `scripts/xai_client.py` OU exporter `XAI_MODEL`
2. Mettre à jour la section "Coût" ci-dessous si le pricing diffère
3. Bumper `PROMPTS_VERSION` si la bascule change le comportement attendu des prompts

## Forme de la requête

```json
{
  "model": "grok-4-1-fast-non-reasoning",
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
  ],
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "briefing_items",
      "strict": true,
      "schema": { /* voir scripts/xai_client.py:ITEMS_SCHEMA */ }
    }
  }
}
```

**Contraintes connues** (sources : `docs.x.ai`, blog xAI, GitHub openclaw/openclaw#26355) :

- `allowed_x_handles` : **max 10 handles par appel** → les 15 comptes du PRD sont splittés en 2 appels (gérés par `sourcing.py`)
- `allowed_x_handles` est mutuellement exclusif avec `excluded_x_handles`
- Live Search API (legacy `search_parameters` sur `/chat/completions`) est **dépréciée 2026-01-12** (410 Gone) — on n'utilise QUE la Responses API
- Le tool `web_search` accepte un `allowed_domains` (à confirmer en runtime)

## Forme de la réponse

✅ **Confirmée au premier appel live** (issue #15, 2026-04-19) :

```json
{
  "id": "resp_xxx",
  "model": "grok-4-1-fast-non-reasoning",
  "output": [
    {"type": "custom_tool_call", "name": "x_keyword_search", "call_id": "..."},
    {"type": "custom_tool_call", "name": "x_semantic_search", "call_id": "..."},
    {"type": "message", "role": "assistant",
     "content": [{"type": "output_text", "text": "[{...}, {...}]"}]}
  ],
  "usage": {"input_tokens": 16000, "output_tokens": 800}
}
```

**Points à connaître (vs hypothèses initiales)** :

1. **`output_text` est souvent un array JSON nu** `[{...}, {...}]` au lieu du `{"items": [...], "warnings": [...]}` demandé par le prompt — `response_format: json_schema strict` n'est **pas** honoré de façon garantie. Le client wrappe défensivement : si `parsed` est une list, transformée en `{"items": parsed, "warnings": []}`.
2. **Tool calls** : type réel = `"custom_tool_call"` (pas `"tool_call"` / `"tool_use"` / `"function_call"`). Le fallback de comptage accepte maintenant les 4 alias.
3. **`usage.tool_calls` n'existe pas** dans la réponse — les tool calls sont à compter dans `output[]` via le fallback `_count_tool_calls_in_output()`.
4. **Latence** observée : 6-10 s par appel (`grok-4-1-fast-non-reasoning`, ~16-20K tokens input par call).

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

Pricing avril 2026 (`grok-4-1-fast-non-reasoning`) :
- **0.20 $ / 1M tokens input**
- **0.50 $ / 1M tokens output**
- **5 $ / 1000 appels d'outil server-side** (`x_search`, `web_search`)

Estimation par briefing (alignée avec PRD §Contraintes techniques 0.35-0.40 $/jour) :
- 2 appels `x_search` (15 comptes splittés en 2 batches max 10) + 8 appels `x_search` thématiques + 1 appel `web_search` = **~11 appels utilisateur** par briefing
- Chaque appel utilisateur déclenche 1-3 invocations internes des tools (plus efficient avec Grok 4-1-fast vs gros modèles)
- **Tool fees ≈ 11 × 2 × 0.005 $ ≈ 0.11 $ par briefing**
- Tokens : ~8K input + 3K output par briefing ≈ 0.003 $
- **Total ≈ 0.11-0.15 $ par briefing × 2 briefings/jour ≈ 0.22-0.30 $/jour ≈ 7-9 $/mois**

> Marge sur PRD : si Grok déclenche plus d'invocations internes que prévu (3-5 au lieu de 1-3), on remonte à ~0.40 $/jour. Le garde-fou `XAI_MAX_TOKENS_PER_BRIEFING` (Phase 5) coupe à 100K tokens si dérive.

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

## Références

- PRD §S1 (Sourcing), §S1.bis (Prompts), §Modes d'erreur, §Contraintes techniques
- PLAN Phase 3
- Doc xAI : https://docs.x.ai/overview
- Issue OpenClaw confirmant dépréciation Live Search : https://github.com/openclaw/openclaw/issues/26355
