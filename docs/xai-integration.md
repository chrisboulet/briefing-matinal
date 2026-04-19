# Intégration xAI Grok — Référence opérationnelle

> Document maintenu en synchronisation avec `scripts/xai_client.py` et `scripts/sourcing.py`. Toute déviation = bug à signaler.

## TL;DR

Le briefing utilise **xAI Grok via la Responses API** (`POST https://api.x.ai/v1/responses`) avec les outils server-side `x_search` et `web_search`. Le modèle Grok orchestre les appels aux outils, applique nos critères de qualité, et nous retourne une **liste JSON structurée** d'items prêts à dédupliquer/sélectionner localement.

| Quoi | Valeur |
|---|---|
| Endpoint | `POST https://api.x.ai/v1/responses` |
| Modèle V1 | `grok-4-1-fast-latest` (escalade `grok-4.20-latest` si qualité insuffisante) |
| Auth | `Authorization: Bearer ${XAI_API_KEY}` |
| Tools | `x_search`, `web_search` (server-side, intégrés xAI) |
| Output format | `response_format: json_schema` (forçage JSON valide) |
| Doc canonique | https://docs.x.ai/overview |

## Variables d'environnement

| Var | Requis | Exemple | Description |
|---|---|---|---|
| `XAI_API_KEY` | Oui en mode `live` | `xai-...` | Injectée par hermes-agent au runtime |
| `XAI_MODEL` | Non | `grok-4-1-fast-latest` | Override du modèle (défaut alias `-latest`) |
| `XAI_TIMEOUT_S` | Non | `30` | Timeout par appel HTTP (défaut 30s) |
| `XAI_MAX_RETRIES` | Non | `2` | Retries sur 5xx (défaut 2, total 3 essais) |

Mode `fixture` (Phase 1) : aucune variable xAI requise.

## Forme de la requête

```json
{
  "model": "grok-4-1-fast-latest",
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

> ⚠️ **Hypothèse documentée** : la doc complète de `docs.x.ai` n'a pas pu être fetchée depuis le sandbox de dev (403). Le parsing dans `xai_client.py` est défensif et tolère plusieurs formes ; les TODO dans le code marquent les points à valider sur premier appel réel.

Forme **attendue** (alignée Responses API style OpenAI) :

```json
{
  "id": "resp_xxx",
  "model": "grok-4-1-fast-2026-04-15",
  "output": [
    {
      "type": "message",
      "role": "assistant",
      "content": [
        {"type": "output_text", "text": "{\"items\": [...], \"warnings\": [...]}"}
      ]
    }
  ],
  "usage": {
    "input_tokens": 1234,
    "output_tokens": 567,
    "tool_calls": 3
  }
}
```

Le client extrait le `output_text` final et le parse comme JSON conforme à `ITEMS_SCHEMA`.

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

Pricing avril 2026 (`grok-4-1-fast-latest`) :
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

1. Forme exacte de la réponse Responses API (chemin du `output_text`)
2. Format des `tool_params` pour `web_search` (nom exact de `allowed_domains`)
3. Headers de rate limit (`X-RateLimit-*` ou autre)
4. Comportement quand `response_format: json_schema` + tool fails — retourne-t-il quand même un JSON valide ?

## Références

- PRD §S1 (Sourcing), §S1.bis (Prompts), §Modes d'erreur, §Contraintes techniques
- PLAN Phase 3
- Doc xAI : https://docs.x.ai/overview
- Issue OpenClaw confirmant dépréciation Live Search : https://github.com/openclaw/openclaw/issues/26355
