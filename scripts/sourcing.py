"""
Orchestrateur des appels xAI pour le sourcing du briefing.

Voir docs/xai-integration.md pour la référence opérationnelle complète et
PRD §S1 / §S1.bis / §Modes d'erreur pour les specs fonctionnelles.

Conception :
- Synchrone (le client xAI l'est aussi — V1 fait ~11 appels en série).
- Dégradation gracieuse : un appel raté = warning + section vide,
  jamais une exception qui remonte au caller.
- Les sections / comptes / recherches sont tous data-driven depuis
  `sources/comptes.json` (chargé par scripts.config.load_config).
- Le rendu Jinja2 des prompts est isolé dans `_render_prompt` pour
  faciliter le test unitaire.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from scripts.dedup import canonical_url, item_id
from scripts.models import Item
from scripts.xai_client import XAIClient, XAIError, XAIResponse, XAIUsage, iso_date

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

# Limite xAI confirmée dans docs/xai-integration.md : max 10 handles par appel
# x_search avec allowed_x_handles.
MAX_HANDLES_PER_CALL = 10

# Quantités par défaut demandées au LLM (peut être tweaké via param fonction).
DEFAULT_MAX_ACCOUNTS_TOTAL = 12
DEFAULT_MAX_WEB_TOTAL = 8
THEME_BUFFER = 2  # max_items section + 2 pour laisser le dedupe respirer


# ---------------------------------------------------------------------------
# Types de retour
# ---------------------------------------------------------------------------


@dataclass
class SourcingResult:
    """Résultat agrégé de tous les appels xAI pour un briefing."""

    items: list[Item]
    warnings: list[str] = field(default_factory=list)
    total_usage: XAIUsage = field(
        default_factory=lambda: XAIUsage(input_tokens=0, output_tokens=0, tool_calls=0)
    )


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------


def source_briefing(
    client: XAIClient,
    config: dict,
    window_start: datetime,
    window_end: datetime,
    prompts_dir: Path = Path("prompts"),
    max_items_per_call: int = 10,
) -> SourcingResult:
    """
    Orchestre l'ensemble des appels xAI nécessaires au sourcing d'un briefing.

    Pour chaque source (X par compte, X thématique, web) :
    1. Rend le prompt Jinja correspondant ;
    2. Appelle `client.call(...)` avec le bon tool ;
    3. Convertit la sortie LLM en `Item` (filtre fenêtre + section_id valide) ;
    4. Sur erreur xAI, ajoute un warning et continue (jamais d'exception fatale).

    Args:
        client: instance XAIClient déjà configurée (mode live).
        config: dict retourné par scripts.config.load_config().
        window_start: début de la fenêtre temporelle, en UTC.
        window_end: fin de la fenêtre temporelle, en UTC.
        prompts_dir: dossier contenant `system.txt` et les `search_*.txt`.
        max_items_per_call: borne haute par appel pour limiter le coût (sert
            de plafond global, les valeurs effectives sont définies plus bas).

    Returns:
        SourcingResult avec les items normalisés, les warnings collectés et
        l'usage agrégé sur tous les appels.
    """
    env = _make_jinja_env(prompts_dir)
    system_prompt = _render_prompt(env, "system.txt", {})

    section_ids = [s["id"] for s in config["sections"]]
    valid_section_ids = set(section_ids)
    sections_by_id = {s["id"]: s for s in config["sections"]}
    engagement_min = config["engagement_min"]

    window_start_iso = window_start.isoformat()
    window_end_iso = window_end.isoformat()
    from_date = iso_date(window_start.date())
    to_date = iso_date(window_end.date())

    items: list[Item] = []
    warnings: list[str] = []
    total_usage = XAIUsage(input_tokens=0, output_tokens=0, tool_calls=0)

    # -- 1. Comptes X surveillés (batchs de MAX_HANDLES_PER_CALL) ----------
    handles_all = config["comptes_x"]
    batches = list(_chunk(handles_all, MAX_HANDLES_PER_CALL))
    for batch_idx, batch in enumerate(batches, start=1):
        # `allowed_x_handles` attend des handles SANS le `@` initial.
        bare_handles = [h.lstrip("@") for h in batch]
        prompt_label = f"search_accounts_{batch_idx}"

        user_prompt = _render_prompt(
            env,
            "search_accounts.txt",
            {
                "window_start_iso": window_start_iso,
                "window_end_iso": window_end_iso,
                "handles": bare_handles,
                "engagement_min": engagement_min,
                "section_ids": section_ids,
                "max_items_total": min(DEFAULT_MAX_ACCOUNTS_TOTAL, max_items_per_call),
            },
        )

        tool_params = {
            "allowed_x_handles": bare_handles,
            "from_date": from_date,
            "to_date": to_date,
        }

        new_items, new_warnings, new_usage = _do_call(
            client=client,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tool="x_search",
            tool_params=tool_params,
            prompt_label=prompt_label,
            window_start=window_start,
            window_end=window_end,
            valid_section_ids=valid_section_ids,
        )
        items.extend(new_items)
        warnings.extend(new_warnings)
        total_usage = _add_usage(total_usage, new_usage)

    # -- 2. Recherches X thématiques (1 appel / thème) ---------------------
    for theme_cfg in config["recherches_thematiques"]:
        section_id = theme_cfg["section_id"]
        section = sections_by_id.get(section_id)
        # Defensive: section_id devrait être validé par config.load_config(),
        # mais on garde un fallback raisonnable.
        section_max = section["max_items"] if section else 3
        max_items = min(section_max + THEME_BUFFER, max_items_per_call)
        prompt_label = f"search_theme_{theme_cfg['theme']}"

        user_prompt = _render_prompt(
            env,
            "search_theme.txt",
            {
                "theme": theme_cfg["theme"],
                "query": theme_cfg["query"],
                "section_id": section_id,
                "engagement_min": engagement_min,
                "window_start_iso": window_start_iso,
                "window_end_iso": window_end_iso,
                "max_items": max_items,
            },
        )

        tool_params = {
            "from_date": from_date,
            "to_date": to_date,
        }
        # NB: pas de allowed_x_handles ici — c'est une recherche libre.

        new_items, new_warnings, new_usage = _do_call(
            client=client,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tool="x_search",
            tool_params=tool_params,
            prompt_label=prompt_label,
            window_start=window_start,
            window_end=window_end,
            valid_section_ids=valid_section_ids,
        )
        items.extend(new_items)
        warnings.extend(new_warnings)
        total_usage = _add_usage(total_usage, new_usage)

    # -- 3. Recherche web (UN seul appel) ----------------------------------
    web_user_prompt = _render_prompt(
        env,
        "search_web.txt",
        {
            "allowed_domains": config["sources_web"],
            "section_ids": section_ids,
            "window_start_iso": window_start_iso,
            "window_end_iso": window_end_iso,
            "max_items_total": min(DEFAULT_MAX_WEB_TOTAL, max_items_per_call),
        },
    )

    # TODO(live): verify web_search params — la doc xAI accessible ne fige
    # pas le nom exact du champ (`allowed_domains` vs `domains` vs autre)
    # ni la prise en charge de `from_date`/`to_date` côté tool.
    web_tool_params = {
        "allowed_domains": config["sources_web"],
        "from_date": from_date,
        "to_date": to_date,
    }

    new_items, new_warnings, new_usage = _do_call(
        client=client,
        system_prompt=system_prompt,
        user_prompt=web_user_prompt,
        tool="web_search",
        tool_params=web_tool_params,
        prompt_label="search_web",
        window_start=window_start,
        window_end=window_end,
        valid_section_ids=valid_section_ids,
    )
    items.extend(new_items)
    warnings.extend(new_warnings)
    total_usage = _add_usage(total_usage, new_usage)

    return SourcingResult(items=items, warnings=warnings, total_usage=total_usage)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_jinja_env(prompts_dir: Path) -> Environment:
    """Crée l'environnement Jinja2 avec StrictUndefined (fail fast sur typo)."""
    return Environment(
        loader=FileSystemLoader(str(prompts_dir)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,  # prompts texte, pas HTML
    )


def _render_prompt(env: Environment, template_name: str, ctx: dict[str, Any]) -> str:
    """Rend un template Jinja en str."""
    return env.get_template(template_name).render(**ctx)


def _chunk(seq: list[Any], size: int) -> Iterable[list[Any]]:
    """Découpe une liste en sous-listes de taille max `size`."""
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _do_call(
    *,
    client: XAIClient,
    system_prompt: str,
    user_prompt: str,
    tool: str,
    tool_params: dict[str, Any],
    prompt_label: str,
    window_start: datetime,
    window_end: datetime,
    valid_section_ids: set[str],
) -> tuple[list[Item], list[str], XAIUsage]:
    """
    Effectue un appel xAI et convertit la réponse en items normalisés.

    Sur XAIError, retourne ([], [warning], usage_zéro) et logue — n'élève jamais.
    """
    try:
        # Le type Literal["x_search","web_search"] n'est pas exprimable depuis
        # l'extérieur sans cast — on s'appuie sur la validation runtime du client.
        response: XAIResponse = client.call(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tool=tool,  # type: ignore[arg-type]
            tool_params=tool_params,
            prompt_label=prompt_label,
        )
    except XAIError as exc:
        warning = f"{prompt_label}: {type(exc).__name__}: {exc}"
        logger.warning("xAI call failed: %s", warning)
        zero_usage = XAIUsage(input_tokens=0, output_tokens=0, tool_calls=0)
        return [], [warning], zero_usage

    items, parse_warnings = _items_from_response(
        response,
        prompt_label=prompt_label,
        window_start=window_start,
        window_end=window_end,
        valid_section_ids=valid_section_ids,
    )

    # Warnings émis par le LLM lui-même (ex: "aucun post pour @X").
    # TODO(live): valider que `parsed_output["warnings"]` est toujours présent
    # quand le json_schema strict côté API est respecté.
    llm_warnings = response.parsed_output.get("warnings", []) or []
    all_warnings = [f"{prompt_label}: {w}" for w in llm_warnings] + parse_warnings

    return items, all_warnings, response.usage


def _items_from_response(
    response: XAIResponse,
    *,
    prompt_label: str,
    window_start: datetime,
    window_end: datetime,
    valid_section_ids: set[str],
) -> tuple[list[Item], list[str]]:
    """
    Convertit `response.parsed_output["items"]` en liste d'Item validés.

    Filtres défensifs :
      - drop si published_at hors fenêtre (le LLM peut fudge)
      - drop si section_id non configuré
      - drop si la conversion lève une erreur (warning + skip)
    """
    raw_items = response.parsed_output.get("items", []) or []
    out: list[Item] = []
    warnings: list[str] = []

    for raw in raw_items:
        try:
            item = _to_item(raw)
        except (KeyError, ValueError, TypeError) as exc:
            warnings.append(
                f"{prompt_label}: skipped malformed item ({type(exc).__name__}: {exc})"
            )
            continue

        if not (window_start <= item.published_at <= window_end):
            warnings.append(
                f"{prompt_label}: skipped item outside window "
                f"(published_at={item.published_at.isoformat()})"
            )
            continue

        if item.section_id not in valid_section_ids:
            warnings.append(
                f"{prompt_label}: skipped item with unknown section_id={item.section_id!r}"
            )
            continue

        out.append(item)

    return out, warnings


def _to_item(raw: dict[str, Any]) -> Item:
    """
    Construit un Item à partir du dict brut renvoyé par le LLM.

    L'URL est canonisée (dedupe-friendly) et l'ID dérivé de cette URL.
    Les champs absents du payload LLM (alt_sources, short_url, raw_excerpt)
    reçoivent des valeurs par défaut neutres.
    """
    canonical = canonical_url(raw["canonical_url"])
    published_at = datetime.fromisoformat(raw["published_at"].replace("Z", "+00:00"))

    return Item(
        id=item_id(canonical),
        title=raw["title"],
        summary=raw["summary"],
        canonical_url=canonical,
        section_id=raw["section_id"],
        source_type=raw["source_type"],
        source_handle=raw["source_handle"],
        published_at=published_at,
        score=float(raw["score"]),
        short_url="",
        raw_excerpt="",
        alt_sources=(),
        is_reply=False,
        is_retweet=False,
        likes=int(raw.get("likes", 0)),
        reposts=int(raw.get("reposts", 0)),
    )


def _add_usage(a: XAIUsage, b: XAIUsage) -> XAIUsage:
    """Somme deux XAIUsage (immutable-friendly)."""
    return XAIUsage(
        input_tokens=a.input_tokens + b.input_tokens,
        output_tokens=a.output_tokens + b.output_tokens,
        tool_calls=a.tool_calls + b.tool_calls,
    )
