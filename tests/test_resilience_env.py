"""Tests résilience env (issue #44)."""

from __future__ import annotations

import scripts.sourcing as sourcing
import scripts.xai_client as xai_client


def test_default_timeout_is_at_least_60():
    assert xai_client.DEFAULT_TIMEOUT_S >= 60.0


def test_max_concurrent_default_is_3(monkeypatch):
    monkeypatch.delenv("BRIEFING_XAI_MAX_CONCURRENT_CALLS", raising=False)
    # Re-read helper (function reads env each call)
    assert sourcing._max_concurrent_calls_from_env() == 3


def test_max_concurrent_bounds(monkeypatch):
    monkeypatch.setenv("BRIEFING_XAI_MAX_CONCURRENT_CALLS", "99")
    assert sourcing._max_concurrent_calls_from_env() == 10
    monkeypatch.setenv("BRIEFING_XAI_MAX_CONCURRENT_CALLS", "0")
    assert sourcing._max_concurrent_calls_from_env() == 1
    monkeypatch.setenv("BRIEFING_XAI_MAX_CONCURRENT_CALLS", "nope")
    assert sourcing._max_concurrent_calls_from_env() == 3


def test_make_xai_client_timeout_default(monkeypatch):
    from scripts.build_briefing import _make_xai_client

    monkeypatch.setenv("XAI_API_KEY", "test-key-not-real")
    monkeypatch.delenv("XAI_TIMEOUT_S", raising=False)
    client = _make_xai_client()
    try:
        t = client._client.timeout
        read_timeout = t.read if t.read is not None else t.pool
        assert read_timeout is not None
        assert float(read_timeout) >= 60.0
    finally:
        client.close()
