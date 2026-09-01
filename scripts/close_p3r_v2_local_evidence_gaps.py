#!/usr/bin/env python3
"""Close bounded P3R v2 shortlist gaps from retained local evidence only."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.ops.p3r_v2_tiering import canonical_json, digest

DB = Path("database/wt_ops_v2.db")
BASE_RUN = "p3r-v2-2dec1d40604c1f7c08c8"
ENRICHMENT_RUN = "p3r-v2-enrichment-c03f0fce0b7e3b9d685b"
TARGETS = (
    "p3r-v2-5a23bde39497a50696cf",
    "p3r-v2-6437acd385e566e301a7",
    "p3r-v2-e6462dd56f2e695fcb6c",
    "p3r-v2-6f7738e9702395ba8ad3",
)
VERSION = "P3R_V2_LOCAL_EVIDENCE_GAP_CLOSURE.v1"
DDL = """
CREATE TABLE IF NOT EXISTS p3r_v2_local_gap_closure_runs (
 closure_run_id TEXT PRIMARY KEY, base_run_id TEXT NOT NULL, enrichment_run_id TEXT NOT NULL,
 source_snapshot_json TEXT NOT NULL, contract_digest TEXT NOT NULL, manifest_sha256 TEXT NOT NULL, verdict TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS p3r_v2_local_gap_closures (
 closure_run_id TEXT NOT NULL, candidate_id TEXT NOT NULL, membership_digest TEXT NOT NULL,
 closure_json TEXT NOT NULL, PRIMARY KEY(closure_run_id,candidate_id)
);
"""


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, payload: object) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return {"path": str(path), "sha256": sha(path)}


def amount(value: int | None) -> int | None:
    return value if value not in (None, 0) else None


def edge_key(row: sqlite3.Row) -> tuple[int, str, int | None]:
    return (row["hop_depth"], row["mechanism"], amount(row["amount_lamports"]))


def count_roles(rows: list[sqlite3.Row], column: str) -> dict:
    values = [row[column] for row in rows if row[column]]
    counts = Counter(values)
    return {"distinct": len(counts), "observations": len(values), "max_share": max(counts.values()) / len(values) if values else 0.0}


def alternative_comparison(selected: list[sqlite3.Row], alternatives: list[sqlite3.Row]) -> dict:
    selected_by_mint: dict[str, set[tuple[int, str, int | None]]] = defaultdict(set)
    for row in selected:
        selected_by_mint[row["mint"]].add(edge_key(row))
    result = Counter()
    signatures = Counter()
    pairs = []
    for row in alternatives:
        key = edge_key(row)
        bases = selected_by_mint[row["mint"]]
        if key in bases:
            label = "EVIDENCE_PRESENT_SUPPORTIVE"
        elif any(key[1] == base[1] and key[2] == base[2] for base in bases):
            label = "EVIDENCE_PRESENT_AMBIGUOUS"  # same economic/mechanism shape, different hop
        elif any(key[1] == base[1] for base in bases):
            label = "EVIDENCE_PRESENT_AMBIGUOUS"  # same mechanism, amount/path differs
        else:
            label = "EVIDENCE_PRESENT_CONTRADICTORY"
        result[label] += 1
        signatures[canonical_json({"selected": sorted(bases), "alternative": key, "classification": label})] += 1
        pairs.append({"mint": row["mint"], "selected_shapes": [list(value) for value in sorted(bases)], "alternative_shape": list(key), "classification": label})
    return {"alternative_rows": len(alternatives), "members_with_alternatives": len({row["mint"] for row in alternatives}), "classification_counts": dict(sorted(result.items())), "shape_comparisons": [{"comparison": json.loads(key), "count": count} for key, count in sorted(signatures.items())], "per_member": pairs}


def local_audit(candidate: str, member_mints: list[str], rows: dict[str, list[sqlite3.Row]], queue: dict[str, sqlite3.Row], enrichment: dict) -> dict:
    selected, alternatives, atomic = rows["selected"], rows["alternative"], rows["atomic"]
    member_count = len(member_mints)
    comparison = alternative_comparison(selected, alternatives)
    selected_shapes = Counter(edge_key(row) for row in selected)
    signatures = Counter(row["signature"] for row in selected)
    timing = [row["block_time"] - row["anchor_block_time"] for row in selected if row["block_time"] is not None and row["anchor_block_time"] is not None]
    atom_orders = Counter(row["instruction_order_json"] for row in atomic)
    atomic_semantics = []
    for order, count in sorted(atom_orders.items()):
        try:
            parsed = json.loads(order)
        except json.JSONDecodeError:
            parsed = ["UNPARSEABLE_RETAINED_ORDER"]
        atomic_semantics.append({"instruction_order": parsed, "rows": count})
    creator_rows = [queue[mint] for mint in member_mints if mint in queue]
    rotation = {
        "creators": count_roles(creator_rows, "creator"),
        "direct_funders": count_roles(creator_rows, "funder_wallet"),
        "selected_parents": count_roles(selected, "candidate_parent"),
        "selected_wallets": count_roles(selected, "wallet"),
        "atomic_temporary_accounts": count_roles(atomic, "temporary_account"),
        "atomic_owners": count_roles(atomic, "owner"),
        "atomic_sources": count_roles(atomic, "source_wallet"),
    }
    raw_tx_available = False  # established from the bounded local schema inventory
    channel = {
        "candidate_members": member_count,
        "members_with_selected_edges": len({row["mint"] for row in selected}),
        "members_with_alternatives_retained": len({row["mint"] for row in alternatives}),
        "members_with_atomic_retained": len({row["mint"] for row in atomic}),
        "raw_transaction_instruction_table_available": raw_tx_available,
    }
    supportive = []
    contradictory = []
    ambiguous = []
    if selected_shapes:
        supportive.append("EVIDENCE_PRESENT_SUPPORTIVE: selected-edge shape and exact amount recurrence are retained across the candidate-member denominator.")
    if atomic:
        supportive.append("EVIDENCE_PRESENT_SUPPORTIVE: atomic instruction evidence exists, but only for its atomic-retained denominator.")
    for label, count in comparison["classification_counts"].items():
        text = f"{label}: {count}/{len(alternatives)} retained alternative rows when compared with each member's selected shape."
        if label == "EVIDENCE_PRESENT_CONTRADICTORY": contradictory.append(text)
        elif label == "EVIDENCE_PRESENT_AMBIGUOUS": ambiguous.append(text)
        else: supportive.append(text)
    if not alternatives:
        ambiguous.append("EVIDENCE_NOT_RETAINED: no alternative-edge rows exist for this candidate-member set.")
    if not atomic:
        ambiguous.append("EVIDENCE_NOT_RETAINED: no atomic-flow rows exist for this candidate-member set.")
    previous_category = enrichment["assessment_category"]
    if contradictory and comparison["members_with_alternatives"] / member_count >= 0.5:
        disposition = "LOCAL_CONTRADICTION_STRENGTHENED"
    elif supportive and (not alternatives or not atomic):
        disposition = "LOCAL_SUPPORT_STRENGTHENED"
    elif ambiguous or previous_category == "EVIDENCE_CONTRADICTS_PROMOTION":
        disposition = "LOCAL_MIXED"
    else:
        disposition = "INSUFFICIENT_RETAINED_EVIDENCE"
    return {
        "candidate_id": candidate, "member_mints": member_mints,
        "member_count": member_count, "membership_digest": enrichment["membership_digest"],
        "denominators": channel,
        "selected_edge": {"rows": len(selected), "signatures": len(signatures), "shapes": [{"hop_depth": key[0], "mechanism": key[1], "amount_lamports": key[2], "rows": count} for key, count in sorted(selected_shapes.items())], "timing_seconds_from_anchor": {"count": len(timing), "distinct": sorted(set(timing)), "min": min(timing) if timing else None, "max": max(timing) if timing else None}},
        "atomic": {"rows": len(atomic), "members": len({row["mint"] for row in atomic}), "signatures": len({row["signature"] for row in atomic}), "instruction_sequences": atomic_semantics, "flags": {"has_create": sum(bool(row["has_create"]) for row in atomic), "has_sync_native": sum(bool(row["has_sync_native"]) for row in atomic), "has_close": sum(bool(row["has_close"]) for row in atomic)}},
        "alternative_discrimination": comparison,
        "address_rotation": rotation,
        "supportive": supportive, "contradictory": contradictory, "ambiguous_or_not_retained": ambiguous,
        "disposition": disposition,
        "near_tier_1": False,
        "material_tier_progress": "No: no new strongly recurrent atomic behaviour was established from retained local evidence.",
    }


def rpc_handoff(result: dict) -> dict:
    missing_alt = [mint for mint in result["member_mints"] if mint not in {entry["mint"] for entry in result["alternative_discrimination"]["per_member"]}]
    atomic_members = result["atomic"]["members"]
    unresolved = []
    if result["denominators"]["members_with_alternatives_retained"] == 0:
        unresolved.append("Are alternative funding paths recurrent across the frozen member set?")
    if atomic_members < result["member_count"] * 0.5:
        unresolved.append("Does a recurrent atomic provisioning/close sequence exist across at least half of members?")
    return {
        "candidate_id": result["candidate_id"], "unresolved_questions": unresolved,
        "member_subset_requiring_retrieval": missing_alt if missing_alt else result["member_mints"],
        "local_boundary": "No retained raw transaction/instruction table exists beyond selected/alternative edge rows and atomic-flow rows.",
        "minimum_rpc_evidence": "One pre-launch funding transaction parse per listed member, including outer/inner instructions and balance deltas; retain selected and alternative causal edges.",
        "support_result": "A single address-independent alternative/atomic fingerprint meeting the frozen >=3, >=50% coverage, >=60% dominant-share threshold.",
        "contradict_result": "Multiple incompatible paths persist at >=50% observable member coverage with no dominant behavioural fingerprint.",
        "estimated_bounded_calls": len(missing_alt) if missing_alt else result["member_count"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB)
    parser.add_argument("--reproduce-run")
    args = parser.parse_args()
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if args.reproduce_run:
            prior = conn.execute("SELECT base_run_id,enrichment_run_id,source_snapshot_json FROM p3r_v2_local_gap_closure_runs WHERE closure_run_id=?", (args.reproduce_run,)).fetchone()
            if not prior: raise SystemExit("unknown closure replay")
            base_run, enrichment_run, snapshot = prior["base_run_id"], prior["enrichment_run_id"], json.loads(prior["source_snapshot_json"])
        else:
            base_run, enrichment_run = BASE_RUN, ENRICHMENT_RUN
            snapshot = json.loads(conn.execute("SELECT source_snapshot_json FROM p3r_v2_runs WHERE run_id=?", (base_run,)).fetchone()[0])
        enrichment_rows = {row["candidate_id"]: json.loads(row["assessment_json"]) for row in conn.execute("SELECT candidate_id,assessment_json FROM p3r_v2_shortlist_assessments WHERE assessment_run_id=?", (enrichment_run,))}
        if set(TARGETS) - set(enrichment_rows): raise SystemExit("frozen enrichment inputs incomplete")
        contract = {"version": VERSION, "base_run": base_run, "enrichment_run": enrichment_run, "targets": TARGETS, "source_scope": "retained local wt_walkback_queue, wt_walkback_edge_candidates, wt_walkback_atomic_flows only at frozen high-waters", "prohibitions": {"provider_rpc_calls": 0, "queue_replay": False, "membership_mutation": False, "tier_or_operation_promotion": False, "trading_signal": False}, "code_sha256": sha(Path(__file__))}
        closure_run = "p3r-v2-gap-closure-" + digest({"snapshot": snapshot, "contract": contract, "input": {key: enrichment_rows[key]["membership_digest"] for key in TARGETS}})[:20]
        results = []
        for candidate in TARGETS:
            mints = enrichment_rows[candidate]["member_mints"]
            marks = ",".join("?" for _ in mints)
            selected_and_alt = conn.execute(f"SELECT rowid,* FROM wt_walkback_edge_candidates WHERE mint IN ({marks}) AND rowid<=?", [*mints, snapshot["highwaters"]["wt_walkback_edge_candidates"]]).fetchall()
            atomic = conn.execute(f"SELECT rowid,* FROM wt_walkback_atomic_flows WHERE mint IN ({marks}) AND rowid<=?", [*mints, snapshot["highwaters"]["wt_walkback_atomic_flows"]]).fetchall()
            qrows = conn.execute(f"SELECT rowid,* FROM wt_walkback_queue WHERE mint IN ({marks}) AND rowid<=?", [*mints, snapshot["highwaters"]["wt_walkback_queue"]]).fetchall()
            result = local_audit(candidate, mints, {"selected": [row for row in selected_and_alt if row["selection_status"] == "SELECTED"], "alternative": [row for row in selected_and_alt if row["selection_status"] == "ALTERNATIVE"], "atomic": atomic}, {row["mint"]: row for row in qrows}, enrichment_rows[candidate])
            results.append(result)
    finally:
        conn.close()
    # The frozen RPC-worthy set is deliberately not expanded by this local phase.
    original_rpc = ("p3r-v2-70a238399aebef5b72dd", "p3r-v2-711c64846cb08429936f", "p3r-v2-32aa0d5125ef777cfd1e", "p3r-v2-3af3f42197d1681c6933", "p3r-v2-5a23bde39497a50696cf")
    enriched = {row["candidate_id"]: row for row in results}
    prior_conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True); prior_conn.row_factory = sqlite3.Row
    try:
        old = {row["candidate_id"]: json.loads(row["assessment_json"]) for row in prior_conn.execute("SELECT candidate_id,assessment_json FROM p3r_v2_shortlist_assessments WHERE assessment_run_id=?", (enrichment_run,))}
    finally:
        prior_conn.close()
    rpc_order = sorted(original_rpc, key=lambda candidate: (old[candidate]["activity"].get("last_1d", 0), old[candidate]["activity"].get("last_7d", 0), old[candidate]["activity"].get("max_rolling_24h", 0), candidate), reverse=True)
    rpc = []
    for candidate in rpc_order:
        if candidate in enriched: rpc.append(rpc_handoff(enriched[candidate]))
        else:
            old_row = old[candidate]
            rpc.append({"candidate_id": candidate, "unresolved_questions": ["Retained local atomic/alternative evidence is absent or below the frozen recurrence coverage threshold."], "member_subset_requiring_retrieval": old_row["member_mints"], "local_boundary": "Frozen enrichment artifact establishes no sufficient retained local evidence.", "minimum_rpc_evidence": "Pre-launch funding transaction parse per member.", "support_result": "Address-independent atomic/alternative recurrence at the frozen threshold.", "contradict_result": "Sustained incompatible paths at adequate coverage.", "estimated_bounded_calls": len(old_row["member_mints"])})
    payload = {"schema_version": VERSION, "closure_run_id": closure_run, "base_run_id": base_run, "enrichment_run_id": enrichment_run, "source_snapshot": snapshot, "contract": contract, "results": results, "rpc_handoff": rpc, "verdict": "P3R_V2_LOCAL_EVIDENCE_GAP_CLOSURE_COMPLETE", "safety_confirmation": contract["prohibitions"]}
    root = Path("docs/agent_handoff/p3r/v2") / base_run / "local_gap_closure" / closure_run
    artifact = write(root / "p3r_v2_local_evidence_gap_closure.v1.json", payload)
    manifest = write(root / "p3r_v2_local_evidence_gap_closure_manifest.v1.json", {"closure_run_id": closure_run, "base_run_id": base_run, "enrichment_run_id": enrichment_run, "input_assessment_run": ENRICHMENT_RUN, "input_membership_digests": {row["candidate_id"]: row["membership_digest"] for row in results}, "source_snapshot": snapshot, "contract_digest": digest(contract), "artifact": artifact, "result_digest": digest(results)})
    dest = sqlite3.connect(args.db)
    try:
        dest.executescript(DDL); dest.execute("BEGIN IMMEDIATE")
        dest.execute("INSERT OR REPLACE INTO p3r_v2_local_gap_closure_runs VALUES (?,?,?,?,?,?,?)", (closure_run, base_run, enrichment_run, canonical_json(snapshot), digest(contract), manifest["sha256"], payload["verdict"]))
        for result in results:
            dest.execute("INSERT OR REPLACE INTO p3r_v2_local_gap_closures VALUES (?,?,?,?)", (closure_run, result["candidate_id"], result["membership_digest"], canonical_json(result)))
        dest.commit()
    finally:
        dest.close()
    print(json.dumps({"verdict": payload["verdict"], "closure_run_id": closure_run, "artifact": artifact, "manifest": manifest, "dispositions": {row["candidate_id"]: row["disposition"] for row in results}, "rpc_order": [row["candidate_id"] for row in rpc]}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
