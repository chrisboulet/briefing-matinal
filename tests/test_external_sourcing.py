"""Tests phase 0 external sourcing (RSS / Tavily / Reddit / HN)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts.external_sourcing import (
    _clean_text,
    _in_window,
    _make_web_item,
    load_external_config,
    source_external,
)


def test_load_external_config_exists():
    cfg = load_external_config(Path("sources/external.json"))
    assert cfg.get("enabled") is True
    assert cfg.get("rss_feeds")
    assert cfg.get("tavily_queries")
    assert cfg.get("reddit")
    assert cfg.get("hackernews")


def test_make_web_item_stable_id():
    now = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
    a = _make_web_item(
        title="Hello",
        summary="Body",
        url="https://Example.com/path/?utm_source=x",
        section_id="ai-tech",
        handle="example.com",
        published_at=now,
        score=0.5,
    )
    b = _make_web_item(
        title="Hello",
        summary="Body",
        url="https://example.com/path",
        section_id="ai-tech",
        handle="example.com",
        published_at=now,
        score=0.5,
    )
    assert a.id == b.id
    assert a.source_type == "web"


def test_clean_text_strips_html():
    assert _clean_text("<p>Bonjour  <b>monde</b></p>") == "Bonjour monde"


def test_in_window_grace():
    end = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    start = end - timedelta(hours=13)
    early = start - timedelta(hours=3)
    assert _in_window(early, start, end) is True
    too_early = start - timedelta(hours=10)
    assert _in_window(too_early, start, end) is False


def test_source_external_rss_mocked(monkeypatch, tmp_path):
    rss_xml = b"""<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <title>Test</title>
      <item>
        <title>Article chaud AI</title>
        <link>https://example.com/ai-1</link>
        <description>Resume utile</description>
        <pubDate>Sat, 18 Jul 2026 22:00:00 GMT</pubDate>
      </item>
      <item>
        <title>Trop vieux</title>
        <link>https://example.com/old</link>
        <pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>
      </item>
    </channel></rss>"""

    class FakeResp:
        content = rss_xml
        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *a, **k):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def get(self, url):
            return FakeResp()

    monkeypatch.setattr("scripts.external_sourcing.httpx.Client", FakeClient)

    cfg = {
        "enabled": True,
        "rss_feeds": [
            {"url": "https://example.com/rss", "section_id": "ai-tech", "handle": "example.com"}
        ],
        "google_news": [],
        "tavily_queries": [],
        "reddit": [],
        "hackernews": [],
        "timeouts": {"rss_s": 5},
        "max_items_per_source": 5,
    }
    cfg_path = tmp_path / "external.json"
    import json
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")

    end = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    start = end - timedelta(hours=13)
    result = source_external(start, end, {"ai-tech"}, config_path=cfg_path)
    assert len(result.items) == 1
    assert result.items[0].title == "Article chaud AI"
    assert result.items[0].section_id == "ai-tech"


def test_tavily_skipped_without_key(monkeypatch, tmp_path):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    cfg = {
        "enabled": True,
        "rss_feeds": [],
        "google_news": [],
        "tavily_queries": [
            {"query": "AI news", "section_id": "ai-tech", "max_results": 3}
        ],
        "reddit": [],
        "hackernews": [],
        "timeouts": {},
        "max_items_per_source": 5,
    }
    import json
    cfg_path = tmp_path / "external.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    end = datetime.now(tz=UTC)
    start = end - timedelta(hours=12)
    result = source_external(start, end, {"ai-tech"}, config_path=cfg_path)
    assert result.items == []
    assert any("TAVILY_API_KEY" in w for w in result.warnings)
