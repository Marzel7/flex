"""Provider-free coverage for the retained listener resolver diagnostics."""
from __future__ import annotations

import json
import socket

from src.core import pumpfun_curve_listener as listener


def test_resolver_snapshot_is_bounded_metadata_only(tmp_path, monkeypatch):
    target = tmp_path / "resolver.jsonl"
    monkeypatch.setenv("LISTENER_RESOLVER_DIAGNOSTIC_PATH", str(target))
    monkeypatch.setattr(listener, "_RESOLVER_DIAGNOSTIC_SEEN", set())
    monkeypatch.setattr(listener, "_RESOLVER_DIAGNOSTIC_LAST_EMITTED", {})

    assert listener._record_resolver_snapshot(
        provider="PUMPPORTAL",
        endpoint="wss://example.invalid/socket",
        event="resolver_failure",
        exc=socket.gaierror(-2, "name not known"),
        attempt_number=1,
        backoff_seconds=5,
    )
    row = json.loads(target.read_text())
    assert row["event"] == "resolver_failure"
    assert row["hostname"] == "example.invalid"
    assert row["port"] == 443
    assert row["family"] == "AF_UNSPEC"
    assert row["resolver_fingerprint"]["active_resolution"] == "not_performed"
    assert target.stat().st_size <= listener._RESOLVER_DIAGNOSTIC_MAX_BYTES


def test_resolver_failure_is_deduplicated_per_generation(tmp_path, monkeypatch):
    target = tmp_path / "resolver.jsonl"
    monkeypatch.setenv("LISTENER_RESOLVER_DIAGNOSTIC_PATH", str(target))
    monkeypatch.setattr(listener, "_RESOLVER_DIAGNOSTIC_SEEN", set())
    monkeypatch.setattr(listener, "_RESOLVER_DIAGNOSTIC_LAST_EMITTED", {})
    args = dict(provider="PUMPSWAP_HELIUS", endpoint="wss://example.invalid", event="resolver_failure", exc=socket.gaierror(-2, "name not known"))

    assert listener._record_resolver_snapshot(**args)
    assert not listener._record_resolver_snapshot(**args)
    assert len(target.read_text().splitlines()) == 1
