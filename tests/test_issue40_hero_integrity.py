"""Tests issue #40 — hero integrity / item quality / scrapling guards."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from scripts.item_quality import (
    is_acceptable_enrichment_summary,
    is_hero_url_eligible,
    is_homepage_url,
    summary_aligns_with_title,
)
from scripts.scrapling_fetcher import fetch_article_text
from scripts.select import select_dont_miss


def test_is_homepage_url_detects_root_and_index() -> None:
    assert is_homepage_url("https://www.aljazeera.com/") is True
    assert is_homepage_url("https://www.aljazeera.com") is True
    assert is_homepage_url("https://example.com/fr") is True
    assert is_homepage_url("https://example.com/news") is True
    assert is_homepage_url("") is True


def test_is_homepage_url_allows_article_paths() -> None:
    assert is_homepage_url("https://www.aljazeera.com/news/2026/7/17/starship-scrub") is False
    assert is_homepage_url("https://lapresse.ca/affaires/2026-07-17/prime-a-l-ex-pdg") is False
    assert is_homepage_url("https://x.com/user/status/123") is False


def test_hero_url_eligible() -> None:
    assert is_hero_url_eligible("https://www.aljazeera.com/") is False
    assert is_hero_url_eligible("https://spacex.com/updates/flight-13") is True


def test_summary_aligns_starship_vs_iran_case() -> None:
    title = "Starship Flight 13: décollage reporté d’au moins 24 heures"
    good = (
        "SpaceX a reporté le décollage de Starship Flight 13 d’au moins 24 heures "
        "après un problème moteur à T-0. L’équipe procède à la vidange des ergols "
        "et vise une nouvelle tentative dans les prochains jours depuis Starbase. "
        "Il s’agit du treizième vol d’essai du programme Starship."
    )
    bad = (
        "Iran struck Bahrain, Qatar, Oman, Jordan and Syria in retaliation for US strikes. "
        "Iran struck Bahrain, Qatar, Oman, Jordan and Syria in retaliation for US strikes."
    )
    assert summary_aligns_with_title(title, good) is True
    assert summary_aligns_with_title(title, bad) is False
    assert is_acceptable_enrichment_summary(title, good) is True
    assert is_acceptable_enrichment_summary(title, bad) is False
    # short homepage scrape length
    assert is_acceptable_enrichment_summary(title, "Starship short " * 5) is False  # <200


def test_fetch_article_text_rejects_homepage_without_network() -> None:
    result = fetch_article_text("https://www.aljazeera.com/")
    assert result is None


def test_fetch_article_text_still_works_for_article_urls() -> None:
    long_p1 = "Starship Flight 13 a été reporté après un scrub moteur. " * 3
    long_p2 = "SpaceX vidange les ergols et vise une nouvelle tentative. " * 3
    page = MagicMock()

    def css_selector(sel: str):
        if sel == "p":
            els = []
            for t in (long_p1, long_p2):
                el = MagicMock()
                el.get_all_text.return_value = t
                els.append(el)
            return els
        return []

    page.css.side_effect = css_selector
    mock_fetcher = MagicMock()
    mock_fetcher.get.return_value = page
    with patch.dict("sys.modules", {"scrapling.fetchers": MagicMock(Fetcher=mock_fetcher)}):
        result = fetch_article_text("https://www.aljazeera.com/news/2026/7/17/starship")
    assert result is not None
    assert "Starship" in result


def test_select_dont_miss_skips_homepage_leftover(make_item) -> None:
    """Cas 2026-07-17 : leftover homepage high score ne gagne pas le hero."""
    frankenstein = make_item(
        "Starship Flight 13: décollage reporté d’au moins 24 heures",
        "https://www.aljazeera.com/",
        section_id="spacex",
        score=0.99,
        source_handle="@AJENews",
    )
    coherent = make_item(
        "SpaceX reporte le vol d’essai Starship Flight 13",
        "https://x.com/SpaceX/status/123",
        section_id="spacex",
        score=0.85,
        source_handle="@SpaceX",
    )
    selected = {"spacex": [coherent]}  # coherent already in section
    # Only frankenstein is leftover — ineligible → fallback to selected coherent
    out = select_dont_miss([frankenstein, coherent], selected)
    assert out is not None
    assert out.id == coherent.id


def test_select_dont_miss_prefers_eligible_leftover_over_homepage(make_item) -> None:
    homepage = make_item("Noise", "https://news.example.com/", section_id="business", score=0.99)
    article = make_item(
        "Alstom: fronde actionnaires",
        "https://lapresse.ca/affaires/2026-07-17/alstom",
        section_id="business",
        score=0.70,
    )
    selected_item = make_item("Other", "https://e.com/other", section_id="ai-tech", score=0.95)
    selected = {"ai-tech": [selected_item]}
    out = select_dont_miss([homepage, article, selected_item], selected)
    assert out is not None
    assert out.id == article.id


def test_select_dont_miss_still_none_when_all_in_sections(make_item) -> None:
    a = make_item("a", "https://e.com/a", section_id="ai-tech", score=0.9)
    selected = {"ai-tech": [a]}
    assert select_dont_miss([a], selected) is None
