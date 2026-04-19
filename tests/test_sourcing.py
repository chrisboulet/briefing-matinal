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
    assert "search_web" in labels


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
    start, end = window
    client = StubClient(lambda i, r: _ok_response())
    source_briefing(
        client=client, config=base_config,
        window_start=start, window_end=end,
        prompts_dir=PROMPTS_DIR,
    )
    web_calls = [c for c in client.calls if c["prompt_label"] == "search_web"]
    assert len(web_calls) == 1


def test_total_call_count(base_config, window) -> None:
    start, end = window
    client = StubClient(lambda i, r: _ok_response())
    source_briefing(
        client=client, config=base_config,
        window_start=start, window_end=end,
        prompts_dir=PROMPTS_DIR,
    )
    n_themes = len(base_config["recherches_thematiques"])
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
    web_call = next(c for c in client.calls if c["prompt_label"] == "search_web")
    assert web_call["tool"] == "web_search"
    params = web_call["tool_params"]
    assert "allowed_domains" in params
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
