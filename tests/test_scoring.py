"""Tests scoring composite (issue #45)."""

from __future__ import annotations

from datetime import UTC, datetime

from scripts.scoring import (
    composite_score,
    engagement_component,
    rescore_items,
)


def test_engagement_higher_likes_scores_higher():
    low = engagement_component(16, 2)
    high = engagement_component(800, 80)
    assert high > low


def test_composite_prefers_high_engagement_all_else_equal():
    start = datetime(2026, 7, 16, 21, 30, tzinfo=UTC)
    end = datetime(2026, 7, 17, 10, 30, tzinfo=UTC)
    mid = datetime(2026, 7, 17, 4, 0, tzinfo=UTC)
    low = composite_score(0.8, likes=16, reposts=2, published_at=mid, window_start=start, window_end=end)
    high = composite_score(0.8, likes=800, reposts=80, published_at=mid, window_start=start, window_end=end)
    assert high > low


def test_rescore_items_rewrites_score(make_item):
    start = datetime(2026, 7, 16, 21, 30, tzinfo=UTC)
    end = datetime(2026, 7, 17, 10, 30, tzinfo=UTC)
    low = make_item(
        "low eng",
        "https://e.com/1",
        score=0.9,
        likes=16,
        reposts=2,
        published="2026-07-17T04:00:00Z",
    )
    high = make_item(
        "high eng",
        "https://e.com/2",
        score=0.5,
        likes=2000,
        reposts=400,
        published="2026-07-17T04:00:00Z",
    )
    out = rescore_items([low, high], start, end)
    by_title = {it.title: it.score for it in out}
    assert by_title["high eng"] > by_title["low eng"]


def test_web_item_zero_engagement_not_crushed(make_item):
    start = datetime(2026, 7, 16, 21, 30, tzinfo=UTC)
    end = datetime(2026, 7, 17, 10, 30, tzinfo=UTC)
    web = make_item(
        "web",
        "https://lapresse.ca/x",
        source_type="web",
        score=0.8,
        likes=0,
        reposts=0,
        published="2026-07-17T04:00:00Z",
    )
    out = rescore_items([web], start, end)
    # Doit rester dans une plage utilisable (pas ~0.2)
    assert out[0].score >= 0.4
