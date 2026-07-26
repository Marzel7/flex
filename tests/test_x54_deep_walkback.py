from __future__ import annotations

import sqlite3

from src.core.deep_walkback import (
    claim_with_lease, ensure_schema, materialize_atomic_wsol, materialize_candidate,
    persist_atomic_flows, persist_edge_candidate, valid_signature,
)


def db():
    conn=sqlite3.connect(":memory:"); conn.row_factory=sqlite3.Row
    conn.execute("""CREATE TABLE wt_walkback_queue (
      mint TEXT PRIMARY KEY,status TEXT,attempts INTEGER DEFAULT 0,started_at INTEGER,
      updated_at INTEGER,last_error TEXT)""")
    ensure_schema(conn); return conn


def atomic_tx(owner="OWNER", destination="DEST", authority="OWNER"):
    return {"blockTime":100,"transaction":{"message":{"accountKeys":["SOURCE",destination],"instructions":[
      {"parsed":{"type":"createAccount","info":{"source":"SOURCE","newAccount":"TMP"}}},
      {"parsed":{"type":"initializeAccount3","info":{"account":"TMP","owner":owner}}},
      {"parsed":{"type":"transfer","info":{"source":"SOURCE","destination":"TMP","lamports":1000}}},
      {"parsed":{"type":"syncNative","info":{"account":"TMP"}}},
      {"parsed":{"type":"closeAccount","info":{"account":"TMP","authority":authority,"destination":destination}}},
    ]}},"meta":{"preBalances":[2000,0],"postBalances":[900,1000]}}


def test_atomic_wsol_owner_differs_from_destination_and_tmp_is_not_hop():
    flows=materialize_atomic_wsol(atomic_tx(),"SIG")
    assert len(flows)==1
    flow=flows[0]
    assert flow.owner=="OWNER" and flow.close_destination=="DEST"
    assert flow.temporary_account=="TMP" and flow.authority=="OWNER"
    assert flow.net_destination_lamports==1000


def test_atomic_wsol_third_party_authority_and_multiple_accounts():
    tx=atomic_tx(authority="THIRD")
    tx["transaction"]["message"]["instructions"] += [
      {"parsed":{"type":"initializeAccount3","info":{"account":"TMP2","owner":"OWNER2"}}},
      {"parsed":{"type":"transfer","info":{"source":"SOURCE","destination":"TMP2","lamports":500}}},
      {"parsed":{"type":"syncNative","info":{"account":"TMP2"}}},
      {"parsed":{"type":"closeAccount","info":{"account":"TMP2","authority":"AUTH2","destination":"DEST"}}},
    ]
    flows=materialize_atomic_wsol(tx,"SIG")
    assert {(f.temporary_account,f.authority) for f in flows}=={("TMP","THIRD"),("TMP2","AUTH2")}


def test_atomic_persistence_is_idempotent():
    conn=db(); flows=materialize_atomic_wsol(atomic_tx(),"SIG")
    persist_atomic_flows(conn,"MINT",flows); persist_atomic_flows(conn,"MINT",flows); conn.commit()
    row=conn.execute("select * from wt_walkback_atomic_flows").fetchone()
    assert conn.execute("select count(*) from wt_walkback_atomic_flows").fetchone()[0]==1
    assert row["observation_count"]==2


def test_concurrent_claim_has_one_winner_and_expired_lease_can_retry():
    conn=db(); conn.execute("insert into wt_walkback_queue(mint,status) values('M','pending')");conn.commit()
    assert claim_with_lease(conn,"M","worker-a",300)
    assert not claim_with_lease(conn,"M","worker-b",300)
    conn.execute("update wt_walkback_queue set lease_expires_at=0 where mint='M'");conn.commit()
    assert claim_with_lease(conn,"M","worker-b",300)
    assert conn.execute("select claimed_by from wt_walkback_queue").fetchone()[0]=="worker-b"


def test_edge_retry_does_not_duplicate_score_components_or_promote():
    conn=db()
    for mint,child in (("M1","S1"),("M2","S2")):
        args=dict(mint=mint,wallet=child,parent="TREASURY",signature="SIG"+mint,
                  block_time=100,amount_lamports=200_000_000_000,mechanism="PLAIN_XFER",
                  anchor_signature="ANCHOR",anchor_block_time=110,hop_depth=2,
                  selection_status="SELECTED")
        persist_edge_candidate(conn,**args);persist_edge_candidate(conn,**args)
    result=materialize_candidate(conn,"TREASURY")
    assert result["confidence"]=="HIGH_REVIEW"
    assert result["distinct_launches"]==2
    assert conn.execute("select count(*) from wt_walkback_edge_candidates").fetchone()[0]==2
    assert conn.execute("select review_state from wt_infrastructure_candidates").fetchone()[0]=="PENDING_REVIEW"


def test_service_negative_control_and_signature_validation():
    conn=db()
    persist_edge_candidate(conn,mint="M",wallet="S",parent="SERVICE",signature="SIG",block_time=1,
        amount_lamports=500_000_000_000,mechanism="PLAIN_XFER",anchor_signature="A",
        anchor_block_time=2,hop_depth=2,selection_status="SELECTED")
    result=materialize_candidate(conn,"SERVICE",service_or_exchange=True)
    assert result["confidence"]=="REJECTED_SERVICE"
    assert result["candidate_role"]=="SERVICE_OR_EXCHANGE"
    assert valid_signature("1"*88) and not valid_signature("short") and not valid_signature(None)
