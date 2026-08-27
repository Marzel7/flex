import sqlite3

from src.ops import p3r_13a04_fingerprint_monitoring_adapter as adapter
from src.ops.d3de_operation import SELECTED_ROUTE, is_d0_match


def _db(route=adapter.ROUTE):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE wt_walkback_edge_candidates (mint TEXT,selection_status TEXT,hop_depth INTEGER,mechanism TEXT,amount_lamports INTEGER,candidate_parent TEXT,wallet TEXT)")
    for hop, mechanism, amount in route:
        conn.execute("INSERT INTO wt_walkback_edge_candidates VALUES ('m','SELECTED',?,?,?,?,?)", (hop, mechanism, amount, "novel-parent", "novel-creator"))
    return conn


def _d3de_evidence(route):
    return [{"hop_depth": hop, "mechanism": mechanism, "amount_lamports": amount} for hop, mechanism, amount in route]


def test_exact_route_is_address_independent_and_not_d3de():
    first = adapter.observe_p3r_13a04_fingerprint(_db(), "m")
    second = adapter.observe_p3r_13a04_fingerprint(_db(), "m")
    assert first["classification"] == second["classification"] == "EXACT_MATCH"
    assert not is_d0_match(_d3de_evidence(adapter.ROUTE))


def test_d3de_route_is_not_p3r_13a04():
    assert is_d0_match(_d3de_evidence(SELECTED_ROUTE))
    result = adapter.observe_p3r_13a04_fingerprint(_db(SELECTED_ROUTE), "m")
    assert result["classification"] == "NEAR_MATCH_MULTI_DIMENSION"
    assert result["classification"] != "EXACT_MATCH"


def test_one_amount_or_semantic_mutation_is_explainable_near_match():
    wrong_amount = list(adapter.ROUTE); wrong_amount[2] = (3, "PLAIN_XFER", 1)
    wrong_semantic = list(adapter.ROUTE); wrong_semantic[1] = (2, "PLAIN_XFER", 29_999_980_000)
    assert adapter.observe_p3r_13a04_fingerprint(_db(wrong_amount), "m")["classification"] == "NEAR_MATCH_ONE_DIMENSION"
    assert adapter.observe_p3r_13a04_fingerprint(_db(wrong_semantic), "m")["classification"] == "NEAR_MATCH_ONE_DIMENSION"


def test_multi_mutation_is_near_only_with_material_continuity():
    route = list(adapter.ROUTE); route[1] = (2, "WSOL_WRAP_CLOSE", 1); route[2] = (3, "PLAIN_XFER", 2)
    assert adapter.observe_p3r_13a04_fingerprint(_db(route), "m")["classification"] == "NEAR_MATCH_MULTI_DIMENSION"
    generic = tuple((hop, "WSOL_GENERIC", 30_000_000_000) for hop in range(1, 5))
    assert adapter.observe_p3r_13a04_fingerprint(_db(generic), "m")["classification"] == "NO_MEANINGFUL_RELATIONSHIP"


def test_partial_or_missing_route_is_unobservable():
    assert adapter.observe_p3r_13a04_fingerprint(_db(adapter.ROUTE[:3]), "m")["classification"] == "UNOBSERVABLE"
    assert adapter.observe_p3r_13a04_fingerprint(sqlite3.connect(":memory:"), "m")["classification"] == "UNOBSERVABLE"


def test_manifest_preserves_forward_only_unmeasured_baseline():
    manifest = adapter.source_manifest()
    assert manifest["current_membership_count"] == 0
    assert manifest["historical_positive_reference_source"] == "UNRECOVERED"
    assert manifest["baseline_uniqueness_capability"] == "NOT_YET_MEASURED"
    assert manifest["forward_monitoring"] == "ENABLED_BY_ADAPTER"
    assert all(not hop["literal_address_required"] for hop in manifest["exact_behavioural_contract"])


def test_adapter_cannot_project_or_write_or_reach_trading():
    source = open(adapter.__file__).read().lower()
    assert "admit_unambiguous" not in source and "project" not in source
    assert "insert into" not in source and "update " not in source and "delete " not in source
    assert "operator_launch_membership" not in source and "trading" not in source
