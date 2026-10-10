"""Resumable, explicitly-invoked controller for DEV-014 historical research.

This module deliberately has no scheduler, transport, database access, or
provider credentials.  A separately authorized caller may supply the existing
runtime gate, shared-budget adapter, and one-request transport to ``run``.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from src.ops.watchtower_historical_budget import HistoricalForensicsBudgetAdmission

ROOT = Path(__file__).resolve().parents[2]
CHRONOLOGY_PROJECTION = ROOT / "docs/audits/dev014_watchtower_full_chronology_projection_20261010.v1.json"


class ControllerDenied(RuntimeError):
    """A bounded historical session cannot continue safely."""


@dataclass(frozen=True)
class SessionBounds:
    max_requests: int
    max_wall_seconds: int
    max_evidence_bytes: int
    max_consecutive_failures: int
    max_health_gate_failures: int

    def validate(self) -> None:
        if any(not isinstance(value, int) or value <= 0 for value in self.__dict__.values()):
            raise ControllerDenied("FINITE_POSITIVE_SESSION_BOUNDS_REQUIRED")
        if self.max_evidence_bytes >= 500_000_000:
            raise ControllerDenied("SINGLE_FILE_BOUND_TOO_LARGE")


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _request(mint: str, timestamp: int, batch: str) -> dict[str, Any]:
    params = {"address": mint, "chart_type": "mcap", "currency": "usd", "type": "1m", "mode": "range", "padding": "false", "time_from": timestamp, "time_to": timestamp + 3600}
    return {"endpoint": "GET /defi/v3/ohlcv", "params": params, "request_identity": _digest({"batch": batch, "mint": mint, "params": params})}


# The rank assignments are frozen by the existing recent-first plan.  Entry
# evidence is deliberately not copied here: it is projected only from the V2
# population below, where creation and qualified-opening facts are retained.
EXTENDED_RECENT_FIRST_RANKS: tuple[tuple[int, str], ...] = (
    (71, "8mnDxKCJUS59RzvesoYEk2ivtVhn5mBS2tmKj32ppump"),
    (72, "9Q2DMkmqkFPHAQHNgkRLUN7XYoSp87GqUAFVoV71pump"),
    (73, "59F95Wkn5LRpiKmG2FE3g87Lzkz4redvNJNKSi1Npump"),
    (74, "3KdoU8X1DJBvmmxYYttJWQ1KiMfarg3bFAdeCAmspump"),
    (75, "8xySnQGLve5959cr4QiJxpNGW5wkBWPh4VxgJVycpump"),
    (76, "2n3bPZcfbcUNgaP6Ktw8E1wYoycAhuR1sSHa7uB6pump"),
    (77, "A8MBwPZwR6msXrDBRzg8b1W7BpjjPch1WsTELQJzpump"),
    (78, "9DPUFzAMZfbh4ie5RLJ1C7Zhes5eEayPsTU91tSzpump"),
    (79, "8n1Qyjo7LrQMFaZsTJ3K1qPkNRMxDmS6R4eDjkvjpump"),
    (80, "FCoXFnRQsHtb2rPReJUrtkPD8qBm8UVbXNdHx49spump"),
    (81, "7mqDrCDrw7zxqQs3KiRWmx3bns7uy3mMSiAKgDotpump"),
    (82, "7rnZdzwZMkrviU1S6r74e1gGfMnnDzmMSXTgrXKSpump"),
    (83, "5PmsmHC6aqCuBzECBLX5Mv9FA8Pgd9rfrX9bT4kzpump"),
    (84, "B5yWXR3PZeRuEDs7P55SBgyZEiVu3Ak7k3uWmYLdpump"),
    (85, "4Z9eH1BCuq2Y6FFRXkZft9LoxwMqQWo7bJsEbcd7pump"),
    (86, "BvnqdYwuFrvi9ds63EtwQLYME89ikJkULnJPAd83pump"),
    (87, "FvYjAkbhhzpp3Rk3BoSLVKjYpi2zJ4baAS26R9hupump"),
    (88, "7KG3FFuwqs21v2CHL2UEZtrcZpH3tm676as6SEMdpump"),
    (89, "BULbQ4j9WU6r63idgdQVsjt6i3nKSTXgyj7Qqcdpump"),
    (90, "AoMGtae9dteY2XteudmTa9W4uZLfuDVHXxcduJE1pump"),
)


class HistoricalBackfillController:
    """Owns one compact state file and yields deterministic historical work."""

    def __init__(self, state_path: str | Path, reconciliation: dict[str, Any], population: dict[str, Any]):
        self.state_path = Path(state_path)
        self.lock_path = self.state_path.with_suffix(self.state_path.suffix + ".lock")
        self.reconciliation = reconciliation
        self.population = population
        self._lock_fd: int | None = None

    def _chronology(self) -> list[dict[str, Any]]:
        """Load the immutable rank authority; never derive or repair ranks."""
        try:
            value = json.loads(CHRONOLOGY_PROJECTION.read_text())
        except (OSError, ValueError) as exc:
            raise ControllerDenied("CHRONOLOGY_PROJECTION_UNREADABLE") from exc
        payload = {key: item for key, item in value.items() if key != "content_hash"}
        if (value.get("population_identity") != "WATCHTOWER_FORENSIC_POPULATION_V2_20261009"
                or value.get("content_hash") != _digest(payload)
                or value.get("tie_breaker_contract") != "NO_EQUAL_QUALIFIED_CREATION_TIMESTAMPS; ties fail closed and remain unranked"):
            raise ControllerDenied("CHRONOLOGY_PROJECTION_INVALID")
        ranked = value.get("ranked")
        if not isinstance(ranked, list) or len(ranked) != 597:
            raise ControllerDenied("CHRONOLOGY_PROJECTION_INVALID")
        ranks = [item.get("rank") for item in ranked]; mints = [item.get("mint") for item in ranked]; timestamps = [item.get("creation_timestamp") for item in ranked]
        if (ranks != list(range(1, 598)) or len(set(mints)) != 597 or len(set(timestamps)) != 597
                or any(not isinstance(timestamp, int) or timestamp <= 0 for timestamp in timestamps)
                or any(timestamps[index] <= timestamps[index + 1] for index in range(596))):
            raise ControllerDenied("CHRONOLOGY_PROJECTION_INVALID")
        return ranked

    def acquire(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self._lock_fd)
            self._lock_fd = None
            raise ControllerDenied("HISTORICAL_CONTROLLER_ALREADY_OWNED") from exc

    def release(self) -> None:
        if self._lock_fd is not None:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
            self._lock_fd = None

    def __enter__(self) -> "HistoricalBackfillController":
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()

    def _state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"version": 1, "paused": False, "cancelled": False, "completed_request_ids": [], "deferred": [], "evidence_bytes": 0}
        try:
            value = json.loads(self.state_path.read_text())
        except (OSError, ValueError) as exc:
            raise ControllerDenied("HISTORICAL_CONTROLLER_STATE_UNREADABLE") from exc
        if not isinstance(value, dict) or value.get("version") != 1:
            raise ControllerDenied("HISTORICAL_CONTROLLER_STATE_INVALID")
        return value

    def _persist(self, state: dict[str, Any]) -> None:
        if self._lock_fd is None:
            raise ControllerDenied("HISTORICAL_CONTROLLER_OWNERSHIP_REQUIRED")
        encoded = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
        if len(encoded) >= 1_000_000:
            raise ControllerDenied("HISTORICAL_CONTROLLER_STATE_BOUND_EXCEEDED")
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        with temporary.open("wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.state_path)

    @staticmethod
    def _qualified_entry_mc(*, opening: dict[str, Any], anchor: dict[str, Any]) -> float:
        """Project only Entry MC bound to the exact frozen Entry anchor."""
        entry_mc = opening.get("entry_mc_usd")
        if (opening.get("status") != "QUALIFIED" or opening.get("entry_timestamp") != anchor.get("timestamp")
                or opening.get("provenance") != anchor.get("provenance")
                or not isinstance(entry_mc, (int, float)) or isinstance(entry_mc, bool)
                or not math.isfinite(entry_mc) or entry_mc <= 0):
            raise ControllerDenied("QUALIFIED_ENTRY_MC_UNAVAILABLE")
        return float(entry_mc)

    def pause(self) -> None:
        state = self._state(); state["paused"] = True; self._persist(state)

    def cancel(self) -> None:
        state = self._state(); state["cancelled"] = True; self._persist(state)

    def resume(self) -> None:
        state = self._state(); state["paused"] = False; self._persist(state)

    def work(self) -> list[dict[str, Any]]:
        """Return catch-up first, then frozen newest-first continuation.

        Missing anchors are returned as explicit deferred records, never omitted.
        """
        planned = []
        launches = {launch["mint"]: launch for launch in self.population["launches"]}
        for item in self.reconciliation["catchup_batch"]["records"]:
            planned.append({"priority": 2, "rank": item["rank"], "mint": item["mint"], "request": item["proposed_request"], "anchor": item["anchor"], "kind": "CATCHUP"})
        for item in self.reconciliation["batch_5"]["records"]:
            planned_item = {"priority": 3, "rank": item["rank"], "mint": item["mint"], "request": item["proposed_request"], "anchor": item["anchor"], "kind": "HISTORICAL", "deferred_reason": item["skip_reason"]}
            if item["anchor"]["class"] == "QUALIFIED_ENTRY_ANCHOR":
                opening = launches.get(item["mint"], {}).get("evidence", {}).get("opening", {})
                planned_item["entry_mc_usd"] = self._qualified_entry_mc(opening=opening, anchor=item["anchor"])
            planned.append(planned_item)
        # The frozen 70-mint recent-first population supplies ranks 51-70. The
        # rank 71-90 assignments remain fixed above and must resolve to the
        # same V2-population creation facts in strictly descending order.
        continuation = list(enumerate(self.population["reconciliation"]["most_recent_70_mints"][50:], 51))
        continuation.extend(EXTENDED_RECENT_FIRST_RANKS)
        previous_creation_timestamp: int | None = None
        for rank, mint in continuation:
            launch = launches.get(mint)
            if launch is None:
                raise ControllerDenied("FROZEN_CONTINUATION_MINT_UNAVAILABLE")
            if rank >= 70:
                creation = launch.get("creation", {})
                creation_timestamp = creation.get("timestamp")
                if rank >= 71 and (creation.get("chronology_status") != "QUALIFIED_CREATION_TIMESTAMP"
                        or not isinstance(creation_timestamp, int)
                        or creation_timestamp <= 0
                        or (previous_creation_timestamp is not None and creation_timestamp >= previous_creation_timestamp)):
                    raise ControllerDenied("FROZEN_CONTINUATION_ORDER_INVALID")
                previous_creation_timestamp = creation_timestamp
            opening = launch.get("evidence", {}).get("opening", {})
            if opening.get("status") == "QUALIFIED":
                anchor = {"class": "QUALIFIED_ENTRY_ANCHOR", "timestamp": opening["entry_timestamp"], "provenance": opening["provenance"]}
                try:
                    entry_mc = self._qualified_entry_mc(opening=opening, anchor=anchor)
                except ControllerDenied:
                    opening = {}
                else:
                    planned.append({"priority": 3, "rank": rank, "mint": mint, "request": _request(mint, anchor["timestamp"], "DEV014_HISTORICAL_CONTINUATION"), "anchor": anchor, "kind": "HISTORICAL", "entry_mc_usd": entry_mc})
                    continue
            planned.append({"priority": 3, "rank": rank, "mint": mint, "request": None, "anchor": {"class": "NO_QUALIFIED_ENTRY_OR_OBSERVED_PRICE_ANCHOR", "timestamp": None, "provenance": None}, "kind": "HISTORICAL", "deferred_reason": "No qualified Entry anchor or separately qualified observed-price anchor is retained."})
        # The immutable projection is authority only for later ranks; ranks
        # 1-90 above retain their prior controller paths byte-for-byte.
        for item in self._chronology()[90:]:
            rank, mint = item["rank"], item["mint"]
            launch = launches.get(mint)
            if launch is None:
                raise ControllerDenied("CHRONOLOGY_PROJECTION_MINT_UNAVAILABLE")
            opening = launch.get("evidence", {}).get("opening", {})
            if opening.get("status") == "QUALIFIED":
                anchor = {"class": "QUALIFIED_ENTRY_ANCHOR", "timestamp": opening.get("entry_timestamp"), "provenance": opening.get("provenance")}
                try:
                    entry_mc = self._qualified_entry_mc(opening=opening, anchor=anchor)
                except ControllerDenied:
                    pass
                else:
                    planned.append({"priority": 3, "rank": rank, "mint": mint, "request": _request(mint, anchor["timestamp"], "DEV014_HISTORICAL_CONTINUATION"), "anchor": anchor, "kind": "HISTORICAL", "entry_mc_usd": entry_mc})
                    continue
            planned.append({"priority": 3, "rank": rank, "mint": mint, "request": None, "anchor": {"class": "NO_QUALIFIED_ENTRY_OR_OBSERVED_PRICE_ANCHOR", "timestamp": None, "provenance": None}, "kind": "HISTORICAL", "deferred_reason": "No qualified Entry anchor or separately qualified observed-price anchor is retained."})
        return planned

    def next_work(self) -> dict[str, Any] | None:
        state = self._state()
        if state["paused"] or state["cancelled"]:
            return None
        completed = set(state["completed_request_ids"])
        deferred = {item["mint"] for item in state["deferred"]}
        for item in self.work():
            if item["request"] is None:
                if item["mint"] not in deferred:
                    state["deferred"].append({"rank": item["rank"], "mint": item["mint"], "reason": item["deferred_reason"]})
                    self._persist(state)
                continue
            if item["request"]["request_identity"] not in completed:
                return item
        return None

    def run(self, bounds: SessionBounds, *, health_gate: Callable[[], Any], live_pending: Callable[[], bool],
            admit: Callable[..., None], transport: Callable[[dict[str, Any]], tuple[dict[str, Any], int]]) -> dict[str, Any]:
        """Run only an explicitly bounded session; callers provide all live effects."""
        bounds.validate()
        started = time.monotonic(); requests = failures = health_failures = 0
        while requests < bounds.max_requests and time.monotonic() - started < bounds.max_wall_seconds:
            state = self._state()
            if state["paused"] or state["cancelled"]:
                return {"status": "PAUSED_OR_CANCELLED", "requests": requests}
            if live_pending():
                return {"status": "LIVE_PRIORITY_PENDING", "requests": requests}
            item = self.next_work()
            if item is None:
                return {"status": "EXHAUSTED", "requests": requests}
            try:
                health_gate()
            except Exception as exc:
                health_failures += 1
                if health_failures >= bounds.max_health_gate_failures:
                    return {"status": "HEALTH_GATE_DENIED", "requests": requests, "reason": str(exc)}
                continue
            request = item["request"]
            try:
                admit(mint=item["mint"], request_identity=request["request_identity"])
                evidence, evidence_bytes = transport(request)
            except Exception as exc:
                failures += 1
                if failures >= bounds.max_consecutive_failures:
                    return {"status": "FAILURE_BOUND_REACHED", "requests": requests, "reason": str(exc)}
                continue
            if not isinstance(evidence, dict) or not isinstance(evidence_bytes, int) or evidence_bytes < 0:
                raise ControllerDenied("COMPACT_EVIDENCE_CONTRACT_INVALID")
            if state["evidence_bytes"] + evidence_bytes > bounds.max_evidence_bytes:
                return {"status": "EVIDENCE_BOUND_REACHED", "requests": requests}
            state["completed_request_ids"].append(request["request_identity"])
            state["evidence_bytes"] += evidence_bytes
            self._persist(state)
            requests += 1; failures = 0
        return {"status": "SESSION_BOUND_REACHED", "requests": requests}

    def run_with_shared_budget(self, bounds: SessionBounds, *, queue_root: str | Path, health_gate: Callable[[], Any],
                               live_pending: Callable[[], bool], transport: Callable[[dict[str, Any]], tuple[dict[str, Any], int]],
                               admission_factory: Callable[[str | Path], HistoricalForensicsBudgetAdmission] = HistoricalForensicsBudgetAdmission) -> dict[str, Any]:
        """Use the sole approved historical admission adapter; never a new ledger."""
        admission = admission_factory(queue_root)
        return self.run(bounds, health_gate=health_gate, live_pending=live_pending, admit=admission.admit, transport=transport)
