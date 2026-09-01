import json
import sqlite3

from src.ops import live_potential_activity as activity


def _fixture(tmp_path, monkeypatch):
    membership = tmp_path / "membership.json"
    snapshot = tmp_path / "snapshot.json"
    membership.write_text(json.dumps({"families": [
        {"candidate_id": "live", "mints": ["seed"]},
        {"candidate_id": "ambiguous", "mints": ["amb-a"]},
        {"candidate_id": "ambiguous-two", "mints": ["amb-b"]},
    ]}))
    snapshot.write_text(json.dumps([
        {"candidate_id": candidate, "activity": {"latest_matched_route": 900}}
        for candidate in ("live", "ambiguous", "ambiguous-two", "unavailable")
    ]))
    db = tmp_path / "ops.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE wt_walkback_queue(mint TEXT, funder_block_time INTEGER);
        CREATE TABLE wt_walkback_edge_candidates(mint TEXT, hop_depth INTEGER, mechanism TEXT, amount_lamports INTEGER, selection_status TEXT, signature TEXT);
    """)
    for mint, timestamp, amount in (("seed", 900, 1), ("current", 990, 1), ("unknown", 990, 2), ("missing", 990, None)):
        conn.execute("INSERT INTO wt_walkback_queue VALUES(?,?)", (mint, timestamp))
        if amount is not None:
            conn.execute("INSERT INTO wt_walkback_edge_candidates VALUES(?,?,?,?,?,?)", (mint, 1, "XFER", amount, "SELECTED", mint))
    # Two source families share this signature and must be SNAPSHOT_ONLY.
    for mint in ("amb-a", "amb-b"):
        conn.execute("INSERT INTO wt_walkback_edge_candidates VALUES(?,?,?,?,?,?)", (mint, 1, "XFER", 3, "SELECTED", mint))
    conn.commit(); conn.close()
    monkeypatch.setattr(activity, "MEMBERSHIP", membership)
    monkeypatch.setattr(activity, "SNAPSHOT", snapshot)
    return db


def test_live_activity_counts_only_unique_matches_and_never_writes(tmp_path, monkeypatch):
    db = _fixture(tmp_path, monkeypatch)
    values, stats = activity.aggregate(str(db), now=1000)
    assert values["live"]["activity_source"] == "LIVE_CURRENT"
    assert values["live"]["live_launches_24h"] == 2
    assert values["live"]["live_activity_state"] == "ACTIVE"
    assert values["ambiguous"]["activity_source"] == "SNAPSHOT_ONLY"
    assert values["unavailable"]["activity_source"] == "SNAPSHOT_ONLY"
    counts = stats["windows"]["24h"]
    assert counts["UNIQUE_MATCH"] == counts["sum_candidate_unique_assignments"] == 2
    assert counts["NO_MATCH"] == 1
    assert counts["INSUFFICIENT_INPUT"] == 1
    assert "MULTI_MATCH" not in counts
    assert sqlite3.connect(db).execute("SELECT count(*) FROM wt_walkback_queue").fetchone()[0] == 4


def test_live_activity_sorting_never_uses_snapshot_counts_as_current():
    from src.ops.potential_operations import _attention_sort_key
    base = {"candidate_id": "x", "priority_rank": 1, "creator_quality": {"creator_risk_class": "ROBUST_TO_MULTI_CREATOR_FILTER"}}
    live = {**base, "current_evidence": {"activity_source": "LIVE_CURRENT", "activity_state": "DORMANT", "metrics": {}, "matches": 0}}
    snapshot = {**base, "candidate_id": "y", "current_evidence": {"activity_source": "SNAPSHOT_ONLY", "activity_state": "SNAPSHOT_ONLY", "metrics": {"last_1d": 999}, "matches": 999}}
    assert _attention_sort_key(live) < _attention_sort_key(snapshot)
