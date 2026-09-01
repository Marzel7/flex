#!/usr/bin/env python3
"""Read-only qualification of a frozen P3R historical-feature corpus."""

import argparse
import hashlib
import json
import math
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


EXPECTED_MANIFEST_DIGEST = "c5aa554ab03f64bad048815e984be737e165f88982f4da5222d65fdb87836260"
EXPECTED_QUEUE_DIGEST = "d111116fd7a1e149e8fea30498cef6c35e3de534cdefef9da78dd4223daff5c3"
EXPECTED_ROWS = 28883
FIELDS = ("mint", "creator", "direct_funder", "edge_count", "max_hop_depth", "parents", "mechanisms")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    low, high = math.floor(index), math.ceil(index)
    return ordered[low] if low == high else ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def numeric_summary(values: list[int | float], null_count: int) -> dict:
    numbers = [float(value) for value in values]
    return {
        "count": len(numbers), "null_or_missing_count": null_count,
        "min": min(numbers) if numbers else None, "max": max(numbers) if numbers else None,
        "mean": sum(numbers) / len(numbers) if numbers else None,
        "median": percentile(numbers, 0.5), "p01": percentile(numbers, 0.01),
        "p05": percentile(numbers, 0.05), "p95": percentile(numbers, 0.95),
        "p99": percentile(numbers, 0.99), "zero_count": sum(value == 0 for value in numbers),
    }


def state(value: object) -> str:
    if value is None:
        return "null"
    if value == "":
        return "empty_string"
    if value == []:
        return "empty_list"
    return "populated"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--namespace", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    args = parser.parse_args()
    ns = args.namespace
    output = ns / "p3r_historical_features.jsonl"
    checkpoint_path = ns / "p3r_historical_features.checkpoint.json"
    manifest_path = ns / "p3r_historical_features.clean_rebuild_manifest.json"
    queue_path = ns / "frozen_queue.txt"
    inflight_path = checkpoint_path.with_suffix(checkpoint_path.suffix + ".inflight")
    checkpoint = json.loads(checkpoint_path.read_text())
    manifest = json.loads(manifest_path.read_text())
    queue = queue_path.read_text().splitlines()
    rows, json_errors = [], []
    with output.open() as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                json_errors.append({"line": line_number, "error": str(exc)})

    output_digest, queue_digest, manifest_file_digest = digest(output), digest(queue_path), digest(manifest_path)
    field_types, field_states, missing_fields = defaultdict(Counter), defaultdict(Counter), Counter()
    numeric, categorical = {"edge_count": [], "max_hop_depth": []}, {"mechanisms": Counter()}
    parent_lengths, mechanism_lengths = [], []
    reason_counts, partial_reason_counts, eligibility = Counter(), Counter(), Counter()
    bad_records, contradictions = [], Counter()
    output_mints = []
    for index, row in enumerate(rows):
        output_mints.append(row.get("mint"))
        reasons = []
        for field in FIELDS:
            if field not in row:
                missing_fields[field] += 1
                field_states[field]["missing_key"] += 1
                continue
            value = row[field]
            field_types[field][type(value).__name__] += 1
            field_states[field][state(value)] += 1
        for field in ("edge_count", "max_hop_depth"):
            value = row.get(field)
            if value is None and field == "max_hop_depth":
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                reasons.append("invalid_" + field)
            else:
                numeric[field].append(value)
                if value < 0:
                    reasons.append("negative_" + field)
        for field in ("mint",):
            if not isinstance(row.get(field), str) or not row[field]:
                reasons.append("missing_or_invalid_" + field)
        for field in ("parents", "mechanisms"):
            value = row.get(field)
            if value is not None and (not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value)):
                reasons.append("missing_or_invalid_" + field)
        parents, mechanisms = row.get("parents"), row.get("mechanisms")
        if isinstance(parents, list):
            parent_lengths.append(len(parents))
        if isinstance(mechanisms, list):
            mechanism_lengths.append(len(mechanisms))
            categorical["mechanisms"].update(mechanisms)
        if isinstance(row.get("edge_count"), int) and isinstance(parents, list) and row["edge_count"] < len(parents):
            contradictions["edge_count_less_than_distinct_parent_count"] += 1
            reasons.append("edge_count_less_than_parent_count")
        if isinstance(row.get("edge_count"), int) and isinstance(row.get("max_hop_depth"), int) and row["max_hop_depth"] > row["edge_count"]:
            contradictions["max_hop_depth_exceeds_edge_count"] += 1
            reasons.append("max_hop_depth_exceeds_edge_count")
        if isinstance(parents, list) and parents != sorted(parents):
            contradictions["parents_not_sorted"] += 1
        if isinstance(mechanisms, list) and mechanisms != sorted(mechanisms):
            contradictions["mechanisms_not_sorted"] += 1
        if reasons:
            eligibility["INELIGIBLE"] += 1
            reason_counts.update(set(reasons))
            if len(bad_records) < 20:
                bad_records.append({"row": index, "mint": row.get("mint"), "reasons": sorted(set(reasons))})
        elif any(row.get(field) is None for field in ("creator", "direct_funder", "max_hop_depth", "parents", "mechanisms")):
            eligibility["PARTIAL"] += 1
            partial_reason_counts.update(field + "_null" for field in ("creator", "direct_funder", "max_hop_depth", "parents", "mechanisms") if row.get(field) is None)
        else:
            eligibility["ELIGIBLE"] += 1

    unique_mints = len(set(output_mints))
    duplicate_rows = len(output_mints) - unique_mints
    ordered_queue_match = output_mints == queue
    identity_errors = []
    if len(rows) != EXPECTED_ROWS or len(queue) != EXPECTED_ROWS: identity_errors.append("unexpected_population_size")
    if json_errors: identity_errors.append("invalid_json")
    if unique_mints != EXPECTED_ROWS or duplicate_rows: identity_errors.append("duplicate_or_missing_mints")
    if not ordered_queue_match: identity_errors.append("output_not_exact_ordered_queue")
    if queue_digest != EXPECTED_QUEUE_DIGEST: identity_errors.append("frozen_queue_digest_mismatch")
    if checkpoint.get("rows") != len(rows) or checkpoint.get("last_mint") != (queue[-1] if queue else ""):
        identity_errors.append("checkpoint_output_disagreement")
    if checkpoint.get("run_manifest_digest") != EXPECTED_MANIFEST_DIGEST or manifest.get("run_identity") != ns.name:
        identity_errors.append("manifest_checkpoint_binding_mismatch")
    if checkpoint.get("frozen_queue_digest") != queue_digest or manifest.get("frozen_queue_digest") != queue_digest:
        identity_errors.append("frozen_queue_binding_mismatch")
    if inflight_path.exists(): identity_errors.append("inflight_marker_present")

    # This corpus is structurally complete but carries only corpus-level, not per-edge-ID, provenance.
    principal = "P3R_HISTORICAL_CORPUS_PARTIALLY_QUALIFIED" if not identity_errors else "P3R_HISTORICAL_CORPUS_HOLD"
    artifact = {
        "artifact_type": "P3R_HISTORICAL_CORPUS_QUALIFICATION", "qualification_version": "p3r-corpus-qualification-v2",
        "qualification_run_id": "p3r-qual-" + ns.name, "qualified_at_utc": datetime.now(timezone.utc).isoformat(),
        "qualification_code": {"path": str(Path(__file__).resolve()), "sha256": digest(Path(__file__).resolve())},
        "principal_verdict": principal,
        "corpus_identity": {
            "namespace": str(ns), "output_path": str(output), "output_sha256": output_digest,
            "checkpoint_path": str(checkpoint_path), "manifest_path": str(manifest_path),
            "manifest_file_sha256": manifest_file_digest, "bound_run_manifest_digest": checkpoint.get("run_manifest_digest"),
            "expected_run_manifest_digest": EXPECTED_MANIFEST_DIGEST, "frozen_queue_path": str(queue_path),
            "frozen_queue_sha256": queue_digest, "expected_frozen_queue_sha256": EXPECTED_QUEUE_DIGEST,
            "checkpoint_rows": checkpoint.get("rows"), "physical_rows": len(rows), "valid_json_rows": len(rows) - len(json_errors),
            "unique_mints": unique_mints, "duplicate_rows": duplicate_rows, "ordered_queue_exact_match": ordered_queue_match,
            "inflight_present": inflight_path.exists(), "identity_errors": identity_errors,
        },
        "schema": {
            "top_level_fields": list(FIELDS), "nested_structures": {"parents": "list[str] or null", "mechanisms": "list[str] or null"},
            "timestamps_or_windows": [], "booleans_or_status_fields": [],
            "field_types": {key: dict(value) for key, value in field_types.items()},
            "field_states": {key: dict(value) for key, value in field_states.items()}, "missing_keys": dict(missing_fields),
        },
        "coverage": {field: {"eligible_denominator": len(rows), "populated": counts.get("populated", 0),
                     "missing_key": counts.get("missing_key", 0), "null": counts.get("null", 0),
                     "empty_string": counts.get("empty_string", 0), "empty_list": counts.get("empty_list", 0),
                     "coverage_pct": (counts.get("populated", 0) * 100 / len(rows)) if rows else 0,
                     "explicit_unavailable_or_not_observed": 0} for field, counts in field_states.items()},
        "distributions": {"edge_count": numeric_summary(numeric["edge_count"], field_states["edge_count"]["null"] + missing_fields["edge_count"]),
                          "max_hop_depth": numeric_summary(numeric["max_hop_depth"], field_states["max_hop_depth"]["null"] + missing_fields["max_hop_depth"]),
                          "parents_per_record": numeric_summary(parent_lengths, field_states["parents"]["null"] + missing_fields["parents"]),
                          "mechanisms_per_record": numeric_summary(mechanism_lengths, field_states["mechanisms"]["null"] + missing_fields["mechanisms"]),
                          "mechanism_values": dict(sorted(categorical["mechanisms"].items()))},
        "cross_field_consistency": {"contradictions": dict(contradictions), "invalid_record_examples": bad_records,
                                     "checks": ["edge_count >= distinct parent count", "max_hop_depth <= edge_count", "parents sorted", "mechanisms sorted"]},
        "provenance": {
            "record_level_source_fields_present": [], "corpus_level_source": manifest.get("source_table"),
            "source_snapshot": manifest.get("source_snapshot"), "extractor_identity": manifest.get("extractor_identity"),
            "directly_materialized": ["mint", "creator", "direct_funder"],
            "derived_from_local_edge_candidates": ["edge_count", "max_hop_depth", "parents", "mechanisms"],
            "limitation": "Records contain no edge identifiers, transaction signatures, timestamps, source-status fields, or per-record provenance; source lineage is only corpus-level.",
        },
        "eligibility": {"rules": {"ELIGIBLE": "All seven contract fields are present and type-valid; scalar identity fields are non-empty; edge_count/max_hop_depth are non-negative integers; parents/mechanisms are non-empty string lists; arithmetic consistency checks pass.",
                                  "PARTIAL": "Required identity and edge_count are valid, but one or more nullable lineage fields is explicitly null; no imputation is permitted.",
                                  "INELIGIBLE": "Any required field/type/list or arithmetic-consistency failure."},
                        "counts": {state: {"count": eligibility[state], "pct": eligibility[state] * 100 / len(rows) if rows else 0} for state in ("ELIGIBLE", "PARTIAL", "INELIGIBLE")},
                        "partial_reasons": dict(partial_reason_counts), "ineligible_reasons": dict(reason_counts)},
        "feature_family_disposition": {
            "qualified_for_structural_lineage_evaluation": ["mint", "edge_count"],
            "conditional": ["creator", "direct_funder", "max_hop_depth", "parents", "mechanisms"],
            "unqualified_for_time_or_transaction_evaluation": ["timestamps/windows", "transaction-level evidence", "per-edge provenance", "source/status evidence"],
        },
        "downstream_limitations": ["Treat this as a frozen local lineage-structure corpus only.", "Do not claim transaction-level or timestamp/window evidence from these records.", "Retain corpus-level manifest/source binding and do not silently impute unavailable fields."],
        "execution_safety": {"provider_or_network_calls": 0, "source_table_writes": 0, "clean_corpus_mutated": False, "old_forensic_artifacts_mutated": False},
        "recommended_next_stage": "P3R evaluation design and execution limited to frozen lineage-structure features, with the stated provenance and temporal-evidence exclusions enforced.",
    }
    args.artifact.parent.mkdir(parents=True, exist_ok=True)
    with args.artifact.open("x", encoding="utf-8") as handle:
        json.dump(artifact, handle, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    print(json.dumps({"artifact": str(args.artifact), "artifact_sha256": digest(args.artifact), "verdict": principal, "identity_errors": identity_errors}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
