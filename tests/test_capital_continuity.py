import sqlite3

from src.ops.capital_continuity import assess_capital_continuity
from src.ops.watchtower_deep_historical import COORDINATOR, POOL, WINDOW_START


def _db(conflict=False, anchor=True):
    c=sqlite3.connect(':memory:'); c.executescript('''
    CREATE TABLE wt_walkback_queue(mint TEXT PRIMARY KEY,creator TEXT,subprov TEXT,status TEXT,intelligence_outcome TEXT,funding_mechanism TEXT,funder_block_time INTEGER);
    CREATE TABLE operator_launch_membership(mint TEXT PRIMARY KEY,operator_id TEXT);
    CREATE TABLE wt_watchtower_launches(mint TEXT);
    CREATE TABLE wt_walkback_edge_candidates(mint TEXT,hop_depth INTEGER,wallet TEXT,candidate_parent TEXT,signature TEXT,block_time INTEGER,amount_lamports INTEGER,mechanism TEXT,evidence_strength TEXT,selection_status TEXT);
    CREATE TABLE wt_walkback_transaction_roles(signature TEXT,transfer_source TEXT,transfer_destination TEXT,transfer_lamports INTEGER);
    ''')
    for mint in (['anchor'] if anchor else [])+['candidate']:
      c.execute('INSERT INTO wt_walkback_queue VALUES(?,?,?,?,?,?,?)',(mint,'creator'+mint,'sub'+mint,'complete','LINEAGE_GAP','WSOL_WRAP_CLOSE',WINDOW_START+500))
      c.executemany('INSERT INTO wt_walkback_edge_candidates VALUES(?,?,?,?,?,?,?,?,?,?)',[(mint,1,'creator'+mint,'sub'+mint,'one'+mint,WINDOW_START+400,1112039000,'WSOL_WRAP_CLOSE','TRANSACTION_DERIVED','SELECTED'),(mint,2,'sub'+mint,'distribution','two'+mint,WINDOW_START+300,200000000000,'PLAIN_XFER','TRANSACTION_DERIVED','SELECTED')])
    c.executemany('INSERT INTO wt_walkback_edge_candidates VALUES(?,?,?,?,?,?,?,?,?,?)',[('x',3,'distribution',COORDINATOR,'upper',WINDOW_START+200,200000000000,'PLAIN_XFER','TRANSACTION_DERIVED','ALTERNATIVE'),('x',4,COORDINATOR,POOL,'pool',WINDOW_START+100,2000000000000,'PLAIN_XFER','TRANSACTION_DERIVED','ALTERNATIVE')])
    c.executemany('INSERT INTO wt_walkback_transaction_roles VALUES(?,?,?,?)',[('upper',COORDINATOR,'distribution',200000000000),('pool',POOL,COORDINATOR,2000000000000)])
    if anchor: c.execute("INSERT INTO operator_launch_membership VALUES('anchor','op')")
    if conflict: c.execute("INSERT INTO wt_walkback_edge_candidates VALUES(?,?,?,?,?,?,?,?,?,?)",('candidate',3,'distribution','other','bad',WINDOW_START+200,1,'PLAIN_XFER','TRANSACTION_DERIVED','SELECTED'))
    return c


def test_qualifies_only_matching_canonical_causal_anchor():
    assert assess_capital_continuity(_db(), 'candidate', 'op')['state']=='QUALIFIED_PROSPECTIVE_MEMBER'


def test_unknown_anchor_is_insufficient_not_conflict():
    assert assess_capital_continuity(_db(anchor=False), 'candidate', 'op')['state']=='INSUFFICIENT_EVIDENCE'


def test_selected_upstream_conflict_cannot_be_weakened():
    assert assess_capital_continuity(_db(conflict=True), 'candidate', 'op')['state']=='CONFLICT'

