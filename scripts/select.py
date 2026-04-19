"""Sélection finale par section + sections hero (60s, dont_miss). Voir PRD §S1.bis."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from scripts.models import Item

# Compte les items "longs" (vidéo, thread, article long) — utilisé pour dont_miss
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


def select_sixty_seconds(
    selected: dict[str, list[Item]],
    n: int = 3,
) -> list[Item]:
    """
    3 items 'EN 60 SECONDES' choisis parmi l'union de toutes les sections.
    Critère V1 : top par score, en privilégiant la diversité de sections (1 par section idéalement).
    """
    pool: list[Item] = [it for items in selected.values() for it in items]
    pool.sort(key=lambda x: (-x.score, -x.published_at.timestamp(), x.id))

    chosen: list[Item] = []
    seen_sections: set[str] = set()

    for it in pool:
        if it.section_id not in seen_sections:
            chosen.append(it)
            seen_sections.add(it.section_id)
            if len(chosen) == n:
                return chosen

    for it in pool:
        if it not in chosen:
            chosen.append(it)
            if len(chosen) == n:
                break
    return chosen


def select_dont_miss(
    all_items: list[Item],
    selected: dict[str, list[Item]],
) -> Item | None:
    """
    1 item 'À NE PAS MANQUER' choisi parmi les NON-retenus par les sections (anti-redondance).
    Privilégie les longs formats (vidéo / thread / article long).
    Retourne None si aucun candidat valable.
    """
    selected_ids = {it.id for items in selected.values() for it in items}
    leftovers = [it for it in all_items if it.id not in selected_ids]
    if not leftovers:
        return None

    def is_longform(it: Item) -> bool:
        return any(h in it.canonical_url for h in LONGFORM_HINTS)

    longforms = [it for it in leftovers if is_longform(it)]
    pool = longforms or leftovers
    pool.sort(key=lambda x: (-x.score, -x.published_at.timestamp(), x.id))
    return pool[0]


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
