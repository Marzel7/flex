"""One bounded DEV020 pre-peak session; raw provider payloads are never retained."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from src.ops.dev016_lifecycle_metrics import COHORT_ROOT, _opening_baseline
from src.ops.dev020_prepeak_metrics import calculate_prepeak_observed_metrics
from src.ops.strict_migration_window import normalize_opening_ohlcv
from src.ops.token_data_provider_bindings import BirdeyeProductionBinding, build_birdeye_ohlcv_request


ROOT = Path(os.getenv("DEV020_PREPEAK_ROOT", "/private/tmp/dev020-watchtower-prepeak"))
JOURNAL = ROOT / "journal.db"
SNAPSHOT = ROOT / "selection.json"
MAX_TOKENS = 50
MIN_REQUEST_SECONDS = 6
MAX_SNAPSHOT_BYTES = 1_048_576


class RateLimited:
    """The DEV019 single-concurrency, ten-requests-per-minute guard."""

    def __init__(self, binding: Any) -> None:
        self.binding, self.last, self.calls = binding, None, 0

    def __call__(self, request: dict[str, Any]) -> Any:
        if self.calls >= MAX_TOKENS:
            raise RuntimeError("DEV020_PROVIDER_REQUEST_CAP")
        if self.last is not None:
            delay = MIN_REQUEST_SECONDS - (time.monotonic() - self.last)
            if delay > 0:
                time.sleep(delay)
        self.last = time.monotonic()
        self.calls += 1
        return self.binding(request)


def _read_journal(path: Path) -> dict[str, Any] | None:
    try:
        if path.stat().st_size > 1_000_000:
            return None
        row = json.loads(path.read_text(encoding="utf-8"))
        return row if isinstance(row, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def eligible_rows() -> list[dict[str, Any]]:
    """Bounded deterministic candidate snapshot from compact DEV014 facts."""
    rows: list[dict[str, Any]] = []
    for path in sorted((COHORT_ROOT / "journals").glob("*/journal.json")):
        journal = _read_journal(path)
        if not journal or journal.get("state") != "COMPLETED" or not journal.get("mint"):
            continue
        facts = journal.get("lifecycle_metrics") or {}
        baseline = _opening_baseline(str(journal["mint"]))
        if not baseline:
            continue
        opening, opening_at = baseline.get("market_cap_usd"), baseline.get("timestamp")
        peak, peak_at = facts.get("observed_maximum_mc_usd"), facts.get("observed_maximum_timestamp")
        if not all(isinstance(value, (int, float)) and value > 0 for value in (opening, peak)):
            continue
        if not isinstance(opening_at, (int, float)) or not isinstance(peak_at, (int, float)) or opening_at > peak_at:
            continue
        duration = int(peak_at - opening_at)
        if duration <= 0:
            continue
        rows.append({"mint": str(journal["mint"]), "opening_market_cap": float(opening), "opening_timestamp": int(opening_at), "observed_peak_market_cap": float(peak), "observed_peak_timestamp": int(peak_at), "duration_seconds": duration, "baseline_type": baseline.get("baseline_type")})
    return sorted(rows, key=lambda row: (row["duration_seconds"] > 3600, row["duration_seconds"], row["mint"]))


def _connect() -> sqlite3.Connection:
    ROOT.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(JOURNAL)
    connection.execute("CREATE TABLE IF NOT EXISTS dev020_prepeak_tokens(mint TEXT PRIMARY KEY, selection_json TEXT NOT NULL, state TEXT NOT NULL, result_json TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)")
    return connection


def select() -> list[dict[str, Any]]:
    rows = eligible_rows()
    with _connect() as connection:
        terminal = {row[0] for row in connection.execute("SELECT mint FROM dev020_prepeak_tokens WHERE state IN ('OBSERVED_CONTIGUOUS','OBSERVED_SPARSE','INSUFFICIENT_EVIDENCE')")}
        in_flight = connection.execute("SELECT mint FROM dev020_prepeak_tokens WHERE state='REQUEST_IN_FLIGHT'").fetchall()
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


def run() -> dict[str, Any]:
    chosen = select()
    transport = RateLimited(BirdeyeProductionBinding(credential_label="BIRDEYE"))
    counts = {"OBSERVED_CONTIGUOUS": 0, "OBSERVED_SPARSE": 0, "INSUFFICIENT_EVIDENCE": 0, "drawdown_count": 0}
    drawdowns: list[float] = []
    for candidate in chosen:
        mint = candidate["mint"]
        with _connect() as connection:
            state = connection.execute("SELECT state FROM dev020_prepeak_tokens WHERE mint=?", (mint,)).fetchone()[0]
            if state in counts:
                continue
            connection.execute("UPDATE dev020_prepeak_tokens SET state='REQUEST_IN_FLIGHT',updated_at=? WHERE mint=?", (int(time.time()), mint))
        request = build_birdeye_ohlcv_request(address=mint, interval="1m", time_from=candidate["opening_timestamp"], time_to=candidate["observed_peak_timestamp"])
        outcome = transport(request)
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
    return {"tokens_selected": len(chosen), "tokens_processed": sum(counts[key] for key in ("OBSERVED_CONTIGUOUS", "OBSERVED_SPARSE", "INSUFFICIENT_EVIDENCE")), "provider_requests": transport.calls, **counts, "average_drawdown": sum(drawdowns) / len(drawdowns) if drawdowns else None, "maximum_drawdown": max(drawdowns) if drawdowns else None}


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True))
