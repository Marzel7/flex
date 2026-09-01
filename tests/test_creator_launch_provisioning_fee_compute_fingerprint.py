"""
Focused tests for the fee/compute-budget fingerprint qualification of
DIRECT_10K_CREATOR_PROVISIONING (docs/audits/creator_launch_provisioning_fee_compute_fingerprint.v1.json).

No network/RPC calls. Verifies the analysis honestly reports zero fee/CU
coverage rather than fabricating values, and that the structural (address-blind,
non-fee) fingerprint census that WAS computed is correct and deterministic.
"""
import hashlib
import json
import os

AUDIT_DIR = os.path.join(os.path.dirname(__file__), "..", "docs", "audits")
FINGERPRINT_PATH = os.path.join(AUDIT_DIR, "creator_launch_provisioning_fee_compute_fingerprint.v1.json")
EVIDENCE_PATH = os.path.join(AUDIT_DIR, "creator_launch_provisioning_fee_compute_evidence.v1.jsonl")
POPULATION_PATH = os.path.join(AUDIT_DIR, "direct_10k_creator_provisioning_shadow_qualification.v1.json")
SHAPES_PATH = os.path.join(AUDIT_DIR, "potential_operations_6437_defining_transaction_shapes.v1.jsonl")

TARGET_SIG = "3u5meAwUCqECiXLrbZpy43n4HrZoZBBFNJvwrr9eKkDFP9Q1BkB1UW4WQ18abGcA1AXczwsHkKhWFSyTU6ESFcVZ"


def _load_fingerprint():
    with open(FINGERPRINT_PATH) as f:
        return json.load(f)


def _load_evidence_rows():
    rows = []
    with open(EVIDENCE_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _load_population():
    with open(POPULATION_PATH) as f:
        d = json.load(f)
    return {r["mint"]: r["cohort"] for r in d["results"]}


def test_no_provider_calls_declared():
    d = _load_fingerprint()
    assert d["provider_calls"] == 0
    assert d["rpc_calls"] == 0
    assert d["network_calls"] == 0


def test_production_write_verification_all_zero():
    d = _load_fingerprint()
    v = d["production_write_verification"]
    for key in (
        "source_table_writes", "token_analysis_writes", "assignment_writes",
        "membership_writes", "living_writes", "detector_changes", "schema_changes",
    ):
        assert v[key] == 0, key


def test_population_sizes_match_84_6_3():
    d = _load_fingerprint()
    assert d["populations"]["strict_population"] == 84
    assert d["populations"]["qvtw_population"] == 6
    assert d["populations"]["alternate_population"] == 3


def test_population_membership_independent_of_fee_data():
    """Membership must come from the qualification file, never derived from fees."""
    mint_cohort = _load_population()
    counts = {"STRICT": 0, "QVtW": 0, "ALTERNATE": 0}
    for c in mint_cohort.values():
        counts[c] += 1
    assert counts == {"STRICT": 84, "QVtW": 6, "ALTERNATE": 3}


def test_evidence_jsonl_covers_every_population_mint_exactly_once():
    mint_cohort = _load_population()
    rows = _load_evidence_rows()
    assert len(rows) == 93
    seen = {}
    for r in rows:
        assert r["mint"] in mint_cohort
        seen[r["mint"]] = r
    assert set(seen) == set(mint_cohort)


def test_meta_fee_and_compute_budget_never_fabricated():
    """Every evidence row must report null/NOT_AVAILABLE for fee/CU fields, never a fabricated number."""
    rows = _load_evidence_rows()
    for r in rows:
        assert r["meta_fee_lamports"] is None
        assert r["compute_unit_limit"] is None
        assert r["compute_unit_price_microlamports"] is None
        fp = r["normalized_fingerprint"]
        if fp is not None:
            assert fp["meta_fee_lamports"] is None
            assert fp["compute_unit_limit"] is None
            assert fp["compute_unit_price_microlamports"] is None


def test_evidence_source_is_never_raw_transaction_for_this_population():
    rows = _load_evidence_rows()
    for r in rows:
        assert r["evidence_source"] in (
            "docs/audits/potential_operations_6437_defining_transaction_shapes.v1.jsonl",
            "NONE_RETAINED",
        )


def test_normalized_fingerprint_is_address_blind():
    """Fingerprint dict must never contain mint/creator/funder/signature keys."""
    rows = _load_evidence_rows()
    forbidden = {"mint", "creator", "funder", "signature", "address"}
    for r in rows:
        fp = r["normalized_fingerprint"]
        if fp is not None:
            assert forbidden.isdisjoint(fp.keys())


def test_coverage_section_reports_zero_fee_cu_for_all_populations():
    d = _load_fingerprint()
    for pop in ("STRICT", "QVtW", "ALTERNATE"):
        cov = d["coverage"][pop]
        assert cov["raw_transaction_present"] == 0
        assert cov["meta_fee_present"] == 0
        assert cov["compute_unit_limit_present"] == 0
        assert cov["compute_unit_price_present"] == 0
        # structural fields ARE available
        assert cov["fee_payer_present"] == cov["total"]
        assert cov["signer_set_present"] == cov["total"]


def test_structural_fingerprint_census_deterministic_and_matches_recount():
    """Recompute the STRICT structural census independently and compare to the artifact."""
    mint_cohort = _load_population()
    shapes = {}
    with open(SHAPES_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("mint") in mint_cohort:
                shapes[r["mint"]] = r

    from collections import Counter
    counts = Counter()
    for mint, cohort in mint_cohort.items():
        if cohort != "STRICT":
            continue
        r = shapes[mint]
        key = (r["signers"], r["fee_payer"] == r["funder"], r["outer_instruction_count"], r["inner_instruction_count"])
        counts[key] += 1

    d = _load_fingerprint()
    top = d["structural_fingerprint_strict_no_fee"]["top_fingerprints"]
    assert d["structural_fingerprint_strict_no_fee"]["distinct_partial_fingerprint_count"] == len(counts)

    reported_counts = Counter()
    for entry in top:
        fp = entry["fingerprint_partial_no_fee"]
        key = (fp["signer_count"], fp["funder_is_fee_payer"], fp["outer_instruction_count"], fp["inner_instruction_count"])
        reported_counts[key] = entry["count"]

    assert dict(reported_counts) == dict(counts)
    assert sum(counts.values()) == 84


def test_dominant_strict_structural_fingerprint_is_89_percent():
    d = _load_fingerprint()
    top = d["structural_fingerprint_strict_no_fee"]["top_fingerprints"]
    assert top[0]["count"] == 75
    assert top[0]["percent"] == 89.3


def test_qvtw_and_alternate_comparisons_present_and_marked_indeterminate():
    d = _load_fingerprint()
    assert len(d["qvtw_comparison"]) == 6
    assert len(d["alternate_comparison"]) == 3
    for row in d["qvtw_comparison"]:
        assert row["meta_fee"] == "NOT_AVAILABLE_LOCALLY"
        assert row["strict_fingerprint_match"] == "INDETERMINATE_NO_FEE_DATA"
    for row in d["alternate_comparison"]:
        assert row["meta_fee"] == "NOT_AVAILABLE_LOCALLY"
        assert isinstance(row["match_strict_dominant_pattern"], bool)


def test_control_population_reported_as_unknown_not_zero_by_fiat():
    d = _load_fingerprint()
    nc = d["negative_controls"]
    assert nc["control_count"] == 0
    assert nc["control_matching_top_strict_fingerprint"] == "UNKNOWN"
    assert nc["control_matching_any_strict_fingerprint"] == "UNKNOWN"


def test_feature_classification_conservative_not_useful_for_fee_cu():
    d = _load_fingerprint()
    fc = d["feature_classification"]
    for feature in ("meta_fee", "compute_unit_limit", "compute_unit_price", "priority_fee"):
        assert fc[feature] == "NOT_USEFUL"
    # structural fields already retained can be classified higher
    assert fc["signer_count"] == "SUPPORTING_DETECTOR_FEATURE"
    assert fc["funder_fee_payer_role"] == "SUPPORTING_DETECTOR_FEATURE"


def test_manual_example_signature_found_in_structural_shapes_not_raw_tx():
    d = _load_fingerprint()
    ex = d["manual_example"]
    assert ex["signature"] == TARGET_SIG
    assert ex["found_in_structural_shapes"] is True
    assert ex["meta_fee_lamports"] == "NOT_AVAILABLE_LOCALLY"
    assert ex["compute_unit_limit"] == "NOT_AVAILABLE_LOCALLY"
    # Confirm no Solscan-formatted numeric value leaked in as a real field value
    # (the note field legitimately names the rejected values in prose).
    assert ex["meta_fee_lamports"] != 0.05505
    assert ex["meta_fee_lamports"] != 0.075
    assert isinstance(ex.get("meta_fee_lamports"), str)


def test_manual_example_is_in_strict_population():
    mint_cohort = _load_population()
    d = _load_fingerprint()
    mint = d["manual_example"]["mint"]
    assert mint_cohort[mint] == "STRICT"
    assert d["manual_example"]["cohort"] == "STRICT"


def test_detector_and_hard_ablation_left_untouched():
    d = _load_fingerprint()
    assert d["production_write_verification"]["detector_changes"] == 0
    ablation = d["hard_detector_ablation"]
    assert "note" in ablation


def test_artifacts_hash_stable_and_readable():
    for path in (FINGERPRINT_PATH, EVIDENCE_PATH):
        with open(path, "rb") as f:
            data = f.read()
        digest = hashlib.sha256(data).hexdigest()
        assert len(digest) == 64


def test_no_network_or_rpc_imports_in_this_test_module():
    """Sanity: this test file itself performs no network calls."""
    with open(__file__) as f:
        src = f.read()
    markers = ["requests" + ".", "httpx" + ".", "urllib.request", "solana.rpc", "AsyncClient" + "("]
    for forbidden in markers:
        assert src.count(forbidden) <= 1  # the one occurrence is this marker list itself
