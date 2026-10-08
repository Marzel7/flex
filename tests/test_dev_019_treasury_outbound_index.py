from __future__ import annotations

import tempfile
from pathlib import Path

from src.ops.treasury_outbound_index import (
    AMBIGUOUS_KNOWN_TREASURY_RECIPIENT, INVALID_LINEAGE,
    KNOWN_TREASURY_FUNDING_PATH_CONFIRMED, NO_KNOWN_TREASURY_RECIPIENT,
    LineageEdge, index_compact_outbound_fact, intersect_walkback_lineage,
    open_isolated_index, recipients, walkback_attribution_read_interface,
)


def _index(*, treasury="WT", recipient="A", amount=100, slot=10, signature="t1"):
    root = tempfile.TemporaryDirectory(dir="/private/tmp", prefix="dev019-outbound-")
    conn = open_isolated_index(str(Path(root.name) / "index.db"))
    ok = index_compact_outbound_fact(conn, fact={"sender": treasury, "receiver": recipient, "signature": signature, "slot": slot, "transaction_index": 1, "instruction_index": 1, "lamports": amount, "balance_delta_verified": True, "route_semantics": "DIRECT", "provenance": "fixture"}, confirmed_treasuries={"WT", "WT2"}, operation_associations={"WT": "WATCHTOWER"})
    assert ok
    return root, conn


def test_direct_and_multihop_watchtower_connection_is_review_only_and_idempotent():
    root, conn = _index()
    try:
        chain = [LineageEdge("A", "B", "a-b", 20, 1, 1, 90), LineageEdge("B", "CREATOR", "b-c", 30, 1, 1, 80), LineageEdge("CREATOR", "TOKEN", "c-t", 40, 1, 1, 1)]
        result = intersect_walkback_lineage(conn, lineage_id="token-x", edges=chain)
        assert result["status"] == KNOWN_TREASURY_FUNDING_PATH_CONFIRMED
        assert result["treasury"] == "WT" and result["operation_association"] == "WATCHTOWER"
        assert result["operation_assignment"] is None and result["canonical_writes"] is False
        assert intersect_walkback_lineage(conn, lineage_id="token-x", edges=chain)["status"] == KNOWN_TREASURY_FUNDING_PATH_CONFIRMED
        assert len(walkback_attribution_read_interface(conn, lineage_id="token-x")) == 1
    finally:
        root.cleanup()


def test_shared_recipient_never_selects_a_treasury_arbitrarily():
    root, conn = _index()
    try:
        index_compact_outbound_fact(conn, fact={"sender": "WT2", "receiver": "A", "signature": "t2", "slot": 11, "lamports": 100, "balance_delta_verified": True, "route_semantics": "DIRECT"}, confirmed_treasuries={"WT", "WT2"})
        result = intersect_walkback_lineage(conn, lineage_id="ambiguous", edges=[LineageEdge("A", "B", "a-b", 20, 1, 1, 90)])
        assert result["status"] == AMBIGUOUS_KNOWN_TREASURY_RECIPIENT
        assert result["candidate_treasuries"] == ["WT", "WT2"]
    finally:
        root.cleanup()


def test_invalid_chronology_generic_relay_and_unverified_evidence_fail_closed():
    root, conn = _index()
    try:
        assert intersect_walkback_lineage(conn, lineage_id="late", edges=[LineageEdge("A", "B", "late", 9, 1, 1, 1)])["status"] == NO_KNOWN_TREASURY_RECIPIENT
        assert intersect_walkback_lineage(conn, lineage_id="relay", edges=[LineageEdge("A", "B", "relay", 20, 1, 1, 90, route_semantics="GENERIC_RELAY")])["status"] == INVALID_LINEAGE
        assert intersect_walkback_lineage(conn, lineage_id="unverified", edges=[LineageEdge("A", "B", "bad", 20, 1, 1, 90, verified=False)])["status"] == INVALID_LINEAGE
        assert not index_compact_outbound_fact(conn, fact={"sender": "WT", "receiver": "DUST", "signature": "bad", "slot": 1, "lamports": 1, "balance_delta_verified": False, "route_semantics": "DIRECT"}, confirmed_treasuries={"WT"})
    finally:
        root.cleanup()


def test_unknown_funding_account_becomes_linked_without_membership_promotion():
    root, conn = _index(recipient="UNKNOWN_A")
    try:
        assert recipients(conn) == [{"recipient": "UNKNOWN_A", "treasury_count": 1, "transfer_count": 1}]
        result = intersect_walkback_lineage(conn, lineage_id="unknown", edges=[LineageEdge("UNKNOWN_A", "CREATOR", "u-c", 20, 1, 1, 99)])
        assert result["status"] == KNOWN_TREASURY_FUNDING_PATH_CONFIRMED
        assert result["operation_assignment"] is None
    finally:
        root.cleanup()
