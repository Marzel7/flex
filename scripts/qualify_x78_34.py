#!/usr/bin/env python3
"""Persist a bounded natural X78.34 production qualification."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import statistics
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "database/flex_complete_database.db"
LOG = ROOT / "logs/supervisor/creator_funding_worker.log"
OUT = ROOT / "docs/audits/x78_34_qualification.json"
DURATION = int(os.environ.get("X78_34_DURATION_SECONDS", "1800"))
INTERVAL = int(os.environ.get("X78_34_SAMPLE_SECONDS", "30"))


def snapshot() -> dict:
    now = int(time.time())
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    ready = "status in ('pending','retry') and locked_until < ? and next_attempt_at <= ? and created_at >= ?"
    args = (now, now, now - 21600)
    oldest = conn.execute(f"select min(created_at) from creator_funding_queue where {ready}", args).fetchone()[0]
    result = {
        "at": now,
        "hot_ready": conn.execute(f"select count(*) from creator_funding_queue where {ready}", args).fetchone()[0],
        "distinct_creators": conn.execute(f"select count(distinct creator_address) from creator_funding_queue where {ready}", args).fetchone()[0],
        "oldest_hot_age_s": now - oldest if oldest else None,
        "lt15m": conn.execute(f"select count(*) from creator_funding_queue where {ready} and created_at>=?", args + (now-900,)).fetchone()[0],
        "m15_60": conn.execute(f"select count(*) from creator_funding_queue where {ready} and created_at<? and created_at>=?", args + (now-900, now-3600)).fetchone()[0],
        "h1_3": conn.execute(f"select count(*) from creator_funding_queue where {ready} and created_at<? and created_at>=?", args + (now-3600, now-10800)).fetchone()[0],
        "h3_6": conn.execute(f"select count(*) from creator_funding_queue where {ready} and created_at<?", args + (now-10800,)).fetchone()[0],
    }
    conn.close()
    return result


def summarize(text: str, started: int) -> dict:
    ledgers = []
    for line in text.splitlines():
        if "[CFQ_PHASE_LEDGER] " in line:
            try:
                item = json.loads(line.split("[CFQ_PHASE_LEDGER] ", 1)[1])
                if item.get("started", 0) >= started:
                    ledgers.append(item)
            except Exception:
                pass
    elapsed = sorted(float(x.get("elapsed_s", 0)) for x in ledgers)
    def pct(values, q):
        if not values: return None
        return values[min(len(values)-1, int((len(values)-1)*q))]
    phase_totals = {}
    for item in ledgers:
        for key, value in item.get("phases", {}).items():
            phase_totals[key] = phase_totals.get(key, 0.0) + float(value)
    complete = len(re.findall(r"\[CFQ_WORKER\] complete creator=.*path=", text))
    fast = len(re.findall(r"path=KNOWN_CREATOR_FAST", text))
    retries = len(re.findall(r"\[CFQ_WORKER\] retry creator=", text))
    timeouts = len(re.findall(r"timed out after 90s", text))
    return {
        "jobs_with_ledgers": len(ledgers), "completions": complete,
        "full": max(0, complete-fast), "fast": fast, "retries": retries,
        "timeouts": timeouts, "full_elapsed_p50_s": pct(elapsed, .5),
        "full_elapsed_p95_s": pct(elapsed, .95), "full_elapsed_max_s": max(elapsed) if elapsed else None,
        "over_30s": sum(v > 30 for v in elapsed), "over_60s": sum(v > 60 for v in elapsed),
        "rpc_calls": sum(int(x.get("rpc_calls", 0)) for x in ledgers),
        "rpc_sem_wait_total_ms": round(sum(float(x.get("rpc_sem_wait_ms", 0)) for x in ledgers), 3),
        "rpc_sem_wait_max_ms": max((float(x.get("rpc_sem_wait_max_ms", 0)) for x in ledgers), default=0),
        "phase_totals_s": {k: round(v, 3) for k, v in sorted(phase_totals.items())},
    }


def persist(payload: dict) -> None:
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    tmp.replace(OUT)


def main() -> None:
    started = int(time.time())
    offset = LOG.stat().st_size if LOG.exists() else 0
    payload = {"schema": "X78_34_QUALIFICATION_V1", "started_at": started, "duration_target_s": DURATION, "snapshots": [snapshot()]}
    persist(payload)
    deadline = time.time() + DURATION
    while time.time() < deadline:
        time.sleep(min(INTERVAL, max(0, deadline-time.time())))
        payload["snapshots"].append(snapshot())
        if LOG.exists():
            with LOG.open(errors="replace") as fh:
                fh.seek(offset)
                payload["metrics"] = summarize(fh.read(), started)
        payload["observed_s"] = int(time.time()) - started
        persist(payload)
    payload["completed_at"] = int(time.time())
    persist(payload)


if __name__ == "__main__":
    main()
