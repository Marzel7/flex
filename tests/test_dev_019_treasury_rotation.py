import sqlite3

from src.ops.treasury_rotation_discovery import *


def test_unknown_complete_watchtower_and_deep_are_candidates_not_membership():
    assert classify_route(operation_family="WATCHTOWER", full_fingerprint=True, terminal_root="new", route_complete=True, known_at_event_time=False, known_now=False) == NEW_TREASURY_CANDIDATE
    assert classify_route(operation_family="WATCHTOWER_DEEP", full_fingerprint=True, terminal_root="new", route_complete=True, known_at_event_time=False, known_now=False) == NEW_TREASURY_CANDIDATE


def test_amount_only_deep_is_insufficient():
    assert classify_route(operation_family="WATCHTOWER_DEEP", full_fingerprint=False, terminal_root="new", route_complete=True, known_at_event_time=False, known_now=False) == INSUFFICIENT_EVIDENCE


def test_known_at_event_and_hindsight_are_distinct():
    assert classify_route(operation_family="WATCHTOWER", full_fingerprint=True, terminal_root="root", route_complete=True, known_at_event_time=True, known_now=True) == KNOWN_OPERATION_TREASURY
    assert classify_route(operation_family="WATCHTOWER", full_fingerprint=True, terminal_root="root", route_complete=True, known_at_event_time=False, known_now=True) == NEW_TREASURY_CANDIDATE


def test_contradiction_requires_positive_reason():
    assert classify_route(operation_family="WATCHTOWER", full_fingerprint=True, terminal_root="root", route_complete=True, known_at_event_time=False, known_now=False, contradictory_reason="EXCLUDED_INFRASTRUCTURE") == CONTRADICTORY_TREASURY


def test_causal_selector_rejects_future_and_same_slot_unordered_edges():
    child={"wallet":"child","slot":10,"transaction_index":1,"instruction_index":5}
    future={"funded_wallet":"child","slot":11,"transaction_index":1,"instruction_index":1,"route_semantics":"DIRECT"}
    unordered={"funded_wallet":"child","slot":10,"route_semantics":"DIRECT"}
    assert select_causal_parent(child,[future,unordered])[0] is None


def test_causal_selector_is_semantic_deterministic_not_oldest_row():
    child={"wallet":"child","slot":10,"transaction_index":1,"instruction_index":5}
    old={"funded_wallet":"other","slot":1,"transaction_index":1,"instruction_index":1,"route_semantics":"DIRECT","semantic_rank":99}
    valid={"funded_wallet":"child","slot":9,"transaction_index":1,"instruction_index":1,"route_semantics":"DIRECT","semantic_rank":1}
    selected, reason, _ = select_causal_parent(child,[old,valid])
    assert selected is valid and reason == "SEMANTIC_CAUSAL_PREDECESSOR"


def test_candidate_store_is_idempotent_and_separate_from_canonical_registry():
    conn=sqlite3.connect(":memory:")
    route={"event_slot":9,"event_time":8,"terminal_hop":"root","edges":["a"]}
    first=record_candidate(conn,operation_family="WATCHTOWER",treasury="root",launch_mint="m1",fingerprint_version="v1",route=route,known_at_event_time=False,known_now=False,now=1)
    second=record_candidate(conn,operation_family="WATCHTOWER",treasury="root",launch_mint="m1",fingerprint_version="v1",route=route,known_at_event_time=False,known_now=False,now=2)
    assert first == second
    assert conn.execute("SELECT count(*) FROM wt_treasury_rotation_candidates").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM wt_treasury_rotation_candidate_evidence").fetchone()[0] == 1
    assert conn.execute("SELECT status FROM wt_treasury_rotation_candidates").fetchone()[0] == "PENDING_REVIEW"


def test_same_slot_requires_instruction_order_and_ambiguous_top_rank_fails_closed():
    child={"wallet":"child","slot":10,"transaction_index":2,"instruction_index":5}
    unordered={"funded_wallet":"child","slot":10,"route_semantics":"DIRECT","semantic_rank":2}
    assert select_causal_parent(child,[unordered])[1] == "NO_CAUSAL_PARENT"
    left={"funded_wallet":"child","slot":9,"transaction_index":1,"instruction_index":2,"route_semantics":"DIRECT","semantic_rank":2}
    right={"funded_wallet":"child","slot":9,"transaction_index":1,"instruction_index":2,"route_semantics":"DIRECT","semantic_rank":2}
    assert select_causal_parent(child,[left,right])[1] == "AMBIGUOUS_CAUSAL_PARENT"


def test_candidate_accumulates_multiple_launches_and_later_contradiction_is_append_only():
    conn=sqlite3.connect(":memory:")
    first={"event_slot":9,"event_time":8,"terminal_hop":"root","edges":["a"]}
    second={"event_slot":11,"event_time":10,"terminal_hop":"root","edges":["b"]}
    for mint, route in (("m1",first),("m2",second)):
        result=evaluate_discovery(operation_family="WATCHTOWER",fingerprint_version="v1",full_fingerprint=True,terminal_root="root",route_complete=True,known_at_event_time=False,known_now=False,launch_mint=mint,route=route,candidate_conn=conn,now=1)
        assert result["classification"] == NEW_TREASURY_CANDIDATE
    assert conn.execute("SELECT count(*) FROM wt_treasury_rotation_candidates").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM wt_treasury_rotation_candidate_evidence").fetchone()[0] == 2
    result=evaluate_discovery(operation_family="WATCHTOWER",fingerprint_version="v1",full_fingerprint=True,terminal_root="root",route_complete=True,known_at_event_time=False,known_now=False,launch_mint="m3",route=second,contradictory_reason="INDEPENDENT_OPERATION",candidate_conn=conn,now=2)
    assert result["classification"] == CONTRADICTORY_TREASURY
    assert conn.execute("SELECT count(*) FROM wt_treasury_rotation_candidate_evidence").fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM wt_treasury_rotation_contradictions").fetchone()[0] == 1


def test_discovery_never_writes_canonical_treasury_or_operation_membership():
    conn=sqlite3.connect(":memory:")
    evaluate_discovery(operation_family="WATCHTOWER_DEEP",fingerprint_version="deep-v1",full_fingerprint=True,terminal_root="new-root",route_complete=True,known_at_event_time=False,known_now=False,launch_mint="deep",route={"event_slot":1,"event_time":1},candidate_conn=conn,now=1)
    tables={row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "wt_confirmed_treasuries" not in tables
    assert "operator_launch_membership" not in tables


def test_selected_edge_chain_requires_retained_slot_and_order_without_age_threshold():
    child={"wallet":"child","slot":10,"transaction_index":1,"instruction_index":5,"signature":"child-sig"}
    no_slot={"wallet":"child","candidate_parent":"old-root","block_time":1,"instruction_index":-1,"signature":"old-sig"}
    result=qualify_selected_edge_chain(child=child,selected_edges=[no_slot])
    assert result["route_complete"] is False
    assert result["reason"] == "NO_CAUSAL_PARENT"
    parent={"wallet":"child","candidate_parent":"root","slot":9,"transaction_index":1,"instruction_index":2,"signature":"parent-sig"}
    qualified=qualify_selected_edge_chain(child=child,selected_edges=[parent])
    assert qualified["route_complete"] is True
    assert qualified["terminal_root"] == "root"


def test_same_slot_uses_transaction_order_then_same_transaction_instruction_order():
    child={"wallet":"child","slot":10,"transaction_index":3,"instruction_index":1}
    parent={"funded_wallet":"child","slot":10,"transaction_index":2,"instruction_index":99,"route_semantics":"DIRECT"}
    assert causal_order(child,parent) == (True,"EARLIER_TRANSACTION_SAME_SLOT")
    same_tx={"funded_wallet":"child","slot":10,"transaction_index":3,"instruction_index":0,"route_semantics":"DIRECT"}
    assert causal_order(child,same_tx) == (True,"EARLIER_INSTRUCTION_SAME_TRANSACTION")


def test_same_slot_and_same_transaction_missing_order_fail_closed():
    child={"wallet":"child","slot":10,"transaction_index":3,"instruction_index":1}
    missing_tx={"funded_wallet":"child","slot":10,"instruction_index":0,"route_semantics":"DIRECT"}
    assert causal_order(child,missing_tx) == (False,"SAME_SLOT_TRANSACTION_ORDER_UNAVAILABLE")
    missing_ix={"funded_wallet":"child","slot":10,"transaction_index":3,"route_semantics":"DIRECT"}
    assert causal_order(child,missing_ix) == (False,"SAME_TRANSACTION_INSTRUCTION_ORDER_UNAVAILABLE")


def test_old_causally_valid_parent_is_accepted_without_time_threshold():
    child={"wallet":"child","slot":999,"transaction_index":0,"instruction_index":0,"block_time":999999}
    parent={"funded_wallet":"child","slot":1,"transaction_index":0,"instruction_index":0,"block_time":1,"route_semantics":"DIRECT"}
    assert causal_order(child,parent) == (True,"EARLIER_SLOT")
    assert select_causal_parent(child,[parent])[0] is parent


def test_rotation_discovery_fixtures_do_not_change_classic_watchtower_logic():
    route={"event_slot":5,"terminal_hop":"unseen"}
    for operation in ("WATCHTOWER","WATCHTOWER_DEEP"):
        assert evaluate_discovery(operation_family=operation,fingerprint_version="fixture",full_fingerprint=True,terminal_root="unseen",route_complete=True,known_at_event_time=False,known_now=False,launch_mint="fixture-mint",route=route)["classification"] == NEW_TREASURY_CANDIDATE
    assert evaluate_discovery(operation_family="WATCHTOWER_DEEP",fingerprint_version="fixture",full_fingerprint=False,terminal_root="unseen",route_complete=False,known_at_event_time=False,known_now=False,launch_mint="amount-only",route=route)["classification"] == INSUFFICIENT_EVIDENCE


def test_compact_causal_order_evidence_is_append_only_and_not_canonical_state():
    conn=sqlite3.connect(":memory:")
    parent={"signature":"parent","slot":10,"transaction_index":1,"instruction_index":2}
    child={"signature":"child","slot":11,"transaction_index":0,"instruction_index":1}
    record_causal_order_evidence(conn,operation_family="WATCHTOWER",launch_mint="mint",parent=parent,child=child,evidence_source="retained-envelope",parser_version="v1",qualification_state="QUALIFIED",now=1)
    record_causal_order_evidence(conn,operation_family="WATCHTOWER",launch_mint="mint",parent=parent,child=child,evidence_source="retained-envelope",parser_version="v1",qualification_state="QUALIFIED",now=2)
    assert conn.execute("SELECT count(*) FROM wt_treasury_rotation_causal_order_evidence").fetchone()[0] == 1
    assert {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} >= {"wt_treasury_rotation_causal_order_evidence"}
    assert conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('wt_confirmed_treasuries','operator_launch_membership')").fetchone()[0] == 0
