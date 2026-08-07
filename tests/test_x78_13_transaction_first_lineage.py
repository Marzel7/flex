import sqlite3

from src.ops.transaction_first_lineage import (
    LaunchFact,
    ensure_schema,
    extract_context,
    extract_directional_edges,
    graph_digest,
    longest_chronological_path,
    stable_id,
    verify_launch,
)


def _tx(keys, instructions, block_time=100, token_balances=None):
    return {
        "blockTime": block_time,
        "transaction": {"message": {"accountKeys": keys, "instructions": instructions}},
        "meta": {"innerInstructions": [], "preTokenBalances": token_balances or [],
                 "postTokenBalances": []},
    }


def _transfer(source, destination, lamports=1_000_000_000):
    return {"program": "system", "parsed": {"type": "transfer", "info": {
        "source": source, "destination": destination, "lamports": lamports,
    }}}


def test_explicit_transfer_is_tier_one_edge():
    tx = _tx([{"pubkey": "A", "signer": True}, {"pubkey": "B", "signer": False}],
             [_transfer("A", "B")])
    assert extract_directional_edges(tx) == [{
        "sender": "A", "recipient": "B", "amount": "1000000000", "asset": "SOL",
        "relationship_type": "DIRECT_SOL_TRANSFER", "mechanism": "PLAIN_TRANSFER",
        "source_program": "11111111111111111111111111111111",
    }]


def test_wsol_self_close_is_context_not_lineage():
    tx = _tx(
        [{"pubkey": "OWNER", "signer": True}, {"pubkey": "WSOL", "signer": False}],
        [{"program": "spl-token", "parsed": {"type": "closeAccount", "info": {
            "account": "WSOL", "owner": "OWNER", "destination": "OWNER",
        }}}],
        token_balances=[{"accountIndex": 1,
                         "mint": "So11111111111111111111111111111111111111112",
                         "owner": "OWNER"}],
    )
    assert extract_directional_edges(tx) == []
    assert extract_context(tx)[0]["context_type"] == "TRANSACTION_CO_OCCURRENCE"


def test_launch_requires_creator_signature_and_mint_account():
    fact = LaunchFact("MINT", "CREATOR", "SIG", 200, "pumpfun")
    tx = _tx([{"pubkey": "CREATOR", "signer": True},
              {"pubkey": "MINT", "signer": False}], [], block_time=200)
    assert verify_launch(fact, tx)[0] == "VERIFIED_LAUNCH"
    wrong = _tx([{"pubkey": "OTHER", "signer": True},
                 {"pubkey": "MINT", "signer": False}], [], block_time=200)
    assert verify_launch(fact, wrong)[0] == "UNVERIFIABLE_LAUNCH"


def test_chronology_rejects_parent_after_child():
    def edge(source, destination, timestamp):
        return {"sender": source, "recipient": destination, "block_time": timestamp,
                "edge_id": stable_id(source, destination, timestamp)}

    edges = [edge("ROOT", "SUB", 90), edge("SUB", "CREATOR", 80)]
    path = longest_chronological_path(edges, "CREATOR", 100)
    assert [(e["sender"], e["recipient"]) for e in path] == [("SUB", "CREATOR")]


def _graph_connection():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    conn.execute("""INSERT INTO tf_launches
        (mint,creator,creation_signature,creation_time,source_platform,launch_status,
         verification_reason,has_persisted_walkback,acquisition_state)
        VALUES ('M','C','CS',30,'pumpfun','VERIFIED_LAUNCH','verified',1,'DONE')""")
    conn.execute("""INSERT INTO tf_edges
        (edge_id,sender,recipient,signature,block_time,amount,asset,relationship_type,
         mechanism,source_program,hop_depth,launch_context,creator_context,evidence_source,
         observed_or_inherited,rpc_verified)
        VALUES ('E','R','C','S',20,'1','SOL','DIRECT_SOL_TRANSFER','PLAIN_TRANSFER',
                'system',1,'M','C','TEST','OBSERVED',1)""")
    conn.execute("""INSERT INTO tf_paths
        (mint,creator,root,subprovider,edge_count,max_depth,path_status,edge_ids_json,
         termination_reason,chronology_valid,reconstructed_at)
        VALUES ('M','C','R','R',1,1,'PARTIALLY_REDISCOVERED','["E"]','end',1,1)""")
    conn.commit()
    return conn


def test_operator_identity_changes_cannot_feed_back_into_graph():
    conn = _graph_connection()
    before = graph_digest(conn)
    conn.execute("CREATE TABLE operator_identity_state(operator_id TEXT, identity_status TEXT)")
    conn.execute("INSERT INTO operator_identity_state VALUES ('WATCHTOWER','CONFIRMED')")
    conn.execute("UPDATE operator_identity_state SET identity_status='RETIRED'")
    conn.commit()
    assert graph_digest(conn) == before


def test_session_root_changes_cannot_feed_back_into_graph():
    conn = _graph_connection()
    before = graph_digest(conn)
    conn.execute("CREATE TABLE wt_active_subprov_sessions(id INTEGER, treasury_wallet TEXT)")
    conn.execute("INSERT INTO wt_active_subprov_sessions VALUES (1,'69SN')")
    conn.execute("UPDATE wt_active_subprov_sessions SET treasury_wallet='DIFFERENT_ROOT'")
    conn.commit()
    assert graph_digest(conn) == before


def test_schema_and_edges_are_idempotent():
    conn = _graph_connection()
    before = graph_digest(conn)
    ensure_schema(conn)
    conn.execute("""INSERT OR IGNORE INTO tf_edges
        (edge_id,sender,recipient,signature,block_time,amount,asset,relationship_type,
         mechanism,source_program,hop_depth,launch_context,creator_context,evidence_source,
         observed_or_inherited,rpc_verified)
        SELECT edge_id,sender,recipient,signature,block_time,amount,asset,relationship_type,
         mechanism,source_program,hop_depth,launch_context,creator_context,evidence_source,
         observed_or_inherited,rpc_verified FROM tf_edges WHERE edge_id='E'""")
    conn.commit()
    assert graph_digest(conn) == before
    assert conn.execute("SELECT count(*) FROM tf_edges").fetchone()[0] == 1
