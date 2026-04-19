"""Charge des items à partir d'une fixture JSON (mode offline Phase 1)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from scripts.dedup import canonical_url, item_id
from scripts.models import Item


def load_fixture(path: Path) -> tuple[list[Item], dict | None]:
    """
    Lit une fixture et retourne (items, meta).
    `meta` contient `now` (ISO 8601) si la fixture en spécifie un.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    meta = raw.get("_meta")
    items: list[Item] = []
    for entry in raw["items"]:
        canon = canonical_url(entry["canonical_url"])
        items.append(
            Item(
                id=item_id(canon),
                title=entry["title"],
                summary=entry["summary"],
                canonical_url=canon,
                section_id=entry["section_id"],
                source_type=entry["source_type"],
                source_handle=entry["source_handle"],
                published_at=datetime.fromisoformat(entry["published_at"].replace("Z", "+00:00")),
                score=float(entry["score"]),
                likes=int(entry.get("likes", 0)),
                reposts=int(entry.get("reposts", 0)),
                is_reply=bool(entry.get("is_reply", False)),
                is_retweet=bool(entry.get("is_retweet", False)),
            )
        )
    return items, meta
