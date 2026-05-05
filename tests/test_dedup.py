"""Tests dédup : URL canonique + hash titre."""

from __future__ import annotations

from scripts.dedup import canonical_url, dedupe, title_hash


def test_canonical_url_strips_trackers():
    assert canonical_url("https://x.com/u/status/1?utm_source=share&s=20") == "https://x.com/u/status/1"


def test_canonical_url_lowercase_host_https_strip_slash():
    assert canonical_url("HTTP://Example.COM/Path/") == "https://example.com/Path"


def test_canonical_url_keeps_youtube_v_param():
    canon = canonical_url("https://www.youtube.com/watch?v=abc&utm_source=share")
    assert "v=abc" in canon
    assert "utm_source" not in canon


def test_canonical_url_idempotent():
    url = "https://www.lapresse.ca/article-2026-04-19?utm_source=newsletter"
    once = canonical_url(url)
    twice = canonical_url(once)
    assert once == twice


def test_title_hash_punctuation_insensitive():
    assert title_hash("Tesla FSD v14 deploys broadly!") == title_hash("tesla fsd v14, deploys broadly")


def test_title_hash_whitespace_insensitive():
    assert title_hash("Hello  World") == title_hash("Hello World")


def test_dedup_same_url_keeps_higher_score(make_item):
    a = make_item("title A", "https://x.com/u/status/1", score=0.9, source_handle="@first")
    b = make_item("title B", "https://x.com/u/status/1?utm_source=x", score=0.7, source_handle="@second")
    out = dedupe([a, b])
    assert len(out) == 1
    assert out[0].score == 0.9
    assert "@second" in out[0].alt_sources


def test_dedup_same_title_diff_url(make_item):
    a = make_item("Tesla FSD v14 deploys", "https://example.com/a", score=0.9)
    b = make_item("tesla fsd v14, deploys!", "https://example.com/b", score=0.5)
    out = dedupe([a, b])
    assert len(out) == 1
    assert out[0].score == 0.9


def test_dedup_unrelated_items_pass(make_item):
    a = make_item("AI release notes", "https://example.com/a", score=0.9)
    b = make_item("Tesla earnings", "https://example.com/b", score=0.8)
    out = dedupe([a, b])
    assert len(out) == 2


def test_dedup_fuzzy_gamestop_ebay(make_item):
    """Issue #35 : quasi-duplicates couvrant la même histoire doivent être consolidés."""
    a = make_item(
        "GameStop offers $56B to acquire eBay",
        "https://example.com/gamestop-1",
        section_id="business",
        score=0.85,
    )
    b = make_item(
        "GameStop CEO offers $56 billion for eBay",
        "https://example.com/gamestop-2",
        section_id="business",
        score=0.75,
    )
    result = dedupe([a, b])
    assert len(result) == 1
    assert result[0].score == 0.85


def test_dedup_fuzzy_does_not_merge_distinct_events(make_item):
    """Deux items partageant un acteur mais couvrant des événements distincts restent séparés."""
    a = make_item(
        "Tesla FSD v14 rollout next week",
        "https://example.com/tesla-fsd",
        section_id="tesla",
        score=0.8,
    )
    b = make_item(
        "Tesla recalls vehicles for brake failure",
        "https://example.com/tesla-recall",
        section_id="tesla",
        score=0.75,
    )
    result = dedupe([a, b])
    assert len(result) == 2


def test_dedup_fuzzy_does_not_merge_distinct_numeric_events(make_item):
    """Les nombres distinctifs (versions, trimestres, modèles) empêchent la fusion fuzzy."""
    pairs = [
        ("OpenAI releases GPT-5", "OpenAI releases GPT-6"),
        ("Tesla FSD v14 rollout next week", "Tesla FSD v15 rollout next week"),
        ("Tesla reports Q1 earnings", "Tesla reports Q2 earnings"),
    ]
    for idx, (left, right) in enumerate(pairs):
        a = make_item(left, f"https://example.com/numeric-{idx}-a", score=0.8)
        b = make_item(right, f"https://example.com/numeric-{idx}-b", score=0.75)
        assert len(dedupe([a, b])) == 2


def test_dedup_stable_order(make_item):
    items = [
        make_item("A", "https://example.com/a", score=0.5, published="2026-04-19T01:00:00Z"),
        make_item("B", "https://example.com/b", score=0.9, published="2026-04-19T02:00:00Z"),
        make_item("C", "https://example.com/c", score=0.5, published="2026-04-19T03:00:00Z"),
    ]
    out1 = dedupe(items)
    out2 = dedupe(list(reversed(items)))
    assert [it.id for it in out1] == [it.id for it in out2]
    assert out1[0].score == 0.9
