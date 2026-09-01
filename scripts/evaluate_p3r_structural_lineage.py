#!/usr/bin/env python3
"""Read-only, denominator-preserving P3R structural-lineage evaluation."""

import argparse
import hashlib
import json
import math
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


EXPECTED = {
    "corpus": "a1779e0f78f7aff8813e7ec4402073c7a6c99232fc80f0f8dcdd562a945524ce",
    "queue": "d111116fd7a1e149e8fea30498cef6c35e3de534cdefef9da78dd4223daff5c3",
    "manifest": "c5aa554ab03f64bad048815e984be737e165f88982f4da5222d65fdb87836260",
    "qualification": "b7969bce6af3c2f15a88da9ab612ef165dd3d181e7cac85b01d08c61d78bbe39",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def percent(part: int, total: int) -> float | None:
    return part * 100 / total if total else None


def distribution(values) -> dict:
    counts = Counter(values)
    total = sum(counts.values())
    return {str(key): {"count": value, "pct_of_denominator": percent(value, total)} for key, value in sorted(counts.items())}


def numeric(values: list[int]) -> dict:
    ordered = sorted(values)
    total = len(ordered)
    def p(fraction):
        if not ordered: return None
        index = (total - 1) * fraction
        lo, hi = math.floor(index), math.ceil(index)
        return ordered[lo] if lo == hi else ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo)
    return {"count": total, "min": min(ordered) if ordered else None, "max": max(ordered) if ordered else None,
            "mean": sum(ordered) / total if total else None, "median": p(.5), "p05": p(.05), "p95": p(.95), "p99": p(.99),
            "zero_count": sum(value == 0 for value in ordered)}


def top(counter: Counter, denominator: int, limit=20) -> list[dict]:
    return [{"value": key, "count": value, "pct_of_denominator": percent(value, denominator)} for key, value in counter.most_common(limit)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--namespace", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    args = parser.parse_args()
    ns = args.namespace
    corpus, checkpoint, manifest, queue = (ns / "p3r_historical_features.jsonl", ns / "p3r_historical_features.checkpoint.json",
                                           ns / "p3r_historical_features.clean_rebuild_manifest.json", ns / "frozen_queue.txt")
    q = json.loads(args.qualification.read_text())
    cp, mf = json.loads(checkpoint.read_text()), json.loads(manifest.read_text())
    rows = [json.loads(line) for line in corpus.read_text().splitlines()]
    queue_mints = queue.read_text().splitlines()
    input_digests = {"corpus": sha256(corpus), "queue": sha256(queue), "qualification": sha256(args.qualification)}
    identity_errors = []
    if input_digests != {key: EXPECTED[key] for key in input_digests}: identity_errors.append("authoritative_digest_mismatch")
    if cp.get("run_manifest_digest") != EXPECTED["manifest"] or q.get("corpus_identity", {}).get("bound_run_manifest_digest") != EXPECTED["manifest"]: identity_errors.append("manifest_binding_mismatch")
    if len(rows) != 28883 or len(queue_mints) != 28883 or [row.get("mint") for row in rows] != queue_mints: identity_errors.append("ordered_population_mismatch")
    if cp.get("rows") != len(rows) or checkpoint.with_suffix(checkpoint.suffix + ".inflight").exists(): identity_errors.append("durable_checkpoint_mismatch")
    if identity_errors:
        raise SystemExit("P3R_EVALUATION_INPUT_HOLD:" + ",".join(identity_errors))

    population = len(rows)
    contradictions = [row for row in rows if isinstance(row.get("max_hop_depth"), int) and row["max_hop_depth"] > row["edge_count"]]
    contradiction_mints = [row["mint"] for row in contradictions]
    depth_rows = [row for row in rows if row.get("max_hop_depth") is not None and row["mint"] not in set(contradiction_mints)]
    lineage_rows = [row for row in rows if row.get("parents") is not None and row.get("mechanisms") is not None and row["mint"] not in set(contradiction_mints)]
    edge_values = [row["edge_count"] for row in rows]
    creators = [row["creator"] for row in rows if row.get("creator") is not None]
    funders = [row["direct_funder"] for row in rows if row.get("direct_funder") is not None]
    both = [row for row in rows if row.get("creator") is not None and row.get("direct_funder") is not None]
    creator_counts, funder_counts = Counter(creators), Counter(funders)
    pair_counts = Counter((row["creator"], row["direct_funder"]) for row in both)
    observed_by_depth = defaultdict(list)
    parent_by_edge, mechanism_by_edge = defaultdict(list), defaultdict(list)
    mechanism_combos = Counter()
    for row in depth_rows:
        observed_by_depth[row["max_hop_depth"]].append(row["edge_count"])
    for row in lineage_rows:
        parent_by_edge[row["edge_count"]].append(len(row["parents"]))
        combo = "+".join(row["mechanisms"])
        mechanism_combos[combo] += 1
        mechanism_by_edge[combo].append(row["edge_count"])
    missingness = Counter()
    for row in rows:
        missingness[(row.get("creator") is None, row.get("direct_funder") is None, row.get("max_hop_depth") is None, row.get("parents") is None, row.get("mechanisms") is None)] += 1
    null_lineage = [row for row in rows if row.get("max_hop_depth") is None]
    observed_lineage = [row for row in rows if row.get("max_hop_depth") is not None]
    artifact = {
        "artifact_type": "P3R_STRUCTURAL_LINEAGE_EVALUATION", "evaluation_version": "p3r-structural-lineage-evaluation-v1",
        "evaluation_run_id": "p3r-eval-" + ns.name, "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "evaluation_code": {"path": str(Path(__file__).resolve()), "sha256": sha256(Path(__file__).resolve())},
        "principal_verdict": "P3R_STRUCTURAL_LINEAGE_EVALUATION_COMPLETE",
        "input_bindings": {"namespace": str(ns), "corpus_path": str(corpus), "corpus_sha256": input_digests["corpus"],
                           "frozen_queue_sha256": input_digests["queue"], "manifest_digest": EXPECTED["manifest"],
                           "qualification_path": str(args.qualification), "qualification_sha256": input_digests["qualification"],
                           "qualification_verdict": q["principal_verdict"], "identity_errors": identity_errors},
        "denominator_policy": "Every finding uses its observed-feature denominator; partial rows remain in analyses for any populated feature. The 12 depth/count contradictions are excluded only from depth-dependent analyses.",
        "edge_count_full_population": {"denominator": population, "missing": 0, "summary": numeric(edge_values), "distribution": distribution(edge_values)},
        "depth_observed_valid": {"observed_denominator": len(depth_rows), "raw_observed": sum(row.get("max_hop_depth") is not None for row in rows),
                                 "excluded_contradictions": len(contradictions), "missing": population - sum(row.get("max_hop_depth") is not None for row in rows),
                                 "summary": numeric([row["max_hop_depth"] for row in depth_rows]), "distribution": distribution([row["max_hop_depth"] for row in depth_rows]),
                                 "edge_count_by_depth": {str(depth): numeric(values) for depth, values in sorted(observed_by_depth.items())}},
        "parents_observed_valid": {"observed_denominator": len(lineage_rows), "missing": population - sum(row.get("parents") is not None for row in rows),
                                   "excluded_contradictions": len(contradictions), "parent_count_summary": numeric([len(row["parents"]) for row in lineage_rows]),
                                   "parent_count_distribution": distribution([len(row["parents"]) for row in lineage_rows]),
                                   "parent_count_by_edge_count": {str(edge): numeric(values) for edge, values in sorted(parent_by_edge.items())}},
        "mechanisms_observed_valid": {"observed_denominator": len(lineage_rows), "missing": population - sum(row.get("mechanisms") is not None for row in rows),
                                        "excluded_contradictions": len(contradictions), "combination_distribution": {key: {"count": value, "pct_of_observed": percent(value, len(lineage_rows))} for key, value in sorted(mechanism_combos.items())},
                                        "edge_count_by_combination": {key: numeric(values) for key, values in sorted(mechanism_by_edge.items())}},
        "creator": {"observed_denominator": len(creators), "missing": population-len(creators), "unique": len(creator_counts),
                    "repeated_creator_count": sum(value > 1 for value in creator_counts.values()), "rows_in_repeated_creators": sum(value for value in creator_counts.values() if value > 1),
                    "top_repeated": top(creator_counts, len(creators))},
        "direct_funder": {"observed_denominator": len(funders), "missing": population-len(funders), "unique": len(funder_counts),
                          "repeated_funder_count": sum(value > 1 for value in funder_counts.values()), "rows_in_repeated_funders": sum(value for value in funder_counts.values() if value > 1),
                          "top_repeated": top(funder_counts, len(funders))},
        "creator_direct_funder_overlap": {"observed_denominator": len(both), "missing_creator_or_funder": population-len(both), "unique_pairs": len(pair_counts),
                                            "repeated_pair_count": sum(value > 1 for value in pair_counts.values()), "rows_in_repeated_pairs": sum(value for value in pair_counts.values() if value > 1),
                                            "top_pairs": [{"creator": pair[0], "direct_funder": pair[1], "count": value, "pct_of_observed": percent(value, len(both))} for pair, value in pair_counts.most_common(20)]},
        "missingness": {"field_null_counts": {field: sum(row.get(field) is None for row in rows) for field in ("creator", "direct_funder", "max_hop_depth", "parents", "mechanisms")},
                        "strata": [{"creator_null": key[0], "direct_funder_null": key[1], "depth_null": key[2], "parents_null": key[3], "mechanisms_null": key[4], "count": value, "pct_of_population": percent(value, population)} for key, value in sorted(missingness.items())],
                        "lineage_null_edge_count": numeric([row["edge_count"] for row in null_lineage]), "lineage_observed_edge_count": numeric([row["edge_count"] for row in observed_lineage]),
                        "interpretation": "Depth, parents, and mechanisms are null together in this corpus; compare their edge-count summaries without treating null as zero."},
        "contradictions": {"rule": "max_hop_depth > edge_count", "count": len(contradictions), "mints": contradiction_mints,
                             "records": [{"mint": row["mint"], "edge_count": row["edge_count"], "max_hop_depth": row["max_hop_depth"], "parent_count": len(row["parents"]) if row.get("parents") else None, "mechanisms": row.get("mechanisms")} for row in contradictions],
                             "treatment": "Included in full-population edge, creator, direct-funder, and missingness accounting; excluded only from depth-, parent-, and mechanism-dependent analyses."},
        "descriptive_cohorts": [
            {"name": "EDGELESS", "rule": "edge_count == 0", "count": sum(value == 0 for value in edge_values), "denominator": population, "fields": ["edge_count"], "observation_state": "fully observed"},
            {"name": "OBSERVED_VALID_LINEAGE", "rule": "max_hop_depth, parents, and mechanisms populated and max_hop_depth <= edge_count", "count": len(lineage_rows), "denominator": population, "fields": ["edge_count", "max_hop_depth", "parents", "mechanisms"], "observation_state": "conditionally observed"},
            {"name": "BOTH_CREATOR_AND_DIRECT_FUNDER_OBSERVED", "rule": "creator and direct_funder are non-null", "count": len(both), "denominator": population, "fields": ["creator", "direct_funder"], "observation_state": "conditionally observed"},
        ],
        "limitations": ["No temporal, transaction-level, per-edge provenance, source-status, causal, predictive, trading, or production claims are made.", "Nulls are measured states and are never imputed.", "Depth-dependent results exclude 12 contradictory records."],
        "execution_safety": {"provider_or_network_calls": 0, "source_table_writes": 0, "input_artifacts_mutated": False},
        "recommended_next_stage": "P3R discovery hypothesis design using only these descriptive structural cohorts, with an explicit evidence-bound review before any new data acquisition or canonical cohort promotion.",
    }
    args.artifact.parent.mkdir(parents=True, exist_ok=True)
    with args.artifact.open("x", encoding="utf-8") as handle:
        json.dump(artifact, handle, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush(); os.fsync(handle.fileno())
    print(json.dumps({"artifact": str(args.artifact), "sha256": sha256(args.artifact), "verdict": artifact["principal_verdict"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
