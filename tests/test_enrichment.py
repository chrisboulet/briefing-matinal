"""Tests du 2e passage d'enrichissement (issue #25).

Le module `scripts.enrichment` exécute un appel xAI `web_search` par item
(skippe X posts et URLs invalides), merge le résumé / raw_excerpt et
aggrège usage + warnings. Voir `enrich_selected` pour le contrat exact.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from scripts.enrichment import EnrichmentResult, enrich_selected
from scripts.models import Item
from scripts.xai_client import XAIError, XAIResponse, XAIUnavailable, XAIUsage

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enriched_response(
    summary: str = "Synthèse enrichie de plus de 800 caractères détaillant le contexte.",
    warnings: list[str] | None = None,
    input_tokens: int = 100,
    output_tokens: int = 50,
    tool_calls: int = 1,
) -> XAIResponse:
    return XAIResponse(
        parsed_output={"summary": summary, "warnings": warnings or []},
        usage=XAIUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tool_calls=tool_calls,
        ),
        duration_ms=500,
        model="grok-stub",
    )


class StubClient:
    """Stub XAIClient. Enregistre les appels et retourne des réponses scriptées.

    Responses peut être :
    - une list[XAIResponse | Exception] (index-based)
    - un callable(idx, record) -> XAIResponse | Exception
    - un dict[str, XAIResponse | Exception] keyé par un substring du prompt_label
    """

    def __init__(self, responses_or_factory: Any):
        self._responses = responses_or_factory
        self.calls: list[dict[str, Any]] = []
        self.model = "stub"

    def call(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        tool: str,
        tool_params: dict[str, Any] | None = None,
        prompt_label: str = "unspecified",
        response_schema: dict[str, Any] | None = None,
        schema_name: str = "briefing_items",
    ) -> XAIResponse:
        record = {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "tool": tool,
            "tool_params": tool_params,
            "prompt_label": prompt_label,
            "response_schema": response_schema,
            "schema_name": schema_name,
        }
        self.calls.append(record)
        idx = len(self.calls) - 1

        if isinstance(self._responses, dict):
            value: Any = None
            for key, resp in self._responses.items():
                if key in prompt_label:
                    value = resp
                    break
            if value is None:
                value = _enriched_response()
        elif callable(self._responses):
            value = self._responses(idx, record)
        else:
            value = (
                self._responses[idx]
                if idx < len(self._responses)
                else _enriched_response()
            )

        if isinstance(value, BaseException):
            raise value
        return value


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def make_item_factory() -> Callable[..., Item]:
    """Factory d'Item sans dépendre de la conftest fixture pour varier l'URL."""

    def _make(
        item_id: str = "abc123def456",
        title: str = "Titre initial",
        summary: str = "Résumé initial court.",
        url: str = "https://lapresse.ca/foo",
        section_id: str = "ai-tech",
        source_type: str = "web",
        source_handle: str = "lapresse.ca",
        published_at: datetime | None = None,
        score: float = 0.85,
        likes: int = 0,
        reposts: int = 0,
        short_url: str = "https://short.ex/abc",
        raw_excerpt: str = "",
        alt_sources: tuple[str, ...] = (),
    ) -> Item:
        return Item(
            id=item_id,
            title=title,
            summary=summary,
            canonical_url=url,
            section_id=section_id,
            source_type=source_type,  # type: ignore[arg-type]
            source_handle=source_handle,
            published_at=published_at
            or datetime(2026, 4, 19, 3, 0, 0, tzinfo=UTC),
            score=score,
            short_url=short_url,
            raw_excerpt=raw_excerpt,
            alt_sources=alt_sources,
            likes=likes,
            reposts=reposts,
        )

    return _make


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_returns_enrichment_result_dataclass() -> None:
    client = StubClient(lambda i, r: _enriched_response())
    result = enrich_selected(
        client=client,
        sections={},
        dont_miss=None,
        prompts_dir=PROMPTS_DIR,
    )
    assert isinstance(result, EnrichmentResult)
    assert result.sections == {}
    assert result.dont_miss is None
    assert result.warnings == []
    assert isinstance(result.usage, XAIUsage)
    assert result.enriched_count == 0
    assert result.skipped_count == 0


def test_enriches_web_item_happy_path(make_item_factory) -> None:
    original = make_item_factory(url="https://lapresse.ca/foo")
    new_summary = "Synthèse enrichie détaillée avec contexte et chiffres-clés."
    client = StubClient([_enriched_response(summary=new_summary)])

    result = enrich_selected(
        client=client,
        sections={"ai-tech": [original]},
        dont_miss=None,
        prompts_dir=PROMPTS_DIR,
    )

    assert result.enriched_count == 1
    enriched = result.sections["ai-tech"][0]
    assert enriched.summary == new_summary
    assert enriched.raw_excerpt  # non-empty, populated from enrichment
    # Other fields preserved
    assert enriched.title == original.title
    assert enriched.canonical_url == original.canonical_url
    assert enriched.section_id == original.section_id
    assert enriched.score == original.score
    assert enriched.likes == original.likes
    assert enriched.reposts == original.reposts
    assert enriched.published_at == original.published_at


def test_enriches_dont_miss(make_item_factory) -> None:
    dm = make_item_factory(
        item_id="dontmiss1", url="https://lapresse.ca/important",
    )
    new_summary = "Synthèse enrichie du don't-miss."
    client = StubClient([_enriched_response(summary=new_summary)])

    result = enrich_selected(
        client=client,
        sections={},
        dont_miss=dm,
        prompts_dir=PROMPTS_DIR,
    )

    assert result.dont_miss is not None
    assert result.dont_miss.summary == new_summary
    assert result.enriched_count == 1


def test_dont_miss_none_handled(make_item_factory) -> None:
    item = make_item_factory()
    client = StubClient([_enriched_response()])

    result = enrich_selected(
        client=client,
        sections={"ai-tech": [item]},
        dont_miss=None,
        prompts_dir=PROMPTS_DIR,
    )

    assert result.dont_miss is None
    assert result.enriched_count == 1


# ---------------------------------------------------------------------------
# Skip behaviors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com/karpathy/status/1",
        "https://twitter.com/karpathy/status/1",
        "https://www.x.com/user/status/42",
        "https://mobile.twitter.com/user/status/42",
    ],
)
def test_skips_x_com_items(make_item_factory, url: str) -> None:
    item = make_item_factory(url=url)
    client = StubClient(lambda i, r: pytest.fail("must not call xAI for X posts"))

    result = enrich_selected(
        client=client,
        sections={"ai-tech": [item]},
        dont_miss=None,
        prompts_dir=PROMPTS_DIR,
    )

    returned = result.sections["ai-tech"][0]
    # Identity check on summary — original unchanged
    assert returned.summary == item.summary
    assert len(client.calls) == 0
    assert result.skipped_count == 1
    assert result.warnings == []  # no warning for X posts


@pytest.mark.parametrize("bad_url", ["", "not-a-url", "   "])
def test_skips_invalid_url_with_warning(make_item_factory, bad_url: str) -> None:
    item = make_item_factory(url=bad_url)
    client = StubClient(lambda i, r: pytest.fail("must not call for invalid URL"))

    result = enrich_selected(
        client=client,
        sections={"ai-tech": [item]},
        dont_miss=None,
        prompts_dir=PROMPTS_DIR,
    )

    assert result.sections["ai-tech"][0].summary == item.summary
    assert result.skipped_count == 1
    assert len(result.warnings) >= 1


# ---------------------------------------------------------------------------
# Tool params / schema on client.call
# ---------------------------------------------------------------------------


def test_tool_params_contains_allowed_domains(make_item_factory) -> None:
    item = make_item_factory(url="https://www.journaldequebec.com/article/123")
    client = StubClient([_enriched_response()])

    enrich_selected(
        client=client,
        sections={"ai-tech": [item]},
        dont_miss=None,
        prompts_dir=PROMPTS_DIR,
    )

    assert len(client.calls) == 1
    tool_params = client.calls[0]["tool_params"]
    assert tool_params is not None
    assert tool_params["allowed_domains"] == ["journaldequebec.com"]


def test_tool_equals_web_search(make_item_factory) -> None:
    item = make_item_factory(url="https://lapresse.ca/foo")
    client = StubClient([_enriched_response()])

    enrich_selected(
        client=client,
        sections={"ai-tech": [item]},
        dont_miss=None,
        prompts_dir=PROMPTS_DIR,
    )

    assert client.calls[0]["tool"] == "web_search"


def test_response_schema_custom_for_enrichment(make_item_factory) -> None:
    item = make_item_factory(url="https://lapresse.ca/foo")
    client = StubClient([_enriched_response()])

    enrich_selected(
        client=client,
        sections={"ai-tech": [item]},
        dont_miss=None,
        prompts_dir=PROMPTS_DIR,
    )

    call = client.calls[0]
    assert call["response_schema"] is not None
    assert call["schema_name"] != "briefing_items"


# ---------------------------------------------------------------------------
# Graceful degradation — per-item isolation
# ---------------------------------------------------------------------------


def test_single_item_failure_isolated(make_item_factory) -> None:
    item_a = make_item_factory(item_id="aaaaaaaa1111", url="https://lapresse.ca/a")
    item_b = make_item_factory(item_id="bbbbbbbb2222", url="https://lapresse.ca/b")
    item_c = make_item_factory(item_id="cccccccc3333", url="https://lapresse.ca/c")

    def factory(idx: int, rec: dict[str, Any]) -> Any:
        # The middle item (item_b) fails. We detect it via the id prefix appearing
        # in the prompt_label or user_prompt. Fall back to checking user_prompt.
        label = rec.get("prompt_label", "")
        user_prompt = rec.get("user_prompt", "")
        if item_b.id[:8] in label or item_b.id[:8] in user_prompt or "lapresse.ca/b" in user_prompt:
            return XAIUnavailable("xAI down for b")
        return _enriched_response(summary=f"enriched {idx}")

    client = StubClient(factory)

    result = enrich_selected(
        client=client,
        sections={"ai-tech": [item_a, item_b, item_c]},
        dont_miss=None,
        prompts_dir=PROMPTS_DIR,
    )

    out = result.sections["ai-tech"]
    assert len(out) == 3
    # item_b kept original
    kept = next(i for i in out if i.id == item_b.id)
    assert kept.summary == item_b.summary
    # item_a and item_c enriched
    for it in out:
        if it.id in (item_a.id, item_c.id):
            assert it.summary.startswith("enriched")
    assert result.enriched_count == 2
    # Warning mentions failing item + XAIUnavailable
    assert any(
        "XAIUnavailable" in w and item_b.id in w for w in result.warnings
    )


def test_all_items_fail_returns_originals_with_warning(make_item_factory) -> None:
    item_a = make_item_factory(item_id="aaaa1111", url="https://lapresse.ca/a")
    item_b = make_item_factory(item_id="bbbb2222", url="https://lapresse.ca/b")

    client = StubClient(lambda i, r: XAIError("boom"))

    result = enrich_selected(
        client=client,
        sections={"ai-tech": [item_a, item_b]},
        dont_miss=None,
        prompts_dir=PROMPTS_DIR,
    )

    out = result.sections["ai-tech"]
    assert len(out) == 2
    assert out[0].summary == item_a.summary
    assert out[1].summary == item_b.summary
    assert result.enriched_count == 0
    assert len(result.warnings) >= 1  # at least one warning (1 per item ideal)


# ---------------------------------------------------------------------------
# Timeout behavior
# ---------------------------------------------------------------------------


def test_per_item_timeout(make_item_factory) -> None:
    item = make_item_factory(url="https://lapresse.ca/slow")

    class SlowClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []
            self.model = "slow-stub"

        def call(self, *args: Any, **kwargs: Any) -> XAIResponse:
            self.calls.append({"args": args, "kwargs": kwargs})
            time.sleep(2.0)  # longer than timeout_s=0.5
            return _enriched_response()

    client = SlowClient()

    t0 = time.monotonic()
    result = enrich_selected(
        client=client,
        sections={"ai-tech": [item]},
        dont_miss=None,
        prompts_dir=PROMPTS_DIR,
        timeout_s=0.5,
    )
    elapsed = time.monotonic() - t0

    # (a) total time bounded
    assert elapsed < 3.0
    # (b) original preserved
    assert result.sections["ai-tech"][0].summary == item.summary
    # (c) warning mentions timeout
    assert any("timeout" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# Content validation
# ---------------------------------------------------------------------------


def test_empty_summary_from_llm_kept_original(make_item_factory) -> None:
    item = make_item_factory(url="https://lapresse.ca/foo")
    client = StubClient([_enriched_response(summary="")])

    result = enrich_selected(
        client=client,
        sections={"ai-tech": [item]},
        dont_miss=None,
        prompts_dir=PROMPTS_DIR,
    )

    assert result.sections["ai-tech"][0].summary == item.summary
    assert len(result.warnings) >= 1


def test_whitespace_only_summary_kept_original(make_item_factory) -> None:
    item = make_item_factory(url="https://lapresse.ca/foo")
    client = StubClient([_enriched_response(summary="   \n\t  ")])

    result = enrich_selected(
        client=client,
        sections={"ai-tech": [item]},
        dont_miss=None,
        prompts_dir=PROMPTS_DIR,
    )

    assert result.sections["ai-tech"][0].summary == item.summary
    assert len(result.warnings) >= 1


# ---------------------------------------------------------------------------
# Ordering / immutability
# ---------------------------------------------------------------------------


def test_order_preserved_within_section(make_item_factory) -> None:
    items = [
        make_item_factory(
            item_id=f"id{i:08d}", url=f"https://lapresse.ca/article-{i}"
        )
        for i in range(3)
    ]

    # Return different summaries with slightly randomized sleep to force
    # thread scheduling to scramble completion order.
    def factory(idx: int, rec: dict[str, Any]) -> XAIResponse:
        # Reverse sleep duration so later submits complete first
        time.sleep(0.01 * (3 - idx))
        return _enriched_response(summary=f"enriched-{idx}")

    client = StubClient(factory)

    result = enrich_selected(
        client=client,
        sections={"ai-tech": items},
        dont_miss=None,
        prompts_dir=PROMPTS_DIR,
    )

    out = result.sections["ai-tech"]
    assert [it.id for it in out] == [it.id for it in items]


def test_other_fields_preserved_after_enrichment(make_item_factory) -> None:
    original = make_item_factory(
        item_id="xyz12345",
        title="Titre original",
        url="https://lapresse.ca/detail",
        section_id="ai-tech",
        source_type="web",
        source_handle="lapresse.ca",
        score=0.77,
        likes=123,
        reposts=45,
        short_url="https://short.ex/orig",
        alt_sources=("https://alt1.com", "https://alt2.com"),
    )
    client = StubClient([_enriched_response(summary="nouveau résumé")])

    result = enrich_selected(
        client=client,
        sections={"ai-tech": [original]},
        dont_miss=None,
        prompts_dir=PROMPTS_DIR,
    )

    out = result.sections["ai-tech"][0]
    assert out.title == original.title
    assert out.canonical_url == original.canonical_url
    assert out.section_id == original.section_id
    assert out.score == original.score
    assert out.likes == original.likes
    assert out.reposts == original.reposts
    assert out.published_at == original.published_at
    assert out.source_type == original.source_type
    assert out.source_handle == original.source_handle
    assert out.short_url == original.short_url
    assert out.alt_sources == original.alt_sources


# ---------------------------------------------------------------------------
# Usage aggregation
# ---------------------------------------------------------------------------


def test_usage_aggregated_across_calls(make_item_factory) -> None:
    items = [
        make_item_factory(item_id=f"id{i:08d}", url=f"https://lapresse.ca/{i}")
        for i in range(3)
    ]
    client = StubClient(
        lambda i, r: _enriched_response(
            summary=f"s-{i}", input_tokens=100, output_tokens=50, tool_calls=1
        )
    )

    result = enrich_selected(
        client=client,
        sections={"ai-tech": items},
        dont_miss=None,
        prompts_dir=PROMPTS_DIR,
    )

    assert result.usage.input_tokens == 300
    assert result.usage.output_tokens == 150
    assert result.usage.tool_calls == 3


def test_enriched_count_and_skipped_count(make_item_factory) -> None:
    web1 = make_item_factory(item_id="web00001", url="https://lapresse.ca/a")
    web2 = make_item_factory(item_id="web00002", url="https://lesaffaires.com/b")
    x_post = make_item_factory(
        item_id="xpost001",
        url="https://x.com/u/status/1",
        source_type="x_account",
        source_handle="@u",
    )
    invalid = make_item_factory(item_id="inv00001", url="")

    client = StubClient(lambda i, r: _enriched_response(summary=f"ok-{i}"))

    result = enrich_selected(
        client=client,
        sections={"ai-tech": [web1, web2, x_post, invalid]},
        dont_miss=None,
        prompts_dir=PROMPTS_DIR,
    )

    assert result.enriched_count == 2
    assert result.skipped_count == 2


def test_frozen_item_not_mutated(make_item_factory) -> None:
    original = make_item_factory(url="https://lapresse.ca/foo", summary="ORIG")
    snapshot = replace(original)  # detached copy via dataclass replace

    client = StubClient([_enriched_response(summary="nouveau")])

    enrich_selected(
        client=client,
        sections={"ai-tech": [original]},
        dont_miss=None,
        prompts_dir=PROMPTS_DIR,
    )

    # Frozen dataclass : original must still have "ORIG"
    assert original.summary == "ORIG"
    assert original.summary == snapshot.summary
