"""Durable, single-worker recovery of the frozen Near47 JIT ledger.

The runner deliberately owns no provider payload store.  Its checkpoint and
compact-result files are the durable queue and evidence records for this one
already-frozen research contract.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from src.core.walkback_worker import _extract_sol_sender, _get_sigs, _get_tx

ROOT = Path(__file__).resolve().parents[2]
BYZC = "ByZc7RNeYowEg2jKo2giytWb9WmNyZPrQ1hXhnGSzHTY"
DF8C = "Df8CJQR7fUTYAQSQwtsgUDs5b6JWNULzwhJJXDCJkdya"
CALIBRATION_MINT = "2KnKKGQ7SRBtpeJmDqy5UhBj1RZ38LR3CXJK9jcJpump"
MAX_TIME = 10
MAX_SLOT = 23
SIG_LIMIT = 100
MAX_PROVIDER_CALLS_PER_IDENTITY = SIG_LIMIT + 2
CLAIM_LEASE_SECONDS = 300
CONTRACT = "NEAR47_DF8C_BYZC_DURABLE_RUNNER_V1"
TERMINAL = {"ACKED", "FAILED_TERMINAL"}


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w") as handle:
        json.dump(value, handle, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


@contextmanager
def _lock(path: Path):
    """An OS-level lock makes claim/readback transitions process-safe."""
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _keys(tx: dict) -> list[str]:
    return [x if isinstance(x, str) else x.get("pubkey") for x in tx.get("transaction", {}).get("message", {}).get("accountKeys", [])]


def _received(tx: dict, wallet: str) -> int | None:
    try:
        i = _keys(tx).index(wallet)
        delta = tx.get("meta", {}).get("postBalances", [])[i] - tx.get("meta", {}).get("preBalances", [])[i]
        return delta if delta > 0 else None
    except (ValueError, IndexError, TypeError):
        return None


class Near47Runner:
    def __init__(self, *, checkpoint: Path, results: Path, release: Path, ledger: Path, taxonomy: Path, db: Path, owner: str | None = None):
        self.checkpoint, self.results = checkpoint, results
        self.release, self.ledger, self.taxonomy, self.db = release, ledger, taxonomy, db
        self.owner = owner or f"pid-{os.getpid()}"
        self.lock_path = checkpoint.with_suffix(checkpoint.suffix + ".lock")

    def verify_release(self) -> tuple[dict, list[dict], dict]:
        release = json.loads(self.release.read_text())
        if release.get("calibration_result", {}).get("state") != "CALIBRATION_PASS": raise ValueError("RELEASE_LEDGER_INVALID")
        if release.get("independent_replay", {}).get("state") != "CALIBRATION_PASS": raise ValueError("RELEASE_LEDGER_INVALID")
        if release.get("cohort_wide_release_authorized") is not True: raise ValueError("RELEASE_LEDGER_INVALID")
        if release.get("bounds") != {"max_slot_delta": MAX_SLOT, "max_time_delta_seconds": MAX_TIME}: raise ValueError("RELEASE_LEDGER_INVALID")
        source = json.loads(self.ledger.read_text())
        rows = [x for x in source["ledger"] if x["mint"] != CALIBRATION_MINT]
        if len(rows) != 46 or len({x["request_identity"] for x in rows}) != 46: raise ValueError("RELEASE_LEDGER_INVALID")
        taxonomy = json.loads(self.taxonomy.read_text())
        fingerprints = {x["mint"]: int(x["raw_amount_lamports"]) for x in taxonomy["near47"]["rows"]}
        if set(fingerprints) != {x["mint"] for x in source["ledger"]}: raise ValueError("RELEASE_LEDGER_INVALID")
        return release, rows, fingerprints

    def initialize(self) -> dict:
        release, rows, fingerprints = self.verify_release()
        with _lock(self.lock_path):
            if self.checkpoint.exists(): return self._load_locked()
            state = {"contract": CONTRACT, "max_concurrency": 1, "release_checkpoint_id": release["verification_request_id"],
                     "release_checkpoint_digest": release["provenance_digest"], "remaining46_ledger_digest": digest(rows),
                     "max_provider_calls_per_identity": MAX_PROVIDER_CALLS_PER_IDENTITY,
                     "total_hard_provider_call_ceiling": len(rows) * MAX_PROVIDER_CALLS_PER_IDENTITY,
                     "accounting": {"total_new_provider_calls": 0, "http_200": 0, "http_429": 0, "other_http_status": 0, "rpc_success": 0, "rpc_failure": 0, "backoff_events": 0, "retryable_failures": 0, "terminal_failures": 0, "untracked_provider_calls": 0},
                     "requests": {x["request_identity"]: {**x, "fingerprint_amount": fingerprints[x["mint"]], "attempt_state": "PENDING", "attempt_number": 0} for x in rows}, "checkpoint_sequence": 0}
            self._commit_locked(state)
            return state

    def _load_locked(self) -> dict:
        state = json.loads(self.checkpoint.read_text())
        saved = state.pop("checkpoint_digest", None)
        if saved != digest(state): raise ValueError("CRASH_RESUME_FAILURE")
        state["checkpoint_digest"] = saved
        return state

    def _commit_locked(self, state: dict) -> None:
        state.pop("checkpoint_digest", None)
        state["checkpoint_sequence"] += 1
        state["checkpoint_digest"] = digest(state)
        _write(self.checkpoint, state)

    def reconcile(self) -> dict:
        state = self.initialize()
        result_rows = json.loads(self.results.read_text()).get("rows", {}) if self.results.exists() else {}
        counts = {k: 0 for k in ("PENDING", "CLAIMED", "BACKOFF", "COMPLETED_UNACKED", "ACKED", "FAILED_RETRYABLE", "FAILED_TERMINAL")}
        for rid, row in state["requests"].items():
            status = row["attempt_state"]
            if rid in result_rows and status not in TERMINAL: status = "COMPLETED_UNACKED"
            counts[status] = counts.get(status, 0) + 1
        return {"preexisting_completed": sum(1 for rid in state["requests"] if rid in result_rows), "preexisting_acked": counts["ACKED"], "pending_to_process": counts["PENDING"], "states": counts}

    def claim(self) -> tuple[str, dict] | None:
        with _lock(self.lock_path):
            state = self._load_locked()
            # Commit-before-ack crash recovery does not redispatch.
            stored = json.loads(self.results.read_text()).get("rows", {}) if self.results.exists() else {}
            for rid, row in state["requests"].items():
                if rid in stored and row["attempt_state"] != "ACKED":
                    row["attempt_state"] = "ACKED"; row["acked_at"] = time.time(); self._commit_locked(state); return ("ACK_ONLY", {"request_identity": rid})
            now = time.time()
            for rid, row in state["requests"].items():
                if row["attempt_state"] == "BACKOFF" and row.get("backoff_until", 0) <= now: row["attempt_state"] = "PENDING"
                if row["attempt_state"] in {"CLAIMED", "ACQUIRING"} and row.get("claim_expires_at", 0) <= now:
                    # A dead worker never holds a claim indefinitely.  The same
                    # logical identity is resumed; no replacement is created.
                    row["attempt_state"] = "PENDING"
                if row["attempt_state"] == "PENDING":
                    row.update({"attempt_state": "CLAIMED", "claimed_at": now, "claim_owner": self.owner, "claim_expires_at": now + CLAIM_LEASE_SECONDS, "attempt_number": row["attempt_number"] + 1})
                    self._commit_locked(state); return rid, dict(row)
            return None

    def mark_acquiring(self, rid: str) -> None:
        with _lock(self.lock_path):
            state = self._load_locked(); row = state["requests"][rid]
            if row["attempt_state"] != "CLAIMED" or row.get("claim_owner") != self.owner: raise ValueError("CLAIM_NOT_ATOMIC")
            row["attempt_state"] = "ACQUIRING"; self._commit_locked(state)

    def _target_amount(self, mint: str, signature: str, tx: dict | None) -> tuple[int | None, str | None]:
        amount = _received(tx, BYZC) if tx else None
        if amount is not None: return amount, "BALANCE_DELTA"
        # The read-only DB connection is intentionally opened only after network acquisition has ended.
        conn = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        try:
            row = conn.execute("select net_destination_lamports from wt_walkback_atomic_flows where mint=? and signature=?", (mint, signature)).fetchone()
            return (row[0], "RETAINED_ATOMIC_FLOW") if row else (None, None)
        finally: conn.close()

    def acquire_reduce(self, row: dict, transport: Callable[[str, list], object] | None = None) -> dict:
        # No sqlite connection exists while any provider call is in flight.
        rid = row["request_identity"]
        start_slot, start_time = row["target_slot"] - MAX_SLOT, row["target_time"] - MAX_TIME
        page = _get_sigs(BYZC, SIG_LIMIT, before=row["target_signature"], rpc_transport=transport)
        target_tx = _get_tx(row["target_signature"], rpc_transport=transport)
        if not page or not target_tx: raise RuntimeError("PROVIDER_FAILURE")
        first_slot = min((x.get("slot", row["target_slot"]) for x in page)); first_time = min((x.get("blockTime", row["target_time"]) for x in page))
        slot_crossed, time_crossed = first_slot <= start_slot, first_time <= start_time
        truncated = len(page) >= SIG_LIMIT and not (slot_crossed and time_crossed)
        window = [x for x in page if x.get("slot") is not None and x.get("blockTime") is not None and start_slot <= x["slot"] < row["target_slot"] and start_time <= x["blockTime"] <= row["target_time"]]
        incoming = []
        for item in window:
            tx = _get_tx(item["signature"], rpc_transport=transport)
            if tx is None: raise RuntimeError("PROVIDER_FAILURE")
            source, amount = _extract_sol_sender(tx, BYZC), _received(tx, BYZC)
            if source and amount is not None: incoming.append({"signature": item["signature"], "slot": tx.get("slot"), "timestamp": tx.get("blockTime"), "source": source, "amount": amount})
        target_amount, amount_source = self._target_amount(row["mint"], row["target_signature"], target_tx)
        coverage = slot_crossed and time_crossed and not truncated  # signature-history ordering provides no internal gap marker.
        df = [x for x in incoming if x["source"] == DF8C]
        others = [x for x in incoming if x["source"] != DF8C]
        reason = None
        if not coverage: state, reason = "INCOMPLETE_EVIDENCE", "REQUESTED_INTERSECTION_TRUNCATED" if truncated else ("SLOT_BOUND_NOT_CROSSED" if not slot_crossed else "TIME_BOUND_NOT_CROSSED")
        elif target_amount is None: state, reason = "INCOMPLETE_EVIDENCE", "TARGET_AMOUNT_MISSING"
        elif len(df) > 1: state, reason = "INCOMPLETE_EVIDENCE", "AMBIGUOUS_MULTIPLE_DF8C"
        elif df and others: state, reason = "INCOMPLETE_EVIDENCE", "AMBIGUOUS_FUNDER"
        elif df:
            event = df[0]; sd, td = row["target_slot"] - event["slot"], row["target_time"] - event["timestamp"]
            if sd <= 0 or td < 0: state, reason = "INCOMPLETE_EVIDENCE", "ORDERING_UNPROVEN"
            else: state = "PROVEN_DF8C_JIT"
        elif others: state, reason = "INCOMPLETE_EVIDENCE", "AMBIGUOUS_FUNDER"  # nearby funding is not enough to prove target supply.
        else: state = "NEGATIVE_COMPLETE_WINDOW"
        event = df[0] if len(df) == 1 else None
        compact = {"request_identity": rid, "mint": row["mint"], "fingerprint_amount": row["fingerprint_amount"], "target_signature": row["target_signature"], "target_slot": row["target_slot"], "target_timestamp": row["target_time"], "target_amount": target_amount, "target_amount_source": amount_source, "requested_slot_start": start_slot, "requested_time_start": start_time, "covered_slot_low": first_slot, "covered_time_low": first_time, "slot_lower_bound_crossed": slot_crossed, "time_lower_bound_crossed": time_crossed, "internal_gaps": 0, "requested_intersection_truncated": truncated, "coverage_complete": coverage, "incoming_funder_count": len(incoming), "df8c_event_count": len(df), "other_funder_event_count": len(others), "other_funder_addresses": sorted({x["source"] for x in others}), "df8c_signature": event["signature"] if event else None, "df8c_amount": event["amount"] if event else None, "time_delta": row["target_time"] - event["timestamp"] if event else None, "slot_delta": row["target_slot"] - event["slot"] if event else None, "provider_call_count": len(window) + 2, "result_state": state, "incomplete_reason": reason, "contract": CONTRACT}
        compact["provenance_digest"] = digest(compact)
        return compact

    def commit_ack(self, rid: str, compact: dict) -> None:
        with _lock(self.lock_path):
            state = self._load_locked(); row = state["requests"][rid]
            row["attempt_state"] = "REDUCED"; self._commit_locked(state)
            results = json.loads(self.results.read_text()) if self.results.exists() else {"contract": CONTRACT, "rows": {}}
            if rid in results["rows"] and results["rows"][rid]["provenance_digest"] != compact["provenance_digest"]: raise ValueError("DUPLICATE_IDENTITY")
            results["rows"][rid] = {**compact, "committed_at": time.time()}; _write(self.results, results)
            if json.loads(self.results.read_text())["rows"][rid]["provenance_digest"] != compact["provenance_digest"]: raise ValueError("COMMIT_BEFORE_ACK_FAILURE")
            row["attempt_state"] = "COMMITTED"; self._commit_locked(state)
            state["accounting"]["total_new_provider_calls"] += compact["provider_call_count"]
            state["accounting"]["rpc_success"] += compact["provider_call_count"]
            row["attempt_state"] = "ACKED"; row["acked_at"] = time.time(); self._commit_locked(state)

    def backoff(self, rid: str, error: str, seconds: int = 60) -> None:
        with _lock(self.lock_path):
            state = self._load_locked(); row = state["requests"][rid]
            row.update({"attempt_state": "BACKOFF", "last_error_class": error, "backoff_until": time.time() + seconds, "last_attempt_at": time.time()})
            state["accounting"]["backoff_events"] += 1; state["accounting"]["retryable_failures"] += 1; self._commit_locked(state)

    def run(self, transport: Callable[[str, list], object] | None = None, max_rows: int | None = None) -> dict:
        processed = 0
        while max_rows is None or processed < max_rows:
            claim = self.claim()
            if claim is None: break
            rid, row = claim
            if rid == "ACK_ONLY": continue
            try:
                self.mark_acquiring(rid)
                compact = self.acquire_reduce(row, transport); self.commit_ack(rid, compact); processed += 1
            except RuntimeError as error:
                self.backoff(rid, str(error)); break
        return self.reconcile()
