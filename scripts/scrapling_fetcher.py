"""
Fetcher de secours basé sur Scrapling (BSD-3-Clause).

Utilisé comme fallback dans enrichment.py quand l'appel xAI web_search
échoue ou retourne un summary vide. Permet d'extraire le texte principal
d'un article directement depuis son URL, sans coût API.

Conception :
- Stateless : une fonction `fetch_article_text(url)` sans état global.
- Dégradation douce : retourne None sur toute erreur (import, réseau, parsing).
- Timeout configurable (défaut 12s — sous le per-item deadline de 20s).
- Logging structuré JSON sur stderr (cohérent avec enrichment.py).

Limites connues :
- Pages derrière paywall ou auth = contenu vide → retourne None.
- Pages très lourdes JS (SPAs) = Fetcher ne rend pas le JS → utiliser
  StealthyFetcher si nécessaire (mais requiert Playwright installé).
- On n'exécute JAMAIS le JavaScript (Fetcher simple, pas DynamicFetcher)
  pour garder la latence sous contrôle.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

logger = logging.getLogger(__name__)

# Cap du texte extrait (chars) : on ne passe pas un article entier au résumé.
# Aligné sur _MAX_SUMMARY_CHARS de enrichment.py (1500).
_TEXT_CAP = 1500

# Timeout réseau en secondes.
_FETCH_TIMEOUT_S = 12

# Nombre de paragraphes minimum pour considérer qu'on a du contenu utile.
_MIN_PARAGRAPHS = 2


def fetch_article_text(url: str, timeout: int = _FETCH_TIMEOUT_S) -> str | None:
    """
    Fetche une URL et extrait le texte principal de l'article.

    Retourne le texte tronqué à _TEXT_CAP chars, ou None si :
    - Scrapling non installé (ImportError)
    - Erreur réseau / timeout
    - Contenu extrait insuffisant (< _MIN_PARAGRAPHS paragraphes)
    - Toute autre exception

    Args:
        url: URL de l'article à fetcher.
        timeout: timeout réseau en secondes.

    Returns:
        str tronqué ou None.
    """
    try:
        from scrapling.fetchers import Fetcher  # import local pour dégradation gracieuse
    except ImportError:
        _log_fetch_event(url, status="import_error", reason="scrapling not installed")
        return None

    try:
        page = Fetcher.get(url, timeout=timeout)
    except Exception as exc:
        _log_fetch_event(url, status="fetch_error", reason=str(exc)[:200])
        return None

    try:
        text = _extract_main_text(page)
    except Exception as exc:
        _log_fetch_event(url, status="parse_error", reason=str(exc)[:200])
        return None

    if not text:
        _log_fetch_event(url, status="empty_content")
        return None

    _log_fetch_event(url, status="ok", text_len=len(text))
    return text


def _extract_main_text(page: Any) -> str | None:
    """
    Extrait le texte principal depuis la page Scrapling.

    Stratégie :
    1. Cherche les balises <article>, <main>, <section> pour le contenu principal.
    2. Fallback : tous les <p> hors nav/header/footer.
    3. Assemble, truncate, retourne.
    """
    # Tentative 1 : contenu sémantique (article > main > section)
    for selector in ("article", "main", "section"):
        containers = page.css(selector)
        if containers:
            # Prend le plus grand container (heuristique)
            best = max(containers, key=lambda el: len(el.get_all_text(ignore_tags=["script", "style"])))
            paragraphs = best.css("p")
            texts = [p.get_all_text(ignore_tags=["script", "style"]).strip() for p in paragraphs]
            texts = [t for t in texts if len(t) > 40]  # filtrer snippets courts
            if len(texts) >= _MIN_PARAGRAPHS:
                combined = " ".join(texts)
                return combined[:_TEXT_CAP] if combined else None

    # Fallback : tous les <p> de la page
    paragraphs = page.css("p")
    texts = [p.get_all_text(ignore_tags=["script", "style"]).strip() for p in paragraphs]
    texts = [t for t in texts if len(t) > 40]
    if len(texts) < _MIN_PARAGRAPHS:
        return None

    combined = " ".join(texts)
    return combined[:_TEXT_CAP] if combined else None


def _log_fetch_event(url: str, *, status: str, **fields: Any) -> None:
    """Log structuré JSON sur stderr, cohérent avec les autres modules."""
    record = {
        "event": "scrapling_fetch",
        "url": url[:200],
        "status": status,
        **fields,
    }
    print(json.dumps(record, ensure_ascii=False), file=sys.stderr)
