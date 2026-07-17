"""Regression tests for issue #38 — whathappened-inspired prompt patterns.

These assert the prompt contracts stay encoded. They do not call xAI.
"""

from __future__ import annotations

from pathlib import Path

import scripts.build_briefing as build_briefing

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "prompts"


def _read(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")


def test_prompts_version_bumped_for_issue38() -> None:
    assert build_briefing.PROMPTS_VERSION == "prompts-v1.4"


def test_system_prompt_encodes_conditional_room_split() -> None:
    text = _read("system.txt")
    assert "Hot multi-camp topics" in text
    assert "Origin / first-party first" in text
    assert "Room split (max 2 sides)" in text
    assert "No fake balance" in text
    assert "not generated sentiment analysis" in text
    # Preserve PRD bar
    assert "Concrete > opinion" in text


def test_system_prompt_does_not_force_opinion_map_on_all_items() -> None:
    text = _read("system.txt")
    assert "Do **not** invent an \"opinion map\" for calm items" in text
    assert "never invent percentages" in text


def test_search_theme_encodes_conditional_lattice() -> None:
    text = _read("search_theme.txt")
    assert "High-velocity / contested topics" in text
    assert "origin / first-party" in text
    assert "never invent camps or %" in text
    assert "calm thematic noise" in text


def test_search_accounts_prefers_official_announcements() -> None:
    text = _read("search_accounts.txt")
    assert "Official / first-party priority" in text
    assert "Never invent opposition" in text
