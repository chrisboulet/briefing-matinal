"""Tests rendu Jinja2 : structure, escape, taille, contraintes design."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from scripts.config import load_config
from scripts.models import Briefing
from scripts.render import SIZE_BUDGET_BYTES, RenderError, render


@pytest.fixture
def sections_cfg():
    return load_config()["sections"]


@pytest.fixture
def minimal_briefing(make_item) -> Briefing:
    a = make_item("AI release", "https://e.com/ai", section_id="ai-tech", score=0.9)
    t = make_item("Tesla update", "https://e.com/tesla", section_id="tesla", score=0.8)
    p = make_item("Politique QC", "https://e.com/p", section_id="politique", score=0.7)
    dm = make_item("Long video", "https://www.youtube.com/watch?v=z", section_id="business", score=0.6)
    return Briefing(
        briefing_id="2026-04-19-matin",
        moment="matin",
        generated_at=datetime(2026, 4, 19, 10, 44, tzinfo=UTC),
        window_start=datetime(2026, 4, 18, 21, 30, tzinfo=UTC),
        window_end=datetime(2026, 4, 19, 10, 30, tzinfo=UTC),
        sections={"ai-tech": [a], "tesla": [t], "spacex": [], "sante": [], "politique": [p], "business": []},
        dont_miss=dm,
        config_hash="abc1234567890",
        prompts_version="phase-2-test",
        git_commit="deadbee",
    )


def test_render_produces_html(minimal_briefing, sections_cfg):
    html, warnings = render(minimal_briefing, sections_cfg)
    assert html.startswith("<!doctype html>")
    assert "</html>" in html
    assert warnings == []


def test_render_contains_key_sections(minimal_briefing, sections_cfg):
    html, _ = render(minimal_briefing, sections_cfg)
    assert "BRIEFING MATIN" in html
    assert "EN 60 SECONDES" not in html  # retiré via issue #22
    assert "À NE PAS MANQUER" in html
    for section in sections_cfg:
        assert section["label"] in html


def test_render_no_cdn(minimal_briefing, sections_cfg):
    html, _ = render(minimal_briefing, sections_cfg)
    assert "googleapis" not in html
    assert "cloudflare" not in html
    assert "jsdelivr" not in html


def test_render_blocks_cdn_in_summary(minimal_briefing, sections_cfg):
    bad = replace(
        minimal_briefing.sections["ai-tech"][0],
        summary="See https://fonts.googleapis.com/css?foo for details",
    )
    minimal_briefing.sections["ai-tech"][0] = bad
    with pytest.raises(RenderError, match="CDN"):
        render(minimal_briefing, sections_cfg)


def test_render_size_under_budget(minimal_briefing, sections_cfg):
    html, warnings = render(minimal_briefing, sections_cfg)
    assert len(html.encode("utf-8")) < SIZE_BUDGET_BYTES
    assert not any("size" in w.lower() for w in warnings)


def test_render_dark_mode_css_present(minimal_briefing, sections_cfg):
    html, _ = render(minimal_briefing, sections_cfg)
    assert "prefers-color-scheme: dark" in html
    assert "color-scheme" in html


def test_render_escapes_dangerous_titles(make_item, sections_cfg, minimal_briefing):
    evil = make_item('<script>alert(1)</script>', "https://e.com/x", section_id="ai-tech")
    minimal_briefing.sections["ai-tech"] = [evil]
    html, _ = render(minimal_briefing, sections_cfg)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_render_alt_sources_pluralized(make_item, sections_cfg, minimal_briefing):
    one = make_item("a", "https://e.com/1", section_id="ai-tech")
    two = make_item("b", "https://e.com/2", section_id="tesla")
    one_alt = replace(one, alt_sources=("@x",))
    two_alt = replace(two, alt_sources=("@x", "@y"))
    minimal_briefing.sections["ai-tech"] = [one_alt]
    minimal_briefing.sections["tesla"] = [two_alt]
    html, _ = render(minimal_briefing, sections_cfg)
    assert "+ 1 autre" in html
    assert "+ 2 autres" in html


def test_render_empty_section_shows_placeholder(minimal_briefing, sections_cfg):
    minimal_briefing.sections["spacex"] = []
    html, _ = render(minimal_briefing, sections_cfg)
    assert "Rien de marquant" in html


def test_render_dont_miss_optional(minimal_briefing, sections_cfg):
    minimal_briefing.dont_miss = None
    html, _ = render(minimal_briefing, sections_cfg)
    assert "À NE PAS MANQUER" not in html


def test_render_meta_footer_includes_traceability(minimal_briefing, sections_cfg):
    html, _ = render(minimal_briefing, sections_cfg)
    assert minimal_briefing.briefing_id in html
    assert minimal_briefing.config_hash[:7] in html
    assert minimal_briefing.git_commit in html
    assert minimal_briefing.prompts_version in html


def test_render_links_have_rel_noopener(minimal_briefing, sections_cfg):
    html, _ = render(minimal_briefing, sections_cfg)
    anchors = re.findall(r"<a [^>]*>", html)
    article_anchors = [a for a in anchors if 'href="https://e.com' in a or 'href="https://www.youtube' in a]
    assert article_anchors, "expected at least one article link"
    assert all('rel="noopener noreferrer"' in a for a in article_anchors)


def test_render_minimum_font_size_15px(minimal_briefing, sections_cfg):
    html, _ = render(minimal_briefing, sections_cfg)
    assert "16px" in html  # html base font-size


def test_render_idempotent_for_same_briefing(minimal_briefing, sections_cfg):
    html1, _ = render(minimal_briefing, sections_cfg)
    html2, _ = render(minimal_briefing, sections_cfg)
    assert html1 == html2


def test_render_warning_when_size_overflows(make_item, sections_cfg, minimal_briefing):
    bloat_summary = "x" * 70_000
    fat = replace(minimal_briefing.sections["ai-tech"][0], summary=bloat_summary)
    minimal_briefing.sections["ai-tech"] = [fat]
    html, warnings = render(minimal_briefing, sections_cfg)
    assert any("budget" in w for w in warnings)
    assert len(html.encode("utf-8")) > SIZE_BUDGET_BYTES


def test_render_does_not_expose_pipeline_warnings(minimal_briefing, sections_cfg):
    """Issue #19 : les warnings pipeline (items hors fenêtre, sections vides,
    appels xAI dégradés) sont des internaux destinés à stderr et au JSON stdout
    pour hermes-agent — ils ne doivent JAMAIS apparaître dans le HTML rendu."""
    minimal_briefing.warnings = [
        "search_accounts_1: skipped item outside window (published_at=2026-04-18T19:40:41+00:00)",
        "search_theme_Tesla: skipped malformed item (KeyError: 'canonical_url')",
        "section 'spacex' vide",
    ]
    html, _ = render(minimal_briefing, sections_cfg)

    # Aucun warning ne doit fuiter dans le HTML (ni texte, ni emoji, ni classe CSS)
    for warning_text in minimal_briefing.warnings:
        assert warning_text not in html, f"warning leaked into HTML: {warning_text!r}"
    assert "skipped item" not in html
    assert "skipped malformed" not in html
    assert "section 'spacex' vide" not in html
    assert 'class="warn"' not in html
    assert "--warn-bg" not in html  # variable CSS supprimée
