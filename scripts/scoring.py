"""Re-scoring déterministe post-sourcing (issue #45).

Le score LLM reste un signal d'entrée, mais n'est plus le seul ranking :
engagement (likes/reposts, log-scale) + récence dans la fenêtre pèsent
de façon explicite et testable.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime

from scripts.models import Item

# Références pour normaliser l'engagement (≈ top viral X d'une fenêtre courte).
_REF_LIKES = 10_000.0
_REF_REPOSTS = 2_000.0

# Poids par défaut (somme = 1.0)
DEFAULT_W_LLM = 0.45
DEFAULT_W_ENGAGEMENT = 0.35
DEFAULT_W_RECENCY = 0.20


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def engagement_component(likes: int, reposts: int) -> float:
    """Score 0-1 a partir des likes/reposts (log1p, borne par refs)."""
    likes = max(0, int(likes or 0))
    reposts = max(0, int(reposts or 0))
    raw = math.log1p(likes) + math.log1p(reposts)
    denom = math.log1p(_REF_LIKES) + math.log1p(_REF_REPOSTS)
    return _clamp01(raw / denom if denom else 0.0)


def recency_component(
    published_at: datetime,
    window_start: datetime,
    window_end: datetime,
) -> float:
    """1.0 = fin de fenêtre (plus récent), 0.0 = début. Hors fenêtre → clamp."""
    start_ts = window_start.timestamp()
    end_ts = window_end.timestamp()
    if end_ts <= start_ts:
        return 0.5
    pub_ts = published_at.timestamp()
    return _clamp01((pub_ts - start_ts) / (end_ts - start_ts))


def composite_score(
    llm_score: float,
    likes: int,
    reposts: int,
    published_at: datetime,
    window_start: datetime,
    window_end: datetime,
    *,
    w_llm: float = DEFAULT_W_LLM,
    w_engagement: float = DEFAULT_W_ENGAGEMENT,
    w_recency: float = DEFAULT_W_RECENCY,
) -> float:
    """Combine LLM + engagement + recence -> score final 0-1."""
    llm = _clamp01(float(llm_score))
    eng = engagement_component(likes, reposts)
    rec = recency_component(published_at, window_start, window_end)
    # Web items ont souvent 0 likes : ne pas les écraser — boost léger eng via floor
    if likes == 0 and reposts == 0:
        # Garder un plancher pour ne pas tout écraser sous le LLM seul
        eng = max(eng, 0.25)
    return _clamp01(w_llm * llm + w_engagement * eng + w_recency * rec)


def rescore_items(
    items: list[Item],
    window_start: datetime,
    window_end: datetime,
) -> list[Item]:
    """Retourne de nouveaux Items avec `score` composite (immutable Item)."""
    out: list[Item] = []
    for it in items:
        new_score = composite_score(
            it.score,
            it.likes,
            it.reposts,
            it.published_at,
            window_start,
            window_end,
        )
        out.append(replace(it, score=new_score))
    return out
