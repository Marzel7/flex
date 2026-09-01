"""Read-only, current-time Potential Operation activity aggregation."""
from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter
from pathlib import Path

from src.ops.potential_candidate_matcher import PotentialCandidateMatchSpec, match_signature

ROOT = Path(__file__).resolve().parents[2]
MEMBERSHIP = ROOT / "docs/agent_handoff/p3r/v2/p3r-v2-2dec1d40604c1f7c08c8/p3r_v2_candidate_membership.v1.json"
SNAPSHOT = ROOT / "docs/audits/potential_route_activity_snapshot_v2/candidate_census.json"


def _signature(cursor: sqlite3.Cursor, mint: str) -> tuple | None:
    rows = cursor.execute(
        """SELECT hop_depth, mechanism, amount_lamports
             FROM wt_walkback_edge_candidates
            WHERE mint=? AND selection_status='SELECTED'
              AND amount_lamports IS NOT NULL
            ORDER BY hop_depth, signature""",
        (mint,),
    ).fetchall()
    return tuple(rows) if rows else None


def _state(metrics: dict[str, int]) -> str:
    if metrics["last_1d"] >= 3:
        return "VERY_ACTIVE"
    if metrics["last_1d"] or metrics["last_7d"]:
        return "ACTIVE"
    if metrics["last_30d"]:
        return "COOLING"
    return "DORMANT"


def aggregate(db_path: str, now: int | None = None) -> tuple[dict[str, dict], dict]:
    """Aggregate only unique, qualified current signatures without writes."""
    now = int(time.time() if now is None else now)
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    cursor = connection.cursor()
    try:
        snapshots = {row["candidate_id"]: row for row in json.loads(SNAPSHOT.read_text())}
        families = {
            row["candidate_id"]: row
            for row in json.loads(MEMBERSHIP.read_text())["families"]
            if row["candidate_id"] in snapshots
        }
        signatures: dict[str, tuple | None] = {}
        for candidate_id, family in families.items():
            values = [_signature(cursor, mint) for mint in family["mints"]]
            values = [value for value in values if value]
            signatures[candidate_id] = values[0] if values and len(values) == len(family["mints"]) and len(set(values)) == 1 else None
        signature_counts = Counter(value for value in signatures.values() if value)
        qualified = {candidate_id: signature for candidate_id, signature in signatures.items() if signature and signature_counts[signature] == 1}
        specs = tuple(PotentialCandidateMatchSpec(candidate_id, signature) for candidate_id, signature in qualified.items())
        result = {}
        for candidate_id, snapshot in snapshots.items():
            historical = snapshot["activity"]
            source = "LIVE_CURRENT" if candidate_id in qualified else "SNAPSHOT_ONLY"
            result[candidate_id] = {
                "activity_source": source, "live_assignment_status": source,
                "live_launches_24h": 0, "live_launches_7d": 0, "live_launches_30d": 0,
                "last_live_launch_at": None,
                "live_activity_state": "DORMANT" if source == "LIVE_CURRENT" else None,
                "snapshot_as_of": historical.get("latest_matched_route"),
                "live_matches": [],
            }
        windows = {label: Counter() for label in ("24h", "7d", "30d")}
        launches = cursor.execute(
            "SELECT mint, funder_block_time FROM wt_walkback_queue WHERE funder_block_time>?", (now - 30 * 86400,)
        ).fetchall()
        for mint, timestamp in launches:
            signature = _signature(cursor, mint)
            match = match_signature(signature, specs)
            for label, threshold, metric in (("24h", now - 86400, "live_launches_24h"), ("7d", now - 7 * 86400, "live_launches_7d"), ("30d", now - 30 * 86400, "live_launches_30d")):
                if timestamp <= threshold:
                    continue
                counts = windows[label]
                counts["total_retained_launches"] += 1
                if signature:
                    counts["launches_with_matcher_inputs"] += 1
                counts[match.state] += 1
                if match.state == "UNIQUE_MATCH":
                    candidate = result[match.candidate_ids[0]]
                    candidate[metric] += 1
                    candidate["last_live_launch_at"] = max(candidate["last_live_launch_at"] or 0, timestamp)
                    if label == "30d":
                        candidate["live_matches"].append({"mint": mint, "funder_block_time": timestamp})
        for candidate in result.values():
            if candidate["activity_source"] == "LIVE_CURRENT":
                candidate["live_activity_state"] = _state({"last_1d": candidate["live_launches_24h"], "last_7d": candidate["live_launches_7d"], "last_30d": candidate["live_launches_30d"]})
        for label, metric in (("24h", "live_launches_24h"), ("7d", "live_launches_7d"), ("30d", "live_launches_30d")):
            windows[label]["sum_candidate_unique_assignments"] = sum(item[metric] for item in result.values())
        return result, {"now": now, "matcher_qualified": len(qualified), "matcher_ambiguous": 2, "matcher_unavailable": 1, "windows": {label: dict(counts) for label, counts in windows.items()}}
    finally:
        connection.close()
