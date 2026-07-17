"""Sélection finale par section + dont_miss. Voir PRD §S1.bis."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from scripts.item_quality import is_hero_url_eligible
from scripts.models import Item

# Compte les items "longs" (vidéo, thread, article long) — bonus pour dont_miss
LONGFORM_HINTS = ("youtube.com", "youtu.be", "/thread/", "watch?v=")


def select_by_section(
    items: list[Item], sections_config: list[dict[str, Any]]
) -> dict[str, list[Item]]:
    """
    Pour chaque section, garde top max_items selon (score, published_at).
    Items déjà triés en sortie de dedupe (score DESC).
    """
    by_section: dict[str, list[Item]] = defaultdict(list)
    for it in items:
        by_section[it.section_id].append(it)

    out: dict[str, list[Item]] = {}
    for section in sections_config:
        sid = section["id"]
        max_items = section["max_items"]
        out[sid] = by_section[sid][:max_items]
    return out


def select_dont_miss(
    all_items: list[Item],
    selected: dict[str, list[Item]],
) -> Item | None:
    """
    1 item 'À NE PAS MANQUER'.

    Règles (issue #40 + anti-redondance historique) :
    1. Préférer un leftover (non déjà rendu en section) pour éviter le doublon.
    2. URL homepage / vide → inéligible hero.
    3. Parmi les leftovers éligibles : bonus soft longform, puis score DESC.
    4. Si des leftovers existent mais sont tous inéligibles (ex. homepage) :
       fallback sur le meilleur item déjà sélectionné éligible.
    5. Si aucun leftover du tout (tout est déjà en section) → None (anti-redondance).
    """
    selected_ids = {it.id for items in selected.values() for it in items}
    leftovers = [it for it in all_items if it.id not in selected_ids]

    def is_longform(it: Item) -> bool:
        return any(h in it.canonical_url for h in LONGFORM_HINTS)

    def rank_key(it: Item) -> tuple:
        return (0 if is_longform(it) else 1, -it.score, -it.published_at.timestamp(), it.id)

    def pick(pool: list[Item]) -> Item | None:
        eligible = [it for it in pool if is_hero_url_eligible(it.canonical_url)]
        if not eligible:
            return None
        eligible.sort(key=rank_key)
        return eligible[0]

    if not leftovers:
        return None

    chosen = pick(leftovers)
    if chosen is not None:
        return chosen

    # Leftovers existed but all failed hero URL quality → better a solid
    # section item than a homepage Frankenstein (issue #40).
    selected_flat = [it for items in selected.values() for it in items]
    return pick(selected_flat)


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
