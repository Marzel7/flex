"""Durable qualified-entry to Monitor activation bridge.

This is intentionally operation-agnostic: policy producers establish a
qualified entry elsewhere; this consumer only validates the generic contract,
commits the existing Monitor fact transition, then creates its deterministic
queue activation identity.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import sqlite3
from typing import Any

from src.ops.operation_monitor_capabilities import monitor_capability_for_operation


_QUALIFIED = {"QUALIFIED", "NATIVE_QUALIFIED", "ENTRY_REFERENCE_QUALIFIED"}


@dataclass
class QualifiedEntryMonitorBridge:
    """Use the existing ``MonitorWorker`` activation writer and ``MonitorQueue``.

    The caller supplies a worker solely so the exact established fact contract
    remains the activation authority.  No provider transport is referenced.
    """
    worker: Any
    queue: Any

    def activate(self, entry: dict[str, Any]) -> dict[str, str]:
        item = dict(entry)
        state = str(item.get("entry_reference_state") or item.get("policy_state") or "")
        if state not in _QUALIFIED:
            raise ValueError("UNQUALIFIED_ENTRY_REFERENCE")
        if not item.get("mint") or not item.get("operation_id"):
            raise ValueError("MISSING_MONITOR_IDENTITY")
        if not item.get("entry_timestamp"):
            raise ValueError("MISSING_QUALIFIED_ENTRY_TIMESTAMP")
        if item.get("entry_mc_usd") is None and item.get("entry_native_mc_sol") is None:
            raise ValueError("MISSING_QUALIFIED_ENTRY_VALUE")
        capability = monitor_capability_for_operation(str(item["operation_id"]))
        if not capability:
            raise ValueError("MISSING_OPERATION_MONITOR_CAPABILITY")
        if item.get("terminal") is True:
            raise ValueError("TERMINAL_ENTRY_CANNOT_ACTIVATE")
        # This synchronously commits through the established Monitor writer.
        # Only after it returns can a durable queue envelope be created.
        self.worker._activate_from_qualified_opening(item)
        activation_id = self.queue.enqueue_qualified_activation(qualified_entry=item)
        universal_status = "FORWARD_MONITOR_DISABLED"
        # DEV-only prospective hook: it runs strictly after the established
        # Entry commit and cannot reinterpret opening evidence.
        if os.getenv("FLEX_DEV_UNIVERSAL_FORWARD_MONITOR", "").lower() in {"1", "true", "yes", "on"}:
            from src.ops.universal_monitor_runtime import enrol_qualified_forward_entry
            with sqlite3.connect(str(self.worker.db_path)) as conn:
                result = enrol_qualified_forward_entry(
                    conn, operation_id=str(item["operation_id"]), mint=str(item["mint"]),
                    assignment=dict(item.get("assignment") or {}),
                    entry={"qualified": True, "timestamp": int(item["entry_timestamp"]),
                           "mc_usd": float(item["entry_mc_usd"])}, now=int(item.get("observed_at") or item["entry_timestamp"]),
                )
            universal_status = str(result["status"])
        return {"status": "ACTIVATED", "activation_id": activation_id, "universal_status": universal_status}
