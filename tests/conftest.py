"""Fixtures pytest partagées."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scripts.dedup import canonical_url, item_id
from scripts.models import Item


def _make_item(
    title: str,
    url: str,
    section_id: str = "ai-tech",
    score: float = 0.5,
    source_type: str = "x_account",
    source_handle: str = "@test",
    published: str = "2026-04-19T03:00:00Z",
    likes: int = 100,
    reposts: int = 20,
    is_reply: bool = False,
    is_retweet: bool = False,
) -> Item:
    canon = canonical_url(url)
    return Item(
        id=item_id(canon),
        title=title,
        summary="Test summary.",
        canonical_url=canon,
        section_id=section_id,
        source_type=source_type,  # type: ignore[arg-type]
        source_handle=source_handle,
        published_at=datetime.fromisoformat(published.replace("Z", "+00:00")),
        score=score,
        likes=likes,
        reposts=reposts,
        is_reply=is_reply,
        is_retweet=is_retweet,
    )


@pytest.fixture
def make_item():
    """Factory pour items de test."""
    return _make_item


@pytest.fixture
def fixed_now() -> datetime:
    return datetime(2026, 4, 19, 6, 44, 30, tzinfo=timezone.utc)
