"""
Enrichissement 2e passe des items sélectionnés (issue #25).

Hook point dans `scripts.build_briefing.build()` : APRES `select_by_section`
+ `select_dont_miss`, AVANT la construction du `Briefing`. Pour chaque item
sélectionné (hors X/Twitter — redondant avec la 1re passe `x_search`) on
appelle `web_search` restreint au domaine de l'URL pour obtenir un résumé
substantiel (700-900 chars FR-QC).

Conception :
- Synchrone côté appel client, parallélisé via `ThreadPoolExecutor` (4 workers)
  pour tenir le budget latence < 30s même sur 10-15 items enrichis.
- Dégradation gracieuse : un échec par-item = warning + item d'origine conservé.
  Jamais d'exception propagée au caller.
- Deadline globale wall-clock (30s) : les futures en retard sont cancellées
  et leurs items d'origine conservés avec un warning.
- Schema JSON override : on passe `response_schema={summary, warnings}` à
  `XAIClient.call()` qui accepte depuis issue #25 cette clé optionnelle.

Voir `prompts/enrich.txt` pour le template, `docs/xai-integration.md#Enrichissement`
pour la référence opérationnelle (coût, kill switch, etc).
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from scripts.models import Item
from scripts.scrapling_fetcher import fetch_article_text
from scripts.xai_client import XAIClient, XAIError, XAIUsage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

DEFAULT_MAX_WORKERS = 4
DEFAULT_PER_ITEM_TIMEOUT_S = 20.0
GLOBAL_DEADLINE_S = 30.0

# Hosts à skipper (redondant avec la 1re passe x_search).
# On matche suffix (ex: "mobile.x.com" → skip, "foox.com" → NE PAS skip).
ENRICH_X_HOSTS = ("x.com", "twitter.com")

# Cap sur `raw_excerpt` — on stocke une copie brute du nouveau summary à des
# fins d'audit/debug sans faire exploser la taille de l'Item.
_RAW_EXCERPT_CAP = 2000

# Cap minimal/maximal attendu sur le summary enrichi (informatif — le prompt
# cible 700-900, on accepte 100-1500 pour tolérer les variations LLM).
_MIN_SUMMARY_CHARS = 100
_MAX_SUMMARY_CHARS = 1500

# Nom du schema JSON pour le response_format override.
_ENRICH_SCHEMA_NAME = "enrich_item"

# Schema JSON single-item pour l'appel d'enrichissement.
# Note : comme ITEMS_SCHEMA, on évite `format: uri` en strict (400 API).
_ENRICH_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "warnings"],
    "properties": {
        "summary": {"type": "string", "minLength": 0, "maxLength": 2000},
        "warnings": {
            "type": "array",
            "minItems": 0,
            "items": {"type": "string"},
        },
    },
}


# ---------------------------------------------------------------------------
# Types de retour
# ---------------------------------------------------------------------------


@dataclass
class EnrichmentResult:
    """Résultat agrégé de l'enrichissement 2e passe sur une sélection."""

    sections: dict[str, list[Item]]
    dont_miss: Item | None
    warnings: list[str] = field(default_factory=list)
    usage: XAIUsage = field(
        default_factory=lambda: XAIUsage(input_tokens=0, output_tokens=0, tool_calls=0)
    )
    enriched_count: int = 0
    skipped_count: int = 0


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------


def enrich_selected(
    client: XAIClient,
    sections: dict[str, list[Item]],
    dont_miss: Item | None,
    *,
    prompts_dir: Path = Path("prompts"),
    max_workers: int = DEFAULT_MAX_WORKERS,
    timeout_s: float = DEFAULT_PER_ITEM_TIMEOUT_S,
) -> EnrichmentResult:
    """
    Enrichit les items sélectionnés via un appel `web_search` par item.

    Pour chaque item non skippé :
      1. Restreint `allowed_domains` au hostname de `canonical_url` ;
      2. Rend `prompts/enrich.txt` avec url/title/section_id ;
      3. Appelle `client.call(..., response_schema=<single-item>)` ;
      4. Remplace `summary` + `raw_excerpt` via `dataclasses.replace`.

    Skip :
      - hôtes `x.com` / `twitter.com` (silencieux, redondant avec 1re passe) ;
      - URL vide ou sans hostname valide (avec warning).

    Dégradation :
      - XAIError per-item → warning, item d'origine conservé ;
      - timeout per-item (future.result timeout) → warning + original ;
      - deadline globale 30s dépassée → cancel, warning + original pour les
        restants ;
      - summary vide/whitespace → warning + original.

    Args:
        client: instance `XAIClient` partagée (1 httpx.Client pour toutes
            les requêtes, thread-safe en lecture pour `post`).
        sections: dict section_id → list[Item] issu de `select_by_section`.
        dont_miss: Item (ou None) issu de `select_dont_miss`.
        prompts_dir: dossier contenant `enrich.txt`.
        max_workers: taille du pool (défaut 4).
        timeout_s: timeout par futur (défaut 20s).

    Returns:
        `EnrichmentResult` avec `sections` et `dont_miss` enrichis (ou
        d'origine sur skip/échec), `warnings`, `usage` agrégée, et
        compteurs `enriched_count` / `skipped_count`.
    """
    deadline = time.monotonic() + GLOBAL_DEADLINE_S

    env = _make_jinja_env(prompts_dir)
    user_prompt_tmpl = env.get_template("enrich.txt")

    # Collecte la liste plate d'items à traiter, en gardant un mapping
    # vers leur emplacement (section_id, idx) ou ("__dont_miss__", 0).
    Job = tuple[Item, str, int]  # (item, location_key, idx)
    jobs: list[Job] = []
    for section_id, items in sections.items():
        for idx, item in enumerate(items):
            jobs.append((item, section_id, idx))
    if dont_miss is not None:
        jobs.append((dont_miss, "__dont_miss__", 0))

    # Partitionne en "à enrichir" vs "skip silencieux" (X/Twitter).
    warnings: list[str] = []
    to_enrich: list[Job] = []
    skipped_silent = 0
    for job in jobs:
        item, _, _ = job
        if _should_skip(item):
            skipped_silent += 1
            continue
        host = _extract_host(item.canonical_url)
        if not host:
            warnings.append(
                f"enrich[{item.id}]: invalid or empty canonical_url — skipped"
            )
            skipped_silent += 1
            continue
        to_enrich.append(job)

    # Résultats : on part des originaux, on remplace au fur et à mesure.
    enriched_by_location: dict[tuple[str, int], Item] = {}
    total_usage = XAIUsage(input_tokens=0, output_tokens=0, tool_calls=0)
    enriched_count = 0
    failed_count = 0

    if not to_enrich:
        _log_total(
            enriched_count=0,
            skipped_count=skipped_silent,
            failed_count=0,
            usage=total_usage,
            warnings_count=len(warnings),
        )
        return EnrichmentResult(
            sections=sections,
            dont_miss=dont_miss,
            warnings=warnings,
            usage=total_usage,
            enriched_count=0,
            skipped_count=skipped_silent,
        )

    # Dispatch parallèle. On utilise submit() + future → job pour relier
    # chaque résultat à son emplacement et à son item d'origine.
    workers = max(1, min(max_workers, len(to_enrich)))
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    future_to_job: dict[concurrent.futures.Future[tuple[Item | None, XAIUsage]], Job] = {}
    try:
        for job in to_enrich:
            item, _, _ = job
            future = executor.submit(
                _enrich_one, client, item, user_prompt_tmpl,
            )
            future_to_job[future] = job

        for future, job in future_to_job.items():
            item, loc_key, idx = job
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # Deadline globale atteinte avant même d'attendre ce futur.
                future.cancel()
                warnings.append(
                    f"enrich[{item.id}]: global deadline ({GLOBAL_DEADLINE_S}s) "
                    f"exceeded — keeping original"
                )
                failed_count += 1
                continue

            per_item_timeout = min(timeout_s, remaining)
            try:
                enriched_item, usage = future.result(timeout=per_item_timeout)
            except concurrent.futures.TimeoutError:
                future.cancel()
                warnings.append(
                    f"enrich[{item.id}]: timeout after {timeout_s}s — keeping original"
                )
                failed_count += 1
                continue
            except XAIError as exc:
                # Normalement _enrich_one catche déjà les XAIError et retourne
                # None — mais on se protège d'une fuite éventuelle.
                warnings.append(
                    f"enrich[{item.id}]: {type(exc).__name__}: {exc} — keeping original"
                )
                failed_count += 1
                continue
            except Exception as exc:
                # Garde-fou absolu : un worker ne doit jamais tuer l'enrichissement
                # global. On capte tout (ValueError, RuntimeError, etc.) et on
                # conserve l'item d'origine avec un warning explicite.
                warnings.append(
                    f"enrich[{item.id}]: unexpected {type(exc).__name__}: {exc} "
                    f"— keeping original"
                )
                failed_count += 1
                continue

            total_usage = _add_usage(total_usage, usage)

            if enriched_item is None:
                # _enrich_one a logué un warning structuré et renvoie None
                # pour signaler un échec "soft" (XAIError, summary vide, etc).
                warnings.append(
                    f"enrich[{item.id}]: enrichment returned no summary "
                    f"— keeping original"
                )
                failed_count += 1
                continue

            enriched_by_location[(loc_key, idx)] = enriched_item
            enriched_count += 1
    finally:
        # Ne pas bloquer sur les futures restantes — on a déjà cancellé
        # celles qui dépassent la deadline. `wait=False` évite de
        # sérialiser les appels réseau en vol encore.
        executor.shutdown(wait=False, cancel_futures=True)

    # Reconstruction des sections avec les items enrichis substitués.
    new_sections: dict[str, list[Item]] = {
        sid: list(items) for sid, items in sections.items()
    }
    new_dont_miss = dont_miss
    for (loc_key, idx), enriched_item in enriched_by_location.items():
        if loc_key == "__dont_miss__":
            new_dont_miss = enriched_item
        else:
            new_sections[loc_key][idx] = enriched_item

    skipped_count = skipped_silent + failed_count
    _log_total(
        enriched_count=enriched_count,
        skipped_count=skipped_count,
        failed_count=failed_count,
        usage=total_usage,
        warnings_count=len(warnings),
    )

    return EnrichmentResult(
        sections=new_sections,
        dont_miss=new_dont_miss,
        warnings=warnings,
        usage=total_usage,
        enriched_count=enriched_count,
        skipped_count=skipped_count,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _should_skip(item: Item) -> bool:
    """
    Retourne True si l'item doit être skippé silencieusement (pas de warning).

    On skippe tout ce qui est hébergé sur `x.com` ou `twitter.com` : la 1re
    passe `x_search` a déjà produit un résumé à partir du `content` du post
    — un 2e appel `web_search` sur la même URL serait redondant et coûteux
    (et x.com bloque le plus souvent le scraping).
    """
    host = _extract_host(item.canonical_url)
    if not host:
        return False  # laisse le caller signaler via warning
    return any(host == h or host.endswith("." + h) for h in ENRICH_X_HOSTS)


def _extract_host(url: str) -> str:
    """Extrait le hostname (sans `www.`) d'une URL ; '' si invalide/vide."""
    if not url or not isinstance(url, str):
        return ""
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return ""
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _make_jinja_env(prompts_dir: Path) -> Environment:
    """Env Jinja2 isolé (StrictUndefined, cohérent avec sourcing.py)."""
    return Environment(
        loader=FileSystemLoader(str(prompts_dir)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )


def _enrich_one(
    client: XAIClient,
    item: Item,
    user_prompt_tmpl: Any,  # jinja2.Template — typé Any pour éviter l'import
) -> tuple[Item | None, XAIUsage]:
    """
    Enrichit UN item via `web_search` restreint au domaine.

    Stratégie à deux niveaux :
      1. Appel xAI web_search (1re tentative, contexte LLM riche).
      2. Fallback Scrapling si xAI retourne summary vide ou lève XAIError
         (fetch HTML direct + extraction texte — 0 coût API, latence locale).

    Retourne :
      - `(enriched_item, usage)` en cas de succès (xAI OU Scrapling) ;
      - `(None, zero_usage)` si les deux échouent ; le caller ajoutera un warning.

    **Laisse remonter `XAIError`** au caller pour que le warning inclue le
    nom exact de l'exception (observability — issue #25 test coverage).
    Note : si Scrapling prend le relais, l'exception xAI est absorbée ici
    pour permettre la dégradation gracieuse (on logue le fallback).
    """
    zero_usage = XAIUsage(input_tokens=0, output_tokens=0, tool_calls=0)
    host = _extract_host(item.canonical_url)
    if not host:
        # Déjà filtré en amont, mais garde-fou défensif.
        return None, zero_usage

    prompt_label = f"enrich_{item.id[:8]}"

    # Système inline dans enrich.txt (pas de system.txt ici — sémantique
    # différente : single-item vs multi-items).
    user_prompt = user_prompt_tmpl.render(
        url=item.canonical_url,
        title=item.title,
        section_id=item.section_id,
    )

    tool_params = {"allowed_domains": [host]}

    # --- Tentative 1 : xAI web_search ---
    xai_usage = zero_usage
    new_summary = ""
    try:
        # TODO(live): valider que `allowed_domains` accepte la forme `["host"]`
        # pour `web_search` et que le retour du tool contient bien le body de
        # l'article (et non juste un snippet SERP). Fallback Scrapling si vide.
        response = client.call(
            system_prompt="",  # le prompt de rôle est inclus dans enrich.txt
            user_prompt=user_prompt,
            tool="web_search",
            tool_params=tool_params,
            prompt_label=prompt_label,
            response_schema=_ENRICH_RESPONSE_SCHEMA,
            schema_name=_ENRICH_SCHEMA_NAME,
        )
        xai_usage = response.usage
        parsed = response.parsed_output
        new_summary_raw = parsed.get("summary") if isinstance(parsed, dict) else None
        new_summary = str(new_summary_raw or "").strip()

        if new_summary:
            # Succès xAI — log et retourne
            _log_enrich_ok(
                prompt_label,
                item_id=item.id,
                host=host,
                summary_len=len(new_summary),
                tokens_in=response.usage.input_tokens,
                tokens_out=response.usage.output_tokens,
                tool_calls=response.usage.tool_calls,
                cost_usd=round(response.usage.cost_usd, 4),
                duration_ms=response.duration_ms,
                via="xai",
            )
            enriched = dataclasses.replace(
                item,
                summary=new_summary,
                raw_excerpt=new_summary[:_RAW_EXCERPT_CAP],
            )
            return enriched, xai_usage
        else:
            _log_enrich_skip(prompt_label, item.id, reason="xai_empty_summary_trying_scrapling")

    except XAIError as exc:
        # On absorbe ici pour tenter Scrapling ; si Scrapling échoue aussi,
        # on retourne (None, zero_usage) sans re-lever (dégradation gracieuse).
        _log_enrich_skip(prompt_label, item.id, reason=f"xai_error_trying_scrapling:{type(exc).__name__}")

    # --- Tentative 2 : fallback Scrapling (fetch HTML direct) ---
    scrapling_text = fetch_article_text(item.canonical_url)
    if scrapling_text:
        _log_enrich_ok(
            prompt_label,
            item_id=item.id,
            host=host,
            summary_len=len(scrapling_text),
            tokens_in=0,
            tokens_out=0,
            tool_calls=0,
            cost_usd=0.0,
            duration_ms=0,
            via="scrapling_fallback",
        )
        enriched = dataclasses.replace(
            item,
            summary=scrapling_text,
            raw_excerpt=scrapling_text[:_RAW_EXCERPT_CAP],
        )
        return enriched, xai_usage  # usage = xai attempt (peut être zero)

    _log_enrich_skip(prompt_label, item.id, reason="both_xai_and_scrapling_failed")
    return None, xai_usage


def _add_usage(a: XAIUsage, b: XAIUsage) -> XAIUsage:
    """Somme deux XAIUsage (même pattern que sourcing._add_usage)."""
    return XAIUsage(
        input_tokens=a.input_tokens + b.input_tokens,
        output_tokens=a.output_tokens + b.output_tokens,
        tool_calls=a.tool_calls + b.tool_calls,
    )


# ---------------------------------------------------------------------------
# Logs structurés (stderr JSON, parseable via jq)
# ---------------------------------------------------------------------------


def _log_enrich_ok(prompt_label: str, **fields: Any) -> None:
    record = {
        "event": "enrichment_call",
        "prompt": prompt_label,
        "status": "ok",
        **fields,
    }
    print(json.dumps(record, ensure_ascii=False), file=sys.stderr)


def _log_enrich_skip(prompt_label: str, item_id: str, *, reason: str) -> None:
    record = {
        "event": "enrichment_call",
        "prompt": prompt_label,
        "status": "skipped",
        "item_id": item_id,
        "reason": reason,
    }
    print(json.dumps(record, ensure_ascii=False), file=sys.stderr)


def _log_total(
    *,
    enriched_count: int,
    skipped_count: int,
    failed_count: int,
    usage: XAIUsage,
    warnings_count: int,
) -> None:
    record = {
        "event": "enrichment_total",
        "enriched_count": enriched_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
        "warnings_count": warnings_count,
        "tokens_in": usage.input_tokens,
        "tokens_out": usage.output_tokens,
        "tool_calls": usage.tool_calls,
        "cost_usd": round(usage.cost_usd, 4),
    }
    print(json.dumps(record, ensure_ascii=False), file=sys.stderr)
