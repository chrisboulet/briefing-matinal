"""Sélection finale : top signaux + sections + budget 10-25 items."""

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
DEFAULT_TOP_SIGNALS_MAX = 15
# Budget global d'items rendus (Chris 2026-07 : min 10, max 25).
DEFAULT_ITEMS_MIN = 10
DEFAULT_ITEMS_MAX = 25


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


def _count_selected(top: list[Item], sections: dict[str, list[Item]]) -> int:
    return len(top) + sum(len(v) for v in sections.values())


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


def assemble_selection(
    items: list[Item],
    sections_config: list[dict[str, Any]],
    *,
    top_signals_max: int = DEFAULT_TOP_SIGNALS_MAX,
    items_min: int = DEFAULT_ITEMS_MIN,
    items_max: int = DEFAULT_ITEMS_MAX,
    max_items_per_author: int = DEFAULT_MAX_ITEMS_PER_AUTHOR,
) -> tuple[list[Item], dict[str, list[Item]], list[str]]:
    """
    Assemble top_signals + sections dans le budget [items_min, items_max].

    1. Top signaux (diversité auteur stricte)
    2. Sections depuis le reste
    3. Si total > max → coupe d'abord les sections (score bas), puis le top
    4. Si total < min → backfill dans le top (cap auteur 1, puis 2, puis illimité)
    """
    warnings: list[str] = []
    items_min = max(0, int(items_min))
    items_max = max(items_min, int(items_max))  # max never below min
    top_cap = max(0, min(int(top_signals_max), items_max))

    top = select_top_signals(
        items,
        max_n=top_cap,
        max_items_per_author=max_items_per_author,
    )
    top_ids = {it.id for it in top}
    remaining = [it for it in items if it.id not in top_ids]
    sections = select_by_section(
        remaining,
        sections_config,
        max_items_per_author=max_items_per_author,
        prior_handles=[it.source_handle for it in top],
    )

    total = _count_selected(top, sections)

    # --- Cap max ---
    if total > items_max:
        top, sections = _trim_to_max(top, sections, items_max)
        warnings.append(
            f"items_budget: tronque a {items_max} (avait {total})"
        )
        total = _count_selected(top, sections)

    # --- Floor min : backfill dans top_signals ---
    if total < items_min:
        used_ids = {it.id for it in top}
        for sec_items in sections.values():
            used_ids.update(it.id for it in sec_items)
        pool = [it for it in items if it.id not in used_ids]
        before = total
        # Progressive author relaxation: strict → 2 → unlimited (0)
        for author_cap in (max_items_per_author, 2, 0):
            if total >= items_min:
                break
            need = items_min - total
            room = items_max - total
            add_n = min(need, room)
            if add_n <= 0:
                break
            extra = select_top_signals(
                pool,
                max_n=add_n,
                max_items_per_author=author_cap if author_cap > 0 else 0,
            )
            if author_cap > 0:
                counts = Counter(normalize_handle(it.source_handle) for it in top)
                for sec_items in sections.values():
                    for it in sec_items:
                        counts[normalize_handle(it.source_handle)] += 1
                filtered_extra: list[Item] = []
                for it in extra:
                    h = normalize_handle(it.source_handle)
                    if counts[h] >= author_cap:
                        continue
                    filtered_extra.append(it)
                    counts[h] += 1
                    if len(filtered_extra) >= add_n:
                        break
                extra = filtered_extra
            if not extra:
                continue
            top.extend(extra)
            extra_ids = {it.id for it in extra}
            pool = [it for it in pool if it.id not in extra_ids]
            total = _count_selected(top, sections)
        if total > before:
            warnings.append(
                f"items_budget: backfill +{total - before} "
                f"pour atteindre min {items_min} (total={total})"
            )
        if total < items_min:
            warnings.append(
                f"items_below_min: {total} items < min {items_min} "
                f"(pool source insuffisant)"
            )

    return top, sections, warnings


def _trim_to_max(
    top: list[Item],
    sections: dict[str, list[Item]],
    items_max: int,
) -> tuple[list[Item], dict[str, list[Item]]]:
    """Coupe d'abord les sections (scores bas), puis le bas du top."""
    total = _count_selected(top, sections)
    if total <= items_max:
        return top, sections

    sec_flat: list[tuple[str, int, Item]] = []
    for sid, sec_items in sections.items():
        for idx, it in enumerate(sec_items):
            sec_flat.append((sid, idx, it))
    sec_flat.sort(key=lambda t: (t[2].score, t[2].published_at.timestamp()))
    drop_sec: set[tuple[str, int]] = set()
    while total > items_max and sec_flat:
        sid, idx, _ = sec_flat.pop(0)
        drop_sec.add((sid, idx))
        total -= 1

    new_sections: dict[str, list[Item]] = {}
    for sid, sec_items in sections.items():
        kept = [
            it for idx, it in enumerate(sec_items) if (sid, idx) not in drop_sec
        ]
        new_sections[sid] = kept

    if total > items_max:
        keep_n = max(0, items_max - sum(len(v) for v in new_sections.values()))
        top = top[:keep_n]

    return top, new_sections


def select_dont_miss(
    all_items: list[Item],
    selected: dict[str, list[Item]],
    max_items_per_author: int = DEFAULT_MAX_ITEMS_PER_AUTHOR,
) -> Item | None:
    """
    Legacy 1-item hero (conserve pour tests / fallback).

    Preferer assemble_selection dans le pipeline principal.
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


def soften_engagement_min(engagement_min: dict[str, int]) -> dict[str, int]:
    """Reduit les seuils d'engagement pour densifier un brief trop vide."""
    likes = int(engagement_min.get("likes", 0))
    reposts = int(engagement_min.get("reposts", 0))
    return {
        "likes": max(3, likes // 2),
        "reposts": max(1, reposts // 2),
    }
