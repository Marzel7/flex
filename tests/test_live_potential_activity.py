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


def test_batched_signature_index_matches_single_mint_semantics(tmp_path, monkeypatch):
    db = _fixture(tmp_path, monkeypatch)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO wt_walkback_edge_candidates VALUES(?,?,?,?,?,?)", ("multiple", 2, "XFER", 0, "SELECTED", "z"))
    conn.execute("INSERT INTO wt_walkback_edge_candidates VALUES(?,?,?,?,?,?)", ("multiple", 1, "WSOL", 7, "SELECTED", "a"))
    conn.execute("INSERT INTO wt_walkback_edge_candidates VALUES(?,?,?,?,?,?)", ("null-only", 1, "XFER", None, "SELECTED", "n"))
    conn.commit()
    cursor = conn.cursor()
    mints = {"seed", "multiple", "null-only", "absent"}
    batched = activity._signatures_by_mint(cursor, mints, chunk_size=2)
    assert batched == {mint: activity._signature(cursor, mint) for mint in mints}
    assert batched["multiple"] == ((1, "WSOL", 7), (2, "XFER", 0))
    assert batched["null-only"] is None
    assert batched["absent"] is None
    conn.close()


def test_signature_batching_is_bounded_and_aggregate_output_is_unchanged(tmp_path, monkeypatch):
    db = _fixture(tmp_path, monkeypatch)
    original = activity._signatures_by_mint
    values, stats = activity.aggregate(str(db), now=1000)

    calls = []
    def legacy_index(cursor, mints, *, chunk_size=900):
        mints = tuple(mints)
        calls.append(len(mints))
        return {mint: activity._signature(cursor, mint) for mint in mints}

    monkeypatch.setattr(activity, "_signatures_by_mint", legacy_index)
    legacy_values, legacy_stats = activity.aggregate(str(db), now=1000)
    assert (values, stats) == (legacy_values, legacy_stats)
    assert calls and calls[0] == 6

    conn = sqlite3.connect(db)
    query_count = 0
    # The helper accepts a 1,001-mint universe in two bounded SELECTs rather
    # than one SELECT per mint.
    class CountingCursor:
        def execute(self, sql, parameters=()):
            nonlocal query_count
            query_count += 1
            return conn.execute(sql, parameters)
    index = original(CountingCursor(), {f"mint-{n}" for n in range(1001)})
    assert len(index) == 1001
    assert query_count == 2
    conn.close()
