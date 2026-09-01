"""
Focused tests for the v2 fee/Compute-Budget acquisition + requalification of
DIRECT_10K_CREATOR_PROVISIONING (scripts/run_direct_10k_fee_cu_acquisition.py,
scripts/run_direct_10k_fee_cu_analysis.py).

No network calls in this test module itself.
"""
import base58
import hashlib
import json
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.run_direct_10k_fee_cu_acquisition import (
    decode_compute_budget_instruction,
    extract_compute_budget,
    extract_features,
    load_raw_cache_index,
)
from scripts.run_direct_10k_fee_cu_analysis import (
    normalized_fingerprint,
    structural_fingerprint,
    distribution,
    value_census,
)

ROOT = os.path.join(os.path.dirname(__file__), "..")
AUDIT_DIR = os.path.join(ROOT, "docs", "audits")
LEDGER_PATH = os.path.join(AUDIT_DIR, "direct_10k_fee_cu_acquisition_run_ledger.v1.json")
RAW_CACHE_PATH = os.path.join(AUDIT_DIR, "direct_10k_fee_cu_transaction_cache.v1.jsonl")
FINGERPRINT_V2_PATH = os.path.join(AUDIT_DIR, "creator_launch_provisioning_fee_compute_fingerprint.v2.json")
EVIDENCE_V2_PATH = os.path.join(AUDIT_DIR, "creator_launch_provisioning_fee_compute_evidence.v2.jsonl")
POPULATION_PATH = os.path.join(AUDIT_DIR, "direct_10k_creator_provisioning_shadow_qualification.v1.json")

TARGET_SIG = "3u5meAwUCqECiXLrbZpy43n4HrZoZBBFNJvwrr9eKkDFP9Q1BkB1UW4WQ18abGcA1AXczwsHkKhWFSyTU6ESFcVZ"


# ---------------- ComputeBudget decoding ----------------

def test_decode_set_compute_unit_limit():
    raw = struct.pack("<BI", 2, 1_000_000)
    decoded = decode_compute_budget_instruction(raw)
    assert decoded["kind"] == "SetComputeUnitLimit"
    assert decoded["compute_unit_limit"] == 1_000_000


def test_decode_set_compute_unit_price():
    raw = struct.pack("<BQ", 3, 50)
    decoded = decode_compute_budget_instruction(raw)
    assert decoded["kind"] == "SetComputeUnitPrice"
    assert decoded["compute_unit_price_microlamports"] == 50


def test_decode_request_heap_frame():
    raw = struct.pack("<BI", 1, 32768)
    decoded = decode_compute_budget_instruction(raw)
    assert decoded["kind"] == "RequestHeapFrame"
    assert decoded["heap_frame_bytes"] == 32768


def test_decode_set_loaded_accounts_data_size_limit():
    raw = struct.pack("<BI", 4, 65536)
    decoded = decode_compute_budget_instruction(raw)
    assert decoded["kind"] == "SetLoadedAccountsDataSizeLimit"


def test_decode_unsupported_discriminator_fails_closed_not_crash():
    raw = struct.pack("<BI", 99, 12345)
    decoded = decode_compute_budget_instruction(raw)
    assert decoded["kind"] == "UNSUPPORTED_DISCRIMINATOR"
    assert decoded["discriminator"] == 99


def test_decode_empty_bytes_fails_closed():
    decoded = decode_compute_budget_instruction(b"")
    assert decoded["kind"] == "UNSUPPORTED_EMPTY"


def test_decode_malformed_short_bytes_fails_closed():
    raw = struct.pack("<B", 2)  # discriminator claims limit but no payload
    decoded = decode_compute_budget_instruction(raw)
    assert decoded["kind"] == "UNSUPPORTED_DISCRIMINATOR"  # too short to match len>=5 branch


def test_decode_against_real_observed_bytes():
    """Validated against actual cached instruction data from
    canonical_birth_transaction_cache.v1.jsonl (base58 'FXNmT5' / '3k76Bc2j4urX')."""
    d1 = base58.b58decode("FXNmT5")
    decoded1 = decode_compute_budget_instruction(d1)
    assert decoded1["kind"] == "SetComputeUnitLimit"
    assert decoded1["compute_unit_limit"] == 275000

    d2 = base58.b58decode("3k76Bc2j4urX")
    decoded2 = decode_compute_budget_instruction(d2)
    assert decoded2["kind"] == "SetComputeUnitPrice"
    assert decoded2["compute_unit_price_microlamports"] == 80567


def test_extract_compute_budget_from_instruction_list():
    instrs = [
        {"programId": "11111111111111111111111111111111", "data": "abc"},
        {"programId": "ComputeBudget111111111111111111111111111111", "data": base58.b58encode(struct.pack("<BI", 2, 1000000)).decode()},
        {"programId": "ComputeBudget111111111111111111111111111111", "data": base58.b58encode(struct.pack("<BQ", 3, 50)).decode()},
    ]
    cb = extract_compute_budget(instrs)
    assert cb["compute_unit_limit"] == 1000000
    assert cb["compute_unit_price_microlamports"] == 50
    assert len(cb["compute_budget_instructions"]) == 2


# ---------------- feature extraction ----------------

def _fake_tx_result(fee=5050, cu_limit=1000000, cu_price=50, funder="F1", n_signers=1):
    return {
        "slot": 100,
        "blockTime": 1700000000,
        "version": "legacy",
        "meta": {"fee": fee, "innerInstructions": []},
        "transaction": {
            "message": {
                "accountKeys": [{"pubkey": funder, "signer": True}] + [{"pubkey": f"X{i}", "signer": False} for i in range(n_signers)],
                "instructions": [
                    {"programId": "ComputeBudget111111111111111111111111111111", "data": base58.b58encode(struct.pack("<BI", 2, cu_limit)).decode()},
                    {"programId": "ComputeBudget111111111111111111111111111111", "data": base58.b58encode(struct.pack("<BQ", 3, cu_price)).decode()},
                    {"programId": "11111111111111111111111111111111"},
                ],
            }
        },
    }


def test_extract_features_meta_fee_raw_lamports():
    feats = extract_features(_fake_tx_result(fee=5050), funder="F1")
    assert feats["meta_fee_lamports"] == 5050


def test_extract_features_fee_payer_resolution():
    feats = extract_features(_fake_tx_result(funder="F1"), funder="F1")
    assert feats["fee_payer"] == "F1"
    assert feats["funder_is_fee_payer"] is True


def test_extract_features_signer_count():
    feats = extract_features(_fake_tx_result(), funder="F1")
    assert feats["signer_count"] == 1


def test_extract_features_cu_limit_and_price_normalized():
    feats = extract_features(_fake_tx_result(cu_limit=1000000, cu_price=50), funder="F1")
    assert feats["compute_unit_limit"] == 1000000
    assert feats["compute_unit_price_microlamports"] == 50


def test_extract_features_outer_inner_counts():
    feats = extract_features(_fake_tx_result(), funder="F1")
    assert feats["outer_instruction_count"] == 3
    assert feats["inner_instruction_count"] == 0


# ---------------- normalized fingerprint determinism + address-blindness ----------------

def test_normalized_fingerprint_deterministic():
    feats = extract_features(_fake_tx_result(), funder="F1")
    fp1 = normalized_fingerprint(feats)
    fp2 = normalized_fingerprint(feats)
    assert fp1 == fp2


def test_normalized_fingerprint_is_address_blind():
    feats = extract_features(_fake_tx_result(funder="SomeLongFunderAddress"), funder="SomeLongFunderAddress")
    fp = normalized_fingerprint(feats)
    for element in fp:
        assert element != "SomeLongFunderAddress"


def test_structural_fingerprint_excludes_fee_cu():
    feats = extract_features(_fake_tx_result(), funder="F1")
    sfp = structural_fingerprint(feats)
    assert len(sfp) == 4  # signer_count, funder_is_fee_payer, outer, inner


# ---------------- distribution / census math ----------------

def test_distribution_percentiles():
    d = distribution([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    assert d["min"] == 1
    assert d["max"] == 10
    assert d["median"] == 5.5
    assert d["count"] == 10


def test_distribution_empty_no_crash():
    d = distribution([])
    assert d["count"] == 0
    assert d["min"] is None


def test_value_census_percent_sums_within_rounding():
    census = value_census([5050] * 74 + [5000] * 10)
    total_pct = sum(c["percent"] for c in census)
    assert 99.0 <= total_pct <= 101.0
    assert census[0]["value"] == 5050
    assert census[0]["count"] == 74


# ---------------- acquisition run: no getSignatures, ledger conservation ----------------

def test_ledger_only_used_get_transaction_method():
    assert os.path.exists(LEDGER_PATH), "run ledger must exist after acquisition"
    with open(LEDGER_PATH) as f:
        d = json.load(f)
    methods = set(a["rpc_method"] for a in d["attempts"])
    assert methods == {"getTransaction"}, "must never call getSignaturesForAddress for known-signature population"


def test_ledger_conservation_no_overrun():
    with open(LEDGER_PATH) as f:
        d = json.load(f)
    assert d["calls_attempted"] <= d["authorized_max_network_calls"]
    assert d["calls_remaining"] == d["authorized_max_network_calls"] - d["calls_attempted"]


def test_ledger_covers_exactly_93_known_signatures():
    with open(LEDGER_PATH) as f:
        d = json.load(f)
    targets = set(a["target"] for a in d["attempts"])
    assert len(targets) == 93, "one getTransaction call per unique population signature, no duplicates"


def test_raw_cache_no_duplicate_signature_rows():
    seen = set()
    with open(RAW_CACHE_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            assert row["signature"] not in seen, "duplicate signature fetched — dedupe failed"
            seen.add(row["signature"])
    assert len(seen) == 93


def test_raw_cache_before_classify_full_response_persisted():
    with open(RAW_CACHE_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row["outcome"] == "SUCCESS":
                assert row["provider_result"] is not None
                assert "meta" in row["provider_result"]
                assert "transaction" in row["provider_result"]


# ---------------- population conservation in v2 evidence ----------------

def _load_population():
    with open(POPULATION_PATH) as f:
        d = json.load(f)
    return {r["mint"]: r["cohort"] for r in d["results"]}


def test_v2_evidence_covers_all_93_population_rows():
    mint_cohort = _load_population()
    rows = []
    with open(EVIDENCE_V2_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    assert len(rows) == 93
    assert set(r["mint"] for r in rows) == set(mint_cohort)
    for r in rows:
        assert r["population"] == mint_cohort[r["mint"]]


def test_v2_evidence_population_counts_84_6_3():
    rows = []
    with open(EVIDENCE_V2_PATH) as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    from collections import Counter
    counts = Counter(r["population"] for r in rows)
    assert counts == {"STRICT": 84, "QVtW": 6, "ALTERNATE": 3}


def test_v2_evidence_no_addresses_in_fingerprint():
    forbidden = {"mint", "creator", "funder", "signature", "address"}
    with open(EVIDENCE_V2_PATH) as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            fp = r.get("normalized_fingerprint")
            if fp is not None:
                assert forbidden.isdisjoint(fp.keys())


def test_v2_evidence_strict_has_real_meta_fee_values():
    """Unlike v1 (all None), v2 must have real integers for STRICT rows that succeeded."""
    rows = []
    with open(EVIDENCE_V2_PATH) as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    strict = [r for r in rows if r["population"] == "STRICT"]
    with_fee = [r for r in strict if r["meta_fee_lamports"] is not None]
    assert len(with_fee) == 84
    for r in with_fee:
        assert isinstance(r["meta_fee_lamports"], int)
        assert r["meta_fee_lamports"] > 0


# ---------------- v2 fingerprint analysis correctness ----------------

def _load_v2():
    with open(FINGERPRINT_V2_PATH) as f:
        return json.load(f)


def test_v2_dominant_fingerprint_recurs_across_many_funders_and_creators():
    d = _load_v2()
    top = d["top_strict_fingerprints"][0]
    assert top["distinct_funders"] > 1
    assert top["distinct_creators"] > 1
    assert top["count"] >= 50  # strong majority recurrence


def test_v2_control_matching_computed_not_fabricated_unknown():
    d = _load_v2()
    oc = d["outside_controls"]
    assert oc["control_count"] > 0
    assert isinstance(oc["control_matching_dominant_strict_fingerprint"], int)
    assert isinstance(oc["control_matching_any_strict_fingerprint"], int)


def test_v2_qvtw_and_alternate_comparison_present():
    d = _load_v2()
    assert len(d["qvtw_comparison"]) == 6
    assert len(d["alternate_comparison"]) == 3


def test_v2_manual_example_uses_raw_lamports_not_explorer_text():
    d = _load_v2()
    ex = d["manual_example"]
    assert ex["signature"] == TARGET_SIG
    assert ex["meta_fee_lamports"] == 5050
    assert abs(ex["meta_fee_sol"] - 0.00000505) < 1e-12
    # confirm the ACTUAL DATA fields never equal the rejected explorer-rendered values
    assert ex["meta_fee_sol"] != 0.05505
    assert ex["meta_fee_sol"] != 0.075


def test_v2_manual_example_is_strict_population():
    d = _load_v2()
    assert d["manual_example"]["population"] == "STRICT"


def test_v2_no_detector_or_production_writes():
    d = _load_v2()
    v = d["production_write_verification"]
    for key in v:
        assert v[key] == 0


def test_v2_hard_ablation_present_and_offline_only():
    d = _load_v2()
    assert len(d["hard_detector_ablation"]) >= 1
    for entry in d["hard_detector_ablation"]:
        assert "strict_recall_after" in entry
        assert "qvtw_match_after" in entry
        assert "alternate_match_after" in entry


def test_artifacts_sha256_readable():
    for path in (FINGERPRINT_V2_PATH, EVIDENCE_V2_PATH, LEDGER_PATH, RAW_CACHE_PATH):
        with open(path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        assert len(digest) == 64


def test_no_network_calls_performed_by_this_test_module():
    with open(__file__) as f:
        src = f.read()
    markers = ["requests" + ".", "httpx" + ".", "urlopen" + "("]
    for m in markers:
        assert src.count(m) <= 1
