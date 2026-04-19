"""Tests fenêtres temporelles."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from scripts.window import TZ, briefing_id, compute_window


def test_morning_window_covers_previous_evening_to_dawn():
    now = datetime(2026, 4, 19, 6, 44, 30, tzinfo=TZ)
    start, end = compute_window("matin", now)
    assert start == datetime(2026, 4, 18, 17, 30, tzinfo=TZ)
    assert end == datetime(2026, 4, 19, 6, 30, tzinfo=TZ)


def test_evening_window_covers_morning_to_late_afternoon():
    now = datetime(2026, 4, 19, 17, 29, 30, tzinfo=TZ)
    start, end = compute_window("soir", now)
    assert start == datetime(2026, 4, 19, 6, 30, tzinfo=TZ)
    assert end == datetime(2026, 4, 19, 17, 15, tzinfo=TZ)


def test_briefing_id_format():
    now = datetime(2026, 4, 19, 6, 44, 30, tzinfo=TZ)
    assert briefing_id("matin", now) == "2026-04-19-matin"
    assert briefing_id("soir", now) == "2026-04-19-soir"


def test_window_invalid_moment_raises():
    now = datetime(2026, 4, 19, 6, 44, 30, tzinfo=TZ)
    with pytest.raises(ValueError):
        compute_window("midi", now)  # type: ignore[arg-type]


def test_window_handles_utc_input_naive_to_aware():
    now_utc = datetime(2026, 4, 19, 10, 44, 30, tzinfo=ZoneInfo("UTC"))
    start, end = compute_window("matin", now_utc)
    assert start.tzinfo == TZ
    assert end.tzinfo == TZ
