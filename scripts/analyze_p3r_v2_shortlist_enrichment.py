#!/usr/bin/env python3
"""Bounded local enrichment of a frozen P3R v2 shortlist.

This script reads the v2 run's recorded high-waters only.  It neither calls a
provider nor alters v2 membership, tier membership, or operational state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ops.p3r_v2_tiering import (
    FINGERPRINT_CONTRACT_VERSION,
    TIER_CONTRACT_VERSION,
    alternative_fingerprint,
    atomic_fingerprint,
    canonical_json,
    digest,
    recurrence_state,
)


DEFAULT_DB = Path("database/wt_ops_v2.db")
DEFAULT_RUN = "p3r-v2-2dec1d40604c1f7c08c8"
ARTIFACT_ROOT = Path("docs/agent_handoff/p3r/v2")
ASSESSMENT_VERSION = "P3R_V2_SHORTLIST_ENRICHMENT.v1"

DDL = """
CREATE TABLE IF NOT EXISTS p3r_v2_shortlist_assessment_runs (
  assessment_run_id TEXT PRIMARY KEY, base_run_id TEXT NOT NULL,
  source_snapshot_json TEXT NOT NULL, contract_digest TEXT NOT NULL,
  artifact_manifest_sha256 TEXT NOT NULL, verdict TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS p3r_v2_shortlist_assessments (
  assessment_run_id TEXT NOT NULL, candidate_id TEXT NOT NULL,
  membership_digest TEXT NOT NULL, assessment_json TEXT NOT NULL,
  PRIMARY KEY(assessment_run_id, candidate_id)
);
"""


def sha_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, payload: object) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return {"path": str(path), "sha256": sha_path(path)}


def value_counts(values: list[str | None]) -> dict:
    populated = [value for value in values if value]
    counts = Counter(populated)
    return {
        "distinct": len(counts), "observations": len(populated),
        "max_share": (max(counts.values()) / len(populated)) if populated else 0.0,
    }


def address_status(creators: list[str | None], funders: list[str | None], parents: list[str | None]) -> tuple[str, dict]:
    roles = {"creators": value_counts(creators), "direct_funders": value_counts(funders), "upstream_parents": value_counts(parents)}
    fully = all(role["distinct"] >= 3 and role["max_share"] <= 0.5 for role in roles.values())
    if fully:
        state = "FULLY_ADDRESS_BLIND"
    elif all(role["distinct"] >= 3 for role in roles.values()):
        state = "HYBRID"
    elif sum(role["distinct"] >= 3 for role in roles.values()) >= 2:
        state = "MOSTLY_ADDRESS_BLIND"
    elif not any(role["observations"] for role in roles.values()):
        state = "ADDRESS_INSUFFICIENT_EVIDENCE"
    else:
        state = "ADDRESS_DEPENDENT"
    return state, roles


def recurrence_class(rows: list[dict], member_count: int, kind: str) -> dict:
    if not rows:
        return {
            "classification": f"{kind}_INSUFFICIENT_COVERAGE",
            "canonical_state": "NOT_OBSERVED", "coverage": 0.0,
            "distinct_fingerprints": 0, "dominant_count": 0, "dominant_share": 0.0,
            "dominant_fingerprint": None,
        }
    state, coverage, dominant_count = recurrence_state(rows, member_count)
    hashes = [digest(row) for row in rows]
    counts = Counter(hashes)
    dominant_hash, _ = counts.most_common(1)[0]
    dominant = next(row for row in rows if digest(row) == dominant_hash)
    dominant_share = dominant_count / len(rows)
    if state == "STRONGLY_RECURRENT":
        classification = f"{kind}_STRONGLY_RECURRENT"
    elif dominant_count >= 2 and dominant_share >= 0.5:
        classification = f"{kind}_RECURRENT"
    elif len(counts) > 1:
        classification = f"{kind}_MIXED"
    else:
        classification = f"{kind}_NON_RECURRENT"
    return {
        "classification": classification, "canonical_state": state, "coverage": coverage,
        "distinct_fingerprints": len(counts), "dominant_count": dominant_count,
        "dominant_share": dominant_share, "dominant_fingerprint": dominant,
    }


def atomic_assessment(rows: list[sqlite3.Row], member_count: int) -> dict:
    fingerprints, covered, signatures, sequences = [], set(), set(), Counter()
    for row in rows:
        try:
            order = json.loads(row["instruction_order_json"])
        except json.JSONDecodeError:
            continue
        fp = atomic_fingerprint(order, row["has_create"], row["has_sync_native"], row["has_close"], row["transfer_lamports"])
        fingerprints.append(fp)
        covered.add(row["mint"])
        signatures.add(row["signature"])
        sequences[canonical_json(order)] += 1
    recurrence = recurrence_class(fingerprints, member_count, "ATOMIC")
    if not fingerprints:
        recurrence["classification"] = "ATOMIC_INSUFFICIENT_COVERAGE"
        completeness = "NO_ATOMIC_EVIDENCE_RETAINED"
    elif len(covered) / member_count < 0.5:
        completeness = "ATOMIC_EVIDENCE_PARTIALLY_RETAINED"
    else:
        completeness = "ATOMIC_EVIDENCE_SUFFICIENTLY_RETAINED"
    recurrence.update({
        "member_mints_covered": len(covered), "member_coverage": len(covered) / member_count,
        "atomic_rows": len(fingerprints), "signatures": len(signatures),
        "observed_sequence_count": len(sequences), "evidence_completeness": completeness,
        "sequence_variants": [{"instruction_order": json.loads(key), "count": count} for key, count in sorted(sequences.items())],
    })
    return recurrence


def alternative_assessment(rows: list[sqlite3.Row], member_count: int) -> dict:
    by_mint: dict[str, list[tuple[int, str, int | None]]] = defaultdict(list)
    mechanisms, positive_amounts, hops, missing_amounts = Counter(), Counter(), Counter(), 0
    for row in rows:
        amount = row["amount_lamports"]
        by_mint[row["mint"]].append((row["hop_depth"], row["mechanism"], amount))
        mechanisms[row["mechanism"]] += 1
        hops[str(row["hop_depth"])] += 1
        if amount is None or amount == 0:
            missing_amounts += 1
        elif amount > 0:
            positive_amounts[str(amount)] += 1
    fingerprints = [alternative_fingerprint(edges) for edges in by_mint.values()]
    recurrence = recurrence_class(fingerprints, member_count, "ALTERNATIVE")
    if recurrence["classification"] == "ALTERNATIVE_STRONGLY_RECURRENT":
        recurrence["classification"] = "STRONGLY_RECURRENT"
    elif recurrence["classification"] == "ALTERNATIVE_RECURRENT":
        recurrence["classification"] = "RECURRENT"
    elif recurrence["classification"] == "ALTERNATIVE_MIXED":
        recurrence["classification"] = "MIXED"
    elif recurrence["classification"] == "ALTERNATIVE_NON_RECURRENT":
        recurrence["classification"] = "NON_RECURRENT"
    else:
        recurrence["classification"] = "INSUFFICIENT_COVERAGE"
    recurrence.update({
        "members_with_alternatives": len(by_mint), "member_coverage": len(by_mint) / member_count,
        "alternative_rows": len(rows), "alternatives_per_member": len(rows) / len(by_mint) if by_mint else 0.0,
        "alternatives_per_hop": dict(sorted(hops.items())), "mechanisms": dict(sorted(mechanisms.items())),
        "positive_nonzero_amounts": dict(sorted(positive_amounts.items())),
        "missing_zero_or_null_amount_rows": missing_amounts,
    })
    return recurrence


def selected_semantics(rows: list[sqlite3.Row]) -> list[dict]:
    grouped: Counter[tuple[int, str, int | None]] = Counter()
    for row in rows:
        amount = row["amount_lamports"]
        grouped[(row["hop_depth"], row["mechanism"], amount if amount not in (None, 0) else None)] += 1
    return [
        {
            "relationship": "upstream parent -> direct funder; creator association retained separately",
            "hop_depth": depth, "mechanism": mechanism, "amount_lamports": amount,
            "observations": count,
            "role_semantics_note": "The retained schema does not prove a more specific role name than upstream parent/direct funder/creator.",
        }
        for (depth, mechanism, amount), count in sorted(grouped.items())
    ]


def gap_matrix(tier: str, evidence: dict, atomic: dict, alternative: dict, address: str) -> tuple[dict, str]:
    watch_now = tier in {"V2_TIER_1_ACTIVE_MULTI_LAYER", "V2_TIER_2_ACTIVE_STRUCTURAL", "V2_TIER_3_ACTIVE_BASE"}
    atomic_strong = atomic["canonical_state"] == "STRONGLY_RECURRENT"
    alternative_strong = alternative["canonical_state"] == "STRONGLY_RECURRENT"
    matrix = {
        "WATCH_NOW": "PASS" if watch_now else "FAIL",
        "STRONG_BASE_RECURRENCE": "PASS" if evidence["base_strong"] else "FAIL",
        "STRONG_ALTERNATIVE_RECURRENCE": "PASS" if alternative_strong else ("NOT_MEASURED" if not alternative["alternative_rows"] else "FAIL"),
        "STRONG_ATOMIC_RECURRENCE": "PASS" if atomic_strong else ("NOT_MEASURED" if not atomic["atomic_rows"] else "FAIL"),
        "ADDRESS_INDEPENDENT_PERSISTENCE": "PASS" if address == "FULLY_ADDRESS_BLIND" else ("NOT_MEASURED" if address == "ADDRESS_INSUFFICIENT_EVIDENCE" else "FAIL"),
    }
    failed = [key for key, value in matrix.items() if value != "PASS"]
    if not failed:
        category = "NEAR_TIER_1"
    elif failed == ["STRONG_ATOMIC_RECURRENCE"]:
        category = "ATOMIC_ENRICHMENT_REQUIRED"
    elif failed == ["STRONG_ALTERNATIVE_RECURRENCE"]:
        category = "ALTERNATIVE_ENRICHMENT_REQUIRED"
    elif failed == ["ADDRESS_INDEPENDENT_PERSISTENCE"]:
        category = "ADDRESS_VALIDATION_REQUIRED"
    elif (
        alternative["member_coverage"] >= 0.5 and alternative["classification"] in {"MIXED", "NON_RECURRENT"}
    ) or (
        atomic["member_coverage"] >= 0.5 and atomic["classification"] in {"ATOMIC_MIXED", "ATOMIC_NON_RECURRENT"}
    ):
        category = "EVIDENCE_CONTRADICTS_PROMOTION"
    else:
        category = "MULTIPLE_EVIDENCE_GAPS"
    return matrix, category


def next_action(assessment: dict) -> str:
    gaps = assessment["gap_matrix"]
    if gaps["STRONG_ALTERNATIVE_RECURRENCE"] != "PASS" and assessment["alternative"]["alternative_rows"]:
        return "local alternative-edge analysis"
    if gaps["STRONG_ATOMIC_RECURRENCE"] != "PASS" and assessment["atomic"]["atomic_rows"]:
        return "local semantics audit"
    if gaps["ADDRESS_INDEPENDENT_PERSISTENCE"] != "PASS":
        return "address-rotation validation"
    if gaps["STRONG_ATOMIC_RECURRENCE"] != "PASS":
        return "targeted upstream RPC after authorization"
    return "no further work yet"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--base-run", default=DEFAULT_RUN)
    parser.add_argument("--reproduce-assessment")
    args = parser.parse_args()
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if args.reproduce_assessment:
            prior = conn.execute("SELECT base_run_id,source_snapshot_json FROM p3r_v2_shortlist_assessment_runs WHERE assessment_run_id=?", (args.reproduce_assessment,)).fetchone()
            if prior is None:
                raise SystemExit("unknown assessment replay")
            base_run = prior["base_run_id"]
            snapshot = json.loads(prior["source_snapshot_json"])
        else:
            base_run = args.base_run
            prior = conn.execute("SELECT source_snapshot_json FROM p3r_v2_runs WHERE run_id=?", (base_run,)).fetchone()
            if prior is None:
                raise SystemExit("unknown v2 base run")
            snapshot = json.loads(prior["source_snapshot_json"])
        shortlist = conn.execute("SELECT rank,candidate_id FROM p3r_v2_shortlist WHERE run_id=? ORDER BY rank", (base_run,)).fetchall()
        if len(shortlist) != 10:
            raise SystemExit("frozen shortlist binding is incomplete")
        contract = {
            "version": ASSESSMENT_VERSION, "base_run_id": base_run,
            "fingerprint_contract_version": FINGERPRINT_CONTRACT_VERSION,
            "tier_contract_version": TIER_CONTRACT_VERSION,
            "recurrence_threshold": "Canonical recurrence_state: >=3 observations, >=50% member coverage, dominant >=3 and >=60% of observed fingerprints.",
            "source_scope": "Only retained local rows at the base run high-waters; no provider/RPC calls.",
            "code_sha256": sha_path(Path(__file__)),
            "constructor_sha256": sha_path(Path(__file__).resolve().parents[1] / "src/ops/p3r_v2_tiering.py"),
        }
        assessment_run_id = "p3r-v2-enrichment-" + digest({"base_run": base_run, "snapshot": snapshot, "contract": contract})[:20]
        results = []
        hw = snapshot["highwaters"]
        for shortlist_row in shortlist:
            candidate_id = shortlist_row["candidate_id"]
            family = conn.execute("SELECT membership_digest,member_count FROM p3r_v2_candidate_families WHERE run_id=? AND candidate_id=?", (base_run, candidate_id)).fetchone()
            tier = conn.execute("SELECT tier,evidence_json FROM p3r_v2_tier_membership WHERE run_id=? AND candidate_id=?", (base_run, candidate_id)).fetchone()
            activity = conn.execute("SELECT metrics_json FROM p3r_v2_activity WHERE run_id=? AND candidate_id=?", (base_run, candidate_id)).fetchone()
            mints = [row[0] for row in conn.execute("SELECT mint FROM p3r_v2_candidate_membership WHERE run_id=? AND candidate_id=? ORDER BY mint", (base_run, candidate_id))]
            marks = ",".join("?" for _ in mints)
            params = [*mints, hw["wt_walkback_atomic_flows"]]
            atomic_rows = conn.execute(f"SELECT rowid,* FROM wt_walkback_atomic_flows WHERE mint IN ({marks}) AND rowid<=?", params).fetchall()
            params = [*mints, hw["wt_walkback_edge_candidates"]]
            edge_rows = conn.execute(f"SELECT rowid,* FROM wt_walkback_edge_candidates WHERE mint IN ({marks}) AND rowid<=?", params).fetchall()
            selected = [row for row in edge_rows if row["selection_status"] == "SELECTED"]
            alternatives = [row for row in edge_rows if row["selection_status"] == "ALTERNATIVE"]
            queue_rows = conn.execute(f"SELECT mint,creator,funder_wallet FROM wt_walkback_queue WHERE mint IN ({marks}) AND rowid<=?", [*mints, hw["wt_walkback_queue"]]).fetchall()
            queue = {row["mint"]: row for row in queue_rows}
            creators = [queue.get(mint)["creator"] if mint in queue else None for mint in mints]
            funders = [queue.get(mint)["funder_wallet"] if mint in queue else None for mint in mints]
            parents = [row["candidate_parent"] for row in selected]
            atomic = atomic_assessment(atomic_rows, family["member_count"])
            alternative = alternative_assessment(alternatives, family["member_count"])
            address, roles = address_status(creators, funders, parents)
            evidence = json.loads(tier["evidence_json"])["evidence"]
            matrix, category = gap_matrix(tier["tier"], evidence, atomic, alternative, address)
            assessment = {
                "rank": shortlist_row["rank"], "candidate_id": candidate_id, "membership_digest": family["membership_digest"],
                "member_mints": mints, "member_count": family["member_count"], "current_tier": tier["tier"],
                "activity": json.loads(activity["metrics_json"]), "atomic": atomic, "alternative": alternative,
                "address_independence": {"classification": address, "roles": roles},
                "selected_funding_semantics": selected_semantics(selected), "gap_matrix": matrix,
                "assessment_category": category,
            }
            assessment["recommended_next_action"] = next_action(assessment)
            assessment["rpc_worthy_after_local_exhaustion"] = bool(
                assessment["activity"].get("last_7d", 0) >= 7 and evidence["base_strong"] and
                (atomic["atomic_rows"] == 0 or alternative["alternative_rows"] == 0)
            )
            results.append(assessment)
    finally:
        conn.close()
    def priority_key(row: dict) -> tuple:
        activity = row["activity"]
        passes = sum(value == "PASS" for value in row["gap_matrix"].values())
        retained = row["atomic"]["member_coverage"] + row["alternative"]["member_coverage"]
        address = row["address_independence"]["classification"] == "FULLY_ADDRESS_BLIND"
        return (activity.get("last_1d", 0), activity.get("last_7d", 0), activity.get("max_rolling_24h", 0), passes, retained, address, row["member_count"], row["candidate_id"])
    ordered = sorted(results, key=priority_key, reverse=True)
    for rank, row in enumerate(ordered, 1):
        row["research_priority_rank"] = rank
    summary = {
        "schema_version": ASSESSMENT_VERSION, "assessment_run_id": assessment_run_id, "base_run_id": base_run,
        "source_snapshot": snapshot, "contract": contract,
        "results": results,
        "top_5": [{"rank": row["research_priority_rank"], "candidate_id": row["candidate_id"], "next_action": row["recommended_next_action"]} for row in ordered[:5]],
        "counts": {
            "near_tier_1": sum(row["assessment_category"] == "NEAR_TIER_1" for row in results),
            "contradicts_promotion": sum(row["assessment_category"] == "EVIDENCE_CONTRADICTS_PROMOTION" for row in results),
            "rpc_worthy_after_local_exhaustion": sum(row["rpc_worthy_after_local_exhaustion"] for row in results),
        },
        "safety": {"v1_tiers_not_reconstructed": True, "v2_membership_frozen": True, "provider_rpc_calls": 0, "queue_replay": False, "operation_promotion": False, "trading_signal": False},
    }
    artifact_dir = ARTIFACT_ROOT / base_run / "shortlist_enrichment" / assessment_run_id
    enrichment = write_json(artifact_dir / "p3r_v2_shortlist_enrichment.v1.json", summary)
    manifest = {"assessment_run_id": assessment_run_id, "base_run_id": base_run, "source_snapshot": snapshot, "contract_digest": digest(contract), "artifact": enrichment, "result_digest": digest(results)}
    manifest_artifact = write_json(artifact_dir / "p3r_v2_shortlist_enrichment_manifest.v1.json", manifest)
    dest = sqlite3.connect(args.db)
    try:
        dest.executescript(DDL)
        dest.execute("BEGIN IMMEDIATE")
        dest.execute("INSERT OR REPLACE INTO p3r_v2_shortlist_assessment_runs VALUES (?,?,?,?,?,?)", (assessment_run_id, base_run, canonical_json(snapshot), digest(contract), manifest_artifact["sha256"], "P3R_V2_SHORTLIST_ENRICHMENT_COMPLETE"))
        for row in results:
            dest.execute("INSERT OR REPLACE INTO p3r_v2_shortlist_assessments VALUES (?,?,?,?)", (assessment_run_id, row["candidate_id"], row["membership_digest"], canonical_json(row)))
        dest.commit()
    finally:
        dest.close()
    print(json.dumps({"verdict": "P3R_V2_SHORTLIST_ENRICHMENT_COMPLETE", "assessment_run_id": assessment_run_id, "artifact": enrichment, "manifest": manifest_artifact, "top_5": summary["top_5"], "counts": summary["counts"]}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
