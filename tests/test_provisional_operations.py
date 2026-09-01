import sqlite3

from src.ops.provisional_operations import (
    FROZEN_900B_RECURRENT_FUNDERS, PROVISIONAL_900B_DETECTOR_VERSION,
    PROVISIONAL_900B_OPERATOR_ID, classify_900b, ensure_schema,
    project_900b_completed_walkback,
)

def test_900b_pending_and_unknown_states_are_never_confirmed():
    edge={"selection_status":"SELECTED","hop_depth":1,"mechanism":"WSOL_WRAP_CLOSE","amount_lamports":999985000,"candidate_parent":"r"}
    assert classify_900b(edge,{"r"}) == "PROVISIONAL_MATCH_PENDING"
    assert classify_900b({**edge,"candidate_parent":"new"},{"r"}) == "BEHAVIOURAL_MATCH_UNKNOWN_INFRASTRUCTURE"
    assert classify_900b({**edge,"amount_lamports":1},{"r"}) is None


def _db(edge):
    conn=sqlite3.connect(":memory:")
    ensure_schema(conn)
    conn.execute("CREATE TABLE wt_walkback_edge_candidates(mint TEXT,candidate_parent TEXT,signature TEXT,anchor_signature TEXT,block_time INTEGER,anchor_block_time INTEGER,hop_depth INTEGER,mechanism TEXT,amount_lamports INTEGER,evidence_key TEXT,selection_status TEXT,last_observed_at INTEGER)")
    conn.execute("CREATE TABLE operator_launch_membership(mint TEXT,operator_id TEXT)")
    conn.execute("INSERT INTO wt_walkback_edge_candidates VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", edge)
    return conn


def test_900b_completed_walkback_projection_is_idempotent_and_preserves_reviews():
    funder=next(iter(FROZEN_900B_RECURRENT_FUNDERS))
    edge=("mint",funder,"sig","anchor",10,11,1,"WSOL_WRAP_CLOSE",999985000,"key","SELECTED",12)
    conn=_db(edge)
    assert project_900b_completed_walkback(conn,"mint") == "PROVISIONAL_MATCH_PENDING"
    assert project_900b_completed_walkback(conn,"mint") == "already_recorded"
    assert conn.execute("SELECT COUNT(*) FROM provisional_operation_matches").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM operator_launch_membership").fetchone()[0] == 0
    conn.execute("UPDATE provisional_operation_matches SET state='PROVISIONAL_MATCH_CONFIRMED' WHERE operator_id=? AND mint=? AND detector_version=?",(PROVISIONAL_900B_OPERATOR_ID,"mint",PROVISIONAL_900B_DETECTOR_VERSION))
    assert project_900b_completed_walkback(conn,"mint") == "preserved_terminal"
    conn.execute("UPDATE provisional_operation_matches SET state='PROVISIONAL_MATCH_REJECTED' WHERE operator_id=? AND mint=? AND detector_version=?",(PROVISIONAL_900B_OPERATOR_ID,"mint",PROVISIONAL_900B_DETECTOR_VERSION))
    assert project_900b_completed_walkback(conn,"mint") == "preserved_terminal"


def test_900b_completed_walkback_unknown_and_nonmatching_edges():
    unknown=_db(("unknown","new","sig",None,10,None,1,"WSOL_WRAP_CLOSE",999985000,"key","SELECTED",12))
    assert project_900b_completed_walkback(unknown,"unknown") == "BEHAVIOURAL_MATCH_UNKNOWN_INFRASTRUCTURE"
    wrong=_db(("wrong",next(iter(FROZEN_900B_RECURRENT_FUNDERS)),"sig",None,10,None,1,"WSOL_WRAP_CLOSE",1,"key","SELECTED",12))
    assert project_900b_completed_walkback(wrong,"wrong") == "not_900b"
    semantic=_db(("semantic",next(iter(FROZEN_900B_RECURRENT_FUNDERS)),"sig",None,10,None,1,"PLAIN_XFER",999985000,"key","SELECTED",12))
    assert project_900b_completed_walkback(semantic,"semantic") == "not_900b"


def test_900b_mapping_style_row_is_supported():
    funder=next(iter(FROZEN_900B_RECURRENT_FUNDERS))
    conn=_db(("mapping",funder,"sig",None,10,None,1,"WSOL_WRAP_CLOSE",999985000,"key","SELECTED",12))
    conn.row_factory=sqlite3.Row
    assert project_900b_completed_walkback(conn,"mapping") == "PROVISIONAL_MATCH_PENDING"


def test_900b_malformed_selected_edge_is_safe_noop():
    conn=sqlite3.connect(":memory:")
    ensure_schema(conn)
    conn.execute("CREATE TABLE wt_walkback_edge_candidates(mint TEXT,selection_status TEXT,last_observed_at INTEGER)")
    conn.execute("INSERT INTO wt_walkback_edge_candidates VALUES('mint','SELECTED',1)")
    assert project_900b_completed_walkback(conn,"mint") == "unobservable_selected_edge_schema"
