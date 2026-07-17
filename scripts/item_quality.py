"""Garde-fous qualité items (issue #40 — hero integrity).

Helpers purs, sans I/O, pour :
- détecter les URLs homepage / trop génériques ;
- vérifier qu'un summary d'enrichissement reste aligné avec le titre ;
- filtrer les candidats hero (`dont_miss`).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# Tokens trop génériques pour un overlap titre ↔ body utile.
_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "are",
        "was",
        "were",
        "will",
        "have",
        "has",
        "had",
        "not",
        "but",
        "you",
        "your",
        "les",
        "des",
        "une",
        "un",
        "pour",
        "dans",
        "sur",
        "avec",
        "que",
        "qui",
        "est",
        "pas",
        "plus",
        "aux",
        "par",
        "cette",
        "ces",
        "son",
        "ses",
        "leur",
        "leurs",
        "https",
        "http",
        "www",
        "com",
        "org",
        "net",
        "html",
        "lire",
        "via",
    }
)

# Paths monolingues / index qui ne sont pas un article.
_HOMEPAGE_PATH_PARTS = frozenset(
    {
        "fr",
        "en",
        "es",
        "de",
        "it",
        "pt",
        "news",
        "home",
        "index",
        "index.html",
        "index.php",
    }
)

_TOKEN_RE = re.compile(r"[a-zA-ZÀ-ÿ0-9]{3,}")


def is_homepage_url(url: str) -> bool:
    """True si l'URL est une homepage / index, pas un article deep-link."""
    raw = (url or "").strip()
    if not raw:
        return True
    try:
        parsed = urlparse(raw)
    except Exception:
        return True
    if not parsed.netloc:
        return True
    path = (parsed.path or "").rstrip("/")
    parts = [p for p in path.split("/") if p]
    if not parts:
        return True
    if len(parts) == 1 and parts[0].lower() in _HOMEPAGE_PATH_PARTS:
        return True
    return False


def is_hero_url_eligible(url: str) -> bool:
    """URL acceptable pour un item hero (non vide, non homepage)."""
    return bool((url or "").strip()) and not is_homepage_url(url)


def significant_tokens(text: str) -> set[str]:
    """Tokens significatifs (≥3 chars, hors stopwords) pour overlap titre/body."""
    tokens = _TOKEN_RE.findall((text or "").lower())
    return {t for t in tokens if t not in _STOPWORDS}


def summary_aligns_with_title(title: str, summary: str, *, min_overlap: int = 1) -> bool:
    """True si au moins `min_overlap` token(s) significatif(s) du titre apparaît dans le summary."""
    title_tokens = significant_tokens(title)
    if not title_tokens:
        return True
    summary_tokens = significant_tokens(summary)
    return len(title_tokens & summary_tokens) >= min_overlap


def is_acceptable_enrichment_summary(
    title: str,
    summary: str,
    *,
    min_chars: int = 200,
) -> bool:
    """
    Summary utilisable pour remplacer l'original après enrich/scrapling.

    - longueur minimale (homepage scrapes courts rejetés) ;
    - overlap titre (évite Starship title + Iran body).
    """
    text = (summary or "").strip()
    if len(text) < min_chars:
        return False
    return summary_aligns_with_title(title, text)
