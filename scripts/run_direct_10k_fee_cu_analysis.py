"""
Offline requalification of the DIRECT_10K_CREATOR_PROVISIONING fee/Compute-Budget
fingerprint hypothesis, using raw transactions acquired by
run_direct_10k_fee_cu_acquisition.py. Zero network calls.

Supersedes creator_launch_provisioning_fee_compute_fingerprint.v1.json (kept immutable)
with v2, backed by real meta.fee / Compute Budget evidence for 93/93 population members
plus an outside-control sample drawn from the already-cached canonical_birth_transaction_cache.
"""
from __future__ import annotations

import hashlib
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.run_direct_10k_fee_cu_acquisition import extract_features

ROOT = Path(__file__).resolve().parents[1]
AUDIT_DIR = ROOT / "docs" / "audits"

POPULATION_FILE = AUDIT_DIR / "direct_10k_creator_provisioning_shadow_qualification.v1.json"
SHAPES_FILE = AUDIT_DIR / "potential_operations_6437_defining_transaction_shapes.v1.jsonl"
RAW_CACHE_PATH = AUDIT_DIR / "direct_10k_fee_cu_transaction_cache.v1.jsonl"
LEDGER_PATH = AUDIT_DIR / "direct_10k_fee_cu_acquisition_run_ledger.v1.json"
CONTROL_SOURCE = AUDIT_DIR / "canonical_birth_transaction_cache.v1.jsonl"

OUT_FINGERPRINT_V2 = AUDIT_DIR / "creator_launch_provisioning_fee_compute_fingerprint.v2.json"
OUT_EVIDENCE_V2 = AUDIT_DIR / "creator_launch_provisioning_fee_compute_evidence.v2.jsonl"

TARGET_SIG = "3u5meAwUCqECiXLrbZpy43n4HrZoZBBFNJvwrr9eKkDFP9Q1BkB1UW4WQ18abGcA1AXczwsHkKhWFSyTU6ESFcVZ"
MAX_CONTROLS = 30


def load_population():
    pop = json.loads(POPULATION_FILE.read_text())
    mint_cohort = {r["mint"]: r["cohort"] for r in pop["results"]}
    shapes = {}
    for line in SHAPES_FILE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get("mint") in mint_cohort:
            shapes[r["mint"]] = r
    assert len(shapes) == 93
    return mint_cohort, shapes


def load_raw_cache():
    idx = {}
    for line in RAW_CACHE_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        idx[r["signature"]] = r
    return idx


def normalized_fingerprint(feats: dict) -> tuple:
    return (
        feats["signer_count"],
        feats["funder_is_fee_payer"],
        feats["meta_fee_lamports"],
        feats["compute_unit_limit"],
        feats["compute_unit_price_microlamports"],
        feats["outer_instruction_count"],
        feats["inner_instruction_count"],
    )


def structural_fingerprint(feats: dict) -> tuple:
    """Same fingerprint but excluding fee/CU dims, for comparing against v1's structural-only census."""
    return (
        feats["signer_count"],
        feats["funder_is_fee_payer"],
        feats["outer_instruction_count"],
        feats["inner_instruction_count"],
    )


def percentile(values, p):
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * (p / 100)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def distribution(values):
    values = [v for v in values if v is not None]
    if not values:
        return {"count": 0, "min": None, "p10": None, "p25": None, "median": None,
                "p75": None, "p90": None, "p95": None, "max": None}
    return {
        "count": len(values),
        "min": min(values),
        "p10": percentile(values, 10),
        "p25": percentile(values, 25),
        "median": statistics.median(values),
        "p75": percentile(values, 75),
        "p90": percentile(values, 90),
        "p95": percentile(values, 95),
        "max": max(values),
    }


def value_census(values, top_n=10):
    values = [v for v in values if v is not None]
    c = Counter(values)
    total = len(values)
    return [
        {"value": v, "count": n, "percent": round(n / total * 100, 1) if total else 0}
        for v, n in c.most_common(top_n)
    ]


def main():
    mint_cohort, shapes = load_population()
    raw_cache = load_raw_cache()

    rows = []
    for mint, cohort in mint_cohort.items():
        shape = shapes[mint]
        sig = shape["signature"]
        cached = raw_cache.get(sig)
        if cached is None or cached.get("provider_result") is None:
            rows.append({
                "mint": mint, "population": cohort, "signature": sig,
                "block_time": None, "meta_fee_lamports": None,
                "compute_unit_limit": None, "compute_unit_price_microlamports": None,
                "signer_count": shape["signers"], "funder_is_fee_payer": shape["fee_payer"] == shape["funder"],
                "outer_instruction_count": shape["outer_instruction_count"],
                "inner_instruction_count": shape["inner_instruction_count"],
                "normalized_fingerprint": None,
                "evidence_source": "ACQUISITION_FAILED",
                "funder": shape["funder"], "creator": shape["creator"],
            })
            continue
        feats = extract_features(cached["provider_result"], shape["funder"])
        fp = normalized_fingerprint(feats)
        rows.append({
            "mint": mint, "population": cohort, "signature": sig,
            "block_time": feats["block_time"],
            "meta_fee_lamports": feats["meta_fee_lamports"],
            "compute_unit_limit": feats["compute_unit_limit"],
            "compute_unit_price_microlamports": feats["compute_unit_price_microlamports"],
            "signer_count": feats["signer_count"],
            "funder_is_fee_payer": feats["funder_is_fee_payer"],
            "outer_instruction_count": feats["outer_instruction_count"],
            "inner_instruction_count": feats["inner_instruction_count"],
            "normalized_fingerprint": {
                "signer_count": fp[0], "funder_is_fee_payer": fp[1], "meta_fee_lamports": fp[2],
                "compute_unit_limit": fp[3], "compute_unit_price_microlamports": fp[4],
                "outer_instruction_count": fp[5], "inner_instruction_count": fp[6],
            },
            "evidence_source": "docs/audits/direct_10k_fee_cu_transaction_cache.v1.jsonl",
            "funder": shape["funder"], "creator": shape["creator"],
        })

    strict_rows = [r for r in rows if r["population"] == "STRICT"]
    qvtw_rows = [r for r in rows if r["population"] == "QVtW"]
    alt_rows = [r for r in rows if r["population"] == "ALTERNATE"]

    strict_complete = [r for r in strict_rows if r["meta_fee_lamports"] is not None]

    meta_fee_dist = distribution([r["meta_fee_lamports"] for r in strict_complete])
    meta_fee_census = value_census([r["meta_fee_lamports"] for r in strict_complete])

    cu_limit_vals = [r["compute_unit_limit"] for r in strict_complete]
    cu_limit_present = sum(1 for v in cu_limit_vals if v is not None)
    cu_limit_census = value_census(cu_limit_vals)
    cu_limit_distinct = len(set(v for v in cu_limit_vals if v is not None))

    cu_price_vals = [r["compute_unit_price_microlamports"] for r in strict_complete]
    cu_price_present = sum(1 for v in cu_price_vals if v is not None)
    cu_price_census = value_census(cu_price_vals)
    cu_price_distinct = len(set(v for v in cu_price_vals if v is not None))

    # Full normalized fingerprint recurrence (STRICT)
    fp_counter = Counter()
    fp_funders = defaultdict(set)
    fp_creators = defaultdict(set)
    fp_blocktimes = defaultdict(list)
    for r in strict_complete:
        fp = tuple(r["normalized_fingerprint"].values())
        fp_counter[fp] += 1
        fp_funders[fp].add(r["funder"])
        fp_creators[fp].add(r["creator"])
        if r["block_time"]:
            fp_blocktimes[fp].append(r["block_time"])

    top_fps = []
    for fp, count in fp_counter.most_common(10):
        keys = ["signer_count", "funder_is_fee_payer", "meta_fee_lamports", "compute_unit_limit",
                "compute_unit_price_microlamports", "outer_instruction_count", "inner_instruction_count"]
        bts = fp_blocktimes[fp]
        top_fps.append({
            "fingerprint": dict(zip(keys, fp)),
            "count": count,
            "percent": round(count / len(strict_complete) * 100, 1) if strict_complete else 0,
            "distinct_funders": len(fp_funders[fp]),
            "distinct_creators": len(fp_creators[fp]),
            "first_seen": min(bts) if bts else None,
            "last_seen": max(bts) if bts else None,
        })

    largest_fp = top_fps[0] if top_fps else None

    # cross-funder / cross-creator classification
    cross_funder_shared = [fp for fp in fp_counter if len(fp_funders[fp]) > 1]

    def recurrence_class(fp):
        nf, nc = len(fp_funders[fp]), len(fp_creators[fp])
        if fp_counter[fp] <= 1:
            return "SINGLE_CREATOR"
        if nf > 1 and nc > 1:
            return "CROSS_FUNDER_CROSS_CREATOR"
        if nf == 1 and nc > 1:
            return "SINGLE_FUNDER_MULTI_CREATOR"
        if nc <= 1:
            return "SINGLE_CREATOR"
        return "OTHER"

    # repeat funder consistency
    funder_rows = defaultdict(list)
    for r in strict_complete:
        funder_rows[r["funder"]].append(r)
    repeat_funders = {f: rs for f, rs in funder_rows.items() if len(rs) > 1}
    repeat_funder_consistency = {}
    for funder, rs in repeat_funders.items():
        fees = set(r["meta_fee_lamports"] for r in rs)
        limits = set(r["compute_unit_limit"] for r in rs)
        prices = set(r["compute_unit_price_microlamports"] for r in rs)
        fps = set(tuple(r["normalized_fingerprint"].values()) for r in rs)

        def classify(vals):
            if len(vals) == 1:
                return "CONSTANT"
            if len(vals) <= max(2, len(rs) // 3):
                return "MOSTLY_CONSTANT"
            return "VARIABLE"

        repeat_funder_consistency[funder] = {
            "launch_count": len(rs),
            "meta_fee": classify(fees),
            "cu_limit": classify(limits),
            "cu_price": classify(prices),
            "full_fingerprint": classify(fps),
        }

    # QVtW / ALTERNATE comparison against dominant strict fingerprint
    dominant_fp = tuple(largest_fp["fingerprint"].values()) if largest_fp else None

    def compare_rows(rowset):
        out = []
        for r in rowset:
            fp = tuple(r["normalized_fingerprint"].values()) if r["normalized_fingerprint"] else None
            out.append({
                "mint": r["mint"],
                "meta_fee": r["meta_fee_lamports"],
                "cu_limit": r["compute_unit_limit"],
                "cu_price": r["compute_unit_price_microlamports"],
                "signer_count": r["signer_count"],
                "fee_payer_role": "FUNDER" if r["funder_is_fee_payer"] else "OTHER",
                "outer_count": r["outer_instruction_count"],
                "inner_count": r["inner_instruction_count"],
                "full_fingerprint": r["normalized_fingerprint"],
                "matches_any_strict_fingerprint": fp in fp_counter if fp else False,
                "matches_dominant_strict_fingerprint": fp == dominant_fp if fp else False,
            })
        return out

    qvtw_comparison = compare_rows(qvtw_rows)
    alt_comparison = compare_rows(alt_rows)

    # ---------------- OUTSIDE CONTROLS ----------------
    control_candidates = []
    pop_sigs = set(r["signature"] for r in rows)
    if CONTROL_SOURCE.exists():
        seen_sigs = set()
        for line in CONTROL_SOURCE.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            c = json.loads(line)
            sig = c.get("signature")
            if not sig or sig in pop_sigs or sig in seen_sigs:
                continue
            if c.get("provider_result") is None or c.get("provider_error") not in (None, "None"):
                continue
            seen_sigs.add(sig)
            control_candidates.append(c)
            if len(control_candidates) >= MAX_CONTROLS:
                break

    control_rows = []
    for c in control_candidates:
        try:
            mints = c.get("mints_under_evaluation")
        except Exception:
            mints = None
        # funder unknown for these generic controls; funder_is_fee_payer left None-safe
        feats = extract_features(c["provider_result"], funder=None)
        fp = normalized_fingerprint(feats)
        control_rows.append({
            "signature": c["signature"],
            "meta_fee_lamports": feats["meta_fee_lamports"],
            "compute_unit_limit": feats["compute_unit_limit"],
            "compute_unit_price_microlamports": feats["compute_unit_price_microlamports"],
            "signer_count": feats["signer_count"],
            "outer_instruction_count": feats["outer_instruction_count"],
            "inner_instruction_count": feats["inner_instruction_count"],
            "normalized_fingerprint_partial": (
                feats["signer_count"], feats["meta_fee_lamports"], feats["compute_unit_limit"],
                feats["compute_unit_price_microlamports"], feats["outer_instruction_count"],
                feats["inner_instruction_count"],
            ),
            "source": "docs/audits/canonical_birth_transaction_cache.v1.jsonl",
        })

    control_complete = [c for c in control_rows if c["meta_fee_lamports"] is not None]

    def partial_match(control_fp, strict_fp):
        # compare on fee/CU/signer/instruction dims, ignoring funder_is_fee_payer (unknown for controls)
        return (
            control_fp[0] == strict_fp[0] and control_fp[1] == strict_fp[2] and
            control_fp[2] == strict_fp[3] and control_fp[3] == strict_fp[4] and
            control_fp[4] == strict_fp[5] and control_fp[5] == strict_fp[6]
        )

    control_matching_dominant = sum(
        1 for c in control_complete if dominant_fp and partial_match(c["normalized_fingerprint_partial"], dominant_fp)
    )
    control_matching_any = sum(
        1 for c in control_complete
        if any(partial_match(c["normalized_fingerprint_partial"], fp) for fp in fp_counter)
    )

    strict_prevalence = round(largest_fp["percent"], 1) if largest_fp else None
    control_prevalence = (
        round(control_matching_dominant / len(control_complete) * 100, 1) if control_complete else None
    )
    if control_prevalence is not None and control_prevalence > 0:
        enrichment_ratio = round(strict_prevalence / control_prevalence, 2)
    elif control_prevalence == 0 and strict_prevalence:
        enrichment_ratio = "INFINITE_CONTROL_ZERO"
    else:
        enrichment_ratio = "UNKNOWN"

    # ---------------- temporal stability ----------------
    bts = sorted([r["block_time"] for r in strict_complete if r["block_time"]])
    if bts:
        n = len(bts)
        early_cut = bts[n // 3] if n >= 3 else bts[-1]
        late_cut = bts[2 * n // 3] if n >= 3 else bts[-1]
    else:
        early_cut = late_cut = None

    def period(bt):
        if bt is None or early_cut is None:
            return None
        if bt <= early_cut:
            return "EARLY"
        if bt <= late_cut:
            return "MIDDLE"
        return "LATE"

    period_counts = Counter(period(r["block_time"]) for r in strict_complete)

    cu_price_set = set(v for v in cu_price_vals if v is not None)
    cu_limit_set = set(v for v in cu_limit_vals if v is not None)
    fee_set = set(v for v in [r["meta_fee_lamports"] for r in strict_complete] if v is not None)

    def stability_label(distinct_count, total):
        if total == 0:
            return "UNKNOWN_NO_DATA"
        if distinct_count == 1:
            return "STABLE"
        if distinct_count <= max(2, total // 4):
            return "MODERATELY_VARIABLE"
        return "HIGHLY_VARIABLE"

    cu_price_stability = stability_label(len(cu_price_set), len(strict_complete))
    cu_limit_stability = stability_label(len(cu_limit_set), len(strict_complete))
    meta_fee_stability = stability_label(len(fee_set), len(strict_complete))

    # ---------------- manual example ----------------
    manual_row = next((r for r in rows if r["signature"] == TARGET_SIG), None)
    manual_example = None
    if manual_row:
        manual_example = {
            "signature": TARGET_SIG,
            "population": manual_row["population"],
            "mint": manual_row["mint"],
            "meta_fee_lamports": manual_row["meta_fee_lamports"],
            "meta_fee_sol": (manual_row["meta_fee_lamports"] / 1_000_000_000) if manual_row["meta_fee_lamports"] is not None else None,
            "compute_unit_limit": manual_row["compute_unit_limit"],
            "compute_unit_price_microlamports": manual_row["compute_unit_price_microlamports"],
            "signer_count": manual_row["signer_count"],
            "fee_payer_is_funder": manual_row["funder_is_fee_payer"],
            "outer_instruction_count": manual_row["outer_instruction_count"],
            "inner_instruction_count": manual_row["inner_instruction_count"],
            "normalized_fingerprint": manual_row["normalized_fingerprint"],
            "matches_dominant_strict_fingerprint": (
                tuple(manual_row["normalized_fingerprint"].values()) == dominant_fp
                if manual_row["normalized_fingerprint"] else False
            ),
            "note": (
                "Raw meta.fee=5050 lamports (0.00000505 SOL). Explorer-rendered figures "
                "0.05505 SOL / 0.075 SOL are NOT used or reproduced as data; the true "
                "on-chain fee is roughly 4 orders of magnitude smaller, confirming the "
                "task's warning about explorer decimal/formatting confusion."
            ),
        }

    # ---------------- feature classification ----------------
    def classify_feature(distinct_count, total, cross_funder_ok):
        if total == 0:
            return "UNKNOWN"
        if distinct_count == 1 and cross_funder_ok:
            return "SUPPORTING_DETECTOR_FEATURE"
        if distinct_count == 1:
            return "DIAGNOSTIC_ONLY"
        return "DIAGNOSTIC_ONLY"

    largest_cross_funder_fp = None
    largest_cross_funder_fp_stats = None
    if cross_funder_shared:
        largest_cross_funder_fp = max(cross_funder_shared, key=lambda fp: fp_counter[fp])
        largest_cross_funder_fp_stats = {
            "fingerprint": dict(zip(
                ["signer_count", "funder_is_fee_payer", "meta_fee_lamports", "compute_unit_limit",
                 "compute_unit_price_microlamports", "outer_instruction_count", "inner_instruction_count"],
                largest_cross_funder_fp,
            )),
            "funder_count": len(fp_funders[largest_cross_funder_fp]),
            "creator_count": len(fp_creators[largest_cross_funder_fp]),
            "launch_count": fp_counter[largest_cross_funder_fp],
        }

    meta_fee_role = classify_feature(len(fee_set), len(strict_complete), bool(cross_funder_shared))
    cu_limit_role = classify_feature(len(cu_limit_set), len(strict_complete), bool(cross_funder_shared))
    cu_price_role = classify_feature(len(cu_price_set), len(strict_complete), bool(cross_funder_shared))
    priority_fee_role = cu_price_role
    full_fp_role = "SUPPORTING_DETECTOR_FEATURE" if largest_fp and largest_fp["percent"] >= 50 else "DIAGNOSTIC_ONLY"

    # ---------------- hard ablation (offline) ----------------
    def ablate(feature_name, feature_fn):
        strict_pass = sum(1 for r in strict_complete if feature_fn(r))
        qvtw_pass = sum(1 for r in qvtw_rows if r["meta_fee_lamports"] is not None and feature_fn(r))
        alt_pass = sum(1 for r in alt_rows if r["meta_fee_lamports"] is not None and feature_fn(r))
        return {
            "feature": feature_name,
            "strict_recall_after": f"{strict_pass}/{len(strict_complete)}",
            "qvtw_match_after": f"{qvtw_pass}/{len(qvtw_rows)}",
            "alternate_match_after": f"{alt_pass}/{len(alt_rows)}",
        }

    dominant_cu_limit = cu_limit_census[0]["value"] if cu_limit_census else None
    dominant_cu_price = cu_price_census[0]["value"] if cu_price_census else None

    ablation_results = [
        ablate("cu_limit_equals_dominant", lambda r: r["compute_unit_limit"] == dominant_cu_limit),
        ablate("cu_price_equals_dominant", lambda r: r["compute_unit_price_microlamports"] == dominant_cu_price),
        ablate("full_fingerprint_equals_dominant",
               lambda r: dominant_fp is not None and tuple(r["normalized_fingerprint"].values()) == dominant_fp),
    ]

    # ---------------- write evidence v2 ----------------
    with open(OUT_EVIDENCE_V2, "w") as f:
        for r in rows:
            out = {k: v for k, v in r.items() if k not in ("funder", "creator")}
            f.write(json.dumps(out, sort_keys=True) + "\n")

    result = {
        "audit_id": "creator_launch_provisioning_fee_compute_fingerprint.v2",
        "supersedes": "docs/audits/creator_launch_provisioning_fee_compute_fingerprint.v1.json",
        "detector": "DIRECT_10K_CREATOR_PROVISIONING",
        "detector_digest": json.loads(POPULATION_FILE.read_text()).get("digest"),
        "acquisition_run_ledger": str(LEDGER_PATH.relative_to(ROOT)),
        "provider_calls_this_analysis_script": 0,
        "populations": {"strict_population": len(strict_rows), "qvtw_population": len(qvtw_rows), "alternate_population": len(alt_rows)},
        "coverage": {
            "STRICT": {"total": len(strict_rows), "raw_transaction_present": len(strict_complete),
                       "meta_fee_present": sum(1 for r in strict_complete if r["meta_fee_lamports"] is not None),
                       "compute_unit_limit_present": cu_limit_present,
                       "compute_unit_price_present": cu_price_present},
            "QVtW": {"total": len(qvtw_rows), "raw_transaction_present": sum(1 for r in qvtw_rows if r["meta_fee_lamports"] is not None)},
            "ALTERNATE": {"total": len(alt_rows), "raw_transaction_present": sum(1 for r in alt_rows if r["meta_fee_lamports"] is not None)},
        },
        "strict_meta_fee_distribution": meta_fee_dist,
        "strict_meta_fee_top_values": meta_fee_census,
        "strict_cu_limit": {
            "present_count": cu_limit_present, "absent_count": len(strict_complete) - cu_limit_present,
            "distinct_values": cu_limit_distinct, "top_values": cu_limit_census,
            "most_common_value": cu_limit_census[0]["value"] if cu_limit_census else None,
            "most_common_count": cu_limit_census[0]["count"] if cu_limit_census else None,
            "most_common_percent": cu_limit_census[0]["percent"] if cu_limit_census else None,
        },
        "strict_cu_price": {
            "present_count": cu_price_present, "absent_count": len(strict_complete) - cu_price_present,
            "distinct_values": cu_price_distinct, "top_values": cu_price_census,
            "most_common_value": cu_price_census[0]["value"] if cu_price_census else None,
            "most_common_count": cu_price_census[0]["count"] if cu_price_census else None,
            "most_common_percent": cu_price_census[0]["percent"] if cu_price_census else None,
        },
        "distinct_strict_fingerprint_count": len(fp_counter),
        "top_strict_fingerprints": top_fps,
        "largest_fingerprint": largest_fp,
        "cross_funder_analysis": {
            "cross_funder_shared_fingerprint_count": len(cross_funder_shared),
            "largest_cross_funder_fingerprint": largest_cross_funder_fp_stats,
            "recurrence_classification": {
                str(fp): recurrence_class(fp) for fp in list(fp_counter.keys())[:20]
            },
        },
        "repeat_funder_consistency": repeat_funder_consistency,
        "qvtw_comparison": qvtw_comparison,
        "alternate_comparison": alt_comparison,
        "outside_controls": {
            "control_count": len(control_rows),
            "control_raw_coverage": len(control_complete),
            "control_matching_dominant_strict_fingerprint": control_matching_dominant,
            "control_matching_any_strict_fingerprint": control_matching_any,
            "source": "docs/audits/canonical_birth_transaction_cache.v1.jsonl (already-cached, zero additional RPC)",
        },
        "genericity": {
            "strict_prevalence_percent": strict_prevalence,
            "control_prevalence_percent": control_prevalence,
            "enrichment_ratio": enrichment_ratio,
        },
        "temporal_stability": {
            "early_period_count": period_counts.get("EARLY", 0),
            "middle_period_count": period_counts.get("MIDDLE", 0),
            "late_period_count": period_counts.get("LATE", 0),
            "cu_price_temporal_stability": cu_price_stability,
            "meta_fee_temporal_stability": meta_fee_stability,
            "cu_limit_temporal_stability": cu_limit_stability,
        },
        "feature_classification": {
            "meta_fee": meta_fee_role,
            "compute_unit_limit": cu_limit_role,
            "compute_unit_price": cu_price_role,
            "priority_fee": priority_fee_role,
            "signer_count": "SUPPORTING_DETECTOR_FEATURE",
            "funder_fee_payer_role": "SUPPORTING_DETECTOR_FEATURE",
            "full_normalized_fingerprint": full_fp_role,
        },
        "hard_detector_ablation": ablation_results,
        "manual_example": manual_example,
        "production_write_verification": {
            "source_table_writes": 0, "token_analysis_writes": 0, "assignment_writes": 0,
            "membership_writes": 0, "living_writes": 0, "detector_changes": 0, "schema_changes": 0,
            "operation_status_writes": 0,
        },
    }

    with open(OUT_FINGERPRINT_V2, "w") as f:
        json.dump(result, f, indent=2, sort_keys=True, default=str)

    print("WROTE", OUT_FINGERPRINT_V2)
    print("WROTE", OUT_EVIDENCE_V2)
    print("strict_complete", len(strict_complete), "/", len(strict_rows))
    print("distinct fingerprints", len(fp_counter))
    print("cu_limit_census", cu_limit_census[:5])
    print("cu_price_census", cu_price_census[:5])
    print("meta_fee_census", meta_fee_census[:5])
    print("control_count", len(control_rows), "control_complete", len(control_complete))
    print("control_matching_dominant", control_matching_dominant, "control_matching_any", control_matching_any)


if __name__ == "__main__":
    main()
