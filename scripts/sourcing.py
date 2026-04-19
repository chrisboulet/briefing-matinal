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
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from scripts.dedup import canonical_url, item_id
from scripts.models import Item
from scripts.xai_client import XAIClient, XAIError, XAIResponse, XAIUsage, iso_date

logger = logging.getLogger(__name__)

# "Financial Times (@FT)" → @FT ; retourne None si aucun @handle trouvé.
_HANDLE_RE = re.compile(r"@[A-Za-z0-9_]{1,30}")

# Nettoyage des titres dérivés de `content` quand le LLM ne synthétise pas
# (issue #19 partie 2). Deux familles de bruit observées sur les posts X :
# 1. Marqueurs de thread en tête : "🧵", "1/", "1/12", "1." etc.
# 2. URLs t.co/... laissées en queue.
_LEADING_NOISE_RE = re.compile(r"^\s*(?:🧵|\d+/(?:\d+)?|\d+[.)])\s*")
_TRAILING_URL_RE = re.compile(r"\s+https?://\S+\s*$")

# Cap titre : 160 chars = bonne densité mobile sans coupure brutale.
_TITLE_MAX_CHARS = 160
_TITLE_MIN_SENTENCE_CHARS = 40  # évite les phrases trop courtes
_TITLE_MAX_SENTENCE_CHARS = 160
_SENTENCE_BOUNDARY_RE = re.compile(
    rf"^(.{{{_TITLE_MIN_SENTENCE_CHARS},{_TITLE_MAX_SENTENCE_CHARS}}}[.!?…])"
)


def _derive_title_from_content(content: str) -> str:
    """
    Dérive un titre lisible depuis le `content` brut d'un post X.

    Fallback utilisé quand le LLM passe les résultats de tool en shape native
    et ne synthétise pas de `title` (issue #19 partie 2). Les posts X santé/tech
    commencent souvent par du bruit ("🧵 1/12 ...") et finissent par une URL
    t.co — les deux sont retirés avant troncature.

    Ordre de traitement :
      1. Strip leading noise (jusqu'à 2 passes : "🧵 1/12 ..." → "...")
      2. Garde première ligne uniquement
      3. Strip trailing URL
      4. Si > 160 chars : coupe à la frontière de phrase (.!?…) entre 40-160
      5. Fallback hard truncation à 160 chars ou "(sans titre)" si vide
    """
    text = (content or "").strip()
    if not text:
        return "(sans titre)"

    # 1. Strip leading noise (2 passes : "🧵 1/12" nécessite 2 itérations)
    for _ in range(2):
        stripped = _LEADING_NOISE_RE.sub("", text).strip()
        if stripped == text:
            break
        text = stripped

    # 2. Première ligne seulement (évite les threads multilignes)
    text = text.split("\n", 1)[0].strip()

    # 3. Strip trailing URL (t.co/... typique)
    text = _TRAILING_URL_RE.sub("", text).strip()

    # 4+5. Troncature : privilégier frontière de phrase, sinon hard cap
    if len(text) > _TITLE_MAX_CHARS:
        match = _SENTENCE_BOUNDARY_RE.match(text)
        text = match.group(1) if match else text[:_TITLE_MAX_CHARS].rstrip()

    return text or "(sans titre)"

# Mapping tool → source_type par défaut quand le LLM n'en fournit pas (issue #17).
_SOURCE_TYPE_BY_TOOL: dict[str, Literal["x_account", "x_search", "web"]] = {
    "x_search_accounts": "x_account",
    "x_search_theme": "x_search",
    "web_search": "web",
}


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

# Limite xAI confirmée dans docs/xai-integration.md : max 10 handles par appel
# x_search avec allowed_x_handles.
MAX_HANDLES_PER_CALL = 10

# Limite xAI confirmée au premier live test post-#24 (issue #31) :
# max 5 domaines par appel web_search avec allowed_domains. Provoque un
# 400 "A maximum of 5 domains can be allowed" au-delà.
MAX_DOMAINS_PER_CALL = 5

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
            default_source_type="x_account",
            # Pas de default_section_id pour accounts : le LLM doit classer
            # chaque post selon son topic. Items sans section_id sont dropped.
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
            default_section_id=section_id,  # theme → section fixe
            default_source_type="x_search",
        )
        items.extend(new_items)
        warnings.extend(new_warnings)
        total_usage = _add_usage(total_usage, new_usage)

    # -- 3. Recherche web (batchs de MAX_DOMAINS_PER_CALL, issue #31) ------
    # xAI web_search plafonne à 5 domaines par appel. Même pattern que les
    # comptes X (max 10) : on splitte en batches. N domaines / 5 → ceil appels.
    sources_web_all = config["sources_web"]
    web_batches = list(_chunk(sources_web_all, MAX_DOMAINS_PER_CALL))
    for batch_idx, domains_batch in enumerate(web_batches, start=1):
        prompt_label = f"search_web_{batch_idx}"

        web_user_prompt = _render_prompt(
            env,
            "search_web.txt",
            {
                "allowed_domains": domains_batch,
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
            "allowed_domains": domains_batch,
            "from_date": from_date,
            "to_date": to_date,
        }

        new_items, new_warnings, new_usage = _do_call(
            client=client,
            system_prompt=system_prompt,
            user_prompt=web_user_prompt,
            tool="web_search",
            tool_params=web_tool_params,
            prompt_label=prompt_label,
            window_start=window_start,
            window_end=window_end,
            valid_section_ids=valid_section_ids,
            default_source_type="web",
            # Web : le LLM classe, pas de default_section_id (articles couvrent
            # politique, business, santé, etc.). Items sans section_id dropped.
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
    default_section_id: str | None = None,
    default_source_type: str | None = None,
) -> tuple[list[Item], list[str], XAIUsage]:
    """
    Effectue un appel xAI et convertit la réponse en items normalisés.

    `default_*` servent au fallback dans `_to_item` quand le modèle passe
    les résultats des tools en shape native (issue #17).

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
        default_section_id=default_section_id,
        default_source_type=default_source_type,
    )

    # Warnings émis par le LLM lui-même (ex: "aucun post pour @X").
    # Normalisé en list[str] par xai_client._parse_response (issue #17).
    llm_warnings_raw = response.parsed_output.get("warnings", [])
    if isinstance(llm_warnings_raw, str):
        llm_warnings = [llm_warnings_raw]
    elif isinstance(llm_warnings_raw, list):
        llm_warnings = [str(w) for w in llm_warnings_raw]
    else:
        llm_warnings = []
    all_warnings = [f"{prompt_label}: {w}" for w in llm_warnings] + parse_warnings

    return items, all_warnings, response.usage


def _items_from_response(
    response: XAIResponse,
    *,
    prompt_label: str,
    window_start: datetime,
    window_end: datetime,
    valid_section_ids: set[str],
    default_section_id: str | None = None,
    default_source_type: str | None = None,
) -> tuple[list[Item], list[str]]:
    """
    Convertit `response.parsed_output["items"]` en liste d'Item validés.

    Filtres défensifs :
      - drop si published_at hors fenêtre (le LLM peut fudge)
      - drop si section_id non configuré
      - drop si la conversion lève une erreur (warning + skip)

    `default_*` propagés vers `_to_item` pour combler les champs manquants
    quand le modèle retourne la shape native xAI (issue #17).
    `window_end` sert aussi de fallback `published_at` pour les items sans date.
    """
    raw_items = response.parsed_output.get("items", []) or []
    out: list[Item] = []
    warnings: list[str] = []

    for raw in raw_items:
        try:
            item = _to_item(
                raw,
                default_section_id=default_section_id,
                default_source_type=default_source_type,
                fallback_published_at=window_end,
            )
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


def _to_item(
    raw: dict[str, Any],
    *,
    default_section_id: str | None = None,
    default_source_type: str | None = None,
    fallback_published_at: datetime | None = None,
) -> Item:
    """
    Construit un Item à partir du dict brut renvoyé par le LLM.

    Accepte deux shapes (issue #17) :
    1. Shape idéale (demandée par le prompt) : `{title, summary, canonical_url,
       section_id, source_type, source_handle, published_at, score, likes, reposts}`
    2. Shape native xAI (observée en pratique) : `{post_id, author, content,
       engagement: {likes, reposts, views}, link}` — le modèle passe souvent
       les résultats des tools tels quels.

    Le contexte (`default_*`) permet de combler les champs que la shape native
    ne fournit pas (section_id pour theme calls, source_type par tool,
    published_at via window_end si absent).
    """
    # URL : `canonical_url` (idéal) ou `link` / `url` (xAI native)
    url = raw.get("canonical_url") or raw.get("link") or raw.get("url")
    if not isinstance(url, str) or not url:
        raise KeyError("no URL field (canonical_url/link/url) in item")
    canonical = canonical_url(url)

    # Engagement : flat (idéal) ou nested sous `engagement`
    engagement = raw.get("engagement") if isinstance(raw.get("engagement"), dict) else {}
    likes = int(raw.get("likes", engagement.get("likes", 0)) or 0)
    reposts = int(raw.get("reposts", engagement.get("reposts", 0)) or 0)

    # Title : explicit si fourni par le LLM, sinon dérivé du content brut
    # via un helper qui nettoie le bruit courant (thread markers, trailing URLs).
    # Voir _derive_title_from_content pour le détail (issue #19 partie 2).
    title = raw.get("title")
    if not isinstance(title, str) or not title.strip():
        title = _derive_title_from_content(raw.get("content") or "")

    # Summary : explicit ou `content` (truncate pour budget rendu)
    # Cap 1200 chars : le prompt demande ~400-500 (issue #23) ; on laisse
    # de la marge sans que ça explose le budget lisibilité du HTML.
    summary = raw.get("summary") or raw.get("content") or title
    summary = str(summary)[:1200]

    # Source handle : explicit ou parse de `author` ("Name (@handle)")
    source_handle = raw.get("source_handle")
    if not isinstance(source_handle, str) or not source_handle.strip():
        author = raw.get("author") or ""
        match = _HANDLE_RE.search(author) if isinstance(author, str) else None
        source_handle = match.group(0) if match else (author or "unknown")

    # Source type : explicit ou default du call
    source_type = raw.get("source_type") or default_source_type
    if source_type not in ("x_account", "x_search", "web"):
        raise KeyError(f"source_type invalid/missing (got {source_type!r})")

    # Section_id : explicit ou default du call (theme calls seulement)
    section_id = raw.get("section_id") or default_section_id
    if not isinstance(section_id, str) or not section_id:
        raise KeyError("section_id missing and no default_section_id provided")

    # Published_at : ISO string ou fallback (window_end)
    pub = raw.get("published_at") or raw.get("created_at")
    if isinstance(pub, str) and pub:
        published_at = datetime.fromisoformat(pub.replace("Z", "+00:00"))
    elif fallback_published_at is not None:
        published_at = fallback_published_at
    else:
        raise KeyError("published_at missing and no fallback provided")

    # Score : 0.0-1.0 (default 0.5 si absent ou invalide)
    try:
        score = float(raw.get("score", 0.5))
    except (TypeError, ValueError):
        score = 0.5
    score = max(0.0, min(1.0, score))

    return Item(
        id=item_id(canonical),
        title=title,
        summary=summary,
        canonical_url=canonical,
        section_id=section_id,
        source_type=source_type,  # type: ignore[arg-type]
        source_handle=source_handle,
        published_at=published_at,
        score=score,
        short_url="",
        raw_excerpt="",
        alt_sources=(),
        is_reply=False,
        is_retweet=False,
        likes=likes,
        reposts=reposts,
    )


def _add_usage(a: XAIUsage, b: XAIUsage) -> XAIUsage:
    """Somme deux XAIUsage (immutable-friendly)."""
    return XAIUsage(
        input_tokens=a.input_tokens + b.input_tokens,
        output_tokens=a.output_tokens + b.output_tokens,
        tool_calls=a.tool_calls + b.tool_calls,
    )
