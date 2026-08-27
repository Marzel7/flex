import sqlite3

from src.ops import p3r_fingerprint_monitoring_adapter as adapter


def _db(selected=("WSOL_WRAP_CLOSE", 99_999_985_000), atomic=99_997_955_720):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE wt_walkback_edge_candidates (mint TEXT, selection_status TEXT, hop_depth INTEGER, mechanism TEXT, amount_lamports INTEGER, signature TEXT)")
    conn.execute("CREATE TABLE wt_walkback_atomic_flows (mint TEXT, has_create INTEGER, has_sync_native INTEGER, has_close INTEGER, transfer_lamports INTEGER)")
    if selected: conn.execute("INSERT INTO wt_walkback_edge_candidates VALUES ('m','SELECTED',1,?,?, 's')", selected)
    if atomic is not None: conn.execute("INSERT INTO wt_walkback_atomic_flows VALUES ('m',1,1,1,?)", (atomic,))
    return conn


def test_positive_uses_authoritative_matcher(monkeypatch):
    conn = _db()
    monkeypatch.setattr(adapter, "evaluate_mint", lambda *_: type("M", (), {"matching_profiles": ("P3R",)})())
    assert adapter.observe_p3r_fingerprint(conn, "m")["classification"] == "EXACT_MATCH"


def test_selected_amount_and_atomic_amount_are_explainable_near_misses(monkeypatch):
    monkeypatch.setattr(adapter, "evaluate_mint", lambda *_: None)
    assert adapter.observe_p3r_fingerprint(_db(("WSOL_WRAP_CLOSE", 1)), "m")["classification"] == "NEAR_MATCH_ONE_DIMENSION"
    assert adapter.observe_p3r_fingerprint(_db(atomic=1), "m")["classification"] == "NEAR_MATCH_ONE_DIMENSION"


def test_generic_or_missing_evidence_is_not_a_match(monkeypatch):
    monkeypatch.setattr(adapter, "evaluate_mint", lambda *_: None)
    assert adapter.observe_p3r_fingerprint(_db(("PLAIN_XFER", 1)), "m")["classification"] == "NO_MEANINGFUL_RELATIONSHIP"
    assert adapter.observe_p3r_fingerprint(_db(atomic=None), "m")["classification"] == "UNOBSERVABLE"


def test_adapter_has_no_membership_writer():
    source = open(adapter.__file__).read()
    assert "operator_launch_membership" not in source or "INSERT INTO operator_launch_membership" not in source
    assert "admit_unambiguous_p3r_match" not in source
