#!/usr/bin/env python3
"""X78.14 selective 69SN lineage quarantine.

This script never deletes or rewrites historical sessions or canonical rows.
It records the frozen audit decisions and exposes only non-quarantined sessions
to Tier-1 lineage readers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ROOT_69SN = "69SNcRC8NqjHBSXEcugCN5oFKRQoKmddmWzZYc3tqtxk"
BINANCE_2 = "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9"

# X78.9/X78.10 frozen branch verdict.  The two unresolved rows and the one
# genuine indirect chain are deliberately not included here.
AUDITED_INVALID_BINANCE_SESSION_IDS = (
    82073, 126621, 127875, 129294, 131187, 132872, 136388, 141292,
    142952, 143648, 143830, 144228, 146921, 152217, 157750, 158152,
    158703, 159079, 160523, 161998, 162476, 164551, 164910, 165015,
    167036, 167228, 172861, 173593, 173746, 174143, 175969, 178790,
    182304, 182735, 184771, 185799, 189121, 204066,
)
AUDITED_UNRESOLVED_BINANCE_SESSION_IDS = (151529, 180073)
AUDITED_VALID_INDIRECT_BINANCE_SESSION_ID = 176429


def qid(session_id: int) -> str:
    return hashlib.sha256(f"wt_active_subprov_sessions:{session_id}".encode()).hexdigest()


def ensure_schema(conn: sqlite3.Connection) -> None:
    from src.ops.lineage_quarantine import ensure_lineage_quarantine_schema
    ensure_lineage_quarantine_schema(conn)


def _session(conn: sqlite3.Connection, session_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM wt_active_subprov_sessions WHERE id=?", (session_id,)
    ).fetchone()
    if not row:
        raise RuntimeError(f"audited session {session_id} is no longer present")
    return row


def _insert(conn: sqlite3.Connection, row: sqlite3.Row, evidence_class: str,
            reason: str, evidence_source: str, evidence: dict) -> None:
    if row["treasury_wallet"] != ROOT_69SN:
        raise RuntimeError(f"session {row['id']} no longer carries the audited 69SN root")
    conn.execute("""
        INSERT INTO wt_lineage_quarantine
          (quarantine_id,source_table,source_row_id,subject_wallet,related_wallet,
           signature,evidence_class,quarantine_reason,evidence_source,evidence_json,
           exclude_from_tier1,quarantined_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,1,?)
        ON CONFLICT(source_table,source_row_id) DO UPDATE SET
          subject_wallet=excluded.subject_wallet,related_wallet=excluded.related_wallet,
          signature=excluded.signature,evidence_class=excluded.evidence_class,
          quarantine_reason=excluded.quarantine_reason,
          evidence_source=excluded.evidence_source,evidence_json=excluded.evidence_json,
          exclude_from_tier1=1
    """, (qid(row["id"]), "wt_active_subprov_sessions", row["id"],
          row["treasury_wallet"], row["subprov_wallet"], row["funding_signature"],
          evidence_class, reason, evidence_source,
          json.dumps(evidence, sort_keys=True, separators=(",", ":")), int(time.time())))


def usage_census(conn: sqlite3.Connection) -> list[dict]:
    """Exact and embedded uses inside the operational database."""
    results = []
    exact_tokens = ("wallet", "treasury", "root", "parent", "funder", "address",
                    "asset_value", "entity", "member", "participant")
    embedded_tokens = ("json", "provenance", "evidence", "context", "payload",
                       "detail", "reason", "metadata")
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )]
    for table in sorted(tables):
        for column in [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')
                       if "TEXT" in str(r[2] or "").upper()]:
            lowered = column.lower()
            check_exact = any(token in lowered for token in exact_tokens)
            check_embedded = any(token in lowered for token in embedded_tokens)
            if not check_exact and not check_embedded:
                continue
            try:
                exact = conn.execute(
                    f'SELECT count(*) FROM "{table}" WHERE "{column}"=?', (ROOT_69SN,)
                ).fetchone()[0] if check_exact else 0
                embedded = conn.execute(
                    f'SELECT count(*) FROM "{table}" WHERE instr("{column}",?)>0 '
                    f'AND "{column}"<>?', (ROOT_69SN, ROOT_69SN)
                ).fetchone()[0] if check_embedded else 0
            except sqlite3.Error:
                continue
            if exact or embedded:
                results.append({"table": table, "column": column,
                                "exact_rows": exact, "embedded_rows": embedded})
    return results


def apply_repair(ops_db: Path, transaction_first_db: Path) -> dict:
    conn = sqlite3.connect(ops_db, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    ensure_schema(conn)
    conn.execute("""
        INSERT INTO wt_lineage_root_policies
          (subject_wallet,require_explicit_tier1,policy_reason,evidence_source,updated_at)
        VALUES (?,1,?,?,?)
        ON CONFLICT(subject_wallet) DO UPDATE SET
          require_explicit_tier1=1,policy_reason=excluded.policy_reason,
          evidence_source=excluded.evidence_source,updated_at=excluded.updated_at
    """, (ROOT_69SN,
          "Historical session-root inheritance is not Tier-1 evidence; require an explicit matching edge",
          "X78.8_X78.14_69SN_CONTAMINATION_AUDITS", int(time.time())))
    before_raw = conn.execute(
        "SELECT count(*) FROM wt_active_subprov_sessions WHERE treasury_wallet=?",
        (ROOT_69SN,),
    ).fetchone()[0]

    for session_id in AUDITED_INVALID_BINANCE_SESSION_IDS:
        row = _session(conn, session_id)
        if row["subprov_wallet"] != BINANCE_2:
            raise RuntimeError(f"session {session_id} is outside the frozen Binance audit")
        _insert(conn, row, "C_INHERITED_SESSION_ONLY",
                "X78.9/X78.10 proved inherited ancestry invalid; no direct 69SN→5tzF edge",
                "X78.9_X78.10_BINANCE_BRANCH_AUDIT",
                {"audit_population": "69SN_TO_5tzF", "verdict": "PROVEN_INVALID"})
    for session_id in AUDITED_UNRESOLVED_BINANCE_SESSION_IDS:
        row = _session(conn, session_id)
        if row["subprov_wallet"] != BINANCE_2:
            raise RuntimeError(f"session {session_id} is outside the frozen Binance audit")
        _insert(conn, row, "G_UNRESOLVED",
                "Direct ancestry unresolved; retained for context and excluded from Tier-1 lineage",
                "X78.9_X78.10_BINANCE_BRANCH_AUDIT",
                {"audit_population": "69SN_TO_5tzF", "verdict": "UNRESOLVED"})

    indirect = _session(conn, AUDITED_VALID_INDIRECT_BINANCE_SESSION_ID)
    if indirect["subprov_wallet"] != BINANCE_2:
        raise RuntimeError("known indirect control is outside the frozen Binance audit")
    _insert(conn, indirect, "B_INDIRECT_TRANSACTION_PROVEN",
            "Valid multi-hop ancestry retained as context; flattened session row is not a direct edge",
            "X78.9_X78.11_POSITIVE_CONTROL",
            {"path": ["69SN", "9St6", "8CEy", "Bvv4", "5tzF"],
             "verdict": "VALID_INDIRECT_NOT_DIRECT"})

    wider_ids = []
    if transaction_first_db.exists():
        clean = sqlite3.connect(f"file:{transaction_first_db}?mode=ro", uri=True)
        rows = clean.execute("""
            SELECT session_id,direct_sender,signature
              FROM tf_session_comparison
             WHERE stored_root=? AND comparison_class='INCORRECT_INHERITED_ANCESTRY'
             ORDER BY session_id
        """, (ROOT_69SN,)).fetchall()
        clean.close()
        for session_id, direct_sender, signature in rows:
            row = _session(conn, session_id)
            _insert(conn, row, "C_INHERITED_SESSION_ONLY",
                    "Cached transaction proves stored 69SN root differs from actual direct sender",
                    "X78.13_TRANSACTION_FIRST_SESSION_COMPARISON",
                    {"actual_sender": direct_sender, "signature": signature,
                     "verdict": "PROVEN_SENDER_MISMATCH"})
            wider_ids.append(session_id)

    # A stored session root is not transaction proof.  Every remaining 69SN
    # session is retained verbatim but held as unresolved context until its
    # own transaction establishes direction.  This is not an invalid verdict
    # and does not prevent later evidence-backed release from quarantine.
    remaining = conn.execute("""
        SELECT sessions.* FROM wt_active_subprov_sessions sessions
         WHERE sessions.treasury_wallet=?
           AND NOT EXISTS (
               SELECT 1 FROM wt_lineage_quarantine quarantine
                WHERE quarantine.source_table='wt_active_subprov_sessions'
                  AND quarantine.source_row_id=sessions.id
           ) ORDER BY sessions.id
    """, (ROOT_69SN,)).fetchall()
    for index, row in enumerate(remaining, 1):
        _insert(conn, row, "G_UNRESOLVED",
                "Stored inherited 69SN root has no cached transaction-derived directional proof",
                "X78.13_TRANSACTION_FIRST_EVIDENCE_UNAVAILABLE",
                {"verdict": "EVIDENCE_UNAVAILABLE", "invalid": False})
        if index % 2000 == 0:
            conn.commit()

    conn.commit()
    census = usage_census(conn)
    after_eligible = conn.execute(
        "SELECT count(*) FROM wt_lineage_eligible_sessions WHERE treasury_wallet=?",
        (ROOT_69SN,),
    ).fetchone()[0]
    binance_eligible = conn.execute(
        "SELECT count(*) FROM wt_lineage_eligible_sessions "
        "WHERE treasury_wallet=? AND subprov_wallet=?", (ROOT_69SN, BINANCE_2),
    ).fetchone()[0]
    quarantine = conn.execute("""
        SELECT evidence_class,count(*) FROM wt_lineage_quarantine
         WHERE subject_wallet=? AND exclude_from_tier1=1 GROUP BY evidence_class
         ORDER BY evidence_class
    """, (ROOT_69SN,)).fetchall()
    raw_preserved = conn.execute(
        "SELECT count(*) FROM wt_active_subprov_sessions WHERE treasury_wallet=?",
        (ROOT_69SN,),
    ).fetchone()[0]
    conn.close()
    return {
        "root": ROOT_69SN,
        "raw_sessions_before": before_raw,
        "raw_sessions_after": raw_preserved,
        "tier1_eligible_sessions_after": after_eligible,
        "quarantine_counts": {row[0]: row[1] for row in quarantine},
        "binance_direct_sessions_after": binance_eligible,
        "audited_binance_rows": 41,
        "wider_cached_sender_mismatches": sorted(set(wider_ids)),
        "unverified_inherited_sessions_quarantined": len(remaining),
        "usage_census": census,
        "canonical_rows_changed": 0,
        "historical_rows_deleted": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ops-db", type=Path, default=ROOT / "database/wt_ops_v2.db")
    parser.add_argument("--transaction-first-db", type=Path,
                        default=ROOT / "database/transaction_first_lineage.db")
    parser.add_argument("--report", type=Path, default=Path("/tmp/x7814_repair_report.json"))
    args = parser.parse_args()
    report = apply_repair(args.ops_db, args.transaction_first_db)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
