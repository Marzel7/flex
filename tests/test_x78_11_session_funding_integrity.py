"""X78.11 forward funding-edge contract controls."""

import sqlite3

from src.core.ws_cascade import _explicit_native_funding_transfers
from src.core import ws_cascade_store as store


WSOL = "So11111111111111111111111111111111111111112"


def _tx(keys, instructions, *, inner=None, pre=None, post=None, token_balances=None):
    return {
        "transaction": {"message": {"accountKeys": keys, "instructions": instructions}},
        "meta": {
            "innerInstructions": [{"index": 0, "instructions": inner or []}],
            "preBalances": pre or [0] * len(keys),
            "postBalances": post or [0] * len(keys),
            "preTokenBalances": token_balances or [],
            "postTokenBalances": [],
        },
        "blockTime": 1_780_000_000,
    }


def _system_transfer(source, destination, lamports=1_000_000_000):
    return {
        "program": "system",
        "programId": "11111111111111111111111111111111",
        "parsed": {"type": "transfer", "info": {
            "source": source, "destination": destination, "lamports": lamports,
        }},
    }


def _close(account, owner, destination):
    return {
        "program": "spl-token",
        "programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
        "parsed": {"type": "closeAccount", "info": {
            "account": account, "owner": owner, "destination": destination,
        }},
    }


def test_explicit_system_transfer_creates_directional_edge():
    tx = _tx(["A", "B"], [_system_transfer("A", "B", 2_500_000_000)])
    assert _explicit_native_funding_transfers(tx, "A") == [{
        "source": "A", "destination": "B", "lamports": 2_500_000_000,
        "funding_mechanism": "PLAIN_TRANSFER",
    }]


def test_inner_explicit_transfer_remains_detectable_for_multihop_funding():
    tx = _tx(["A", "B"], [], inner=[_system_transfer("A", "B")])
    assert _explicit_native_funding_transfers(tx, "A")[0]["destination"] == "B"


def test_positive_delta_and_cooccurrence_do_not_create_edge():
    tx = _tx(["ACTIVE", "GAINER"], [], pre=[10_000, 0], post=[9_000, 1_000])
    assert _explicit_native_funding_transfers(tx, "ACTIVE") == []


def test_directional_edge_requires_transaction_timestamp():
    tx = _tx(["A", "B"], [_system_transfer("A", "B")])
    tx["blockTime"] = None
    assert _explicit_native_funding_transfers(tx, "A") == []


def test_self_owned_wsol_close_does_not_create_inherited_edge():
    tx = _tx(
        ["ACTIVE", "TRADER", "TRADER_WSOL"],
        [_close("TRADER_WSOL", "TRADER", "TRADER")],
        pre=[10_000, 0, 2_000], post=[9_000, 2_000, 0],
        token_balances=[{"accountIndex": 2, "mint": WSOL, "owner": "TRADER"}],
    )
    assert _explicit_native_funding_transfers(tx, "ACTIVE") == []
    assert _explicit_native_funding_transfers(tx, "TRADER") == []


def test_controlled_wsol_close_to_distinct_recipient_is_directional():
    tx = _tx(
        ["FUNDER", "RECIPIENT", "FUNDER_WSOL"],
        [_close("FUNDER_WSOL", "FUNDER", "RECIPIENT")],
        pre=[10_000, 0, 2_000], post=[9_000, 2_000, 0],
        token_balances=[{"accountIndex": 2, "mint": WSOL, "owner": "FUNDER"}],
    )
    assert _explicit_native_funding_transfers(tx, "FUNDER") == [{
        "source": "FUNDER", "destination": "RECIPIENT", "lamports": 2_000,
        "funding_mechanism": "WSOL_WRAP_CLOSE",
    }]


def test_reverse_transfer_does_not_create_forward_edge():
    tx = _tx(["ACTIVE", "TRADER"], [_system_transfer("TRADER", "ACTIVE")])
    assert _explicit_native_funding_transfers(tx, "ACTIVE") == []
    assert _explicit_native_funding_transfers(tx, "TRADER")[0]["destination"] == "ACTIVE"


def test_cosigning_without_transfer_does_not_create_edge():
    keys = [
        {"pubkey": "ACTIVE", "signer": True},
        {"pubkey": "TRADER", "signer": True},
    ]
    tx = _tx(keys, [], pre=[10_000, 0], post=[9_000, 1_000])
    assert _explicit_native_funding_transfers(tx, "ACTIVE") == []


def test_verified_69sn_multihop_shape_requires_each_own_edge():
    path = ["69SN", "9St6", "8CEy", "Bvv4", "5tzF"]
    for source, destination in zip(path, path[1:]):
        tx = _tx([source, destination], [_system_transfer(source, destination)])
        edges = _explicit_native_funding_transfers(tx, source)
        assert [(e["source"], e["destination"]) for e in edges] == [
            (source, destination)
        ]


def test_pending_session_replay_fails_closed_without_transaction_verification():
    conn = sqlite3.connect(":memory:")
    store.ensure_cascade_schema(conn)
    store.enqueue_pending_session(
        conn, treasury="ROOT", subprov="CHILD", funding_sig="SIG",
        funding_amount=50.0, funding_time=1_780_000_000,
        open_reason="TEST", subprov_known=0, ttl_seconds=120,
    )

    assert store.drain_pending_sessions(conn) == (0, 0, 0)
    assert conn.execute(
        "SELECT state, failure_reason FROM wt_pending_session_writes"
    ).fetchone() == ("FAILED", "UNVERIFIED_DIRECTIONAL_EDGE")
    assert conn.execute(
        "SELECT COUNT(*) FROM wt_active_subprov_sessions"
    ).fetchone()[0] == 0
