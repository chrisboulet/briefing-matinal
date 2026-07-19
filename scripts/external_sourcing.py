"""Phase 0 — sources externes hors xAI (RSS, Google News, Tavily, Reddit, HN).

Objectif : garantir un plancher de densité même si xAI timeout.
Tous les items sortent en source_type=\"web\" pour rester compatible avec
le filtre engagement (web passe toujours) et le reste du pipeline.
"""

from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse

import httpx

from scripts.dedup import canonical_url, item_id
from scripts.models import Item

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = Path("sources/external.json")
_USER_AGENT = (
    "BriefingMatinal/1.0 (+https://github.com/chrisboulet/briefing-matinal; personal news digest)"
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


@dataclass
class ExternalSourcingResult:
    items: list[Item] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def load_external_config(path: Path = _DEFAULT_CONFIG) -> dict[str, Any]:
    if not path.exists():
        return {"enabled": False}
    return json.loads(path.read_text(encoding="utf-8"))


def source_external(
    window_start: datetime,
    window_end: datetime,
    valid_section_ids: set[str],
    config_path: Path = _DEFAULT_CONFIG,
) -> ExternalSourcingResult:
    """Collecte RSS + Google News + Tavily + Reddit + HN dans la fenêtre."""
    cfg = load_external_config(config_path)
    if not cfg.get("enabled", True):
        return ExternalSourcingResult(warnings=["external: disabled in config"])

    timeouts = cfg.get("timeouts") or {}
    max_per = int(cfg.get("max_items_per_source", 8))
    items: list[Item] = []
    warnings: list[str] = []

    jobs: list[tuple[str, Any]] = []

    for feed in cfg.get("rss_feeds") or []:
        jobs.append(("rss", feed))
    for gn in cfg.get("google_news") or []:
        jobs.append(("gnews", gn))
    for tq in cfg.get("tavily_queries") or []:
        jobs.append(("tavily", tq))
    for rd in cfg.get("reddit") or []:
        jobs.append(("reddit", rd))
    for hn in cfg.get("hackernews") or []:
        jobs.append(("hn", hn))

    if not jobs:
        return ExternalSourcingResult(warnings=["external: no jobs configured"])

    def run_one(kind: str, spec: dict[str, Any]) -> tuple[list[Item], list[str]]:
        try:
            if kind == "rss":
                return _fetch_rss(spec, window_start, window_end, valid_section_ids, timeouts, max_per)
            if kind == "gnews":
                return _fetch_google_news(
                    spec, window_start, window_end, valid_section_ids, timeouts, max_per
                )
            if kind == "tavily":
                return _fetch_tavily(
                    spec, window_start, window_end, valid_section_ids, timeouts, max_per
                )
            if kind == "reddit":
                return _fetch_reddit(spec, window_start, window_end, valid_section_ids, timeouts, max_per)
            if kind == "hn":
                return _fetch_hn(spec, window_start, window_end, valid_section_ids, timeouts, max_per)
        except Exception as exc:
            label = spec.get("url") or spec.get("query") or spec.get("subreddit") or kind
            return [], [f"external[{kind}]: {label}: {type(exc).__name__}: {exc}"]
        return [], [f"external: unknown kind {kind}"]

    # Parallel but conservative — external APIs are mostly free/rate-limited
    workers = min(8, max(2, len(jobs)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(run_one, k, s): (k, s) for k, s in jobs}
        for fut in as_completed(futs):
            new_items, new_warns = fut.result()
            items.extend(new_items)
            warnings.extend(new_warns)

    warnings.insert(
        0,
        f"external_phase0: {len(items)} items from {len(jobs)} jobs "
        f"(rss/gnews/tavily/reddit/hn)",
    )
    return ExternalSourcingResult(items=items, warnings=warnings)


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------


def _fetch_rss(
    spec: dict[str, Any],
    window_start: datetime,
    window_end: datetime,
    valid_section_ids: set[str],
    timeouts: dict[str, Any],
    max_per: int,
) -> tuple[list[Item], list[str]]:
    import feedparser

    url = spec["url"]
    section_id = spec.get("section_id") or "ai-tech"
    handle = spec.get("handle") or _host_of(url)
    if section_id not in valid_section_ids:
        return [], [f"external[rss]: bad section_id={section_id} for {url}"]

    timeout = float(timeouts.get("rss_s", 12))
    # feedparser can take a URL; prefer httpx for UA control
    with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": _USER_AGENT}) as client:
        resp = client.get(url)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)

    items: list[Item] = []
    for entry in parsed.entries[: max_per * 2]:
        link = entry.get("link") or ""
        title = _clean_text(entry.get("title") or "")
        if not link or not title:
            continue
        published = _parse_entry_date(entry) or window_end
        if not _in_window(published, window_start, window_end):
            continue
        summary = _clean_text(entry.get("summary") or entry.get("description") or title)
        items.append(
            _make_web_item(
                title=title,
                summary=summary[:900],
                url=link,
                section_id=section_id,
                handle=handle,
                published_at=published,
                score=0.55,
            )
        )
        if len(items) >= max_per:
            break
    return items, []


def _fetch_google_news(
    spec: dict[str, Any],
    window_start: datetime,
    window_end: datetime,
    valid_section_ids: set[str],
    timeouts: dict[str, Any],
    max_per: int,
) -> tuple[list[Item], list[str]]:
    query = spec["query"]
    section_id = spec.get("section_id") or "ai-tech"
    hl = spec.get("hl") or "fr-CA"
    gl = spec.get("gl") or "CA"
    if section_id not in valid_section_ids:
        return [], [f"external[gnews]: bad section_id={section_id}"]

    # when:1d keeps feed recent
    q = f"{query} when:1d"
    ceid = quote_plus(gl + ":" + hl.split("-")[0])
    url = (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(q)}&hl={quote_plus(hl)}&gl={quote_plus(gl)}&ceid={ceid}"
    )
    feed_spec = {"url": url, "section_id": section_id, "handle": "news.google.com"}
    items, warns = _fetch_rss(feed_spec, window_start, window_end, valid_section_ids, timeouts, max_per)
    # bump score slightly for curated hot queries
    bumped = [
        Item(
            id=it.id,
            title=it.title,
            summary=it.summary,
            canonical_url=it.canonical_url,
            section_id=it.section_id,
            source_type=it.source_type,
            source_handle=it.source_handle,
            published_at=it.published_at,
            score=min(0.7, it.score + 0.08),
            likes=it.likes,
            reposts=it.reposts,
        )
        for it in items
    ]
    return bumped, warns


def _fetch_tavily(
    spec: dict[str, Any],
    window_start: datetime,
    window_end: datetime,
    valid_section_ids: set[str],
    timeouts: dict[str, Any],
    max_per: int,
) -> tuple[list[Item], list[str]]:
    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        return [], ["external[tavily]: TAVILY_API_KEY missing — skipped"]

    query = spec["query"]
    section_id = spec.get("section_id") or "ai-tech"
    max_results = min(int(spec.get("max_results", 5)), max_per)
    if section_id not in valid_section_ids:
        return [], [f"external[tavily]: bad section_id={section_id}"]

    timeout = float(timeouts.get("tavily_s", 25))
    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": "basic",
        "include_answer": False,
        "max_results": max_results,
        "topic": "news",
        "days": 2,
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.post("https://api.tavily.com/search", json=payload)
        resp.raise_for_status()
        data = resp.json()

    items: list[Item] = []
    for row in data.get("results") or []:
        link = row.get("url") or ""
        title = _clean_text(row.get("title") or "")
        if not link or not title:
            continue
        summary = _clean_text(row.get("content") or title)[:900]
        # Tavily rarely gives precise timestamps — place at window mid/end
        published = window_end
        handle = _host_of(link)
        items.append(
            _make_web_item(
                title=title,
                summary=summary,
                url=link,
                section_id=section_id,
                handle=handle,
                published_at=published,
                score=0.62,
            )
        )
        if len(items) >= max_per:
            break
    return items, []


def _fetch_reddit(
    spec: dict[str, Any],
    window_start: datetime,
    window_end: datetime,
    valid_section_ids: set[str],
    timeouts: dict[str, Any],
    max_per: int,
) -> tuple[list[Item], list[str]]:
    sub = str(spec["subreddit"]).lstrip("r/")
    section_id = spec.get("section_id") or "ai-tech"
    limit = min(int(spec.get("limit", 10)), max_per * 2)
    if section_id not in valid_section_ids:
        return [], [f"external[reddit]: bad section_id={section_id}"]

    url = f"https://old.reddit.com/r/{sub}/hot.json?limit={limit}&raw_json=1"
    timeout = float(timeouts.get("reddit_s", 12))
    headers = {"User-Agent": _USER_AGENT}
    with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.json()

    items: list[Item] = []
    for child in (data.get("data") or {}).get("children") or []:
        post = child.get("data") or {}
        if post.get("stickied"):
            continue
        title = _clean_text(post.get("title") or "")
        permalink = post.get("permalink") or ""
        link = post.get("url_overridden_by_dest") or post.get("url") or ""
        post_url = ("https://www.reddit.com" + permalink) if permalink else link
        if not title or not post_url:
            continue
        created = post.get("created_utc")
        try:
            published = datetime.fromtimestamp(float(created), tz=UTC)
        except (TypeError, ValueError, OSError):
            published = window_end
        if not _in_window(published, window_start, window_end):
            # hot posts can be slightly older — allow 36h grace via window already
            continue
        ups = int(post.get("ups") or post.get("score") or 0)
        summary = _clean_text(post.get("selftext") or title)[:700]
        # Prefer external article URL when it's not a reddit self link
        final_url = link if link and "reddit.com" not in link else post_url
        score = min(0.75, 0.45 + min(ups, 2000) / 4000)
        items.append(
            _make_web_item(
                title=title,
                summary=summary or title,
                url=final_url,
                section_id=section_id,
                handle=f"r/{sub}",
                published_at=published,
                score=score,
                likes=ups,
            )
        )
        if len(items) >= max_per:
            break
    return items, []


def _fetch_hn(
    spec: dict[str, Any],
    window_start: datetime,
    window_end: datetime,
    valid_section_ids: set[str],
    timeouts: dict[str, Any],
    max_per: int,
) -> tuple[list[Item], list[str]]:
    query = spec["query"]
    section_id = spec.get("section_id") or "ai-tech"
    hits = min(int(spec.get("hits", 10)), max_per * 2)
    if section_id not in valid_section_ids:
        return [], [f"external[hn]: bad section_id={section_id}"]

    # numericFilters for recent window (unix ts)
    start_ts = int(window_start.timestamp())
    url = (
        "https://hn.algolia.com/api/v1/search_by_date"
        f"?query={quote_plus(query)}&tags=story&hitsPerPage={hits}"
        f"&numericFilters=created_at_i>{start_ts}"
    )
    timeout = float(timeouts.get("hn_s", 12))
    with httpx.Client(timeout=timeout, headers={"User-Agent": _USER_AGENT}) as client:
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.json()

    items: list[Item] = []
    for hit in data.get("hits") or []:
        title = _clean_text(hit.get("title") or "")
        link = hit.get("url") or (
            f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
            if hit.get("objectID")
            else ""
        )
        if not title or not link:
            continue
        try:
            published = datetime.fromtimestamp(int(hit.get("created_at_i") or 0), tz=UTC)
        except (TypeError, ValueError, OSError):
            published = window_end
        if not _in_window(published, window_start, window_end):
            continue
        points = int(hit.get("points") or 0)
        summary = title
        score = min(0.78, 0.48 + min(points, 500) / 1000)
        items.append(
            _make_web_item(
                title=title,
                summary=summary,
                url=link,
                section_id=section_id,
                handle="news.ycombinator.com",
                published_at=published,
                score=score,
                likes=points,
            )
        )
        if len(items) >= max_per:
            break
    return items, []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_web_item(
    *,
    title: str,
    summary: str,
    url: str,
    section_id: str,
    handle: str,
    published_at: datetime,
    score: float,
    likes: int = 0,
) -> Item:
    canon = canonical_url(url)
    return Item(
        id=item_id(canon),
        title=title[:160],
        summary=summary[:1200],
        canonical_url=canon,
        section_id=section_id,
        source_type="web",
        source_handle=handle,
        published_at=published_at if published_at.tzinfo else published_at.replace(tzinfo=UTC),
        score=max(0.0, min(1.0, float(score))),
        likes=max(0, int(likes)),
        reposts=0,
        is_reply=False,
        is_retweet=False,
    )


def _clean_text(text: str) -> str:
    text = _HTML_TAG_RE.sub(" ", text or "")
    text = _WS_RE.sub(" ", text).strip()
    return text


def _host_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host or "web"
    except Exception:
        return "web"


def _parse_entry_date(entry: dict[str, Any]) -> datetime | None:
    for key in ("published", "updated", "created"):
        raw = entry.get(key)
        if not raw:
            continue
        try:
            dt = parsedate_to_datetime(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        except (TypeError, ValueError, IndexError, OverflowError):
            pass
    for key in ("published_parsed", "updated_parsed"):
        struct = entry.get(key)
        if not struct:
            continue
        try:
            import time as _time

            ts = _time.mktime(struct)
            return datetime.fromtimestamp(ts, tz=UTC)
        except (TypeError, ValueError, OverflowError, OSError):
            continue
    return None


def _in_window(dt: datetime, start: datetime, end: datetime) -> bool:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    # slight grace: allow up to 6h before window start (overnight RSS lag)
    from datetime import timedelta

    grace_start = start - timedelta(hours=6)
    return grace_start <= dt.astimezone(start.tzinfo or UTC) <= end
