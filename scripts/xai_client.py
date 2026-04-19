"""
Client xAI Grok pour la Responses API.

Voir docs/xai-integration.md pour la référence opérationnelle complète :
- forme de la requête / réponse
- modes d'erreur (matrice)
- coût estimé
- points à valider sur premier appel réel (TODO(live))

Conception :
- Synchrone (httpx.Client) — V1 fait ~11 appels par briefing en série, latence
  totale < 3 min, async overkill à cette échelle.
- Défensif sur le parsing de la réponse (la doc complète n'est pas accessible
  depuis le sandbox de dev — voir TODO(live) marqueurs).
- Retry/backoff selon PRD §Modes d'erreur.
- Logs structurés JSON sur stderr pour observabilité.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

import httpx

XAI_BASE_URL = "https://api.x.ai/v1"
DEFAULT_MODEL = "grok-4-1-fast-latest"
DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MAX_RETRIES = 2  # = 3 essais total

# Pricing avril 2026 (per docs/xai-integration.md)
PRICE_INPUT_PER_M_TOKENS = 0.20
PRICE_OUTPUT_PER_M_TOKENS = 0.50
PRICE_TOOL_CALL = 0.005  # = 5 $ / 1000 appels


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class XAIError(Exception):
    """Base xAI error."""


class XAIAuthError(XAIError):
    """401 / 403 — clé invalide ou non autorisée. Aucun retry."""


class XAIRequestError(XAIError):
    """4xx hors auth — requête malformée. Aucun retry."""


class XAIRateLimited(XAIError):
    """429 persistant après 1 retry."""


class XAIUnavailable(XAIError):
    """5xx ou réseau persistant après retries — caller doit dégrader gracieusement."""


class XAIInvalidResponse(XAIError):
    """Réponse 200 mais corps non parseable / JSON malformé après retry."""


# ---------------------------------------------------------------------------
# Schéma de la réponse structurée demandée à Grok
# ---------------------------------------------------------------------------

# Schéma JSON utilisé en `response_format.json_schema.schema`. Aligné sur
# Item dans scripts/models.py + le contrat documenté dans docs/xai-integration.md.
# Note (review feedback): `format: uri` et `format: date-time` ne sont pas
# supportés en `strict: true` json_schema (cause 400 côté API). On valide ces
# formats côté Python (sourcing.py) après parse.
ITEMS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items", "warnings"],
    "properties": {
        "items": {
            "type": "array",
            "minItems": 0,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "title", "summary", "canonical_url", "source_type",
                    "source_handle", "published_at", "score", "section_id",
                    "likes", "reposts",
                ],
                "properties": {
                    "title": {"type": "string", "minLength": 1, "maxLength": 500},
                    "summary": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "canonical_url": {"type": "string", "minLength": 1},
                    "source_type": {"enum": ["x_account", "x_search", "web"]},
                    "source_handle": {"type": "string", "minLength": 1},
                    "published_at": {"type": "string", "minLength": 1},
                    "score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "section_id": {"type": "string"},
                    "likes": {"type": "integer", "minimum": 0},
                    "reposts": {"type": "integer", "minimum": 0},
                },
            },
        },
        "warnings": {
            "type": "array",
            "minItems": 0,
            "items": {"type": "string"},
        },
    },
}

# Clés autorisées dans tool_params, par tool. Évite que le caller écrase
# accidentellement `type` ou injecte un champ inconnu (review CRITICAL #3).
ALLOWED_TOOL_PARAMS = {
    "x_search": {
        "allowed_x_handles", "excluded_x_handles", "from_date", "to_date",
        "max_results",
    },
    "web_search": {
        "allowed_domains", "excluded_domains", "from_date", "to_date",
        "max_results",
    },
}


# ---------------------------------------------------------------------------
# Types de retour
# ---------------------------------------------------------------------------


@dataclass
class XAIUsage:
    """Consommation d'un appel, pour log et budget."""

    input_tokens: int
    output_tokens: int
    tool_calls: int

    @property
    def cost_usd(self) -> float:
        token_cost = (
            self.input_tokens / 1_000_000 * PRICE_INPUT_PER_M_TOKENS
            + self.output_tokens / 1_000_000 * PRICE_OUTPUT_PER_M_TOKENS
        )
        return token_cost + self.tool_calls * PRICE_TOOL_CALL


@dataclass
class XAIResponse:
    """Réponse parsée d'un appel /v1/responses."""

    parsed_output: dict[str, Any]  # JSON conforme à ITEMS_SCHEMA
    usage: XAIUsage
    duration_ms: int
    model: str


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class XAIClient:
    """
    Wrapper synchrone autour de POST /v1/responses.

    Une instance = un client httpx réutilisable. Thread-safe pour usage simple
    (un thread par instance recommandé).
    """

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_retries: int = DEFAULT_MAX_RETRIES,
        base_url: str = XAI_BASE_URL,
        client: httpx.Client | None = None,
    ):
        self.model = model
        self.max_retries = max_retries
        self._client = client or httpx.Client(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout_s,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> XAIClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def call(
        self,
        system_prompt: str,
        user_prompt: str,
        tool: Literal["x_search", "web_search"],
        tool_params: dict[str, Any] | None = None,
        prompt_label: str = "unspecified",
    ) -> XAIResponse:
        """
        Effectue UN appel à /v1/responses avec un seul tool, et retourne
        la réponse parsée + usage.

        Lève une XAIError concrète sur échec persistant (le caller dégrade).

        Politique de retry (review CRITICAL #1 + #2) :
        - 5xx / timeout / network : `max_retries` retries, backoff 2s, 4s, 8s, capé à 10s
        - 429 : compteur séparé, 1 retry après 60s (la rate limit ne consomme pas
          le budget de retry général)
        - JSON invalide : 1 retry max (compteur séparé)
        - Total max : ~30s + ~14s + 60s = ~104s, sous le budget 180s du PRD §S3
        """
        body = self._build_body(system_prompt, user_prompt, tool, tool_params)

        attempt = 0          # 5xx / timeout / network
        attempt_429 = 0      # rate limit (séparé)
        attempt_invalid = 0  # JSON invalide (séparé)

        while True:
            t0 = time.monotonic()
            try:
                resp = self._client.post("/responses", json=body)
            except httpx.TimeoutException as e:
                self._log_call(prompt_label, tool, status="timeout", attempt=attempt + 1)
                attempt += 1
                if attempt > self.max_retries:
                    raise XAIUnavailable(
                        f"xAI unavailable after {attempt} attempts (last: timeout)"
                    ) from e
                self._sleep_backoff(attempt)
                continue
            except httpx.RequestError as e:
                self._log_call(
                    prompt_label, tool,
                    status="network_error", attempt=attempt + 1, error=str(e),
                )
                attempt += 1
                if attempt > self.max_retries:
                    raise XAIUnavailable(
                        f"xAI unavailable after {attempt} attempts (last: {type(e).__name__})"
                    ) from e
                self._sleep_backoff(attempt)
                continue

            duration_ms = int((time.monotonic() - t0) * 1000)

            if resp.status_code in (401, 403):
                self._log_call(prompt_label, tool, status="auth_error", http=resp.status_code)
                raise XAIAuthError(f"xAI auth failed ({resp.status_code}): {resp.text[:200]}")

            if resp.status_code == 429:
                attempt_429 += 1
                self._log_call(
                    prompt_label, tool,
                    status="rate_limited", http=429, attempt_429=attempt_429,
                )
                if attempt_429 > 1:  # 1 retry max sur 429 (review CRITICAL #1)
                    raise XAIRateLimited("xAI rate limited after retry")
                time.sleep(60)
                continue

            if resp.status_code >= 500:
                self._log_call(
                    prompt_label, tool,
                    status="server_error", http=resp.status_code, attempt=attempt + 1,
                )
                attempt += 1
                if attempt > self.max_retries:
                    raise XAIUnavailable(
                        f"xAI unavailable after {attempt} attempts "
                        f"(last: HTTP {resp.status_code})"
                    )
                self._sleep_backoff(attempt)
                continue

            if resp.status_code >= 400:
                self._log_call(
                    prompt_label, tool,
                    status="request_error", http=resp.status_code, body=resp.text[:200],
                )
                raise XAIRequestError(f"xAI 4xx ({resp.status_code}): {resp.text[:200]}")

            # 2xx — parser la réponse
            try:
                parsed = self._parse_response(resp.json())
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                attempt_invalid += 1
                self._log_call(
                    prompt_label, tool,
                    status="invalid_response", attempt=attempt_invalid, error=str(e),
                )
                if attempt_invalid > 1:  # 1 retry max sur JSON invalide
                    raise XAIInvalidResponse(f"xAI returned malformed response: {e}") from e
                self._sleep_backoff(attempt_invalid)
                continue

            usage = parsed["usage"]
            self._log_call(
                prompt_label, tool,
                status="ok",
                tokens_in=usage.input_tokens,
                tokens_out=usage.output_tokens,
                tool_calls=usage.tool_calls,
                duration_ms=duration_ms,
                cost_usd=round(usage.cost_usd, 4),
            )
            return XAIResponse(
                parsed_output=parsed["output"],
                usage=usage,
                duration_ms=duration_ms,
                model=parsed["model"],
            )

    # ----- Internals --------------------------------------------------------

    def _build_body(
        self,
        system_prompt: str,
        user_prompt: str,
        tool: str,
        tool_params: dict[str, Any] | None,
    ) -> dict[str, Any]:
        # Validation tool_params (review CRITICAL #3) : refus de toute clé
        # inconnue ou conflictuelle avec `type`.
        params = tool_params or {}
        allowed = ALLOWED_TOOL_PARAMS.get(tool, set())
        unknown = set(params.keys()) - allowed
        if unknown:
            raise XAIRequestError(
                f"Unknown tool_params keys for tool '{tool}': {sorted(unknown)}. "
                f"Allowed: {sorted(allowed)}"
            )
        if "type" in params:
            raise XAIRequestError(
                "tool_params must not contain 'type' (set via positional arg)"
            )

        return {
            "model": self.model,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "tools": [{"type": tool, **params}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "briefing_items",
                    "strict": True,
                    "schema": ITEMS_SCHEMA,
                },
            },
        }

    def _parse_response(self, raw: dict[str, Any]) -> dict[str, Any]:
        """
        Extrait `model`, l'output texte (JSON conforme à ITEMS_SCHEMA), et l'usage.

        TODO(live): valider la forme exacte au premier appel réel.
        Forme actuelle assumée :
          {
            "id": ..., "model": "...",
            "output": [{type:message, content:[{type:output_text, text:"<json>"}]}],
            "usage": {input_tokens, output_tokens, tool_calls}
          }
        """
        model = raw.get("model", self.model)

        # Extraction défensive du output_text final
        output_text = self._extract_output_text(raw)
        parsed = json.loads(output_text)

        # Validation minimale de la structure (le strict json_schema côté API
        # devrait déjà garantir, mais on re-vérifie en cas de fallback)
        if "items" not in parsed or "warnings" not in parsed:
            raise ValueError(
                f"missing 'items' or 'warnings' in parsed output: {list(parsed.keys())}"
            )

        usage_raw = raw.get("usage", {})
        # TODO(live): valider la clé exacte du compteur tool_calls.
        # Hypothèses tolérées : `tool_calls`, `num_tool_calls`, ou compter les
        # entrées output[type=tool_call] (dernier recours pour ne pas sous-estimer
        # le coût). Review MAJOR #4.
        tool_calls = int(
            usage_raw.get("tool_calls")
            or usage_raw.get("num_tool_calls")
            or self._count_tool_calls_in_output(raw)
            or 0
        )
        usage = XAIUsage(
            input_tokens=int(usage_raw.get("input_tokens", 0)),
            output_tokens=int(usage_raw.get("output_tokens", 0)),
            tool_calls=tool_calls,
        )

        return {"output": parsed, "usage": usage, "model": model}

    @staticmethod
    def _count_tool_calls_in_output(raw: dict[str, Any]) -> int:
        """Fallback: compte les entrées de type tool_call dans output[]."""
        output = raw.get("output")
        if not isinstance(output, list):
            return 0
        tool_call_types = ("tool_call", "tool_use", "function_call")
        return sum(
            1 for entry in output
            if isinstance(entry, dict) and entry.get("type") in tool_call_types
        )

    @staticmethod
    def _extract_output_text(raw: dict[str, Any]) -> str:
        """
        Récupère le texte synthétisé final.

        Tolère plusieurs chemins probables (la forme exacte n'est pas figée
        par la doc accessible) :
        1. raw["output"][-1]["content"][-1]["text"]   (forme principale assumée)
        2. raw["output_text"]                          (raccourci hypothétique)
        3. raw["choices"][0]["message"]["content"]     (fallback chat-completions style)
        """
        if "output_text" in raw and isinstance(raw["output_text"], str):
            return raw["output_text"]

        output = raw.get("output")
        if isinstance(output, list) and output:
            last = output[-1]
            content = last.get("content") if isinstance(last, dict) else None
            if isinstance(content, list) and content:
                for chunk in reversed(content):
                    if isinstance(chunk, dict) and chunk.get("type") in ("output_text", "text"):
                        text = chunk.get("text")
                        if isinstance(text, str):
                            return text

        choices = raw.get("choices")
        if isinstance(choices, list) and choices:
            msg = choices[0].get("message", {})
            text = msg.get("content")
            if isinstance(text, str):
                return text

        raise ValueError(f"could not extract output_text from response keys={list(raw.keys())}")

    def _sleep_backoff(self, attempt: int) -> None:
        """
        Backoff exponentiel borné : 2s, 4s, 8s, 10s, 10s...

        Capé à 10s pour rester dans le budget 180s du PRD §S3 même si
        cumul timeout (30s x 3) + 429 (60s) + plusieurs retries 5xx.
        Review CRITICAL #2.
        """
        if attempt <= 0:
            return
        delay = min(2 ** attempt, 10)
        time.sleep(delay)

    @staticmethod
    def _log_call(prompt_label: str, tool: str, **fields: Any) -> None:
        """Log structuré JSON sur stderr (parseable via jq)."""
        record = {"event": "xai_call", "prompt": prompt_label, "tool": tool, **fields}
        print(json.dumps(record, ensure_ascii=False), file=sys.stderr)


def iso_date(d: date) -> str:
    """Formate une date en 'YYYY-MM-DD' pour les params x_search/web_search."""
    return d.isoformat()
