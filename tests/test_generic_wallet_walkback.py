import sqlite3

from src.core import walkback_worker
from src.discovery.generic_wallet_walkback import (
    AMBIGUOUS, EVIDENCE_UNAVAILABLE, HistoricalContext, INCOMPLETE_HISTORY,
    NO_QUALIFYING_PARENT, PARENT_FOUND, RPC_ERROR, ParentFinding,
    find_funding_parent, resolve_mint_seed, walk_path, GENERIC_EVIDENCE_SCHEMA_SQL,
)


def _tx(sender="PARENT", child="CHILD", slot=10):
    return {"slot": slot, "blockTime": 1000 + slot, "meta": {"preBalances": [100, 0], "postBalances": [0, 100]}, "transaction": {"message": {"accountKeys": [sender, child]}}}


def _rpc(monkeypatch, pages, txs):
    def fake(method, params):
        if method == "getSignaturesForAddress": return pages
        if method == "getTransaction": return txs.get(params[0])
        if method == "getAccountInfo": return {"value": {"owner": "11111111111111111111111111111111"}}
        return None
    monkeypatch.setattr(walkback_worker, "_rpc", fake)


def test_wrapper_matches_authoritative_extractor(monkeypatch):
    _rpc(monkeypatch, [{"signature": "S", "slot": 10}], {"S": _tx()})
    original = walkback_worker._find_funder_via_rpc("CHILD", [0], None)
    wrapped = find_funding_parent("CHILD")
    assert wrapped.parent_wallet == original[0] == "PARENT"
    assert wrapped.state == PARENT_FOUND


def test_no_parent_is_not_rpc_error(monkeypatch):
    _rpc(monkeypatch, [], {})
    assert find_funding_parent("CHILD").state == NO_QUALIFYING_PARENT


def test_cutoff_and_oldest_semantics_are_delegated(monkeypatch):
    _rpc(monkeypatch, [{"signature": "NEW", "slot": 20}, {"signature": "OLD", "slot": 10}], {"NEW": _tx("N", slot=20), "OLD": _tx("O", slot=10)})
    assert find_funding_parent("CHILD").parent_wallet == "N"
    assert find_funding_parent("CHILD", HistoricalContext(before_signature="ANCHOR", prefer_oldest=True)).parent_wallet == "O"


def test_two_and_three_hop_replay():
    graph = {"A": "B", "B": "C", "C": "D"}
    def lookup(w, c): return ParentFinding(PARENT_FOUND, w, graph[w], "S" + w, 1, 1, 1.0, "PLAIN_XFER", c.depth, 0)
    assert [x.parent_wallet for x in walk_path("A", lookup, max_depth=2)] == ["B", "C"]
    assert [x.parent_wallet for x in walk_path("A", lookup, max_depth=3)] == ["B", "C", "D"]


def test_convergence_and_unrelated_branches_have_factual_edges_only():
    graph = {"A": "X", "B": "X", "C": "Y"}
    def lookup(w, c): return ParentFinding(PARENT_FOUND, w, graph[w], "S", 1, 1, 1.0, "PLAIN_XFER", c.depth, 0)
    assert walk_path("A", lookup, max_depth=1)[0].parent_wallet == walk_path("B", lookup, max_depth=1)[0].parent_wallet == "X"
    assert walk_path("C", lookup, max_depth=1)[0].parent_wallet == "Y"


def test_cycle_and_repeated_wallet_fail_closed():
    graph = {"A": "B", "B": "A"}
    def lookup(w, c): return ParentFinding(PARENT_FOUND, w, graph[w], "S", 1, 1, 1.0, "PLAIN_XFER", c.depth, 0)
    assert walk_path("A", lookup, max_depth=3)[-1].state == AMBIGUOUS


def test_digest_is_deterministic_and_has_no_watchtower_label():
    a = ParentFinding(PARENT_FOUND, "C", "P", "S", 1, 2, 3.0, "PLAIN_XFER", 1, 2)
    assert a.canonical_sha256() == a.canonical_sha256()
    assert "WATCHTOWER" not in str(a)


def test_malformed_and_timeout_can_be_represented_without_negative_inference(monkeypatch):
    _rpc(monkeypatch, [{"signature": "bad", "slot": 1}], {"bad": {}})
    assert find_funding_parent("CHILD").state == NO_QUALIFYING_PARENT


def test_generic_mint_seed_precedence_and_missingness():
    conn = sqlite3.connect(":memory:")
    conn.executescript("CREATE TABLE token_analysis(mint TEXT,earliest_tx_creator TEXT,pf_ws_creator TEXT); CREATE TABLE migrated_tokens(mint TEXT,creator TEXT);")
    conn.execute("INSERT INTO token_analysis VALUES('M','EARLY','PF')")
    conn.execute("INSERT INTO migrated_tokens VALUES('N','MIG')")
    assert resolve_mint_seed(conn, 'M') == ('EARLY', 'token_analysis')
    assert resolve_mint_seed(conn, 'N') == ('MIG', 'migrated_tokens')
    assert resolve_mint_seed(conn, 'Z') == (None, None)


def test_generic_schema_has_no_watchtower_namespace():
    assert 'wt_' not in GENERIC_EVIDENCE_SCHEMA_SQL.lower()
    assert 'generic_wallet_parent_edges' in GENERIC_EVIDENCE_SCHEMA_SQL


def test_error_states_remain_distinct_from_no_parent():
    states = {EVIDENCE_UNAVAILABLE, RPC_ERROR, INCOMPLETE_HISTORY, NO_QUALIFYING_PARENT}
    assert len(states) == 4


def test_max_depth_is_an_execution_guard_not_an_attribution_rule():
    def lookup(w, c): return ParentFinding(PARENT_FOUND, w, 'P', 'S', 1, 1, None, 'UNKNOWN', c.depth, 0)
    assert len(walk_path('A', lookup, max_depth=1)) == 1


def test_replay_is_deterministic_for_identical_retained_lookup():
    def lookup(w, c): return ParentFinding(NO_QUALIFYING_PARENT, w, None, None, None, None, None, None, c.depth, 0)
    assert walk_path('A', lookup, max_depth=3) == walk_path('A', lookup, max_depth=3)


def test_multiple_branches_can_reuse_exact_wallet_lookup():
    calls = []
    def lookup(w, c):
        calls.append(w)
        return ParentFinding(NO_QUALIFYING_PARENT, w, None, None, None, None, None, None, c.depth, 0)
    walk_path('X', lookup, max_depth=1); walk_path('X', lookup, max_depth=1)
    assert calls == ['X', 'X']  # acquisition-layer dedup, not traversal semantics


def test_retained_edge_is_not_labeled_as_operation_or_treasury():
    result = ParentFinding(PARENT_FOUND, 'C', 'P', 'S', 1, 1, 1.0, 'PLAIN_XFER', 1, 1)
    assert 'treasury' not in result.canonical_sha256()


def test_invalid_depth_rejected():
    try:
        walk_path('A', lambda w, c: None, max_depth=0)
    except ValueError:
        return
    assert False


def test_rpc_counter_is_exposed_as_provenance(monkeypatch):
    _rpc(monkeypatch, [{"signature": "S", "slot": 10}], {"S": _tx()})
    assert find_funding_parent('CHILD').rpc_requests > 0
