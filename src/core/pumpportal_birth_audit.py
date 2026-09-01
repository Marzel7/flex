"""Optional, bounded PumpPortal birth receive telemetry for EB0.1.

This collector is deliberately independent of ingestion: it is disabled by
default, performs no RPC or database work, and drops telemetry rather than
blocking the socket if its bounded writer queue is full.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import hashlib
import time
from pathlib import Path
from typing import Any, Optional


class PumpPortalBirthAudit:
    def __init__(self, *, enabled: bool, path: str, max_pending: int = 2048) -> None:
        self.enabled = bool(enabled)
        self.path = Path(path)
        self._pending: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=max(1, int(max_pending)))
        self._dropped = 0
        self._started = False
        self._lock = threading.Lock()

    def record(
        self,
        *, receive_utc_ns: int, receive_monotonic_ns: int, signature: Optional[str],
        mint: Optional[str], creator: Optional[str], parser_utc_ns: int,
        market_cap_sol: Optional[float] = None,
        virtual_sol_reserves: Optional[float] = None,
        bonding_curve: Optional[str] = None,
        raw_payload: Optional[dict[str, Any]] = None,
    ) -> None:
        """Queue one already-received create event; never block the listener."""
        if not self.enabled:
            return
        # Canonical, immutable PumpPortal-create evidence.  The payload digest is
        # the identity: reconnect delivery of identical evidence is harmless.
        raw = raw_payload or {}
        raw_json = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        raw_digest = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
        row = {
            "event_schema_version": 2,
            "parser_version": "pumpportal_create.v1",
            "source": "pumpportal_create",
            "receive_utc_ns": int(receive_utc_ns),
            "receive_monotonic_ns": int(receive_monotonic_ns),
            "parser_utc_ns": int(parser_utc_ns),
            "signature": signature or None,
            "mint": mint or None,
            "creator": creator or None,
            # EB0.3: values already supplied by PumpPortal at the receive
            # boundary. This append-only shadow file captures no RPC-derived
            # state and does not affect token persistence.
            "market_cap_sol": float(market_cap_sol) if market_cap_sol is not None else None,
            "virtual_sol_reserves": float(virtual_sol_reserves) if virtual_sol_reserves is not None else None,
            "bonding_curve": bonding_curve or None,
            "received_at": int(receive_utc_ns // 1_000_000_000),
            "raw_payload": raw,
            "raw_payload_sha256": raw_digest,
        }
        try:
            self._pending.put_nowait(row)
        except queue.Full:
            with self._lock:
                self._dropped += 1
            return
        self._ensure_writer()

    def health(self) -> dict[str, int | bool]:
        with self._lock:
            dropped = self._dropped
        return {"enabled": self.enabled, "pending": self._pending.qsize(), "dropped": dropped}

    def _ensure_writer(self) -> None:
        if self._started:
            return
        with self._lock:
            if self._started:
                return
            self._started = True
            threading.Thread(target=self._drain, name="pumpportal-birth-audit", daemon=True).start()

    def _drain(self) -> None:
        # A directory is an append-only content-addressed store.  The legacy
        # JSONL path remains supported only when explicitly configured with a
        # suffix, preserving old telemetry tests/deployments.
        content_store = self.path.suffix != ".jsonl"
        (self.path if content_store else self.path.parent).mkdir(parents=True, exist_ok=True)
        while True:
            row = self._pending.get()
            try:
                encoded = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                if content_store:
                    target = self.path / (row["raw_payload_sha256"] + ".json")
                    try:
                        with target.open("x", encoding="utf-8") as fh:
                            fh.write(encoded)
                    except FileExistsError:
                        pass
                else:
                    with self.path.open("a", encoding="utf-8") as fh:
                        fh.write(encoded + "\n")
            finally:
                self._pending.task_done()


def configured_birth_audit() -> PumpPortalBirthAudit:
    enabled = os.environ.get("PUMPPORTAL_CANONICAL_BIRTH_RETENTION_ENABLED", "1").lower() in {"1", "true", "yes"}
    path = os.environ.get(
        "PUMPPORTAL_CANONICAL_BIRTH_RETENTION_PATH",
        str(Path(__file__).resolve().parents[2] / "database" / "evidence_platform" / "production" / "pumpportal_birth_evidence"),
    )
    return PumpPortalBirthAudit(enabled=enabled, path=path)
