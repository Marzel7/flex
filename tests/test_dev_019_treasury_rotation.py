import json
import sqlite3
from pathlib import Path

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


def test_signature_window_requires_all_bounded_page_signatures_before_negative_result():
    result = signature_window_coverage(page_signatures=["a", "b", "c"], decoded_signatures=["a", "c"])
    assert result["status"] == COVERAGE_INCOMPLETE
    assert result["missing_signatures"] == ["b"]
    complete = signature_window_coverage(page_signatures=["a", "b"], decoded_signatures=["a", "b"])
    assert complete["status"] == COVERAGE_COMPLETE


def test_unknown_mesh_wallet_is_partial_not_a_confirmed_treasury():
    assert classify_mesh_role(wallet="unknown", confirmed_treasuries={"treasury"}, known_subproviders={"sub"}, funding_accounts={"funding"}) == PARTIAL_LINEAGE
    assert classify_mesh_role(wallet="sub", confirmed_treasuries={"treasury"}, known_subproviders={"sub"}, funding_accounts=set()) == KNOWN_SUBPROVIDER_MATCH


def test_complete_verified_mesh_route_requires_direct_ordered_edges_and_verified_launch():
    treasury = {"wallet": "T", "slot": 1, "transaction_index": 0, "instruction_index": 0, "signature": "t"}
    edges = [
        {"sender": "T", "receiver": "S", "slot": 2, "transaction_index": 0, "instruction_index": 0, "signature": "ts", "balance_delta_verified": True},
        {"sender": "S", "receiver": "F", "slot": 3, "transaction_index": 0, "instruction_index": 0, "signature": "sf", "balance_delta_verified": True},
        {"sender": "F", "receiver": "C", "slot": 4, "transaction_index": 0, "instruction_index": 0, "signature": "fc", "balance_delta_verified": True},
    ]
    launch = {"creator": "C", "slot": 5, "transaction_index": 0, "instruction_index": 0, "signature": "launch", "status": "VERIFIED_CREATOR_LAUNCH"}
    result = qualify_mesh_route(treasury=treasury, transfers=edges, creator_launch=launch)
    assert result["route_complete"] is True
    assert result["classification"] == CONFIRMED_TREASURY_MATCH


def test_contextual_creator_mint_colocation_never_completes_mesh_route():
    treasury = {"wallet": "T", "slot": 1, "transaction_index": 0, "instruction_index": 0, "signature": "t"}
    edge = {"sender": "T", "receiver": "C", "slot": 2, "transaction_index": 0, "instruction_index": 0, "signature": "tc", "balance_delta_verified": True}
    result = qualify_mesh_route(treasury=treasury, transfers=[edge], creator_launch={"creator": "C", "slot": 3, "signature": "co", "status": "CONTEXTUAL_CREATOR_AND_MINT_COLOCATION"})
    assert result["route_complete"] is False
    assert result["reason"] == "CREATOR_LAUNCH_CONTEXTUAL_ONLY"


def test_cycle_or_reversed_edge_cannot_complete_mesh_route():
    treasury = {"wallet": "T", "slot": 10, "transaction_index": 0, "instruction_index": 0, "signature": "t"}
    reversed_edge = {"sender": "T", "receiver": "C", "slot": 9, "transaction_index": 0, "instruction_index": 0, "signature": "old", "balance_delta_verified": True}
    result = qualify_mesh_route(treasury=treasury, transfers=[reversed_edge], creator_launch=None)
    assert result["route_complete"] is False
    assert result["reason"] == "PARENT_AFTER_CHILD_SLOT"


def test_compact_decoder_records_outer_and_inner_system_transfers_with_coordinates():
    tx = {
        "slot": 10, "transactionIndex": 4,
        "transaction": {"message": {"accountKeys": ["sender", "receiver", "other"], "instructions": [
            {"program": "system", "parsed": {"type": "transfer", "info": {"source": "sender", "destination": "receiver", "lamports": 10}}}
        ]}},
        "meta": {"preBalances": [100, 0, 50], "postBalances": [80, 15, 55], "innerInstructions": [
            {"index": 0, "instructions": [{"program": "system", "parsed": {"type": "transfer", "info": {"source": "sender", "destination": "receiver", "lamports": 5}}}]}
        ]},
    }
    facts = extract_compact_native_facts(tx, signature="sig")
    assert [(x["instruction_index"], x["inner_instruction_index"], x["kind"]) for x in facts] == [(0, None, "SYSTEM_TRANSFER"), (0, 0, "SYSTEM_TRANSFER")]
    assert all(x["balance_delta_verified"] for x in facts)


def test_wsol_close_is_context_not_direct_funding_even_when_balances_move():
    tx = {
        "slot": 12, "transactionIndex": 2,
        "transaction": {"message": {"accountKeys": ["wsol_account", "creator"], "instructions": [
            {"program": "spl-token", "parsed": {"type": "closeAccount", "info": {"account": "wsol_account", "destination": "creator"}}}
        ]}},
        "meta": {"preBalances": [1112039, 0], "postBalances": [0, 1112039]},
    }
    fact = extract_compact_native_facts(tx, signature="close")[0]
    assert fact["kind"] == "WRAPPED_SOL_ACCOUNT_CLOSE_CONTEXT"
    assert fact["route_semantics"] == "ACCOUNT_CLOSE"
    assert fact["balance_delta_verified"] is True


def test_net_balance_without_transfer_instruction_creates_no_mesh_fact():
    tx = {"slot": 1, "transaction": {"message": {"accountKeys": ["a", "b"], "instructions": []}}, "meta": {"preBalances": [10, 0], "postBalances": [0, 10]}}
    assert extract_compact_native_facts(tx, signature="net-only") == []


def test_transaction_request_contract_supports_legacy_v0_and_v1_reads():
    assert transaction_request_config()["maxSupportedTransactionVersion"] == 1
    assert transaction_version_status({"version": "legacy"}) == "SUPPORTED"
    assert transaction_version_status({"version": 0}) == "SUPPORTED"
    assert transaction_version_status({"version": 1}) == "SUPPORTED"


def test_unsupported_version_fails_closed_without_creating_transfer_facts():
    tx = {
        "version": 2,
        "transaction": {"message": {"accountKeys": ["a", "b"], "instructions": [
            {"program": "system", "parsed": {"type": "transfer", "info": {"source": "a", "destination": "b", "lamports": 1}}}
        ]}},
        "meta": {"preBalances": [1, 0], "postBalances": [0, 1]},
    }
    assert transaction_version_status(tx) == "UNSUPPORTED_TRANSACTION_VERSION"
    assert extract_compact_native_facts(tx, signature="unsupported") == []


def test_page_boundary_is_available_even_when_first_decode_would_fail():
    boundary = signature_page_boundary(page_number=1, before_cursor=None, signatures=["sig-a", "sig-b"])
    assert boundary == {"page": 1, "before_cursor": None, "returned_signature_count": 2, "first_signature": "sig-a", "last_signature": "sig-b"}
    coverage = signature_window_coverage(page_signatures=["sig-a", "sig-b"], decoded_signatures=[])
    assert coverage["status"] == COVERAGE_INCOMPLETE
    assert coverage["missing_signature_count"] == 2


def test_amq_8cub_temporal_counterexample_preserves_edges_but_rejects_combined_route():
    fixture = json.loads((Path(__file__).parent / "fixtures" / "dev019_amq_8cub_temporal_counterexample.v1.json").read_text())
    later_funding = fixture["amq_to_8cub"]
    earlier_economic_event = fixture["creator_to_2bvf"]
    # Individual verified direct transfers remain usable compact facts, but
    # their actual slot order forbids treating the later funding as cause.
    assert later_funding["balance_delta_verified"] is True
    assert earlier_economic_event["balance_delta_verified"] is True
    assert causal_order(earlier_economic_event, later_funding) == (False, "PARENT_AFTER_CHILD_SLOT")
    assert fixture["8cub_to_creator"]["status"] == "NOT_ESTABLISHED_COMPLETE_COVERAGE"
    route = qualify_mesh_route(
        treasury={"wallet": "treasury", "slot": 1, "transaction_index": 0, "instruction_index": 0, "signature": "t"},
        transfers=[{"sender": "treasury", "receiver": "8CUbQw5zjzR1hvExdRQLcS6MpdCHprp6ohwPBYfoWTHM", "slot": 2, "transaction_index": 0, "instruction_index": 0, "signature": "f", "balance_delta_verified": True}],
        creator_launch={"creator": earlier_economic_event["sender"], "slot": earlier_economic_event["slot"], "transaction_index": earlier_economic_event["transaction_index"], "instruction_index": earlier_economic_event["instruction_index"], "signature": earlier_economic_event["signature"], "status": "CONTEXTUAL_CREATOR_AND_MINT_COLOCATION"},
    )
    assert route["route_complete"] is False
    assert route["reason"] == "CREATOR_LAUNCH_LINK_UNAVAILABLE"
    assert fixture["wsol_context"]["status"] == "ACCOUNT_CLOSE_CONTEXT_NOT_DIRECT_FUNDING"
    assert earlier_economic_event["canonical_creator_launch"] is False


def test_incomplete_exact_signature_coverage_cannot_establish_8cub_creator_absence():
    coverage = signature_window_coverage(page_signatures=["tffs", "missing"], decoded_signatures=["tffs"])
    assert coverage["status"] == COVERAGE_INCOMPLETE
    assert coverage["missing_signature_count"] == 1
