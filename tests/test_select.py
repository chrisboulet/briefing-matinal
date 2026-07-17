"""Tests anti-monologue / cap auteur (issue #43) + sélection existante."""

from __future__ import annotations

from scripts.select import (
    apply_engagement_filter,
    normalize_handle,
    select_by_section,
    select_dont_miss,
)

SECTIONS_CFG = [
    {"id": "ai-tech", "label": "AI", "emoji": "🤖", "max_items": 2},
    {"id": "tesla", "label": "Tesla", "emoji": "🚗", "max_items": 1},
]


def test_select_by_section_respects_max_items(make_item):
    items = [
        make_item("AI 1", "https://e.com/1", section_id="ai-tech", score=0.9, source_handle="@a"),
        make_item("AI 2", "https://e.com/2", section_id="ai-tech", score=0.8, source_handle="@b"),
        make_item("AI 3", "https://e.com/3", section_id="ai-tech", score=0.7, source_handle="@c"),
        make_item("Tesla 1", "https://e.com/4", section_id="tesla", score=0.6, source_handle="@d"),
    ]
    out = select_by_section(items, SECTIONS_CFG)
    assert len(out["ai-tech"]) == 2
    assert len(out["tesla"]) == 1


def test_select_by_section_takes_top_score(make_item):
    items = [
        make_item("low", "https://e.com/1", section_id="ai-tech", score=0.3, source_handle="@a"),
        make_item("high", "https://e.com/2", section_id="ai-tech", score=0.9, source_handle="@b"),
    ]
    items_sorted = sorted(items, key=lambda x: -x.score)
    out = select_by_section(
        items_sorted, [{"id": "ai-tech", "label": "AI", "emoji": "🤖", "max_items": 1}]
    )
    assert out["ai-tech"][0].title == "high"


def test_select_by_section_empty_section(make_item):
    items = [make_item("AI 1", "https://e.com/1", section_id="ai-tech", score=0.9)]
    out = select_by_section(items, SECTIONS_CFG)
    assert out["ai-tech"]
    assert out["tesla"] == []


def test_author_cap_limits_same_handle_in_section(make_item):
    """4 posts @GaryMarcus AI → max 1 rendu (issue #43)."""
    items = [
        make_item(
            f"Marcus {i}",
            f"https://x.com/GaryMarcus/status/{i}",
            section_id="ai-tech",
            score=0.99 - i * 0.01,
            source_handle="@GaryMarcus",
        )
        for i in range(4)
    ]
    cfg = [{"id": "ai-tech", "label": "AI", "emoji": "🤖", "max_items": 6}]
    out = select_by_section(items, cfg, max_items_per_author=1)
    assert len(out["ai-tech"]) == 1
    assert out["ai-tech"][0].title == "Marcus 0"  # highest score


def test_author_cap_global_across_sections(make_item):
    """Un handle déjà pris en AI-tech ne repasse pas en politique."""
    items = [
        make_item(
            "AI take",
            "https://x.com/GaryMarcus/status/1",
            section_id="ai-tech",
            score=0.95,
            source_handle="@GaryMarcus",
        ),
        make_item(
            "Pol take",
            "https://x.com/GaryMarcus/status/2",
            section_id="politique",
            score=0.94,
            source_handle="@GaryMarcus",
        ),
        make_item(
            "Other pol",
            "https://x.com/WSJ/status/3",
            section_id="politique",
            score=0.5,
            source_handle="@WSJ",
        ),
    ]
    cfg = [
        {"id": "ai-tech", "label": "AI", "emoji": "🤖", "max_items": 3},
        {"id": "politique", "label": "Pol", "emoji": "🏛️", "max_items": 3},
    ]
    out = select_by_section(items, cfg, max_items_per_author=1)
    assert len(out["ai-tech"]) == 1
    assert out["ai-tech"][0].source_handle == "@GaryMarcus"
    assert len(out["politique"]) == 1
    assert out["politique"][0].source_handle == "@WSJ"


def test_author_cap_disabled_when_zero(make_item):
    items = [
        make_item("a", "https://e.com/1", section_id="ai-tech", score=0.9, source_handle="@same"),
        make_item("b", "https://e.com/2", section_id="ai-tech", score=0.8, source_handle="@same"),
    ]
    cfg = [{"id": "ai-tech", "label": "AI", "emoji": "🤖", "max_items": 2}]
    out = select_by_section(items, cfg, max_items_per_author=0)
    assert len(out["ai-tech"]) == 2


def test_author_cap_case_insensitive(make_item):
    items = [
        make_item("a", "https://e.com/1", section_id="ai-tech", score=0.9, source_handle="@GaryMarcus"),
        make_item("b", "https://e.com/2", section_id="ai-tech", score=0.8, source_handle="@garymarcus"),
    ]
    cfg = [{"id": "ai-tech", "label": "AI", "emoji": "🤖", "max_items": 2}]
    out = select_by_section(items, cfg, max_items_per_author=1)
    assert len(out["ai-tech"]) == 1
    assert normalize_handle("@GaryMarcus") == normalize_handle("@garymarcus")


def test_multi_author_still_fills_slots(make_item):
    items = [
        make_item(
            f"t{i}",
            f"https://e.com/{i}",
            section_id="ai-tech",
            score=0.9 - i * 0.05,
            source_handle=f"@u{i}",
        )
        for i in range(6)
    ]
    cfg = [{"id": "ai-tech", "label": "AI", "emoji": "🤖", "max_items": 4}]
    out = select_by_section(items, cfg, max_items_per_author=1)
    assert len(out["ai-tech"]) == 4
    handles = {it.source_handle for it in out["ai-tech"]}
    assert len(handles) == 4


def test_select_dont_miss_avoids_redundancy(make_item):
    a = make_item("a", "https://e.com/a", section_id="ai-tech", score=0.95, source_handle="@a")
    b = make_item("b", "https://e.com/b", section_id="ai-tech", score=0.50, source_handle="@b")
    selected = {"ai-tech": [a]}
    out = select_dont_miss([a, b], selected)
    assert out is not None
    assert out.id == b.id


def test_select_dont_miss_skips_same_author(make_item):
    a = make_item("a", "https://e.com/a", section_id="ai-tech", score=0.95, source_handle="@GaryMarcus")
    b = make_item("b", "https://e.com/b", section_id="ai-tech", score=0.90, source_handle="@GaryMarcus")
    c = make_item("c", "https://e.com/c", section_id="ai-tech", score=0.40, source_handle="@other")
    selected = {"ai-tech": [a]}
    out = select_dont_miss([a, b, c], selected, max_items_per_author=1)
    assert out is not None
    assert out.source_handle == "@other"


def test_select_dont_miss_no_duplicate_when_only_same_author_leftovers(make_item):
    """Leftovers same-author only → None (pas de hero = doublon section)."""
    a = make_item(
        "a",
        "https://e.com/a",
        section_id="ai-tech",
        score=0.95,
        source_handle="@GaryMarcus",
    )
    b = make_item(
        "b",
        "https://e.com/b",
        section_id="ai-tech",
        score=0.90,
        source_handle="@GaryMarcus",
    )
    selected = {"ai-tech": [a]}
    out = select_dont_miss([a, b], selected, max_items_per_author=1)
    assert out is None


def test_select_dont_miss_prefers_longform(make_item):
    article = make_item(
        "article", "https://lapresse.ca/x", section_id="business", score=0.80, source_handle="lapresse.ca"
    )
    video = make_item(
        "video",
        "https://www.youtube.com/watch?v=zzz",
        section_id="business",
        score=0.75,
        source_handle="@yt",
    )
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
