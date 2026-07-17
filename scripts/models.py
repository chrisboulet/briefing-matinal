"""Data model du pipeline. Voir PRD §Data model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class Item:
    """Unité de contenu manipulée dans tout le pipeline."""

    id: str
    title: str
    summary: str
    canonical_url: str
    section_id: str
    source_type: Literal["x_account", "x_search", "web"]
    source_handle: str
    published_at: datetime
    score: float
    short_url: str = ""
    raw_excerpt: str = ""
    alt_sources: tuple[str, ...] = ()
    is_reply: bool = False
    is_retweet: bool = False
    likes: int = 0
    reposts: int = 0


@dataclass
class Briefing:
    """Briefing complet prêt au rendu."""

    briefing_id: str
    moment: Literal["matin", "soir"]
    generated_at: datetime
    window_start: datetime
    window_end: datetime
    sections: dict[str, list[Item]]
    # Legacy single-hero (tests + fallback). Prefer top_signals for product.
    dont_miss: Item | None = None
    # Top N sujets chauds (signal-density) — rendus en tête du HTML.
    top_signals: list[Item] = field(default_factory=list)
    config_hash: str = ""
    prompts_version: str = ""
    git_commit: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def items_count(self) -> int:
        n = sum(len(v) for v in self.sections.values())
        if self.top_signals:
            n += len(self.top_signals)
        elif self.dont_miss is not None:
            n += 1
        return n

    @property
    def heroes(self) -> list[Item]:
        """Liste des items hero à rendre (top_signals prioritaire)."""
        if self.top_signals:
            return list(self.top_signals)
        if self.dont_miss is not None:
            return [self.dont_miss]
        return []
