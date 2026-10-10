"""Crash-safe, explicitly invoked binding for DEV-014 historical requests."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Callable

from scripts.run_watchtower_recent_first_price_forensics_batch_4 import HEADER_ALLOWLIST, _normalize
from src.ops.watchtower_observed_minimum import observed_minima


STATES = frozenset({"PENDING", "ADMITTED", "ATTEMPTED", "COMPLETED", "OUTCOME_UNKNOWN", "DEFERRED"})
MAX_BYTES = 1_000_000


class BindingDenied(RuntimeError): pass


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _atomic(path: Path, value: dict[str, Any]) -> None:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    if len(raw) >= MAX_BYTES: raise BindingDenied("COMPACT_ARTIFACT_BOUND_EXCEEDED")
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as handle:
        handle.write(raw); handle.flush(); os.fsync(handle.fileno())
    os.replace(tmp, path)


class HistoricalExecutionBinding:
    """A file-backed request journal; transport is injected, never scheduled."""
    def __init__(self, path: str | Path): self.path = Path(path)

    def read(self) -> dict[str, Any]:
        if not self.path.exists(): return {"version": 1, "records": []}
        try: value = json.loads(self.path.read_text())
        except (OSError, ValueError) as exc: raise BindingDenied("EXECUTION_JOURNAL_UNREADABLE") from exc
        if value.get("version") != 1 or not isinstance(value.get("records"), list): raise BindingDenied("EXECUTION_JOURNAL_INVALID")
        return value

    def _write(self, value: dict[str, Any]) -> None: self.path.parent.mkdir(parents=True, exist_ok=True); _atomic(self.path, value)

    @staticmethod
    def _validate_evidence_readiness(item: dict[str, Any]) -> dict[str, Any]:
        """Reject incomplete frozen work before it can reach budget admission."""
        request, anchor = item.get("request"), item.get("anchor")
        if not isinstance(item.get("mint"), str) or not item["mint"] or not isinstance(request, dict) or not isinstance(anchor, dict):
            raise BindingDenied("EVIDENCE_READINESS_INVALID_WORK_ITEM")
        timestamp, provenance = anchor.get("timestamp"), anchor.get("provenance")
        params = request.get("params")
        if (not isinstance(timestamp, int) or timestamp <= 0
                or not isinstance(params, dict) or params.get("time_from") != timestamp or params.get("time_to") != timestamp + 3600
                or params.get("type") != "1m" or not isinstance(request.get("request_identity"), str) or not request["request_identity"]):
            raise BindingDenied("EVIDENCE_READINESS_FROZEN_REQUEST_INVALID")
        if anchor.get("class") == "QUALIFIED_ENTRY_ANCHOR":
            entry_mc = item.get("entry_mc_usd")
            if (not isinstance(provenance, str) or not provenance or not isinstance(entry_mc, (int, float)) or isinstance(entry_mc, bool)
                    or not math.isfinite(entry_mc) or entry_mc <= 0):
                raise BindingDenied("EVIDENCE_READINESS_QUALIFIED_ENTRY_MC_UNAVAILABLE")
        elif anchor.get("class") == "OBSERVED_PRICE_ANCHOR":
            if not isinstance(anchor.get("source"), str) or not anchor["source"]:
                raise BindingDenied("EVIDENCE_READINESS_OBSERVED_ANCHOR_INVALID")
        else:
            raise BindingDenied("EVIDENCE_READINESS_ANCHOR_CLASS_INVALID")
        return request

    def recover(self) -> dict[str, Any]:
        journal = self.read(); changed = False
        for record in journal["records"]:
            if record.get("state") == "ATTEMPTED" and record.get("evidence_identity"):
                record["state"] = "COMPLETED"; record["recovery_reason"] = "COMPACT_EVIDENCE_ALREADY_DURABLE"; changed = True
                continue
            if record.get("state") in {"ADMITTED", "ATTEMPTED"}:
                record["state"] = "OUTCOME_UNKNOWN"; record["recovery_reason"] = "INTERRUPTED_AFTER_POSSIBLE_ADMISSION_OR_ATTEMPT"; changed = True
        if changed: self._write(journal)
        return journal

    def execute(self, item: dict[str, Any], *, health_gate: Callable[[], Any], live_pending: Callable[[], bool] | None = None,
                admit: Callable[..., None], transport: Callable[[dict[str, Any]], Any], crash_at: str | None = None) -> dict[str, Any]:
        request = self._validate_evidence_readiness(item)
        identity = request["request_identity"]; journal = self.recover()
        existing = next((x for x in journal["records"] if x.get("request_identity") == identity), None)
        if existing and existing.get("state") != "PENDING":
            raise BindingDenied(f"REQUEST_NOT_RETRYABLE:{existing.get('state')}")
        # Historical work shares the existing atomic budget but does not claim a
        # system-wide LIVE-priority guarantee.  A caller with an independently
        # qualified predicate may defer here; ordinary callers must pass None.
        if live_pending is not None and live_pending(): return {"status": "LIVE_PRIORITY_PENDING"}
        health_gate()
        if existing:
            # PENDING is the sole pre-admission state.  It is safe to resume
            # because no budget admission has been attempted yet.
            record = existing
        else:
            record = {"mint": item["mint"], "chronological_rank": item["rank"], "anchor": item["anchor"], "request_identity": identity,
                      "requested_window": {"time_from": request["params"]["time_from"], "time_to": request["params"]["time_to"], "interval": "1m"}, "state": "PENDING"}
            journal["records"].append(record); self._write(journal)
            if crash_at == "PENDING": raise RuntimeError("SIMULATED_CRASH")
        # Persist the conservative post-admission boundary before invoking
        # the shared budget gate.  A process crash immediately after a debit
        # can therefore never be mistaken for a retryable PENDING request.
        record["state"] = "ADMITTED"; self._write(journal)
        admit(mint=item["mint"], request_identity=identity)
        if crash_at == "AFTER_BUDGET": raise RuntimeError("SIMULATED_CRASH")
        if crash_at == "ADMITTED": raise RuntimeError("SIMULATED_CRASH")
        record["state"] = "ATTEMPTED"; self._write(journal)
        if crash_at == "ATTEMPTED": raise RuntimeError("SIMULATED_CRASH")
        try: outcome = transport({"endpoint": request["endpoint"].removeprefix("GET "), "request_parameters": request["params"]})
        except Exception:
            self.recover(); raise
        rows, failure = _normalize(outcome, start=request["params"]["time_from"], end=request["params"]["time_to"])
        if crash_at == "RESPONSE": raise RuntimeError("SIMULATED_CRASH")
        evidence = {**record, "provider": "BIRDEYE", "http_status": outcome.status_code, "provider_metadata": {str(k).lower(): str(v) for k,v in outcome.response_headers.items() if str(k).lower() in HEADER_ALLOWLIST}, "normalization_status": "FAILED", "failure": failure}
        if failure is None:
            candles = [{"timestamp": int(row["timestamp"]), "low_mc_usd": float(row["low_mc_usd"])} for row in rows]
            if item["anchor"]["class"] == "QUALIFIED_ENTRY_ANCHOR":
                # Entry price is intentionally supplied by the frozen item, never invented here.
                evidence["entry_relative_observed_minima"] = observed_minima(entry_timestamp=item["anchor"]["timestamp"], entry_mc_usd=float(item["entry_mc_usd"]), candles=candles, provider_provenance="BIRDEYE_1M_MCAP")["results"]
            else: evidence["observed_price_relative_extrema"] = observed_minima(entry_timestamp=item["anchor"]["timestamp"], entry_mc_usd=float(rows[0]["open_mc_usd"]), candles=candles, provider_provenance="BIRDEYE_1M_MCAP")["results"]
            evidence["normalization_status"] = "NORMALIZED"; evidence["failure"] = None; evidence["returned_candle_count"] = len(rows)
        evidence["evidence_identity"] = _digest({k:v for k,v in evidence.items() if k != "state"})
        record.update(evidence)
        if crash_at == "EVIDENCE": self._write(journal); raise RuntimeError("SIMULATED_CRASH")
        record["state"] = "COMPLETED"; self._write(journal)
        return record
