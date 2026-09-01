"""Read-only, deterministic recovery of launch evidence after Leviathan's last confirmed boundary."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

OPERATOR_ID = "777211c3-211e-551b-9310-ff9301570627"
C357_SUBTYPE = "p3r-subtype-03f916dfa97fb93a4b9c"
C357_AMOUNT = 99_999_985_000


def utc(value: int | None) -> str | None:
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def digest(value: dict) -> str:
    copy = dict(value)
    copy.pop("artifact_digest", None)
    return hashlib.sha256(json.dumps(copy, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def recover(db_path: str) -> dict:
    conn = sqlite3.connect(db_path, uri=db_path.startswith("file:"))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN")
        op = conn.execute("SELECT * FROM operators WHERE operator_id=?", (OPERATOR_ID,)).fetchone()
        if not op:
            raise RuntimeError("Leviathan/P3R operator not found")
        profile = conn.execute("SELECT * FROM operation_behavioural_profiles WHERE operator_id=? ORDER BY profile_version DESC LIMIT 1", (OPERATOR_ID,)).fetchone()
        snap = conn.execute("SELECT * FROM operation_activity_snapshots WHERE operator_id=? ORDER BY observed_at DESC LIMIT 1", (OPERATOR_ID,)).fetchone()
        metrics = json.loads(snap["metrics_json"])
        boundary = int(metrics["last_observed_launch_timestamp"])
        members = {r[0] for r in conn.execute("SELECT mint FROM operator_launch_membership WHERE operator_id=?", (OPERATOR_ID,))}
        boundary_mint = conn.execute("""
            SELECT e.mint FROM wt_walkback_edge_candidates e JOIN operator_launch_membership m ON m.mint=e.mint
            WHERE m.operator_id=? AND COALESCE(e.anchor_block_time,e.block_time)<=?
            ORDER BY COALESCE(e.anchor_block_time,e.block_time) DESC,e.mint LIMIT 1
        """, (OPERATOR_ID, boundary)).fetchone()
        highwaters = {name: conn.execute(f"SELECT count(*),max(rowid) FROM {name}").fetchone() for name in ("wt_walkback_queue", "wt_walkback_edge_candidates", "wt_walkback_atomic_flows")}
        known = conn.execute("""
            SELECT DISTINCT e.wallet,e.candidate_parent FROM wt_walkback_edge_candidates e
            JOIN operator_launch_membership m ON m.mint=e.mint WHERE m.operator_id=?
        """, (OPERATOR_ID,)).fetchall()
        known_wallets = {x[0] for x in known} | {x[1] for x in known}
        sql = """
          WITH launch AS (SELECT mint,MIN(COALESCE(anchor_block_time,block_time)) ts
                          FROM wt_walkback_edge_candidates GROUP BY mint),
          first_edge AS (SELECT e.*,ROW_NUMBER() OVER (PARTITION BY e.mint ORDER BY e.hop_depth,e.instruction_index,e.evidence_key) n
                         FROM wt_walkback_edge_candidates e)
          SELECT l.mint,l.ts,e.wallet creator,e.candidate_parent funder,
                 a.transfer_lamports,a.has_create,a.has_sync_native,a.has_close,a.instruction_order_json,
                 EXISTS(SELECT 1 FROM wt_walkback_atomic_flows af WHERE af.mint=l.mint) has_atomic
          FROM launch l JOIN first_edge e ON e.mint=l.mint AND e.n=1
          LEFT JOIN wt_walkback_atomic_flows a ON a.mint=l.mint
          WHERE l.ts>? ORDER BY l.ts,l.mint
        """
        launches = []
        for r in conn.execute(sql, (boundary,)):
            exact_c357 = all((r["transfer_lamports"] == C357_AMOUNT, r["has_create"] == 1, r["has_sync_native"] == 1, r["has_close"] == 1))
            infra = r["creator"] in known_wallets or r["funder"] in known_wallets
            classification = "EXACT_EXISTING" if r["mint"] in members else "EXACT_C357_COMPATIBLE" if exact_c357 else "INFRASTRUCTURE_RELATED_BEHAVIOUR_CHANGED" if infra else "UNRELATED"
            launches.append({"mint": r["mint"], "timestamp_utc": utc(r["ts"]), "timestamp": r["ts"], "creator": r["creator"], "direct_funder": r["funder"], "atomic_evidence_available": bool(r["has_atomic"]), "existing_leviathan_member": r["mint"] in members, "c357_compatible": exact_c357, "known_infrastructure_related": infra, "classification": classification})
        c357 = [x for x in launches if x["c357_compatible"]]
        infra = [x for x in launches if x["known_infrastructure_related"] and not x["existing_leviathan_member"]]
        verdict = "LEVIATHAN_TRACKING_LOSS_POSSIBLE" if infra else "LEVIATHAN_OBSERVED_DORMANT"
        result = {
            "schema_version": "LEVIATHAN_POST_LAST_CONFIRMED_RECOVERY.v1",
            "read_only": True,
            "provider_calls": 0,
            "identity": {"operator_id": OPERATOR_ID, "persisted_display_name": op["display_name"], "ui_alias": "Leviathan", "historical_lineage": "P3R", "primary_membership": len(members), "profile_source": profile["source_candidate_id"] if profile else None},
            "last_confirmed_boundary": {"mint": boundary_mint[0] if boundary_mint else None, "timestamp_utc": utc(boundary), "timestamp": boundary, "source": "operation_activity_snapshots.metrics_json.last_observed_launch_timestamp"},
            "observation_boundary": {"latest_retained_edge_utc": utc(conn.execute("SELECT max(COALESCE(anchor_block_time,block_time)) FROM wt_walkback_edge_candidates").fetchone()[0]), "queue_high_water": list(highwaters["wt_walkback_queue"]), "selected_edge_high_water": list(highwaters["wt_walkback_edge_candidates"]), "atomic_flow_high_water": list(highwaters["wt_walkback_atomic_flows"]), "source_database": str(Path(db_path).resolve())},
            "observation_window": "POST_LEVIATHAN_OBSERVATION_WINDOW_EXISTS" if launches else "NO_POST_LEVIATHAN_OBSERVATION_WINDOW",
            "post_boundary_launches": launches,
            "counts": {"post_boundary_launches": len(launches), "existing_leviathan_matches": sum(x["existing_leviathan_member"] for x in launches), "c357_compatible": len(c357), "infrastructure_related": len(infra)},
            "c357_branch_results": [{"mint": x["mint"], "status": "C357_COMPATIBLE_UNRESOLVED"} for x in c357],
            "latest": {"last_confirmed": utc(boundary), "exact_existing": utc(max((x["timestamp"] for x in launches if x["existing_leviathan_member"]), default=None)), "c357_compatible": utc(max((x["timestamp"] for x in c357), default=None)), "infrastructure_related": utc(max((x["timestamp"] for x in infra), default=None)), "near_evolved": None},
            "tracking_status": verdict,
            "c357_post_boundary_status": "NO_COMPATIBLE_ACTIVITY_OBSERVED" if not c357 else "COMPATIBLE_UNATTRIBUTED_ACTIVITY",
            "focus_next": None if not infra else {"label": "Post-boundary Leviathan infrastructure continuity", "launch_count": len(infra), "latest_launch": max(infra, key=lambda x: x["timestamp"])["mint"], "missing_evidence": "Independent repeated role-graph or higher-order funding continuity.", "bounded_next_action": "Inspect the highest two infrastructure-related launches against retained role paths; do not expand membership."},
        }
        result["artifact_digest"] = digest(result)
        return result
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="database/wt_ops_v2.db")
    parser.add_argument("--output", default="docs/audits/leviathan_post_last_confirmed_recovery.v1.json")
    args = parser.parse_args()
    value = recover(args.db)
    Path(args.output).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    print(value["tracking_status"], value["artifact_digest"])


if __name__ == "__main__":
    main()
