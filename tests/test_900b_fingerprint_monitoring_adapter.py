import sqlite3
from importlib import import_module

from src.ops.provisional_operations import FROZEN_900B_RECURRENT_FUNDERS

adapter = import_module("src.ops.900b_fingerprint_monitoring_adapter")


def _db(*, hop=1, mechanism="WSOL_WRAP_CLOSE", amount=999_985_000, funder="novel", selected=True):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE wt_walkback_edge_candidates (mint TEXT,selection_status TEXT,hop_depth INTEGER,mechanism TEXT,amount_lamports INTEGER,candidate_parent TEXT,last_observed_at INTEGER)")
    conn.execute("CREATE TABLE provisional_operation_matches (detector_version TEXT)")
    if selected:
        conn.execute("INSERT INTO wt_walkback_edge_candidates VALUES ('m','SELECTED',?,?,?,?,1)", (hop, mechanism, amount, funder))
    return conn


def test_exact_behaviour_is_exact_with_known_or_novel_funder():
    known = _db(funder=next(iter(FROZEN_900B_RECURRENT_FUNDERS)))
    novel = _db(funder="a-completely-novel-direct-funder")
    assert adapter.observe_900b_behavioural_fingerprint(known, "m")["classification"] == "EXACT_MATCH"
    assert adapter.observe_900b_behavioural_fingerprint(novel, "m")["classification"] == "EXACT_MATCH"
    assert adapter.observe_900b_behavioural_fingerprint(known, "m")["infrastructure_observation"] != adapter.observe_900b_behavioural_fingerprint(novel, "m")["infrastructure_observation"]


def test_known_funder_cannot_make_wrong_behaviour_exact():
    conn = _db(amount=1, funder=next(iter(FROZEN_900B_RECURRENT_FUNDERS)))
    assert adapter.observe_900b_behavioural_fingerprint(conn, "m")["classification"] == "NEAR_MATCH_ONE_DIMENSION"


def test_semantic_anchor_makes_generic_wsol_unrelated():
    assert adapter.observe_900b_behavioural_fingerprint(_db(mechanism="WSOL_GENERIC"), "m")["classification"] == "NO_MEANINGFUL_RELATIONSHIP"


def test_two_behavioural_differences_are_explainable_multi_near_match():
    result = adapter.observe_900b_behavioural_fingerprint(_db(hop=2, amount=1), "m")
    assert result["classification"] == "NEAR_MATCH_MULTI_DIMENSION"
    assert result["differing_dimensions"] == ["selected_hop", "selected_amount"]


def test_missing_retained_selected_evidence_is_unobservable():
    assert adapter.observe_900b_behavioural_fingerprint(_db(selected=False), "m")["classification"] == "UNOBSERVABLE"


def test_manifest_preserves_frozen_provisional_contract_and_baseline():
    manifest = adapter.source_manifest(_db())
    assert manifest["qualification"] == "PROVISIONAL"
    assert manifest["positive_reference_count"] == 44
    assert manifest["comparison_count"] == 15
    assert manifest["baseline_uniqueness"]["value_percent"] == 74.58
    assert all(not field["literal_address_required"] for field in manifest["behavioural_inputs"])


def test_adapter_has_no_projector_or_writer_paths():
    source = open(adapter.__file__).read()
    assert "project_900b_completed_walkback" not in source
    assert "record_provisional_match" not in source
    assert "INSERT INTO" not in source and "UPDATE " not in source and "DELETE " not in source
    assert "operator_launch_membership" not in source
    assert "trading" not in source.lower()


def test_observation_does_not_mutate_retained_queue_evidence():
    conn = _db()
    before = conn.execute("SELECT COUNT(*) FROM wt_walkback_edge_candidates").fetchone()[0]
    adapter.observe_900b_behavioural_fingerprint(conn, "m")
    assert conn.execute("SELECT COUNT(*) FROM wt_walkback_edge_candidates").fetchone()[0] == before
