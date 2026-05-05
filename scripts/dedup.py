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
_MONEY_B_SUFFIX_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)
_MONEY_B_WORD_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*billion\b", re.IGNORECASE)

# Mots vides FR + EN supprimés avant calcul de similarité Jaccard.
_STOPWORDS = frozenset({
    "a", "an", "the", "of", "in", "at", "by", "to", "for", "is", "it",
    "its", "on", "as", "or", "and", "with", "that", "this", "from",
    "de", "du", "le", "la", "les", "un", "une", "des", "en", "et",
    "au", "aux", "par", "sur", "pour", "que", "qui",
})

_FUZZY_THRESHOLD = 0.5


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


def _normalize_numeric_phrases(title: str) -> str:
    """Normalise les montants fréquents pour comparer des titres quasi identiques."""
    text = title.lower()
    text = _MONEY_B_WORD_RE.sub(r" money_\1_billion ", text)
    return _MONEY_B_SUFFIX_RE.sub(r" money_\1_billion ", text)


def _title_tokens(title: str) -> frozenset[str]:
    """Tokens normalisés pour similarité Jaccard : lowercase, stopwords retirés."""
    text = _PUNCT_RE.sub(" ", _normalize_numeric_phrases(title))
    return frozenset(t for t in _WS_RE.split(text) if len(t) > 1 and t not in _STOPWORDS)


def _numeric_markers(title: str) -> frozenset[str]:
    """Marqueurs numériques conservés pour éviter de fusionner GPT-5/GPT-6, Q1/Q2, v14/v15."""
    text = _PUNCT_RE.sub(" ", _normalize_numeric_phrases(title))
    return frozenset(t for t in _WS_RE.split(text) if any(ch.isdigit() for ch in t))


def _numeric_markers_compatible(a: frozenset[str], b: frozenset[str]) -> bool:
    """Autorise la fusion sauf si les deux titres portent des nombres contradictoires."""
    return not (a and b and a != b)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def item_id(canonical: str) -> str:
    """ID stable d'un item à partir de son URL canonique."""
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]


def dedupe(items: list[Item]) -> list[Item]:
    """
    Dédup en trois passes :
    1. URL canonique exacte
    2. Hash titre normalisé exact
    3. Similarité Jaccard sur tokens de titre (seuil 0.5) — capture quasi-duplicates
       comme « GameStop offers $56B to acquire eBay » / « GameStop CEO offers $56 billion for eBay ».
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

    # Passe 3 : quasi-duplicates par similarité Jaccard sur tokens de titre.
    fuzzy: list[Item] = []
    for it in by_title.values():
        tokens_it = _title_tokens(it.title)
        markers_it = _numeric_markers(it.title)
        merged = False
        for i, existing in enumerate(fuzzy):
            markers_existing = _numeric_markers(existing.title)
            if not _numeric_markers_compatible(markers_it, markers_existing):
                continue
            if _jaccard(tokens_it, _title_tokens(existing.title)) >= _FUZZY_THRESHOLD:
                winner, loser = (it, existing) if it.score > existing.score else (existing, it)
                fuzzy[i] = replace(winner, alt_sources=(*winner.alt_sources, loser.source_handle))
                merged = True
                break
        if not merged:
            fuzzy.append(it)

    fuzzy.sort(key=lambda x: (-x.score, -x.published_at.timestamp(), x.id))
    return fuzzy
