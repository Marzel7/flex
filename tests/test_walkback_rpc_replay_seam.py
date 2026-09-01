"""Offline worker-path proof for the retained transaction-role projection."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from unittest.mock import patch

import pytest

from src.core import deep_walkback
import src.core.walkback_worker as ww


WALLET = "Creator1111111111111111111111111111111111111"
SENDER = "Funder11111111111111111111111111111111111111"
MINT = "Mint111111111111111111111111111111111111111"
SIG = "A" * 88
ANCHOR = "B" * 88


class LocalRpc:
    """Bounded fixture: exact method/params matching, never network-backed."""
    def __init__(self, responses):
        self.responses = responses
        self.calls = Counter()

    def __call__(self, method, params):
        key = (method, repr(params))
        if key not in self.responses:
            raise AssertionError(f"unexpected local RPC request: {method} {params!r}")
        self.calls[method] += 1
        return self.responses[key]


def _tx(*, instructions=None):
    return {
        "slot": 123, "blockTime": 1700000000,
        "transaction": {"message": {
            "accountKeys": [
                {"pubkey": SENDER, "signer": True},
                {"pubkey": WALLET, "signer": False},
            ],
            "instructions": instructions if instructions is not None else [{
                "programId": "11111111111111111111111111111111",
                "parsed": {"type": "transfer", "info": {
                    "source": SENDER, "destination": WALLET, "lamports": 10000,
                }},
            }],
        }},
        "meta": {"preBalances": [50000, 0], "postBalances": [40000, 10000],
                 "innerInstructions": [], "logMessages": []},
    }


def _transport(tx_result=None, *, before=None):
    sig_params = [WALLET, {"limit": ww.SIG_PAGE_LIMIT, "commitment": "confirmed"}]
    if before:
        sig_params[1]["before"] = before
    tx_params = [SIG, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0,
                       "commitment": "confirmed"}]
    owner_params = [SENDER, {"encoding": "base64", "commitment": "confirmed"}]
    return LocalRpc({
        ("getSignaturesForAddress", repr(sig_params)): [{"signature": SIG, "slot": 123, "err": None}],
        ("getTransaction", repr(tx_params)): _tx() if tx_result is None else tx_result,
        ("getAccountInfo", repr(owner_params)): {"value": {"owner": ww._SYSTEM_PROGRAM}},
    })


def _ops(path=":memory:"):
    conn = sqlite3.connect(path)
    deep_walkback.ensure_schema(conn)
    conn.execute("CREATE TABLE wt_confirmed_treasuries (treasury TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE wt_discovered_subprovs (subprov TEXT PRIMARY KEY, state TEXT)")
    conn.execute("CREATE TABLE wt_known_spam_wallets (wallet TEXT PRIMARY KEY)")
    conn.commit()
    return conn


def test_default_path_uses_existing_rpc_dependency_with_unchanged_requests():
    transport = _transport()
    with patch.object(ww, "_rpc", side_effect=transport) as real_rpc:
        result = ww._find_with_evidence(WALLET, [0], None, source_mint=MINT,
                                        hop_depth=1, signature_page_count=1)
    assert result[0:2] == (SENDER, SIG)
    assert real_rpc.call_count == 3
    assert transport.calls == {"getSignaturesForAddress": 1, "getTransaction": 1,
                               "getAccountInfo": 1}


def test_injected_transport_executes_real_worker_selection_and_commits_retained_roles(tmp_path):
    path = tmp_path / "walkback-proof.db"
    transport, conn = _transport(before=ANCHOR), _ops(path)
    with patch.object(ww, "_rpc", side_effect=AssertionError("real RPC fallback")):
        result = ww._find_with_evidence(WALLET, [0], conn, source_mint=MINT,
                                        hop_depth=1, before_signature=ANCHOR,
                                        signature_page_count=1, rpc_transport=transport)
    assert result[0:2] == (SENDER, SIG)
    # Fresh reader proves the worker's own commit made both retained records durable.
    conn.close()
    reader = sqlite3.connect(path)
    row = reader.execute("SELECT signature, transfer_source, transfer_destination, "
                       "transfer_lamports, fee_payer, route_semantics FROM wt_walkback_transaction_roles").fetchone()
    edge = reader.execute("SELECT signature, selection_status FROM wt_walkback_edge_candidates").fetchone()
    assert row == (SIG, SENDER, WALLET, 10000, SENDER, "DIRECT")
    assert edge == (SIG, "SELECTED")
    assert transport.calls == {"getSignaturesForAddress": 1, "getTransaction": 1,
                               "getAccountInfo": 1}

    # This is a retained-only reconstruction: no worker, RPC, or network call.
    signer_rows = json.loads(reader.execute("SELECT signers_json FROM wt_walkback_transaction_roles").fetchone()[0])
    from src.ops.direct_10k_creator_provisioning import detect_direct_10k_creator_provisioning
    assert detect_direct_10k_creator_provisioning({
        "mint": MINT, "creator": WALLET, "direct_funder": SENDER,
        "defining_signature": SIG, "transfer_source": row[1],
        "transfer_destination": row[2], "transfer_amount_lamports": row[3],
        "fee_payer": row[4], "signers": signer_rows, "launch_coupled": True,
        "intermediary_route": row[5] == "INTERMEDIARY",
        "ambiguous_transfer": row[5] == "AMBIGUOUS_OR_UNSUPPORTED",
    })["result"] == "UNIQUE_MATCH"
    reader.close()


def test_injected_transport_fails_fast_without_real_rpc_fallback():
    transport = LocalRpc({})
    with patch.object(ww, "_rpc", side_effect=AssertionError("real RPC fallback")):
        with pytest.raises(AssertionError, match="unexpected local RPC request"):
            ww._find_with_evidence(WALLET, [0], None, source_mint=MINT, hop_depth=1,
                                   signature_page_count=1, rpc_transport=transport)


@pytest.mark.parametrize(("instructions", "route"), [
    (None, "DIRECT"),
    ([{"programId": "11111111111111111111111111111111", "parsed": {"type": "transfer", "info": {"source": SENDER, "destination": WALLET, "lamports": 1}}},
      {"programId": "11111111111111111111111111111111", "parsed": {"type": "transfer", "info": {"source": WALLET, "destination": "Other", "lamports": 1}}}], "INTERMEDIARY"),
    ([], "AMBIGUOUS_OR_UNSUPPORTED"),
])
def test_retained_role_route_semantics_are_deterministic(instructions, route):
    conn = _ops()
    deep_walkback.persist_decoded_transaction_roles(conn, mint=MINT, signature=SIG,
                                                    anchor_signature=None, tx=_tx(instructions=instructions))
    assert conn.execute("SELECT route_semantics FROM wt_walkback_transaction_roles").fetchone()[0] == route


def test_role_projection_conflict_fails_closed():
    conn = _ops()
    deep_walkback.persist_decoded_transaction_roles(conn, mint=MINT, signature=SIG,
                                                    anchor_signature=None, tx=_tx())
    changed = _tx()
    changed["transaction"]["message"]["accountKeys"][0]["signer"] = False
    with pytest.raises(ValueError, match="conflicting authoritative"):
        deep_walkback.persist_decoded_transaction_roles(conn, mint=MINT, signature=SIG,
                                                        anchor_signature=None, tx=changed)
