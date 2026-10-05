"""Provider-free coexistence checks for the assembled immutable source line."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.ops.byzantine_event_mint_resolver import resolve_event_mint
from src.ops.operation_monitor_worker import MonitorQueue, MonitorWorker
from src.ops.qualified_entry_monitor_bridge import QualifiedEntryMonitorBridge
from src.ops.watchtower_first_available_mc import collect_first_available


ROOT = Path(__file__).resolve().parents[1]
PEPEINU = json.loads((ROOT / "docs/fixtures/byzantine_actual_entry_v1/pepeinu.json").read_text())


def _receipt(item):
    """Offline writer acknowledgement: retain the existing worker SQL unexecuted."""
    from src.core.db_writer import DurableWriteReceipt
    return DurableWriteReceipt(True, 1.0, len(item.statements))


@pytest.mark.parametrize("expected", [172234, 167954, 108285, 166730])
def test_watchtower_frozen_controls_keep_first_available_values(expected):
    calls = []
    result = collect_first_available(
        mint="control", canonical_create_signature="create", qualified_supply=1_000_000_000,
        helius_get_transaction=lambda signature: calls.append(("helius", signature)) or {"slot": 10, "blockTime": 100},
        birdeye_get_trades=lambda mint, after, before: calls.append(("birdeye", mint, after, before)) or [
            {"slot": 10, "price_usd": 0.000000001},
            {"slot": 11, "price_usd": expected / 1_000_000_000},
        ],
    )
    assert result.status == "QUALIFIED"
    assert result.evidence["first_available_mc_usd"] == expected
    assert calls == [("helius", "create"), ("birdeye", "control", 99, 130)]


def _bridge_with_real_worker(tmp_path):
    queue = MonitorQueue(tmp_path / "queue", enabled=True)
    return QualifiedEntryMonitorBridge(MonitorWorker(queue, transport=lambda _: {}, persist=_receipt), queue), queue


def test_watchtower_opening_flows_through_exact_bridge_and_worker(tmp_path, monkeypatch):
    monkeypatch.delenv("FLEX_DEV_UNIVERSAL_FORWARD_MONITOR", raising=False)
    result = collect_first_available(
        mint="watchtower-mint", canonical_create_signature="create", qualified_supply=1_000_000_000,
        helius_get_transaction=lambda _: {"slot": 10, "blockTime": 100},
        birdeye_get_trades=lambda *_: [{"slot": 11, "price_usd": 0.000172234}],
    )
    bridge, queue = _bridge_with_real_worker(tmp_path)
    entry = {"operation_id": "watchtower", "mint": "watchtower-mint", "entry_reference_state": "QUALIFIED",
             "entry_timestamp": result.evidence["first_available"]["timestamp"] or 101,
             "entry_mc_usd": result.evidence["first_available_mc_usd"], "entry_method": "FIRST_AVAILABLE"}
    activation = bridge.activate(entry)
    assert activation["status"] == "ACTIVATED"
    assert queue.queue.depth()["pending"] == 1


def test_retained_pepeinu_opening_guard_then_bridge_preserves_selected_value(tmp_path, monkeypatch):
    monkeypatch.delenv("FLEX_DEV_UNIVERSAL_FORWARD_MONITOR", raising=False)
    outcome = PEPEINU["outcome"]
    selected = outcome["selected"]
    assert selected["signature"] == "1M8iBZQLkfCJr5GgZY9K33QHuXhr7gvqutg6KxknBAzKj7gkRbrnxWyVAdrFFMBrjvkGMfBG9EjtSaFz7zTfNNa"
    assert selected["timestamp"] == 1791112830
    assert outcome["entry_mc_usd"] == 3925.9516827750313
    assert resolve_event_mint(outcome["candidates"][0]["event_mint_identity"], PEPEINU["assignment"]["mint"])["state"] == "MATCH"
    bridge, queue = _bridge_with_real_worker(tmp_path)
    activation = bridge.activate({"operation_id": "byzantine", "mint": PEPEINU["assignment"]["mint"],
                                  "entry_reference_state": "QUALIFIED", "entry_timestamp": selected["timestamp"],
                                  "entry_mc_usd": outcome["entry_mc_usd"], "entry_method": "HISTORICAL_ACTUAL_ENTRY"})
    assert activation["status"] == "ACTIVATED"
    pending = next((queue.queue.root / "pending").glob("*.json"))
    assert json.loads(pending.read_text())["envelope"]["entry_mc_usd"] == 3925.9516827750313


def test_frozen_source_hashes_and_historical_producer_guard_separation():
    expected = {
        "src/ops/watchtower_first_available_mc.py": "52231ff4403eb1eaa6ebe0e89480059f6e26d4eab979ef75c7ff2ece89584af2",
        "src/ops/operation_monitor_worker.py": "f8360c939b69a7ff5e8c11d297e50ad44761934febed925d3ab097aa8e2050a2",
        "src/ops/qualified_entry_monitor_bridge.py": "b7a1608e4e1ed3c7a7419035c72cc506a4bf8f4f001be78c4a85fa28d1dc685c",
        "scripts/run_byzantine_actual_entry_24h_backfill.py": "5645ab3e23968deb7a0e8006263c413a21bbc09001bf3dcadf2a3df3141bd0c3",
    }
    for relative, digest in expected.items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == digest
    producer = (ROOT / "scripts/run_byzantine_actual_entry_24h_backfill.py").read_text()
    assert "byzantine_event_mint_resolver" not in producer
    assert "select_earliest_target_event" not in producer
