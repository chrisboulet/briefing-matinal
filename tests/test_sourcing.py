"""Tests sourcing : orchestration appels xAI, conversion items, dégradation gracieuse."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from scripts.dedup import canonical_url, item_id
from scripts.models import Item
from scripts.sourcing import MAX_HANDLES_PER_CALL, source_briefing
from scripts.xai_client import (
    XAIAuthError,
    XAIResponse,
    XAIUnavailable,
    XAIUsage,
)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def base_config() -> dict[str, Any]:
    """Config minimale alignée sur sources/comptes.json."""
    return {
        "schema_version": 1,
        "comptes_x": [f"@user{i:02d}" for i in range(1, 16)],  # 15 handles
        "recherches_thematiques": [
            {"theme": "AI", "query": "AI LLM", "section_id": "ai-tech"},
            {"theme": "Tesla", "query": "Tesla FSD", "section_id": "tesla"},
            {"theme": "SpaceX", "query": "Starship", "section_id": "spacex"},
        ],
        "sources_web": ["lapresse.ca", "lesaffaires.com"],
        "sections": [
            {"id": "ai-tech", "label": "AI", "emoji": "AI", "max_items": 3},
            {"id": "tesla", "label": "Tesla", "emoji": "T", "max_items": 2},
            {"id": "spacex", "label": "SpaceX", "emoji": "S", "max_items": 2},
        ],
        "engagement_min": {"likes": 50, "reposts": 10},
    }


@pytest.fixture
def window() -> tuple[datetime, datetime]:
    start = datetime(2026, 4, 18, 22, 0, 0, tzinfo=UTC)
    end = datetime(2026, 4, 19, 10, 0, 0, tzinfo=UTC)
    return start, end


@pytest.fixture
def raw_item_factory() -> Callable[..., dict[str, Any]]:
    def _make(
        title: str = "Item title",
        url: str = "https://example.com/post/1",
        section_id: str = "ai-tech",
        source_type: str = "x_account",
        source_handle: str = "@karpathy",
        published_at: str = "2026-04-19T03:00:00Z",
        score: float = 0.85,
        likes: int = 250,
        reposts: int = 50,
    ) -> dict[str, Any]:
        return {
            "title": title,
            "summary": "Quelque chose s'est passé.",
            "canonical_url": url,
            "source_type": source_type,
            "source_handle": source_handle,
            "published_at": published_at,
            "score": score,
            "section_id": section_id,
            "likes": likes,
            "reposts": reposts,
        }

    return _make


class StubClient:
    """Stub minimal de XAIClient — enregistre les appels et retourne des réponses scriptées."""

    def __init__(self, responses_or_factory):
        # Soit une liste de réponses, soit un callable(call_idx, args) -> XAIResponse | Exception
        self._responses = responses_or_factory
        self.calls: list[dict[str, Any]] = []
        self.model = "stub"

    def call(self, *, system_prompt, user_prompt, tool, tool_params, prompt_label):
        record = {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "tool": tool,
            "tool_params": tool_params,
            "prompt_label": prompt_label,
        }
        self.calls.append(record)
        idx = len(self.calls) - 1
        if callable(self._responses):
            value = self._responses(idx, record)
        else:
            value = self._responses[idx] if idx < len(self._responses) else _ok_response()
        if isinstance(value, BaseException):
            raise value
        return value


def _ok_response(
    items: list[dict] | None = None,
    warnings: list[str] | None = None,
    input_tokens: int = 100,
    output_tokens: int = 50,
    tool_calls: int = 2,
) -> XAIResponse:
    return XAIResponse(
        parsed_output={"items": items or [], "warnings": warnings or []},
        usage=XAIUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tool_calls=tool_calls,
        ),
        duration_ms=42,
        model="stub",
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_source_briefing_returns_sourcing_result(
    base_config, window, raw_item_factory
) -> None:
    start, end = window
    client = StubClient(lambda i, r: _ok_response(items=[raw_item_factory()]))

    result = source_briefing(
        client=client,
        config=base_config,
        window_start=start,
        window_end=end,
        prompts_dir=PROMPTS_DIR,
    )

    assert result.warnings == []
    assert len(result.items) > 0
    assert all(isinstance(it, Item) for it in result.items)
    assert isinstance(result.total_usage, XAIUsage)


def test_all_three_source_types_invoked(base_config, window) -> None:
    start, end = window
    client = StubClient(lambda i, r: _ok_response())
    source_briefing(
        client=client, config=base_config,
        window_start=start, window_end=end,
        prompts_dir=PROMPTS_DIR,
    )
    tools_used = {c["tool"] for c in client.calls}
    assert tools_used == {"x_search", "web_search"}
    labels = [c["prompt_label"] for c in client.calls]
    assert any(label.startswith("search_accounts_") for label in labels)
    assert any(label.startswith("search_theme_") for label in labels)
    assert any(label.startswith("search_web_") for label in labels)


def test_15_accounts_split_into_two_calls(base_config, window) -> None:
    start, end = window
    client = StubClient(lambda i, r: _ok_response())
    source_briefing(
        client=client, config=base_config,
        window_start=start, window_end=end,
        prompts_dir=PROMPTS_DIR,
    )
    account_calls = [c for c in client.calls if c["prompt_label"].startswith("search_accounts_")]
    assert len(account_calls) == 2
    # Premier batch = MAX_HANDLES_PER_CALL handles, second batch = reste.
    assert len(account_calls[0]["tool_params"]["allowed_x_handles"]) == MAX_HANDLES_PER_CALL
    assert len(account_calls[1]["tool_params"]["allowed_x_handles"]) == 15 - MAX_HANDLES_PER_CALL


def test_n_themes_yields_n_calls(base_config, window) -> None:
    start, end = window
    client = StubClient(lambda i, r: _ok_response())
    source_briefing(
        client=client, config=base_config,
        window_start=start, window_end=end,
        prompts_dir=PROMPTS_DIR,
    )
    theme_calls = [c for c in client.calls if c["prompt_label"].startswith("search_theme_")]
    assert len(theme_calls) == len(base_config["recherches_thematiques"])


def test_web_yields_one_call(base_config, window) -> None:
    """base_config a 2 domaines → 1 seul batch (≤ MAX_DOMAINS_PER_CALL=5)."""
    start, end = window
    client = StubClient(lambda i, r: _ok_response())
    source_briefing(
        client=client, config=base_config,
        window_start=start, window_end=end,
        prompts_dir=PROMPTS_DIR,
    )
    web_calls = [c for c in client.calls if c["prompt_label"].startswith("search_web_")]
    assert len(web_calls) == 1
    assert web_calls[0]["prompt_label"] == "search_web_1"


def test_web_batched_when_more_than_5_domains(base_config, window) -> None:
    """Issue #31 : xAI web_search plafonne à 5 domaines. 8 domaines → 2 batches
    (5 + 3), même pattern que les comptes X splittés par MAX_HANDLES_PER_CALL."""
    start, end = window
    cfg = {
        **base_config,
        "sources_web": [f"site{i}.example.com" for i in range(8)],
    }
    client = StubClient(lambda i, r: _ok_response())
    source_briefing(
        client=client, config=cfg,
        window_start=start, window_end=end,
        prompts_dir=PROMPTS_DIR,
    )
    web_calls = [c for c in client.calls if c["prompt_label"].startswith("search_web_")]
    assert len(web_calls) == 2
    # Chaque batch doit contenir AU PLUS 5 domaines
    for call in web_calls:
        domains = call["tool_params"]["allowed_domains"]
        assert len(domains) <= 5, f"batch {call['prompt_label']} a {len(domains)} domaines"
    # Tous les domaines doivent être couverts, sans doublon
    all_sent = [d for call in web_calls for d in call["tool_params"]["allowed_domains"]]
    assert sorted(all_sent) == sorted(cfg["sources_web"])
    assert len(set(all_sent)) == len(all_sent)  # pas de doublon


def test_web_no_call_when_sources_empty(base_config, window) -> None:
    """sources_web vide → aucun appel web (pas de 400 avec allowed_domains=[])."""
    start, end = window
    cfg = {**base_config, "sources_web": []}
    client = StubClient(lambda i, r: _ok_response())
    source_briefing(
        client=client, config=cfg,
        window_start=start, window_end=end,
        prompts_dir=PROMPTS_DIR,
    )
    web_calls = [c for c in client.calls if c["prompt_label"].startswith("search_web_")]
    assert web_calls == []


def test_total_call_count(base_config, window) -> None:
    start, end = window
    client = StubClient(lambda i, r: _ok_response())
    source_briefing(
        client=client, config=base_config,
        window_start=start, window_end=end,
        prompts_dir=PROMPTS_DIR,
    )
    n_themes = len(base_config["recherches_thematiques"])
    # base_config : 15 handles / 10 = 2 batches accounts + N themes + 1 web batch
    # (2 domaines → 1 seul batch, ≤ 5)
    expected = 2 + n_themes + 1
    assert len(client.calls) == expected


# ---------------------------------------------------------------------------
# Item conversion
# ---------------------------------------------------------------------------


def test_canonical_url_applied_strips_trackers(
    base_config, window, raw_item_factory
) -> None:
    start, end = window
    raw = raw_item_factory(url="https://x.com/u/status/1?utm_source=share&s=20")
    # Use only a single section/no themes for simplicity.
    cfg = {**base_config, "comptes_x": ["@only"], "recherches_thematiques": [], "sources_web": []}
    client = StubClient(lambda i, r: _ok_response(items=[raw] if i == 0 else []))

    result = source_briefing(
        client=client, config=cfg,
        window_start=start, window_end=end,
        prompts_dir=PROMPTS_DIR,
    )

    assert len(result.items) == 1
    item = result.items[0]
    assert item.canonical_url == canonical_url(raw["canonical_url"])
    assert "utm_source" not in item.canonical_url


def test_item_id_derived_from_canonical_url(
    base_config, window, raw_item_factory
) -> None:
    start, end = window
    raw = raw_item_factory(url="https://example.com/article/X?utm_source=foo")
    cfg = {**base_config, "comptes_x": ["@a"], "recherches_thematiques": [], "sources_web": []}
    client = StubClient(lambda i, r: _ok_response(items=[raw] if i == 0 else []))

    result = source_briefing(
        client=client, config=cfg,
        window_start=start, window_end=end,
        prompts_dir=PROMPTS_DIR,
    )

    item = result.items[0]
    assert item.id == item_id(canonical_url(raw["canonical_url"]))


def test_published_at_parsed_with_z_suffix(
    base_config, window, raw_item_factory
) -> None:
    start, end = window
    raw = raw_item_factory(published_at="2026-04-19T03:00:00Z")
    cfg = {**base_config, "comptes_x": ["@a"], "recherches_thematiques": [], "sources_web": []}
    client = StubClient(lambda i, r: _ok_response(items=[raw] if i == 0 else []))

    result = source_briefing(
        client=client, config=cfg,
        window_start=start, window_end=end,
        prompts_dir=PROMPTS_DIR,
    )

    item = result.items[0]
    assert item.published_at == datetime(2026, 4, 19, 3, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Defensive filtering
# ---------------------------------------------------------------------------


def test_item_outside_window_dropped(
    base_config, window, raw_item_factory
) -> None:
    start, end = window
    cfg = {**base_config, "comptes_x": ["@a"], "recherches_thematiques": [], "sources_web": []}
    out_of_window = raw_item_factory(published_at="2025-01-01T00:00:00Z")
    in_window = raw_item_factory(
        url="https://e.com/in-window",
        published_at="2026-04-19T03:00:00Z",
    )
    client = StubClient(
        lambda i, r: _ok_response(items=[out_of_window, in_window]) if i == 0 else _ok_response()
    )

    result = source_briefing(
        client=client, config=cfg,
        window_start=start, window_end=end,
        prompts_dir=PROMPTS_DIR,
    )

    assert len(result.items) == 1
    assert result.items[0].canonical_url.endswith("/in-window")
    assert any("outside window" in w for w in result.warnings)


def test_item_with_unknown_section_id_dropped(
    base_config, window, raw_item_factory
) -> None:
    start, end = window
    cfg = {**base_config, "comptes_x": ["@a"], "recherches_thematiques": [], "sources_web": []}
    bad = raw_item_factory(section_id="non-existent")
    good = raw_item_factory(url="https://e.com/good", section_id="ai-tech")
    client = StubClient(
        lambda i, r: _ok_response(items=[bad, good]) if i == 0 else _ok_response()
    )

    result = source_briefing(
        client=client, config=cfg,
        window_start=start, window_end=end,
        prompts_dir=PROMPTS_DIR,
    )

    assert len(result.items) == 1
    assert result.items[0].section_id == "ai-tech"
    assert any("unknown section_id" in w for w in result.warnings)


def test_malformed_item_dropped_others_pass(
    base_config, window, raw_item_factory
) -> None:
    start, end = window
    cfg = {**base_config, "comptes_x": ["@a"], "recherches_thematiques": [], "sources_web": []}
    malformed = {"title": "broken"}  # missing required keys
    good = raw_item_factory(url="https://e.com/ok")
    client = StubClient(
        lambda i, r: _ok_response(items=[malformed, good]) if i == 0 else _ok_response()
    )

    result = source_briefing(
        client=client, config=cfg,
        window_start=start, window_end=end,
        prompts_dir=PROMPTS_DIR,
    )

    assert len(result.items) == 1
    assert result.items[0].canonical_url.endswith("/ok")
    assert any("skipped malformed item" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------


def test_one_call_fails_others_proceed(
    base_config, window, raw_item_factory
) -> None:
    start, end = window
    cfg = {**base_config, "comptes_x": ["@a"], "recherches_thematiques": [], "sources_web": ["e.com"]}

    def factory(idx, rec):
        if idx == 0:
            return XAIUnavailable("downstream broken")
        return _ok_response(items=[raw_item_factory(url=f"https://e.com/{idx}")])

    client = StubClient(factory)

    result = source_briefing(
        client=client, config=cfg,
        window_start=start, window_end=end,
        prompts_dir=PROMPTS_DIR,
    )

    # web call (idx=1) should still succeed
    assert len(result.items) == 1
    assert any("XAIUnavailable" in w for w in result.warnings)


def test_all_calls_fail_returns_empty_with_warnings(
    base_config, window
) -> None:
    start, end = window
    cfg = {**base_config, "comptes_x": ["@a"], "recherches_thematiques": [], "sources_web": ["e.com"]}
    client = StubClient(lambda i, r: XAIUnavailable("down"))

    result = source_briefing(
        client=client, config=cfg,
        window_start=start, window_end=end,
        prompts_dir=PROMPTS_DIR,
    )

    assert result.items == []
    assert len(result.warnings) >= 2  # one per failed call (accounts + web)
    assert result.total_usage == XAIUsage(0, 0, 0)


def test_mix_success_and_failure(
    base_config, window, raw_item_factory
) -> None:
    start, end = window
    cfg = {**base_config, "comptes_x": ["@a"], "recherches_thematiques": [], "sources_web": ["e.com"]}

    def factory(idx, rec):
        if idx == 0:  # accounts call OK
            return _ok_response(items=[raw_item_factory()])
        return XAIAuthError("nope")  # web call fails

    client = StubClient(factory)

    result = source_briefing(
        client=client, config=cfg,
        window_start=start, window_end=end,
        prompts_dir=PROMPTS_DIR,
    )

    assert len(result.items) == 1
    assert any("XAIAuthError" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Usage aggregation
# ---------------------------------------------------------------------------


def test_usage_aggregation_sums_correctly(base_config, window) -> None:
    start, end = window
    cfg = {**base_config, "comptes_x": ["@a"], "recherches_thematiques": [], "sources_web": ["e.com"]}

    def factory(idx, rec):
        return _ok_response(input_tokens=100, output_tokens=50, tool_calls=2)

    client = StubClient(factory)

    result = source_briefing(
        client=client, config=cfg,
        window_start=start, window_end=end,
        prompts_dir=PROMPTS_DIR,
    )

    n_calls = len(client.calls)
    assert result.total_usage.input_tokens == 100 * n_calls
    assert result.total_usage.output_tokens == 50 * n_calls
    assert result.total_usage.tool_calls == 2 * n_calls


# ---------------------------------------------------------------------------
# Tool params per call type
# ---------------------------------------------------------------------------


def test_accounts_call_tool_params(base_config, window) -> None:
    start, end = window
    client = StubClient(lambda i, r: _ok_response())
    source_briefing(
        client=client, config=base_config,
        window_start=start, window_end=end,
        prompts_dir=PROMPTS_DIR,
    )
    accounts_call = next(c for c in client.calls if c["prompt_label"].startswith("search_accounts_"))
    assert accounts_call["tool"] == "x_search"
    params = accounts_call["tool_params"]
    assert "allowed_x_handles" in params
    assert all(not h.startswith("@") for h in params["allowed_x_handles"])
    assert "from_date" in params and "to_date" in params


def test_theme_call_tool_params(base_config, window) -> None:
    start, end = window
    client = StubClient(lambda i, r: _ok_response())
    source_briefing(
        client=client, config=base_config,
        window_start=start, window_end=end,
        prompts_dir=PROMPTS_DIR,
    )
    theme_call = next(c for c in client.calls if c["prompt_label"].startswith("search_theme_"))
    assert theme_call["tool"] == "x_search"
    params = theme_call["tool_params"]
    assert "allowed_x_handles" not in params
    assert "from_date" in params and "to_date" in params


def test_web_call_tool_params(base_config, window) -> None:
    start, end = window
    client = StubClient(lambda i, r: _ok_response())
    source_briefing(
        client=client, config=base_config,
        window_start=start, window_end=end,
        prompts_dir=PROMPTS_DIR,
    )
    web_call = next(c for c in client.calls if c["prompt_label"].startswith("search_web_"))
    assert web_call["tool"] == "web_search"
    params = web_call["tool_params"]
    assert "allowed_domains" in params
    # base_config a ≤ 5 domaines → un seul batch contient TOUS les domaines
    assert params["allowed_domains"] == base_config["sources_web"]
    assert "from_date" in params and "to_date" in params


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------


def test_each_call_has_non_empty_system_prompt(base_config, window) -> None:
    start, end = window
    client = StubClient(lambda i, r: _ok_response())
    source_briefing(
        client=client, config=base_config,
        window_start=start, window_end=end,
        prompts_dir=PROMPTS_DIR,
    )
    for call in client.calls:
        assert call["system_prompt"]
        assert isinstance(call["system_prompt"], str)
        assert len(call["system_prompt"]) > 50


def test_accounts_user_prompt_mentions_handles(base_config, window) -> None:
    start, end = window
    client = StubClient(lambda i, r: _ok_response())
    source_briefing(
        client=client, config=base_config,
        window_start=start, window_end=end,
        prompts_dir=PROMPTS_DIR,
    )
    accounts_call = next(c for c in client.calls if c["prompt_label"].startswith("search_accounts_"))
    user_prompt = accounts_call["user_prompt"]
    expected_handles = accounts_call["tool_params"]["allowed_x_handles"]
    for h in expected_handles:
        assert h in user_prompt


def test_theme_user_prompt_mentions_theme_name(base_config, window) -> None:
    start, end = window
    client = StubClient(lambda i, r: _ok_response())
    source_briefing(
        client=client, config=base_config,
        window_start=start, window_end=end,
        prompts_dir=PROMPTS_DIR,
    )
    theme_calls = [c for c in client.calls if c["prompt_label"].startswith("search_theme_")]
    for call in theme_calls:
        # The label is "search_theme_<theme>"; the theme name should appear in the rendered prompt.
        theme_name = call["prompt_label"].removeprefix("search_theme_")
        assert theme_name in call["user_prompt"]


# ---------------------------------------------------------------------------
# Issue #17 : adapter _to_item pour shape native xAI
# ---------------------------------------------------------------------------


def test_to_item_accepts_xai_native_shape(base_config, window):
    """xAI tool results have shape {post_id, author, content, engagement, link}
    at the top level. The adapter must map them to our Item fields."""
    from scripts.sourcing import _to_item

    _, end = window
    raw_native = {
        "post_id": 2045842784033059114,
        "author": "Financial Times (@FT)",
        "content": "Recursive Superintelligence has been valued at $500B in latest round.",
        "engagement": {"likes": 312, "reposts": 42, "views": 87491},
        "link": "https://www.ft.com/content/a92bf04b-bbac-400f-9554-5b1c70957ad4",
    }
    item = _to_item(
        raw_native,
        default_section_id="ai-tech",
        default_source_type="x_search",
        fallback_published_at=end,
    )
    assert item.canonical_url == "https://www.ft.com/content/a92bf04b-bbac-400f-9554-5b1c70957ad4"
    assert item.source_handle == "@FT"  # parsed from "Financial Times (@FT)"
    assert item.likes == 312
    assert item.reposts == 42
    assert item.section_id == "ai-tech"
    assert item.source_type == "x_search"
    assert item.published_at == end  # fallback
    assert "Recursive Superintelligence" in item.summary
    assert item.score == 0.5  # default when missing


def test_to_item_prefers_ideal_over_native_fields(base_config):
    """When BOTH ideal and native fields are present, ideal wins (prompt worked)."""
    from scripts.sourcing import _to_item

    raw = {
        # Ideal shape
        "title": "Ideal title",
        "summary": "Ideal summary",
        "canonical_url": "https://ideal.example.com/a",
        "source_handle": "@ideal",
        "source_type": "x_account",
        "published_at": "2026-04-19T03:00:00Z",
        "score": 0.85,
        "section_id": "ai-tech",
        "likes": 100,
        "reposts": 20,
        # Also native fields (should be ignored)
        "content": "Native content",
        "link": "https://native.example.com/b",
        "author": "Native Name (@native)",
        "engagement": {"likes": 999, "reposts": 999},
    }
    item = _to_item(raw)
    assert item.title == "Ideal title"
    assert item.canonical_url == "https://ideal.example.com/a"
    assert item.source_handle == "@ideal"
    assert item.likes == 100


def test_to_item_raises_on_missing_section_id_and_no_default(base_config):
    from scripts.sourcing import _to_item

    raw_native = {
        "content": "A tweet",
        "link": "https://x.com/a/status/1",
        "author": "@a",
    }
    # No default_section_id → must raise
    with pytest.raises(KeyError, match="section_id"):
        _to_item(raw_native, default_source_type="x_account", fallback_published_at=datetime.now(UTC))


def test_to_item_parses_bare_author_without_handle(base_config):
    from scripts.sourcing import _to_item

    raw = {
        "content": "Post text",
        "link": "https://x.com/a/status/1",
        "author": "Some Org Without Handle",
    }
    item = _to_item(
        raw, default_section_id="ai-tech", default_source_type="x_search",
        fallback_published_at=datetime.now(UTC),
    )
    # No @ in author → falls back to the raw author string
    assert item.source_handle == "Some Org Without Handle"


def test_theme_call_injects_default_section_id(base_config, window):
    """Theme calls pass default_section_id so native-shape items still classify."""
    start, end = window
    cfg = {**base_config, "comptes_x": [], "sources_web": [],
           "recherches_thematiques": [
               {"theme": "Tesla", "query": "tesla", "section_id": "tesla"}
           ]}
    native_item = {
        "post_id": 1,
        "author": "Tesla (@Tesla)",
        "content": "Cybertruck production hits milestone",
        "engagement": {"likes": 5000, "reposts": 800},
        "link": "https://x.com/Tesla/status/1",
    }
    # Only one call expected (no accounts, no themes but Tesla, no web): actually
    # 1 theme + 1 web = 2 calls. First is theme (Tesla), second is web.
    client = StubClient([_ok_response(items=[native_item]), _ok_response()])

    result = source_briefing(
        client=client, config=cfg,
        window_start=start, window_end=end,
        prompts_dir=PROMPTS_DIR,
    )
    assert len(result.items) == 1
    item = result.items[0]
    assert item.section_id == "tesla"  # from default, LLM didn't provide
    assert item.source_type == "x_search"
    assert item.source_handle == "@Tesla"


def test_account_call_drops_items_without_section_id(base_config, window):
    """Account calls don't inject default_section_id — items without section_id
    are dropped with a warning (can't classify across 15 handles deterministically)."""
    start, end = window
    cfg = {**base_config, "comptes_x": ["@karpathy"], "recherches_thematiques": [], "sources_web": []}
    native_no_section = {
        "post_id": 1,
        "author": "@karpathy",
        "content": "Some tweet",
        "engagement": {"likes": 1000, "reposts": 100},
        "link": "https://x.com/karpathy/status/1",
    }
    client = StubClient([_ok_response(items=[native_no_section]), _ok_response()])

    result = source_briefing(
        client=client, config=cfg,
        window_start=start, window_end=end,
        prompts_dir=PROMPTS_DIR,
    )
    assert result.items == []
    assert any("section_id" in w for w in result.warnings)


def test_warnings_string_does_not_iterate_char_by_char(base_config, window):
    """Issue #17 bug 2 : if `parsed_output["warnings"]` is a string, it
    must not be iterated char-by-char when building all_warnings."""
    start, end = window
    cfg = {**base_config, "comptes_x": ["@a"], "recherches_thematiques": [], "sources_web": []}
    # Simulate xai_client normalization result: string wrapped in list.
    resp_with_str_warning = XAIResponse(
        parsed_output={"items": [], "warnings": ["Aucun résultat pour @a"]},
        usage=XAIUsage(input_tokens=1, output_tokens=1, tool_calls=0),
        duration_ms=10, model="stub",
    )
    client = StubClient([resp_with_str_warning, _ok_response()])

    result = source_briefing(
        client=client, config=cfg,
        window_start=start, window_end=end,
        prompts_dir=PROMPTS_DIR,
    )
    # Must see ONE warning with the full message, not 20+ single-char warnings.
    matching = [w for w in result.warnings if "Aucun résultat pour @a" in w]
    assert len(matching) == 1
    # Also assert no single-char warnings
    assert not any(len(w.split(": ", 1)[-1]) == 1 for w in result.warnings)


# ---------------------------------------------------------------------------
# Issue #19 partie 2 : dérivation de titre propre depuis content brut
# ---------------------------------------------------------------------------


def test_derive_title_strips_thread_marker_emoji():
    from scripts.sourcing import _derive_title_from_content

    title = _derive_title_from_content("🧵 Some interesting new research came out.")
    assert title == "Some interesting new research came out."


def test_derive_title_strips_thread_numbering():
    from scripts.sourcing import _derive_title_from_content

    title = _derive_title_from_content("1/12 New longitudinal study on NMN shows gains.")
    assert title == "New longitudinal study on NMN shows gains."


def test_derive_title_strips_combined_thread_markers():
    """Cas réel santé observé : '🧵 1/12 ...' combine les deux."""
    from scripts.sourcing import _derive_title_from_content

    title = _derive_title_from_content(
        "🧵 1/12 New longitudinal study on NMN shows significant biomarker improvements."
    )
    assert title.startswith("New longitudinal study on NMN")
    assert "🧵" not in title
    assert "1/12" not in title


def test_derive_title_strips_trailing_url():
    from scripts.sourcing import _derive_title_from_content

    title = _derive_title_from_content("Interesting finding worth reading. https://t.co/abc123")
    assert title == "Interesting finding worth reading."
    assert "t.co" not in title
    assert "http" not in title


def test_derive_title_keeps_first_line_only_for_threads():
    from scripts.sourcing import _derive_title_from_content

    title = _derive_title_from_content(
        "Main headline here.\n\nDetails and second paragraph here."
    )
    assert title == "Main headline here."


def test_derive_title_truncates_at_sentence_boundary_when_long():
    from scripts.sourcing import _derive_title_from_content

    # Content > 160 chars : la 1re phrase seule fait ~120 char, la suite pousse
    # le total au-delà → doit couper à la frontière de phrase.
    content = (
        "This is a reasonably long informative first sentence that stands on its own. "
        "Additional commentary and detail and discussion follow in the second sentence and beyond, "
        "and those details should not end up inside the title text."
    )
    assert len(content) > 160, "test fixture must exceed TITLE_MAX_CHARS"
    title = _derive_title_from_content(content)
    # Doit couper après la 1re phrase (frontière .), pas à 160 char brutaux
    assert title == "This is a reasonably long informative first sentence that stands on its own."
    assert title.endswith(".")


def test_derive_title_hard_truncates_when_no_sentence_boundary():
    from scripts.sourcing import _derive_title_from_content

    content = "x" * 300
    title = _derive_title_from_content(content)
    assert len(title) <= 160
    assert title == "x" * 160


def test_derive_title_returns_placeholder_for_empty():
    from scripts.sourcing import _derive_title_from_content

    assert _derive_title_from_content("") == "(sans titre)"
    assert _derive_title_from_content("   ") == "(sans titre)"
    assert _derive_title_from_content("🧵") == "(sans titre)"


def test_derive_title_preserves_short_content_as_is():
    from scripts.sourcing import _derive_title_from_content

    assert _derive_title_from_content("Short tweet.") == "Short tweet."


def test_to_item_uses_derived_title_when_llm_omits(window):
    """End-to-end : _to_item dérive le titre propre quand le LLM ne fournit
    pas `title` et passe seulement content native xAI."""
    from scripts.sourcing import _to_item

    _, end = window
    raw = {
        "post_id": 1,
        "author": "Peter Attia (@PeterAttiaMD)",
        "content": "🧵 1/8 Here is what the latest research on fasting actually reveals. https://t.co/xyz",
        "engagement": {"likes": 500, "reposts": 80},
        "link": "https://x.com/PeterAttiaMD/status/1",
    }
    item = _to_item(
        raw,
        default_section_id="sante",
        default_source_type="x_search",
        fallback_published_at=end,
    )
    # Le titre est nettoyé : pas de 🧵, pas de 1/8, pas de t.co trailing
    assert "🧵" not in item.title
    assert "1/8" not in item.title
    assert "t.co" not in item.title
    assert item.title.startswith("Here is what the latest research")
