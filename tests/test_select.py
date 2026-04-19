"""Tests sélection par section + sections hero."""

from __future__ import annotations

from scripts.select import (
    apply_engagement_filter,
    select_by_section,
    select_dont_miss,
)

SECTIONS_CFG = [
    {"id": "ai-tech", "label": "AI", "emoji": "🤖", "max_items": 2},
    {"id": "tesla", "label": "Tesla", "emoji": "🚗", "max_items": 1},
]


def test_select_by_section_respects_max_items(make_item):
    items = [
        make_item("AI 1", "https://e.com/1", section_id="ai-tech", score=0.9),
        make_item("AI 2", "https://e.com/2", section_id="ai-tech", score=0.8),
        make_item("AI 3", "https://e.com/3", section_id="ai-tech", score=0.7),
        make_item("Tesla 1", "https://e.com/4", section_id="tesla", score=0.6),
    ]
    out = select_by_section(items, SECTIONS_CFG)
    assert len(out["ai-tech"]) == 2
    assert len(out["tesla"]) == 1


def test_select_by_section_takes_top_score(make_item):
    items = [
        make_item("low", "https://e.com/1", section_id="ai-tech", score=0.3),
        make_item("high", "https://e.com/2", section_id="ai-tech", score=0.9),
    ]
    items_sorted = sorted(items, key=lambda x: -x.score)
    out = select_by_section(items_sorted, [{"id": "ai-tech", "label": "AI", "emoji": "🤖", "max_items": 1}])
    assert out["ai-tech"][0].title == "high"


def test_select_by_section_empty_section(make_item):
    items = [make_item("AI 1", "https://e.com/1", section_id="ai-tech", score=0.9)]
    out = select_by_section(items, SECTIONS_CFG)
    assert out["ai-tech"]
    assert out["tesla"] == []


def test_select_dont_miss_avoids_redundancy(make_item):
    a = make_item("a", "https://e.com/a", section_id="ai-tech", score=0.95)
    b = make_item("b", "https://e.com/b", section_id="ai-tech", score=0.50)
    selected = {"ai-tech": [a]}
    out = select_dont_miss([a, b], selected)
    assert out is not None
    assert out.id == b.id


def test_select_dont_miss_prefers_longform(make_item):
    article = make_item("article", "https://lapresse.ca/x", section_id="business", score=0.80)
    video = make_item("video", "https://www.youtube.com/watch?v=zzz", section_id="business", score=0.75)
    out = select_dont_miss([article, video], selected={})
    assert out is not None
    assert out.id == video.id


def test_select_dont_miss_returns_none_if_empty(make_item):
    a = make_item("a", "https://e.com/a", section_id="ai-tech", score=0.9)
    selected = {"ai-tech": [a]}
    out = select_dont_miss([a], selected)
    assert out is None


def test_engagement_filter_drops_low_engagement_x(make_item):
    high = make_item("high", "https://e.com/1", source_type="x_account", likes=1000, reposts=200)
    low = make_item("low", "https://e.com/2", source_type="x_account", likes=10, reposts=2)
    out = apply_engagement_filter([high, low], {"likes": 50, "reposts": 10})
    assert len(out) == 1
    assert out[0].title == "high"


def test_engagement_filter_drops_replies_and_retweets(make_item):
    reply = make_item("r", "https://e.com/1", source_type="x_account", likes=999, is_reply=True)
    rt = make_item("rt", "https://e.com/2", source_type="x_account", likes=999, is_retweet=True)
    out = apply_engagement_filter([reply, rt], {"likes": 50, "reposts": 10})
    assert out == []


def test_engagement_filter_keeps_web_items_unconditionally(make_item):
    web = make_item("web", "https://e.com/x", source_type="web", likes=0, reposts=0)
    out = apply_engagement_filter([web], {"likes": 50, "reposts": 10})
    assert len(out) == 1


def test_engagement_filter_or_logic(make_item):
    likes_only = make_item("l", "https://e.com/1", source_type="x_account", likes=60, reposts=0)
    reposts_only = make_item("r", "https://e.com/2", source_type="x_account", likes=0, reposts=15)
    out = apply_engagement_filter([likes_only, reposts_only], {"likes": 50, "reposts": 10})
    assert len(out) == 2
