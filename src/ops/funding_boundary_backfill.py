"""X29.3 — Funding Boundary backfill (renamed from X29.2's Capital Origin backfill).

Populates wt_funding_boundary for the existing wt_attribution_outcomes corpus
using ONLY already-persisted evidence (wt_walkback_queue + token_analysis).
Zero new RPC calls -- every value here already existed in the database
before this sprint ran.

Idempotent: safe to re-run; upsert_funding_boundary()'s UNIQUE(launch_mint,
subject_wallet) + ON CONFLICT DO UPDATE means running this twice never
creates duplicate rows and leaves the corpus in the same final state.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from src.ops.funding_boundary import (
    ensure_schema, derive_funding_boundary, upsert_funding_boundary,
    age_bucket_for, STATUS_ORDER, TYPE_ORDER,
)
from src.ops.known_spam_wallets import confirmed_spam_addresses


def backfill_funding_boundary(ops_db_path: str, core_db_path: str, *, now: int | None = None) -> dict[str, Any]:
    """Reads every wt_attribution_outcomes row whose outcome_type is one of
    the boundary types (KNOWN_CEX_REACHED/KNOWN_BRIDGE_REACHED/
    KNOWN_RELAY_REACHED), derives a funding-boundary record from ALREADY
    -PERSISTED wt_walkback_queue + token_analysis data, and upserts it.

    Returns a full replay report: counts by status, by type, age buckets,
    non-causal rows, missing-signature rows -- consistent with X29.2's
    original backfill report shape."""
    now = int(now or time.time())

    ops_conn = sqlite3.connect(ops_db_path)
    ops_conn.row_factory = sqlite3.Row
    ensure_schema(ops_conn)

    core_conn = sqlite3.connect(core_db_path)
    core_conn.row_factory = sqlite3.Row

    known_spam_wallets = frozenset(confirmed_spam_addresses(ops_conn))

    rows = ops_conn.execute(
        "SELECT o.mint, o.outcome_type, o.evidence_json, "
        "       q.funder_wallet, q.funder_sig, q.funder_block_time, q.funder_amount_sol, q.rpc_used, "
        "       q.creator "
        "FROM wt_attribution_outcomes o "
        "LEFT JOIN wt_walkback_queue q ON q.mint = o.mint "
        "WHERE o.outcome_type IN ('KNOWN_CEX_REACHED','KNOWN_BRIDGE_REACHED','KNOWN_RELAY_REACHED')"
    ).fetchall()

    status_counts = {s: 0 for s in STATUS_ORDER}
    type_counts = {t: 0 for t in TYPE_ORDER}
    status_by_type = {t: {s: 0 for s in STATUS_ORDER} for t in TYPE_ORDER}
    age_buckets = {b: 0 for b in ("<=1d", "1-7d", "8-30d", "31-100d", ">100d", "unknown")}
    non_causal = 0
    missing_signature = 0
    written = 0

    for r in rows:
        evidence = json.loads(r["evidence_json"]) if r["evidence_json"] else {}
        boundary = evidence.get("boundary")
        subject_wallet = r["creator"] or evidence.get("creator")
        if not subject_wallet:
            continue

        launch_row = core_conn.execute(
            "SELECT created_at FROM token_analysis WHERE mint=?", (r["mint"],)
        ).fetchone()
        launch_block_time_raw = launch_row["created_at"] if launch_row else None

        record = derive_funding_boundary(
            mint=r["mint"],
            outcome_type=r["outcome_type"],
            boundary=boundary,
            subject_wallet=subject_wallet,
            origin_wallet=r["funder_wallet"],
            origin_signature=r["funder_sig"],
            origin_block_time_raw=r["funder_block_time"],
            origin_amount_sol=r["funder_amount_sol"],
            rpc_used=r["rpc_used"],
            launch_block_time_raw=launch_block_time_raw,
            known_spam_wallets=known_spam_wallets,
        )

        upsert_funding_boundary(ops_conn, record)
        written += 1

        status_counts[record["boundary_status"]] += 1
        type_counts[record["boundary_type"]] += 1
        status_by_type[record["boundary_type"]][record["boundary_status"]] += 1
        if record["resolution_reason"] == "NON_CAUSAL_FUNDING_EVENT":
            non_causal += 1
        if not record.get("boundary_signature"):
            missing_signature += 1
        age_buckets[age_bucket_for(record.get("boundary_age_at_launch_seconds"))] += 1

    ops_conn.commit()
    ops_conn.close()
    core_conn.close()

    return {
        "generated_at": now,
        "rows_considered": len(rows),
        "rows_written": written,
        "status_counts": status_counts,
        "type_counts": type_counts,
        "status_by_type": status_by_type,
        "age_buckets": age_buckets,
        "non_causal_rows": non_causal,
        "missing_signature_rows": missing_signature,
    }
