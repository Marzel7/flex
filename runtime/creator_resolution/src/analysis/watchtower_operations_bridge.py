"""
watchtower_operations_bridge.py — bridge launch-attributed WATCHTOWER creators
into the Operations layer (wt_operations / wt_operation_members).

Layer discipline:
    Dashboard  = ALL watchtower_related creators (creator_risk_scores)
    Operators  = launch / campaign / operator STRUCTURE only

So this bridge feeds ONLY the launch-side records into Operations:
    LAUNCH_PROVISIONING  → one operation per provisioning hub
    LAUNCH_DIRECT        → grouped by funding corridor (same infra amount) + a
                           7-day single-linkage timing window
Extraction (PROFIT_RELAY) and collector flows are intentionally excluded — they
stay on dashboard/evidence views unless they later form a campaign.

Identity: operator_identity='WATCHTOWER', identity_confidence='LINEAGE_CONFIRMED'.
The operation human_name is human-readable (WATCHTOWER_HUB_<prefix>); identity
stays canonical.

Idempotent: operations are upserted by a stable auto_name; members by
(operation_id, token_mint). Safe to re-run.

No RPC. Reads creator_risk_scores (categories), wt_provisioning_hubs (CONFIRMED),
token_analysis (migrated tokens), creator_funders (direct corridor amounts).
"""

from __future__ import annotations

import argparse
import datetime
import json
import sqlite3
import time
from collections import defaultdict

from src.analysis import watchtower_detector as wt

LAUNCH_CATEGORIES = ("LAUNCH_PROVISIONING", "LAUNCH_DIRECT")
DIRECT_TIMING_WINDOW_S = 7 * 86400  # single-linkage gap for direct-launch grouping


# ── helpers ───────────────────────────────────────────────────────────────────

def _launch_creators(conn: sqlite3.Connection) -> dict[str, dict]:
    """
    {creator: {category, hub, evidence}} for launch-side WATCHTOWER creators.
    `hub` is the provisioning hub address for LAUNCH_PROVISIONING, else None.
    """
    out: dict[str, dict] = {}
    rows = conn.execute(
        "SELECT creator_address, evidence_basis, watchtower_evidence_json "
        "FROM creator_risk_scores "
        "WHERE watchtower_related = 1 AND evidence_basis IS NOT NULL"
    ).fetchall()
    for cr, basis_json, ev_json in rows:
        try:
            category = json.loads(basis_json).get("category")
        except (TypeError, json.JSONDecodeError):
            category = None
        if category not in LAUNCH_CATEGORIES:
            continue
        hub = None
        try:
            for e in json.loads(ev_json or "[]"):
                if e.get("rule") == "lineage_to_infrastructure" and e.get("terminal_kind") == "hub":
                    hub = e.get("terminal")
        except (TypeError, json.JSONDecodeError):
            pass
        out[cr] = {"category": category, "hub": hub}
    return out


def _migrated_tokens(conn: sqlite3.Connection, creator: str) -> list[tuple[str, int]]:
    """[(mint, migrated_at)] for a creator's migrated tokens."""
    return [
        (r[0], r[1]) for r in conn.execute(
            "SELECT mint, migrated_at FROM token_analysis "
            "WHERE COALESCE(earliest_tx_creator, pf_ws_creator) = ? "
            "AND migrated_at IS NOT NULL",
            (creator,),
        ).fetchall()
    ]


def _direct_corridor(conn: sqlite3.Connection, creator: str) -> tuple[float | None, int | None]:
    """(infra_funding_amount, first_detected_epoch) for a direct-launch creator."""
    ph = ",".join("?" * len(wt._INFRA_SET))
    row = conn.execute(
        f"SELECT amount_sol, first_detected_at FROM creator_funders "
        f"WHERE creator_address = ? AND funder_address IN ({ph}) "
        f"ORDER BY amount_sol DESC LIMIT 1",
        [creator, *wt._INFRA_SET],
    ).fetchone()
    if not row:
        return None, None
    amt = row[0]
    ts = None
    if row[1]:
        try:
            ts = int(datetime.datetime.strptime(str(row[1]), "%Y-%m-%d %H:%M:%S")
                     .replace(tzinfo=datetime.timezone.utc).timestamp())
        except ValueError:
            ts = None
    return amt, ts


def _upsert_operation(conn: sqlite3.Connection, auto_name: str, human_name: str,
                      signals: list[str], window: tuple[int | None, int | None],
                      corridor: str | None, now: int) -> int:
    """Upsert an operation by stable auto_name; return its operation_id."""
    existing = conn.execute(
        "SELECT operation_id FROM wt_operations WHERE auto_name = ?", (auto_name,)
    ).fetchone()
    if existing:
        op_id = existing[0]
        conn.execute(
            "UPDATE wt_operations SET operator_identity='WATCHTOWER', "
            "identity_confidence='LINEAGE_CONFIRMED', identity_validated_at=?, "
            "discovery_signals=?, corridor_amount=?, window_start=?, window_end=?, "
            "updated_at=? WHERE operation_id=?",
            (now, json.dumps(signals), corridor, window[0], window[1], now, op_id),
        )
        return op_id
    cur = conn.execute(
        "INSERT INTO wt_operations "
        "(auto_name, human_name, operator_identity, state, confidence, "
        " corridor_amount, window_start, window_end, discovery_signals, "
        " identity_confidence, identity_validated_at, discovered_at, "
        " first_discovered_at, updated_at) "
        "VALUES (?, ?, 'WATCHTOWER', 'DISCOVERED', 1.0, ?, ?, ?, ?, "
        " 'LINEAGE_CONFIRMED', ?, ?, ?, ?)",
        (auto_name, human_name, corridor, window[0], window[1],
         json.dumps(signals), now, now, now, now),
    )
    return cur.lastrowid


def _set_member(conn: sqlite3.Connection, op_id: int, mint: str, creator: str,
                funding: float | None, migrated_at: int | None, signal: str) -> None:
    conn.execute(
        "INSERT INTO wt_operation_members "
        "(operation_id, token_mint, creator_wallet, funding_amount, migrated_at, join_signal) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(operation_id, token_mint) DO UPDATE SET "
        "  creator_wallet=excluded.creator_wallet, funding_amount=excluded.funding_amount, "
        "  migrated_at=excluded.migrated_at, join_signal=excluded.join_signal",
        (op_id, mint, creator, funding, migrated_at, signal),
    )


def _recount(conn: sqlite3.Connection, op_id: int) -> None:
    n_tok = conn.execute(
        "SELECT COUNT(DISTINCT token_mint) FROM wt_operation_members WHERE operation_id=?",
        (op_id,)).fetchone()[0]
    n_cr = conn.execute(
        "SELECT COUNT(DISTINCT creator_wallet) FROM wt_operation_members WHERE operation_id=?",
        (op_id,)).fetchone()[0]
    conn.execute("UPDATE wt_operations SET token_count=?, creator_count=? WHERE operation_id=?",
                 (n_tok, n_cr, op_id))


# ── public API ──────────────────────────────────────────────────────────────

def register_hubs_as_operators(conn: sqlite3.Connection) -> int:
    """
    Register every CONFIRMED provisioning hub in wt_known_operator_hubs as a
    WATCHTOWER operator hub. This also helps the existing _discover_operations
    engine resolve hub-seeded creators (its _funding_root reads this registry).
    Returns the number of hubs registered/updated.
    """
    now = int(time.time())
    hubs = conn.execute(
        "SELECT hub_address, treasury_amount, evidence_json "
        "FROM wt_provisioning_hubs WHERE status='CONFIRMED'"
    ).fetchall()
    n = 0
    for hub, tre_amt, ev in hubs:
        conn.execute(
            "INSERT INTO wt_known_operator_hubs "
            "(hub_wallet, operator_identity, confidence, evidence_json, created_at) "
            "VALUES (?, 'WATCHTOWER', 1.0, ?, ?) "
            "ON CONFLICT(hub_wallet) DO UPDATE SET "
            "  operator_identity='WATCHTOWER', confidence=1.0, evidence_json=excluded.evidence_json",
            (hub, json.dumps({"role": "provisioning_hub", "treasury_amount": tre_amt,
                              "source": "watchtower_operations_bridge"}), now),
        )
        n += 1
    return n


def bridge_launch_operations(conn: sqlite3.Connection) -> dict:
    """
    Create/update wt_operations for launch-side WATCHTOWER creators.

      LAUNCH_PROVISIONING → one operation per hub (auto_name WT_HUB_<hub>)
      LAUNCH_DIRECT       → grouped by (corridor amount, 7-day timing window)
                            (auto_name WT_DIRECT_<amount>_<bucket>)

    Idempotent. Returns a summary dict (does not commit).
    """
    now = int(time.time())
    creators = _launch_creators(conn)
    prov = {c: m for c, m in creators.items() if m["category"] == "LAUNCH_PROVISIONING"}
    direct = {c: m for c, m in creators.items() if m["category"] == "LAUNCH_DIRECT"}

    ops_made: list[dict] = []

    # ── LAUNCH_PROVISIONING: one operation per hub ────────────────────────────
    by_hub: dict[str, list[str]] = defaultdict(list)
    for cr, meta in prov.items():
        if meta["hub"]:
            by_hub[meta["hub"]].append(cr)
    for hub, hub_creators in by_hub.items():
        auto_name = f"WT_HUB_{hub}"
        human_name = f"WATCHTOWER_HUB_{hub[:8]}"
        # gather members + window
        mints: list[tuple[str, str, int]] = []  # (mint, creator, migrated_at)
        for cr in hub_creators:
            for mint, mig in _migrated_tokens(conn, cr):
                mints.append((mint, cr, mig))
        migs = [m[2] for m in mints if m[2]]
        window = (min(migs) if migs else None, max(migs) if migs else None)
        op_id = _upsert_operation(
            conn, auto_name, human_name,
            ["provisioning_hub", "treasury_corridor", "create_+1s"],
            window, None, now)
        for mint, cr, mig in mints:
            _set_member(conn, op_id, mint, cr, None, mig, "hub_seed")
        _recount(conn, op_id)
        ops_made.append({"auto_name": auto_name, "human_name": human_name,
                         "op_id": op_id, "kind": "provisioning",
                         "members": len(mints), "creators": len(hub_creators)})

    # ── LAUNCH_DIRECT: group by corridor amount + 7-day single-linkage timing ─
    enriched = []  # (creator, amount, ts)
    for cr in direct:
        amt, ts = _direct_corridor(conn, cr)
        enriched.append((cr, amt, ts))
    # bucket by amount, then single-linkage on ts within each amount bucket
    by_amount: dict[float, list] = defaultdict(list)
    for cr, amt, ts in enriched:
        by_amount[amt].append((cr, ts))
    for amt, members in by_amount.items():
        members.sort(key=lambda x: x[1] or 0)
        clusters: list[list] = []
        cur: list = []
        for cr, ts in members:
            if not cur:
                cur = [(cr, ts)]
            elif ts and cur[-1][1] and (ts - cur[-1][1]) <= DIRECT_TIMING_WINDOW_S:
                cur.append((cr, ts))
            else:
                clusters.append(cur); cur = [(cr, ts)]
        if cur:
            clusters.append(cur)
        for ci, cluster in enumerate(clusters):
            bucket_ts = min((t for _, t in cluster if t), default=0)
            auto_name = f"WT_DIRECT_{amt}_{bucket_ts}"
            human_name = f"WATCHTOWER_DIRECT_{amt}_{ci+1}"
            mints = []
            for cr, _ in cluster:
                for mint, mig in _migrated_tokens(conn, cr):
                    mints.append((mint, cr, mig))
            migs = [m[2] for m in mints if m[2]]
            window = (min(migs) if migs else None, max(migs) if migs else None)
            op_id = _upsert_operation(
                conn, auto_name, human_name,
                ["direct_infra_funding", "treasury_corridor", "timing_burst"],
                window, str(amt), now)
            for mint, cr, mig in mints:
                _set_member(conn, op_id, mint, cr, amt, mig, "direct_infra_seed")
            _recount(conn, op_id)
            ops_made.append({"auto_name": auto_name, "human_name": human_name,
                             "op_id": op_id, "kind": "direct",
                             "members": len(mints), "creators": len(cluster)})

    return {
        "provisioning_creators": len(prov),
        "direct_creators": len(direct),
        "operations": ops_made,
        "operation_count": len(ops_made),
    }


def run(db_path: str, apply: bool = False) -> dict:
    conn = sqlite3.connect(db_path, timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        wt.ensure_schema(conn)
        n_hubs = register_hubs_as_operators(conn)
        result = bridge_launch_operations(conn)
        result["hubs_registered"] = n_hubs
        if apply:
            conn.commit()
            result["committed"] = True
        else:
            conn.rollback()
            result["committed"] = False
        return result
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="database/flex_complete_database.db")
    ap.add_argument("--apply", action="store_true",
                    help="commit (default: dry-run, rolled back)")
    args = ap.parse_args()
    res = run(args.db, apply=args.apply)
    print(f"hubs registered          : {res['hubs_registered']}")
    print(f"provisioning creators    : {res['provisioning_creators']}")
    print(f"direct creators          : {res['direct_creators']}")
    print(f"operations created/updated: {res['operation_count']}")
    for op in res["operations"]:
        print(f"  [{op['kind']:12}] {op['human_name']:28} "
              f"members={op['members']} creators={op['creators']}")
    print("committed" if res.get("committed") else "(dry-run — pass --apply to commit)")


if __name__ == "__main__":
    main()
