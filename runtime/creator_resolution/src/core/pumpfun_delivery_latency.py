"""Optional, file-only Pump.fun delivery-latency matcher.

It compares receipt times from two feeds on this process's monotonic clock;
it never treats RPC response timing as chain timing.
"""
from __future__ import annotations

import json
import os
import queue
import threading
from pathlib import Path


class PumpfunDeliveryLatencyCapture:
    def __init__(self, *, enabled: bool, path: str, capacity: int = 4096) -> None:
        self.enabled = enabled
        self.path = Path(path)
        self._pumpportal: dict[str, dict] = {}
        self._chain: dict[str, dict] = {}
        self._pending: queue.Queue[dict] = queue.Queue(maxsize=capacity)
        self._started = False
        self._lock = threading.Lock()
        self.dropped = 0

    def observe_pumpportal(self, *, signature: str, mint: str, receive_monotonic_ns: int, receive_utc_ns: int) -> None:
        self._observe("pumpportal", signature, {"mint": mint, "pumpportal_receive_monotonic_ns": receive_monotonic_ns, "pumpportal_receive_utc_ns": receive_utc_ns})

    def observe_chain(self, *, signature: str, slot: int | None, receive_monotonic_ns: int, receive_utc_ns: int) -> None:
        self._observe("chain", signature, {"slot": slot, "chain_feed_receive_monotonic_ns": receive_monotonic_ns, "chain_feed_receive_utc_ns": receive_utc_ns})

    def _observe(self, side: str, signature: str, event: dict) -> None:
        if not self.enabled or not signature:
            return
        mine, other = (self._pumpportal, self._chain) if side == "pumpportal" else (self._chain, self._pumpportal)
        if signature in mine:
            return
        counterpart = other.pop(signature, None)
        if counterpart is None:
            mine[signature] = event
            if len(mine) > 8192:
                mine.pop(next(iter(mine)))
            return
        row = {"schema_version": 1, "signature": signature, **event, **counterpart}
        row["delta_ms"] = (row["pumpportal_receive_monotonic_ns"] - row["chain_feed_receive_monotonic_ns"]) / 1_000_000
        try:
            self._pending.put_nowait(row)
        except queue.Full:
            self.dropped += 1
            return
        self._start_writer()

    def _start_writer(self) -> None:
        if self._started:
            return
        with self._lock:
            if self._started:
                return
            self._started = True
            threading.Thread(target=self._drain, name="pumpfun-delivery-latency", daemon=True).start()

    def _drain(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            row = self._pending.get()
            try:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
            finally:
                self._pending.task_done()


def configured_pumpfun_delivery_latency_capture() -> PumpfunDeliveryLatencyCapture:
    root = Path(__file__).resolve().parents[2]
    return PumpfunDeliveryLatencyCapture(
        enabled=os.environ.get("PUMPFUN_DELIVERY_LATENCY_CAPTURE_ENABLED", "0").lower() in {"1", "true", "yes"},
        path=os.environ.get("PUMPFUN_DELIVERY_LATENCY_CAPTURE_PATH", str(root / "logs" / "pumpfun_delivery_latency.jsonl")),
    )
