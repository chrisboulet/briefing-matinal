"""Sélection finale : top signaux + sections + diversité auteurs (#43, top-10)."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from typing import Any

from scripts.item_quality import is_hero_url_eligible
from scripts.models import Item

# Compte les items "longs" (vidéo, thread, article long) — bonus soft ranking
LONGFORM_HINTS = ("youtube.com", "youtu.be", "/thread/", "watch?v=")

# Défaut issue #43 : un auteur ne monopolise pas le briefing.
DEFAULT_MAX_ITEMS_PER_AUTHOR = 1
# Cible produit : top sujets chauds en tête de brief.
DEFAULT_TOP_SIGNALS_MAX = 10


def normalize_handle(handle: str) -> str:
    """Normalise un handle pour le cap auteur (case-insensitive, strip)."""
    h = (handle or "").strip()
    if not h:
        return "unknown"
    if h.startswith("@"):
        return "@" + h[1:].lower()
    return h.lower()


def _is_longform(it: Item) -> bool:
    return any(h in it.canonical_url for h in LONGFORM_HINTS)


def _rank_key(it: Item) -> tuple:
    # longform d'abord (0), puis score DESC, recence, id stable
    return (0 if _is_longform(it) else 1, -it.score, -it.published_at.timestamp(), it.id)


def select_top_signals(
    items: list[Item],
    max_n: int = DEFAULT_TOP_SIGNALS_MAX,
    max_items_per_author: int = DEFAULT_MAX_ITEMS_PER_AUTHOR,
) -> list[Item]:
    """
    Top N sujets chauds (global), avant les sections thématiques.

    - URL hero-eligible seulement (pas de homepage Frankenstein)
    - Cap par auteur (défaut 1)
    - Ranking : longform soft + score composite + recency
    """
    max_n = max(0, int(max_n))
    if max_n == 0 or not items:
        return []

    max_per_author = max(0, int(max_items_per_author))
    author_counts: Counter[str] = Counter()
    ranked = sorted(items, key=_rank_key)
    out: list[Item] = []
    for it in ranked:
        if len(out) >= max_n:
            break
        if not is_hero_url_eligible(it.canonical_url):
            continue
        handle = normalize_handle(it.source_handle)
        if max_per_author > 0 and author_counts[handle] >= max_per_author:
            continue
        out.append(it)
        author_counts[handle] += 1
    return out


def select_by_section(
    items: list[Item],
    sections_config: list[dict[str, Any]],
    max_items_per_author: int = DEFAULT_MAX_ITEMS_PER_AUTHOR,
    prior_handles: Iterable[str] | None = None,
) -> dict[str, list[Item]]:
    """
    Pour chaque section, garde top max_items selon (score, published_at),
    en respectant un plafond global par `source_handle` (issue #43).

    `prior_handles` : auteurs déjà pris par le top_signals (cap global).
    Items déjà triés en sortie de dedupe (score DESC).
    """
    by_section: dict[str, list[Item]] = defaultdict(list)
    for it in items:
        by_section[it.section_id].append(it)

    author_counts: Counter[str] = Counter(
        normalize_handle(h) for h in (prior_handles or [])
    )
    max_per_author = max(0, int(max_items_per_author))

    out: dict[str, list[Item]] = {}
    for section in sections_config:
        sid = section["id"]
        max_items = section["max_items"]
        kept: list[Item] = []
        for it in by_section[sid]:
            if len(kept) >= max_items:
                break
            handle = normalize_handle(it.source_handle)
            if max_per_author > 0 and author_counts[handle] >= max_per_author:
                continue
            kept.append(it)
            author_counts[handle] += 1
        out[sid] = kept
    return out


def select_dont_miss(
    all_items: list[Item],
    selected: dict[str, list[Item]],
    max_items_per_author: int = DEFAULT_MAX_ITEMS_PER_AUTHOR,
) -> Item | None:
    """
    Legacy 1-item hero (conservé pour tests / fallback).

    Préférer `select_top_signals` dans le pipeline principal.
    """
    selected_ids = {it.id for items in selected.values() for it in items}
    leftovers = [it for it in all_items if it.id not in selected_ids]

    selected_handles = {
        normalize_handle(it.source_handle)
        for items in selected.values()
        for it in items
    }
    max_per_author = max(0, int(max_items_per_author))

    def author_ok(it: Item) -> bool:
        if max_per_author <= 0:
            return True
        return normalize_handle(it.source_handle) not in selected_handles

    def pick(pool: list[Item]) -> Item | None:
        eligible = [
            it
            for it in pool
            if is_hero_url_eligible(it.canonical_url) and author_ok(it)
        ]
        if not eligible:
            return None
        eligible.sort(key=_rank_key)
        return eligible[0]

    if not leftovers:
        return None

    chosen = pick(leftovers)
    if chosen is not None:
        return chosen

    url_ok_leftovers = [
        it for it in leftovers if is_hero_url_eligible(it.canonical_url)
    ]
    if url_ok_leftovers:
        return None

    selected_flat = [it for items in selected.values() for it in items]
    eligible_selected = [
        it for it in selected_flat if is_hero_url_eligible(it.canonical_url)
    ]
    if not eligible_selected:
        return None
    eligible_selected.sort(key=_rank_key)
    return eligible_selected[0]


def apply_engagement_filter(
    items: list[Item], engagement_min: dict[str, int]
) -> list[Item]:
    """Filtre items X faibles (likes < min ET reposts < min). Web items passent toujours."""
    min_likes = engagement_min.get("likes", 0)
    min_reposts = engagement_min.get("reposts", 0)

    def passes(it: Item) -> bool:
        if it.source_type == "web":
            return True
        if it.is_reply or it.is_retweet:
            return False
        return it.likes >= min_likes or it.reposts >= min_reposts

    return [it for it in items if passes(it)]
