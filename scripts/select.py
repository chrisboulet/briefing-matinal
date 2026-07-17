"""Sélection finale par section + dont_miss + diversité auteurs. Voir PRD §S1.bis + #43."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from scripts.item_quality import is_hero_url_eligible
from scripts.models import Item

# Compte les items "longs" (vidéo, thread, article long) — bonus pour dont_miss
LONGFORM_HINTS = ("youtube.com", "youtu.be", "/thread/", "watch?v=")

# Défaut issue #43 : un auteur ne monopolise pas le briefing.
DEFAULT_MAX_ITEMS_PER_AUTHOR = 1


def normalize_handle(handle: str) -> str:
    """Normalise un handle pour le cap auteur (case-insensitive, strip)."""
    h = (handle or "").strip()
    if not h:
        return "unknown"
    # Domaines web (lapresse.ca) vs @handles — garder tel quel en lower
    if h.startswith("@"):
        return "@" + h[1:].lower()
    return h.lower()


def select_by_section(
    items: list[Item],
    sections_config: list[dict[str, Any]],
    max_items_per_author: int = DEFAULT_MAX_ITEMS_PER_AUTHOR,
) -> dict[str, list[Item]]:
    """
    Pour chaque section, garde top max_items selon (score, published_at),
    en respectant un plafond global par `source_handle` (issue #43).

    Items déjà triés en sortie de dedupe (score DESC).
    Le compteur d'auteurs est partagé entre sections (ordre = config).
    """
    by_section: dict[str, list[Item]] = defaultdict(list)
    for it in items:
        by_section[it.section_id].append(it)

    author_counts: Counter[str] = Counter()
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
    1 item 'À NE PAS MANQUER'.

    Règles (issue #40 + anti-redondance historique + #43) :
    1. Préférer un leftover (non déjà rendu en section) pour éviter le doublon.
    2. URL homepage / vide → inéligible hero.
    3. Respecter le cap auteur global (ne pas mettre un 2e post du même handle
       en hero si déjà présent en section), sauf max_items_per_author <= 0.
    4. Parmi les leftovers éligibles : bonus soft longform, puis score DESC.
    5. Si des leftovers existent mais sont tous inéligibles (ex. homepage) :
       fallback sur le meilleur item déjà sélectionné éligible.
    6. Si aucun leftover du tout (tout est déjà en section) → None (anti-redondance).
    """
    selected_ids = {it.id for items in selected.values() for it in items}
    leftovers = [it for it in all_items if it.id not in selected_ids]

    selected_handles = {
        normalize_handle(it.source_handle)
        for items in selected.values()
        for it in items
    }
    max_per_author = max(0, int(max_items_per_author))

    def is_longform(it: Item) -> bool:
        return any(h in it.canonical_url for h in LONGFORM_HINTS)

    def rank_key(it: Item) -> tuple:
        return (0 if is_longform(it) else 1, -it.score, -it.published_at.timestamp(), it.id)

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
        eligible.sort(key=rank_key)
        return eligible[0]

    if not leftovers:
        return None

    chosen = pick(leftovers)
    if chosen is not None:
        return chosen

    # Distinguer deux cas (issue #40 vs #43) :
    # - leftovers URL-ok mais bloqués par cap auteur → pas de hero (anti-doublon)
    # - leftovers uniquement homepage/URL inéligibles → fallback section éligible
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
    eligible_selected.sort(key=rank_key)
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
