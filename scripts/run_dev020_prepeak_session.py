"""One bounded DEV020 pre-peak session; raw provider payloads are never retained."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from src.ops.dev016_lifecycle_metrics import COHORT_ROOT, _opening_baseline
from src.ops.dev_provider_budget import BudgetDenied, DevProviderBudget
from src.ops.dev020_prepeak_metrics import calculate_prepeak_observed_metrics
from src.ops.strict_migration_window import normalize_opening_ohlcv
from src.ops.token_data_provider_bindings import BirdeyeProductionBinding, build_birdeye_ohlcv_request


ROOT = Path(os.getenv("DEV020_PREPEAK_ROOT", "/private/tmp/dev020-watchtower-prepeak"))
JOURNAL = ROOT / "journal.db"
SNAPSHOT = ROOT / "selection.json"
DEV019_JOURNAL = Path(os.getenv("DEV019_OPENING_JOURNAL", "/private/tmp/dev019-watchtower-opening-mc/journal.db"))
MAX_TOKENS = int(os.getenv("DEV020_MAX_TOKENS", "50"))
MIN_REQUEST_SECONDS = 6
MAX_SNAPSHOT_BYTES = 1_048_576
ROLLING_PROVIDER_LIMIT = 10
PREPEAK_REQUEST_CLASS = "DEV020_PREPEAK_1M"


class RateLimited:
    """Single-concurrency DEV020 pacing backed by the shared DEV ledger."""

    def __init__(self, binding: Any, *, budget_root: Path = ROOT,
                 monotonic_clock=time.monotonic, wall_clock=time.time,
                 sleep=time.sleep) -> None:
        self.binding, self.last, self.calls = binding, None, 0
        self._monotonic_clock = monotonic_clock
        self._wall_clock = wall_clock
        self._sleep = sleep
        # This is the established atomic provider_budget.json implementation,
        # not a DEV020-specific counter or replacement ledger.
        self._budget = DevProviderBudget(budget_root, now=wall_clock)

    def admit(self, request: dict[str, Any]) -> None:
        """Atomically debit the established ledger before one transport."""
        if self.calls >= MAX_TOKENS:
            raise RuntimeError("DEV020_PROVIDER_REQUEST_CAP")
        if self.last is not None:
            delay = MIN_REQUEST_SECONDS - (self._monotonic_clock() - self.last)
            if delay > 0:
                self._sleep(delay)
        parameters = request.get("request_parameters") or {}
        mint = parameters.get("address")
        if not isinstance(mint, str) or not mint:
            raise RuntimeError("DEV020_REQUEST_MINT_REQUIRED")
        # Admit immediately before the one transport attempt.  A denial leaves
        # the call count and the transport untouched.
        self._budget.admit(
            mint,
            PREPEAK_REQUEST_CLASS,
            now=int(self._wall_clock()),
            global_limit=ROLLING_PROVIDER_LIMIT,
        )
        self.last = self._monotonic_clock()
        self.calls += 1

    def dispatch(self, request: dict[str, Any]) -> Any:
        return self.binding(request)

    def __call__(self, request: dict[str, Any]) -> Any:
        self.admit(request)
        return self.dispatch(request)


def _require_session_ready(*, now: int) -> None:
    with _connect() as connection:
        row = connection.execute("SELECT next_eligible_at FROM dev020_prepeak_session_control WHERE id=1").fetchone()
        if row and now < int(row[0]):
            raise RuntimeError("DEV020_SESSION_COOLDOWN_ACTIVE")


def _record_session_cooldown(*, now: int) -> None:
    with _connect() as connection:
        connection.execute(
            "INSERT INTO dev020_prepeak_session_control(id,next_eligible_at) VALUES(1,?) "
            "ON CONFLICT(id) DO UPDATE SET next_eligible_at=excluded.next_eligible_at",
            (now + 60,),
        )


def _read_journal(path: Path) -> dict[str, Any] | None:
    try:
        if path.stat().st_size > 1_000_000:
            return None
        row = json.loads(path.read_text(encoding="utf-8"))
        return row if isinstance(row, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _dev019_baselines() -> dict[str, dict[str, Any]]:
    """Read only completed, compact DEV019 opening facts as a fallback source."""
    if not DEV019_JOURNAL.is_file():
        return {}
    try:
        connection = sqlite3.connect(f"file:{DEV019_JOURNAL}?mode=ro", uri=True)
        rows = connection.execute("SELECT mint,result_json FROM watchtower_historical_openings WHERE state='QUALIFIED'").fetchall()
        connection.close()
    except sqlite3.Error:
        return {}
    baselines: dict[str, dict[str, Any]] = {}
    for mint, raw in rows:
        try:
            result = json.loads(raw)
            market_cap, timestamp = result.get("entry_mc_usd"), result.get("entry_timestamp")
            if isinstance(market_cap, (int, float)) and market_cap > 0 and isinstance(timestamp, (int, float)):
                baselines[str(mint)] = {"market_cap_usd": float(market_cap), "timestamp": int(timestamp), "baseline_type": result.get("entry_method"), "confidence": "QUALIFIED", "provenance": {"journal": "DEV019", "interval": result.get("interval"), "market_cap_field": result.get("market_cap_field"), "migration_signature": result.get("migration_signature")}}
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return baselines


def eligible_rows() -> list[dict[str, Any]]:
    """Bounded deterministic candidate snapshot from compact DEV014 facts."""
    rows: list[dict[str, Any]] = []
    dev019 = _dev019_baselines()
    for path in sorted((COHORT_ROOT / "journals").glob("*/journal.json")):
        journal = _read_journal(path)
        if not journal or journal.get("state") != "COMPLETED" or not journal.get("mint"):
            continue
        facts = journal.get("lifecycle_metrics") or {}
        # DEV013/DEV017/local canonical facts retain their existing precedence.
        # DEV019 is only the qualified fallback for a mint with no such fact.
        baseline = _opening_baseline(str(journal["mint"])) or dev019.get(str(journal["mint"]))
        if not baseline:
            continue
        opening, opening_at = baseline.get("market_cap_usd"), baseline.get("timestamp")
        peak, peak_at = facts.get("observed_maximum_mc_usd"), facts.get("observed_maximum_timestamp")
        if not all(isinstance(value, (int, float)) and value > 0 for value in (opening, peak)):
            continue
        if not isinstance(opening_at, (int, float)) or not isinstance(peak_at, (int, float)) or opening_at > peak_at:
            continue
        duration = int(peak_at - opening_at)
        rows.append({"mint": str(journal["mint"]), "opening_market_cap": float(opening), "opening_timestamp": int(opening_at), "observed_peak_market_cap": float(peak), "observed_peak_timestamp": int(peak_at), "duration_seconds": duration, "baseline_type": baseline.get("baseline_type")})
    return sorted(rows, key=lambda row: (row["duration_seconds"] > 3600, row["duration_seconds"], row["mint"]))


def _connect() -> sqlite3.Connection:
    ROOT.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(JOURNAL)
    connection.execute("CREATE TABLE IF NOT EXISTS dev020_prepeak_tokens(mint TEXT PRIMARY KEY, selection_json TEXT NOT NULL, state TEXT NOT NULL, result_json TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)")
    connection.execute("CREATE TABLE IF NOT EXISTS dev020_prepeak_session_control(id INTEGER PRIMARY KEY CHECK(id=1), next_eligible_at INTEGER NOT NULL)")
    return connection


def select() -> list[dict[str, Any]]:
    rows = eligible_rows()
    with _connect() as connection:
        terminal = {row[0] for row in connection.execute("SELECT mint FROM dev020_prepeak_tokens WHERE state IN ('OBSERVED_CONTIGUOUS','OBSERVED_SPARSE','INSUFFICIENT_EVIDENCE','SAFETY_FAILURE')")}
        in_flight = connection.execute("SELECT mint FROM dev020_prepeak_tokens WHERE state IN ('ADMISSION_INTENT','REQUEST_IN_FLIGHT')").fetchall()
        if in_flight:
            raise RuntimeError("DEV020_ATTEMPT_OUTCOME_UNPROVEN")
        chosen = [row for row in rows if row["mint"] not in terminal][:MAX_TOKENS]
        now = int(time.time())
        for row in chosen:
            connection.execute("INSERT OR IGNORE INTO dev020_prepeak_tokens VALUES(?,?,?,?,?,?)", (row["mint"], json.dumps(row, sort_keys=True, separators=(",", ":")), "PENDING", None, now, now))
    payload = {"schema": "DEV020_PREPEAK_SELECTION_V1", "count": len(chosen), "tokens": chosen}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode()) > MAX_SNAPSHOT_BYTES:
        raise RuntimeError("DEV020_SELECTION_SIZE_BOUND")
    SNAPSHOT.write_text(encoded, encoding="utf-8")
    return chosen


def _admit_then_mark_in_flight(*, mint: str, request: dict[str, Any], transport: RateLimited, now: int) -> Any:
    """Keep pre-admission denials retryable and post-admission outcomes fail-closed."""
    with _connect() as connection:
        connection.execute(
            "UPDATE dev020_prepeak_tokens SET state='ADMISSION_INTENT',updated_at=? WHERE mint=? AND state='PENDING'",
            (now, mint),
        )
    try:
        transport.admit(request)
    except BudgetDenied:
        # DevProviderBudget raised before debit.  Restore only this known
        # pre-admission state; unexpected faults retain ADMISSION_INTENT.
        with _connect() as connection:
            connection.execute(
                "UPDATE dev020_prepeak_tokens SET state='PENDING',updated_at=? WHERE mint=? AND state='ADMISSION_INTENT'",
                (now, mint),
            )
        raise
    with _connect() as connection:
        connection.execute(
            "UPDATE dev020_prepeak_tokens SET state='REQUEST_IN_FLIGHT',updated_at=? WHERE mint=? AND state='ADMISSION_INTENT'",
            (now, mint),
        )
    return transport.dispatch(request)


def run() -> dict[str, Any]:
    _require_session_ready(now=int(time.time()))
    chosen = select()
    transport = RateLimited(BirdeyeProductionBinding(credential_label="BIRDEYE"))
    counts = {"OBSERVED_CONTIGUOUS": 0, "OBSERVED_SPARSE": 0, "INSUFFICIENT_EVIDENCE": 0, "drawdown_count": 0}
    drawdowns: list[float] = []
    try:
      for candidate in chosen:
        mint = candidate["mint"]
        with _connect() as connection:
            state = connection.execute("SELECT state FROM dev020_prepeak_tokens WHERE mint=?", (mint,)).fetchone()[0]
            if state in counts:
                continue
        if candidate["duration_seconds"] <= 0:
            compact = {"reason": "NON_POSITIVE_OPENING_TO_PEAK_WINDOW", "baseline_type": candidate["baseline_type"], "metrics": {"evidence_source": "RETAINED_OPENING_AND_PEAK", "observation_coverage": {"qualification_status": "INSUFFICIENT_EVIDENCE", "requested_window_start": candidate["opening_timestamp"], "requested_window_end": candidate["observed_peak_timestamp"], "candle_interval_seconds": 60, "qualifying_candle_count": 0, "first_returned_candle_timestamp": None, "last_returned_candle_timestamp": None, "missing_interval_count": 0, "missing_interval_duration_seconds": 0, "largest_unobserved_gap_seconds": 0, "coverage_is_complete": False}}}
            with _connect() as connection:
                connection.execute("UPDATE dev020_prepeak_tokens SET state='INSUFFICIENT_EVIDENCE',result_json=?,updated_at=? WHERE mint=?", (json.dumps(compact, sort_keys=True, separators=(",", ":")), int(time.time()), mint))
            counts["INSUFFICIENT_EVIDENCE"] += 1
            continue
        request = build_birdeye_ohlcv_request(address=mint, interval="1m", time_from=candidate["opening_timestamp"], time_to=candidate["observed_peak_timestamp"])
        outcome = _admit_then_mark_in_flight(mint=mint, request=request, transport=transport, now=int(time.time()))
        if outcome.status_code != 200:
            with _connect() as connection:
                connection.execute("UPDATE dev020_prepeak_tokens SET state='SAFETY_FAILURE',result_json=?,updated_at=? WHERE mint=?", (json.dumps({"reason": f"HTTP_{outcome.status_code}"}), int(time.time()), mint))
            raise RuntimeError(f"DEV020_PROVIDER_HTTP_{outcome.status_code}")
        metrics = calculate_prepeak_observed_metrics(normalize_opening_ohlcv(outcome.payload or {}), opening_market_cap=candidate["opening_market_cap"], opening_timestamp=candidate["opening_timestamp"], observed_peak_market_cap=candidate["observed_peak_market_cap"], observed_peak_timestamp=candidate["observed_peak_timestamp"], requested_window_start=candidate["opening_timestamp"], requested_window_end=candidate["observed_peak_timestamp"], provenance="BIRDEYE_OHLCV_MCAP_1M_RANGE")
        state = metrics["observation_coverage"]["qualification_status"]
        compact = {"request": request["request_parameters"], "baseline_type": candidate["baseline_type"], "metrics": metrics}
        with _connect() as connection:
            connection.execute("UPDATE dev020_prepeak_tokens SET state=?,result_json=?,updated_at=? WHERE mint=?", (state, json.dumps(compact, sort_keys=True, separators=(",", ":")), int(time.time()), mint))
        counts[state] += 1
        drawdown = metrics.get("maximum_observed_prepeak_drawdown_percent")
        if isinstance(drawdown, (int, float)):
            counts["drawdown_count"] += 1
            drawdowns.append(float(drawdown))
    finally:
        _record_session_cooldown(now=int(time.time()))
    return {"tokens_selected": len(chosen), "tokens_processed": sum(counts[key] for key in ("OBSERVED_CONTIGUOUS", "OBSERVED_SPARSE", "INSUFFICIENT_EVIDENCE")), "provider_requests": transport.calls, **counts, "average_drawdown": sum(drawdowns) / len(drawdowns) if drawdowns else None, "maximum_drawdown": max(drawdowns) if drawdowns else None}


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True))
