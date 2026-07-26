"""
Shadow, evidence-backed Operator projection (X41.0, implementing X40.0 Phase 4).

READ-ONLY with respect to every existing table. This module:
  - reads AttributionEvidence (this session's new ledger) plus wt_confirmed_treasuries
    (as a fallback source, since the AttributionEvidence ledger is only populated going
    forward from this rollout — historical rows require the backfill script)
  - writes ONLY to wt_operator_entities_projection_shadow, a brand-new table
  - NEVER writes to operators or operator_entities
  - NEVER is read by production code in this phase — it exists purely so a
    reconciliation report can be generated and reviewed by a human before any
    future phase considers promoting it to authoritative

Per the frozen model (X39.0): this shadow projection does NOT invent Operator
identity. It regenerates "which wallets have CONFIRMED attribution evidence,"
grouped under the SAME hardcoded WATCHTOWER_OPERATOR_ID used by
watchtower_alignment.py today — because X40.0's Phase 4 lifecycle (PROPOSED/
SUPPORTED/CONFIRMED/REJECTED/SPLIT) is a future rollout step, not yet
implemented. This module's job in THIS phase is narrower: prove that the same
membership set can be reconstructed from evidence alone, and surface where it
currently differs from the live operator_entities table — not to replace it.
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

try:
    from src.utils.db_locking import db_connect
except Exception:                                    # pragma: no cover
    import sqlite3
    def db_connect(path, timeout=30):
        c = sqlite3.connect(path, timeout=timeout); c.row_factory = sqlite3.Row; return c

OPS_DB_PATH = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "database", "wt_ops_v2.db"))

# Same constant watchtower_alignment.py uses today — NOT re-derived, NOT re-decided.
# This module does not assert this is correct; it only reuses it so a like-for-like
# comparison against the live operator_entities table is meaningful.
try:
    from src.ops.watchtower_alignment import WATCHTOWER_OPERATOR_ID
except Exception:                                    # pragma: no cover
    WATCHTOWER_OPERATOR_ID = None

CONFIRMING_DECISIONS = ("CONFIRMED",)


def ensure_schema(conn) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wt_operator_entities_projection_shadow (
            operator_id       TEXT NOT NULL,
            entity_address    TEXT NOT NULL,
            source            TEXT NOT NULL,      -- 'attribution_evidence' | 'confirmed_treasuries_fallback'
            supporting_events TEXT,                -- JSON list of event_ids or method strings
            confidence_axis   TEXT,
            confidence_value  TEXT,
            first_seen        INTEGER,
            last_seen         INTEGER,
            generated_at      INTEGER NOT NULL,
            PRIMARY KEY (operator_id, entity_address)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wt_operator_projection_reconciliation_reports (
            report_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            generated_at    INTEGER NOT NULL,
            identical_count INTEGER NOT NULL,
            missing_count   INTEGER NOT NULL,
            unsupported_count INTEGER NOT NULL,
            conflicting_count INTEGER NOT NULL,
            duplicate_count INTEGER NOT NULL,
            ambiguous_count INTEGER NOT NULL,
            detail_json     TEXT
        )"""
    )
    conn.commit()


def regenerate_shadow_projection(conn) -> dict:
    """Rebuild wt_operator_entities_projection_shadow from scratch, purely from
    AttributionEvidence (+ a documented fallback to wt_confirmed_treasuries for
    wallets that predate this session's ledger). Never touches operator_entities.
    Idempotent — safe to re-run at any time; fully replaces its own prior contents
    inside one short transaction."""
    ensure_schema(conn)
    now = int(time.time())

    conn.execute("DELETE FROM wt_operator_entities_projection_shadow")

    # Primary source: AttributionEvidence CONFIRMED events (going-forward evidence,
    # or backfilled reconstructed=1 rows once the backfill script has run).
    has_evidence_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='attribution_evidence'"
    ).fetchone()
    seen_wallets = set()

    if has_evidence_table:
        rows = conn.execute(
            """SELECT subject_wallet, GROUP_CONCAT(event_id), MIN(timestamp), MAX(timestamp),
                      confidence_axis, confidence_value
               FROM attribution_evidence
               WHERE decision IN (%s) AND claimed_role='TREASURY'
               GROUP BY subject_wallet""" % ",".join("?" * len(CONFIRMING_DECISIONS)),
            CONFIRMING_DECISIONS,
        ).fetchall()
        for wallet, event_ids, first_seen, last_seen, axis, value in rows:
            if not WATCHTOWER_OPERATOR_ID:
                continue
            conn.execute(
                """INSERT INTO wt_operator_entities_projection_shadow
                     (operator_id, entity_address, source, supporting_events,
                      confidence_axis, confidence_value, first_seen, last_seen, generated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(operator_id, entity_address) DO NOTHING""",
                (WATCHTOWER_OPERATOR_ID, wallet, "attribution_evidence", event_ids,
                 axis, value, first_seen, last_seen, now),
            )
            seen_wallets.add(wallet)

    # Fallback: any wt_confirmed_treasuries wallet NOT yet represented in
    # attribution_evidence (i.e. confirmed before this rollout, and not yet
    # backfilled) — included so the shadow projection's coverage can be
    # compared fairly against operator_entities' current 100% coverage,
    # without silently under-representing legacy wallets as "missing."
    if WATCHTOWER_OPERATOR_ID:
        legacy_rows = conn.execute(
            "SELECT treasury, method, confirmed_at FROM wt_confirmed_treasuries"
        ).fetchall()
        for treasury, method, confirmed_at in legacy_rows:
            if treasury in seen_wallets:
                continue
            conn.execute(
                """INSERT INTO wt_operator_entities_projection_shadow
                     (operator_id, entity_address, source, supporting_events,
                      confidence_axis, confidence_value, first_seen, last_seen, generated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(operator_id, entity_address) DO NOTHING""",
                (WATCHTOWER_OPERATOR_ID, treasury, "confirmed_treasuries_fallback",
                 json.dumps({"method": method}), "treasury_role_attribution", "LEGACY",
                 confirmed_at, confirmed_at, now),
            )

    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM wt_operator_entities_projection_shadow").fetchone()[0]
    return {"rows_generated": n, "generated_at": now}


def reconcile_against_live(conn) -> dict:
    """Compare the shadow projection against the LIVE operator_entities table.
    READ-ONLY on both sides except for writing the report row itself. Never
    switches any production reader, never mutates operator_entities."""
    ensure_schema(conn)
    now = int(time.time())

    shadow = {(r[0], r[1]) for r in conn.execute(
        "SELECT operator_id, entity_address FROM wt_operator_entities_projection_shadow"
    ).fetchall()}
    live = {(r[0], r[1]) for r in conn.execute(
        "SELECT operator_id, entity_address FROM operator_entities"
    ).fetchall()}

    identical = shadow & live
    missing = live - shadow          # live has it, shadow doesn't (evidence gap)
    unsupported = shadow - live      # shadow has it, live doesn't (shadow over-generated)

    # conflicting: same entity_address under a DIFFERENT operator_id in each source
    shadow_by_addr = {}
    for op_id, addr in shadow:
        shadow_by_addr.setdefault(addr, set()).add(op_id)
    live_by_addr = {}
    for op_id, addr in live:
        live_by_addr.setdefault(addr, set()).add(op_id)

    conflicting = []
    duplicate = []
    ambiguous = []
    for addr, ops in shadow_by_addr.items():
        if len(ops) > 1:
            duplicate.append({"entity_address": addr, "operator_ids": sorted(ops), "side": "shadow"})
        live_ops = live_by_addr.get(addr)
        if live_ops and live_ops != ops and not (ops & live_ops):
            conflicting.append({"entity_address": addr, "shadow_operator_ids": sorted(ops),
                                "live_operator_ids": sorted(live_ops)})
    for addr, ops in live_by_addr.items():
        if len(ops) > 1:
            duplicate.append({"entity_address": addr, "operator_ids": sorted(ops), "side": "live"})

    detail = {
        "identical": sorted(list(a) for a in identical),
        "missing_from_shadow": sorted(list(a) for a in missing),
        "unsupported_in_shadow": sorted(list(a) for a in unsupported),
        "conflicting": conflicting,
        "duplicate": duplicate,
        "ambiguous": ambiguous,   # reserved: none of this pass's cases met the ambiguous bar
    }

    conn.execute(
        """INSERT INTO wt_operator_projection_reconciliation_reports
             (generated_at, identical_count, missing_count, unsupported_count,
              conflicting_count, duplicate_count, ambiguous_count, detail_json)
           VALUES (?,?,?,?,?,?,?,?)""",
        (now, len(identical), len(missing), len(unsupported),
         len(conflicting), len(duplicate), len(ambiguous), json.dumps(detail)),
    )
    conn.commit()

    return {
        "generated_at": now,
        "identical_count": len(identical),
        "missing_count": len(missing),
        "unsupported_count": len(unsupported),
        "conflicting_count": len(conflicting),
        "duplicate_count": len(duplicate),
        "ambiguous_count": len(ambiguous),
        "detail": detail,
    }


def run(db_path: str = OPS_DB_PATH) -> dict:
    """CLI/manual entry point: regenerate the shadow projection, then reconcile
    it against the live table, returning the reconciliation summary."""
    conn = db_connect(db_path, timeout=30)
    try:
        gen = regenerate_shadow_projection(conn)
        rec = reconcile_against_live(conn)
        rec["generation"] = gen
        return rec
    finally:
        conn.close()


if __name__ == "__main__":                            # pragma: no cover
    import pprint
    pprint.pprint(run())
