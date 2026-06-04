"""
watchtower_lineage_backfill.py — apply lineage-aware WATCHTOWER attribution.

Re-runs the detector over the post-May-8 creator population and persists ONLY
high-confidence promotions:

    apply  : evidence_basis in {LINEAGE_RULE_2, DIRECT_INFRA}
    hold   : fingerprint-only / other soft bases  (NOT written)

This is the safe scoped apply: it restores post-May WATCHTOWER visibility (the
provisioning-hub topology gap) WITHOUT reopening the fingerprint false-positive
problem — fingerprint amount alone is no longer a strong hit (see
watchtower_detector Rule 5).

Writes go through the single-writer queue (db_write_queue) when available; if the
writer thread isn't running (e.g. invoked as a standalone maintenance script in a
window where the app is down), pass --direct to write directly.

Creator selection is the UNION of:
  1. token_analysis creators with first_observed_at >= MAY8
  2. creators funded directly by a CONFIRMED provisioning hub  (catches hub-seeded
     creators whose tokens were never indexed — the listener's blind spot)
  3. creators funded directly by core WATCHTOWER_INFRASTRUCTURE
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections import Counter

from src.analysis import watchtower_detector as wt

MAY8_TS = 1778630400  # 2026-05-08 00:00 UTC
APPLY_BASES = frozenset({"LINEAGE_RULE_2", "DIRECT_INFRA"})


def select_creators(conn: sqlite3.Connection, since_ts: int = MAY8_TS) -> set[str]:
    """Union creator population for the backfill (see module docstring)."""
    hubs = list(wt.load_confirmed_hubs(conn).keys())
    infra = list(wt._INFRA_SET)

    creators: set[str] = set(
        r[0] for r in conn.execute(
            "SELECT DISTINCT COALESCE(earliest_tx_creator, pf_ws_creator) "
            "FROM token_analysis "
            "WHERE COALESCE(earliest_tx_creator, pf_ws_creator) IS NOT NULL "
            "AND first_observed_at >= ?",
            (since_ts,),
        ).fetchall()
    )
    # creator_funders.first_detected_at is a TEXT timestamp ('YYYY-MM-DD HH:MM:SS');
    # convert since_ts to the same form for a correct lexical comparison so the
    # funder-side branches stay bounded to the window (avoids the scope leak where
    # a time-unbounded branch reaches back into pre-window historical infra edges).
    import datetime
    since_text = datetime.datetime.utcfromtimestamp(since_ts).strftime("%Y-%m-%d %H:%M:%S")

    # Hub branch: CONFIRMED provisioning hubs are post-gap by construction, so all
    # hub-seeded creators are in-window. Kept unbounded — this is also what catches
    # hub-launched creators whose tokens were never indexed in token_analysis.
    if hubs:
        qm = ",".join("?" * len(hubs))
        creators |= set(
            r[0] for r in conn.execute(
                f"SELECT DISTINCT creator_address FROM creator_funders "
                f"WHERE funder_address IN ({qm})", hubs).fetchall()
        )
    # Core-infra branch: BOUND to the window, else it surfaces historical
    # (pre-window) direct-infra edges that belong to a separate historical backfill.
    if infra:
        qi = ",".join("?" * len(infra))
        creators |= set(
            r[0] for r in conn.execute(
                f"SELECT DISTINCT creator_address FROM creator_funders "
                f"WHERE funder_address IN ({qi}) AND first_detected_at >= ?",
                [*infra, since_text]).fetchall()
        )
    creators.discard(None)
    return creators


def compute_promotions(conn: sqlite3.Connection, creators: set[str]) -> dict:
    """Classify each creator read-only; split into apply / held."""
    before_flagged = set(
        r[0] for r in conn.execute(
            "SELECT creator_address FROM creator_risk_scores WHERE watchtower_related = 1"
        ).fetchall()
    )
    apply_rows: list[dict] = []
    held_rows: list[dict] = []
    for cr in creators:
        is_rel, ev = wt.detect_watchtower_linkage(cr, conn)
        if not is_rel:
            continue
        grade, basis = wt._grade_and_basis(is_rel, ev)
        row = {
            "creator": cr, "grade": grade, "basis": basis,
            "was_flagged": cr in before_flagged, "evidence": ev,
        }
        (apply_rows if basis in APPLY_BASES else held_rows).append(row)
    return {
        "apply": apply_rows, "held": held_rows,
        "global_before": len(before_flagged),
    }


def _upsert_statements(apply_rows: list[dict]) -> list[tuple[str, list]]:
    """
    Build the persist statements as (UPDATE, then INSERT-if-missing) PAIRS.

    Deliberately NOT a single `INSERT … ON CONFLICT DO UPDATE`: creator_risk_scores
    has AFTER INSERT/UPDATE triggers that do `INSERT OR REPLACE INTO
    token_rescore_queue`. When the firing statement carries an ON CONFLICT clause,
    SQLite propagates that conflict policy into the trigger body, downgrading the
    trigger's REPLACE to ABORT → "UNIQUE constraint failed: token_rescore_queue.mint"
    whenever the rescore-queue row already exists. A plain UPDATE (no conflict
    clause) lets the trigger's REPLACE work normally. All 94 rows pre-exist, so the
    UPDATE is the hot path; the guarded INSERT covers the rare missing row.
    """
    now = int(time.time())
    upd = (
        "UPDATE creator_risk_scores SET "
        "  watchtower_related=?, watchtower_evidence_json=?, "
        "  watchtower_checked_at=?, evidence_grade=?, evidence_basis=? "
        "WHERE creator_address=?"
    )
    ins = (
        "INSERT INTO creator_risk_scores "
        "(creator_address, watchtower_related, watchtower_evidence_json, "
        " watchtower_checked_at, evidence_grade, evidence_basis) "
        "SELECT ?, ?, ?, ?, ?, ? "
        "WHERE NOT EXISTS (SELECT 1 FROM creator_risk_scores WHERE creator_address=?)"
    )
    stmts: list[tuple[str, list]] = []
    for r in apply_rows:
        category = wt._category_for(r["evidence"])
        basis_json = json.dumps(
            {"method": r["basis"], "category": category, "graded_at": str(now)}
        )
        ev_json = json.dumps(r["evidence"])
        stmts.append((upd, [1, ev_json, now, r["grade"], basis_json, r["creator"]]))
        stmts.append((ins, [r["creator"], 1, ev_json, now, r["grade"], basis_json,
                            r["creator"]]))
    return stmts


def persist_via_queue(apply_rows: list[dict]) -> bool:
    """Enqueue the apply batch through the single writer. Returns True on enqueue."""
    from src.core.db_write_queue import WriteItem, enqueue_write
    return enqueue_write(WriteItem(
        domain="scan",
        label="watchtower_lineage_backfill",
        statements=_upsert_statements(apply_rows),
        idempotency_key="watchtower_lineage_backfill",
    ))


def persist_direct(db_path: str, apply_rows: list[dict]) -> int:
    """Direct write (use only when the writer thread isn't running)."""
    conn = sqlite3.connect(db_path, timeout=60)
    try:
        wt.ensure_schema(conn)
        for sql, params in _upsert_statements(apply_rows):
            conn.execute(sql, params)
        conn.commit()
        return len(apply_rows)
    finally:
        conn.close()


def run(db_path: str, since_ts: int = MAY8_TS, apply: bool = False,
        direct: bool = False) -> dict:
    ro = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    ro.row_factory = sqlite3.Row
    try:
        creators = select_creators(ro, since_ts)
        result = compute_promotions(ro, creators)
    finally:
        ro.close()

    apply_rows = result["apply"]
    apply_new = [r for r in apply_rows if not r["was_flagged"]]
    result.update({
        "n_creators": len(creators),
        "apply_new": apply_new,
        "apply_by_basis": dict(Counter(r["basis"] for r in apply_rows)),
        "held_by_basis": dict(Counter(r["basis"] for r in result["held"])),
    })

    if apply:
        if direct:
            result["persisted"] = persist_direct(db_path, apply_rows)
            result["persist_mode"] = "direct"
        else:
            result["persisted"] = persist_via_queue(apply_rows)
            result["persist_mode"] = "queue"
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="database/flex_complete_database.db")
    ap.add_argument("--since", type=int, default=MAY8_TS)
    ap.add_argument("--apply", action="store_true",
                    help="persist promotions (default: dry-run report only)")
    ap.add_argument("--direct", action="store_true",
                    help="write directly instead of via single-writer queue")
    args = ap.parse_args()

    res = run(args.db, args.since, apply=args.apply, direct=args.direct)
    print(f"creators scanned : {res['n_creators']}")
    print(f"global before    : {res['global_before']}")
    print(f"APPLY            : {len(res['apply'])} ({len(res['apply_new'])} new) "
          f"{res['apply_by_basis']}")
    print(f"HELD (soft)      : {len(res['held'])} {res['held_by_basis']}")
    if args.apply:
        print(f"persisted        : {res.get('persisted')} via {res.get('persist_mode')}")
    else:
        print("(dry-run — pass --apply to persist)")


if __name__ == "__main__":
    main()
