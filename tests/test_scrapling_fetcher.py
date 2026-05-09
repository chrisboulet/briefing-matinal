"""
Tests unitaires pour scripts/scrapling_fetcher.py.

On mocke les imports Scrapling pour éviter tout appel réseau réel.
"""

from __future__ import annotations

import importlib
import sys
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from scripts.scrapling_fetcher import fetch_article_text, _extract_main_text


# ---------------------------------------------------------------------------
# Helpers de mock
# ---------------------------------------------------------------------------


def _make_element(text: str) -> MagicMock:
    """Simule un élément Scrapling avec get_all_text()."""
    el = MagicMock()
    el.get_all_text.return_value = text
    return el


def _make_page(paragraphs: list[str], article_paragraphs: list[str] | None = None) -> MagicMock:
    """
    Simule une Response Scrapling.

    Si `article_paragraphs` est fourni, le css("article") retourne un container
    avec ces paragraphes. Sinon, tous les <p> sont dans css("p").
    """
    page = MagicMock()

    article_containers: list[MagicMock] = []
    if article_paragraphs is not None:
        container = MagicMock()
        p_elements = [_make_element(t) for t in article_paragraphs]
        container.css.return_value = p_elements
        container.get_all_text.return_value = " ".join(article_paragraphs)
        article_containers = [container]

    def css_selector(sel: str) -> list[MagicMock]:
        if sel == "article":
            return article_containers
        if sel == "main":
            return []
        if sel == "section":
            return []
        if sel == "p":
            return [_make_element(t) for t in paragraphs]
        return []

    page.css.side_effect = css_selector
    return page


# ---------------------------------------------------------------------------
# Tests _extract_main_text
# ---------------------------------------------------------------------------


class TestExtractMainText:
    def test_returns_none_when_too_few_paragraphs(self) -> None:
        page = _make_page(paragraphs=["Short text only."])
        assert _extract_main_text(page) is None

    def test_fallback_to_all_p_when_no_article(self) -> None:
        long_p1 = "A" * 80
        long_p2 = "B" * 80
        long_p3 = "C" * 80
        page = _make_page(paragraphs=[long_p1, long_p2, long_p3])
        result = _extract_main_text(page)
        assert result is not None
        assert long_p1[:50] in result

    def test_article_container_preferred_over_all_p(self) -> None:
        article_p1 = "Article body paragraph one. " * 3
        article_p2 = "Article body paragraph two. " * 3
        page = _make_page(
            paragraphs=["Junk from sidebar. " * 3, "More junk. " * 3],
            article_paragraphs=[article_p1, article_p2],
        )
        result = _extract_main_text(page)
        assert result is not None
        assert "Article body" in result

    def test_truncates_to_1500_chars(self) -> None:
        long_p = "X" * 600
        page = _make_page(paragraphs=[long_p, long_p, long_p])
        result = _extract_main_text(page)
        assert result is not None
        assert len(result) <= 1500

    def test_filters_short_paragraphs(self) -> None:
        short = "Too short."  # < 40 chars
        long1 = "A" * 80
        long2 = "B" * 80
        page = _make_page(paragraphs=[short, short, short, long1, long2])
        result = _extract_main_text(page)
        assert result is not None
        assert "Too short" not in result


# ---------------------------------------------------------------------------
# Tests fetch_article_text (avec mock Fetcher)
# ---------------------------------------------------------------------------


class TestFetchArticleText:
    def test_returns_none_on_import_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Si scrapling n'est pas installé, retourne None sans crasher."""
        import builtins
        original_import = builtins.__import__

        def mocked_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "scrapling.fetchers":
                raise ImportError("scrapling not installed")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mocked_import):
            result = fetch_article_text("https://example.com/article")
        assert result is None

    def test_returns_none_on_network_error(self) -> None:
        """Erreur réseau → retourne None."""
        mock_fetcher = MagicMock()
        mock_fetcher.get.side_effect = ConnectionError("timeout")
        mock_module = MagicMock()
        mock_module.Fetcher = mock_fetcher

        with patch.dict("sys.modules", {"scrapling.fetchers": mock_module}):
            result = fetch_article_text("https://lapresse.ca/article")
        assert result is None

    def test_returns_text_on_success(self) -> None:
        """Fetch réussi → retourne du texte."""
        long_p1 = "L'intelligence artificielle transforme l'économie québécoise. " * 2
        long_p2 = "Les entreprises adoptent les outils LLM à grande vitesse. " * 2
        page = _make_page(paragraphs=[long_p1, long_p2])

        mock_fetcher = MagicMock()
        mock_fetcher.get.return_value = page

        with patch.dict("sys.modules", {"scrapling.fetchers": MagicMock(Fetcher=mock_fetcher)}):
            result = fetch_article_text("https://lapresse.ca/article")

        assert result is not None
        assert len(result) > 0
        assert len(result) <= 1500
