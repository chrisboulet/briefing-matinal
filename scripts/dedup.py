"""Dédup d'items par URL canonique + hash titre. Voir PRD §S1."""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from scripts.models import Item

# Paramètres trackers à supprimer systématiquement
_TRACKER_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "ref_url",
    "_hsenc", "_hsmi", "hsCtaTracking", "s",  # X share param
}

# Paramètres à conserver (whitelist par domaine)
_KEEP_PARAMS = {
    "youtube.com": {"v", "t"},
    "youtu.be": {"t"},
}

_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def canonical_url(url: str) -> str:
    """Normalise l'URL pour dédup : protocol https, lowercase host, trim slash, strip trackers."""
    parsed = urlparse(url.strip())
    scheme = "https"
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or "/"

    keep = _KEEP_PARAMS.get(netloc, set())
    params = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=False)
        if k not in _TRACKER_PARAMS and (not keep or k in keep)
    ]
    params.sort()
    query = urlencode(params)

    return urlunparse((scheme, netloc, path, "", query, ""))


def title_hash(title: str) -> str:
    """SHA-1 du titre normalisé (lowercase, sans ponctuation, espaces collapsés)."""
    norm = _PUNCT_RE.sub(" ", title.lower())
    norm = _WS_RE.sub(" ", norm).strip()
    return hashlib.sha1(norm[:200].encode("utf-8")).hexdigest()


def item_id(canonical: str) -> str:
    """ID stable d'un item à partir de son URL canonique."""
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]


def dedupe(items: list[Item]) -> list[Item]:
    """
    Dédup par URL canonique en priorité, puis par hash titre.
    Garde l'item avec le score le plus élevé ; les autres sources passent dans alt_sources.
    Tri stable final : score DESC, published_at DESC, id ASC.
    """
    by_key: dict[str, Item] = {}
    for it in items:
        key = it.canonical_url
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = it
            continue
        winner, loser = (it, existing) if it.score > existing.score else (existing, it)
        by_key[key] = replace(winner, alt_sources=(*winner.alt_sources, loser.source_handle))

    by_title: dict[str, Item] = {}
    for it in by_key.values():
        key = title_hash(it.title)
        existing = by_title.get(key)
        if existing is None:
            by_title[key] = it
            continue
        winner, loser = (it, existing) if it.score > existing.score else (existing, it)
        by_title[key] = replace(winner, alt_sources=(*winner.alt_sources, loser.source_handle))

    deduped = list(by_title.values())
    deduped.sort(key=lambda x: (-x.score, -x.published_at.timestamp(), x.id))
    return deduped
