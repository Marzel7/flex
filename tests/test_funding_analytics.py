import sqlite3

from src.ops.funding_analytics import FundingAnalytics


def _main():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
      CREATE TABLE creator_funders (creator_address TEXT, funder_address TEXT, amount_sol REAL,
        first_detected_at, is_cex INTEGER, cex_exchange TEXT, source_type TEXT);
      CREATE TABLE token_analysis (mint TEXT, earliest_tx_creator TEXT, created_at INTEGER);
      CREATE TABLE cex_wallets (cex_address TEXT, exchange_name TEXT, is_active INTEGER);
      CREATE TABLE funding_chain_legacy_first_leg_evidence (evidence_id TEXT PRIMARY KEY,
        source_creator TEXT, bridge_funder TEXT, amount_sol REAL, block_time INTEGER,
        confidence REAL, chain_type TEXT, provenance TEXT);
      CREATE TABLE funding_chain_legacy_target_association (evidence_id TEXT, target_creator TEXT,
        bridge_to_target_amount_sol REAL);
    """)
    c.executemany("INSERT INTO creator_funders VALUES (?,?,?,?,?,?,?)", [
      ('creator-a','funder-a',2.0,100,0,None,'direct'),
      ('creator-b','funder-a',3.0,200,0,None,'direct'),
      ('creator-a','cex-a',1.0,300,1,'ExampleX','direct'),
    ])
    c.executemany("INSERT INTO token_analysis VALUES (?,?,?)", [('m1','creator-a',100),('m2','creator-a',200),('m3','creator-b',300)])
    c.execute("INSERT INTO cex_wallets VALUES ('cex-a','ExampleX',1)")
    c.execute("INSERT INTO funding_chain_legacy_first_leg_evidence VALUES ('e1','creator-a','funder-a',0.1,90,55,'CREATOR_TO_FUNDER_TO_CREATOR','LEGACY')")
    c.execute("INSERT INTO funding_chain_legacy_target_association VALUES ('e1','creator-b',3.0)")
    return c


def _ops():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
      CREATE TABLE wt_walkback_queue (mint TEXT, creator TEXT, funder_wallet TEXT, funder_amount_sol REAL,
        funder_sig TEXT, funder_slot INTEGER, funder_block_time INTEGER, funding_mechanism TEXT, status TEXT, attribution_source TEXT);
      CREATE TABLE wt_walkback_edge_candidates (mint TEXT, candidate_parent TEXT, wallet TEXT, signature TEXT,
        block_time INTEGER, amount_lamports INTEGER, mechanism TEXT, hop_depth INTEGER, selection_status TEXT,
        evidence_strength TEXT, instruction_index INTEGER, inner_instruction_index INTEGER, evidence_key TEXT);
      CREATE TABLE wt_provisioning_sessions (source_mint TEXT, treasury TEXT, subprov TEXT, creator TEXT,
        treasury_to_subprov_block_time INTEGER, subprov_to_creator_block_time INTEGER,
        treasury_to_subprov_amount_sol REAL, subprov_to_creator_amount_sol REAL,
        treasury_to_subprov_mechanism TEXT, subprov_to_creator_mechanism TEXT);
      CREATE TABLE operator_launch_membership (operator_id TEXT, mint TEXT);
      CREATE TABLE potential_operation_evidence_association (potential_operation_id TEXT, evidence_key TEXT,
        evidence_type TEXT, state TEXT);
    """)
    c.execute("INSERT INTO wt_walkback_queue VALUES ('m1','creator-a','funder-a',2,'sig1',99,101,'PLAIN_XFER','complete','selected')")
    c.execute("INSERT INTO wt_walkback_edge_candidates VALUES ('m1','funder-a','creator-a','sig1',101,2000000000,'PLAIN_XFER',1,'SELECTED','HIGH',0,0,'e1')")
    c.execute("INSERT INTO wt_provisioning_sessions VALUES ('m1','treasury','subprov','creator-a',90,101,5,2,'PLAIN_XFER','WSOL_WRAP_CLOSE')")
    c.execute("INSERT INTO operator_launch_membership VALUES ('op1','m1')")
    c.execute("INSERT INTO potential_operation_evidence_association VALUES ('p1','e1','edge','ACTIVE')")
    return c


def test_direct_and_time_window_analytics_are_derived_from_creator_funders():
    a = FundingAnalytics(_main(), _ops())
    assert a.top_funders(limit=1)[0]['funder_address'] == 'funder-a'
    assert len(a.top_funders(start_time=150, end_time=350)) == 2
    assert len(a.funders_for_creator('creator-a')) == 2
    assert len(a.creators_for_funder('funder-a')) == 2
    assert a.shared_funders()[0]['distinct_creators'] == 2
    assert a.creators_with_multiple_funders()[0]['creator_address'] == 'creator-a'
    assert a.funding_amount_ranking(limit=1)[0]['amount_sol'] == 3.0
    assert len(a.cex_funded_creators()) == 1
    assert len(a.cex_funded_creators(start_time=250, end_time=350)) == 1
    assert len(a.funding_activity(start_time=100, end_time=200)) == 2


def test_launch_and_retained_evidence_analytics_are_read_only():
    main, ops = _main(), _ops()
    a = FundingAnalytics(main, ops)
    assert a.top_creators(limit=1)[0]['creator'] == 'creator-a'
    assert a.funding_evidence(mint='m1')[0]['funder_sig'] == 'sig1'
    assert a.operation_funding('op1')[0]['funder_wallet'] == 'funder-a'
    assert a.potential_operation_funding('p1')[0]['signature'] == 'sig1'
    assert a.provisioning_relationships(subprov='subprov')[0]['creator'] == 'creator-a'
    assert a.shared_funders_across_operations(minimum_operations=1)[0]['funder_wallet'] == 'funder-a'
    assert a.legacy_chain_exceptions(bridge_funder='funder-a')[0]['source_creator'] == 'creator-a'
    kinds = {row['kind'] for row in a.funding_chain('m1')}
    assert kinds == {'selected_edge', 'provisioning_session'}
    assert main.total_changes == 9
    assert ops.total_changes == 5


def test_window_validation_and_evidence_lookup_are_explicit():
    a = FundingAnalytics(_main(), _ops())
    try:
        a.top_funders(start_time=2, end_time=1)
    except ValueError:
        pass
    else:
        raise AssertionError('invalid window must fail')
    try:
        a.funding_evidence()
    except ValueError:
        pass
    else:
        raise AssertionError('ambiguous evidence lookup must fail')


def test_launch_join_keeps_creator_funding_separate_from_walkback_selection():
    main, ops = _main(), _ops()
    ops.execute("INSERT INTO wt_walkback_queue VALUES ('m2','creator-a',NULL,NULL,NULL,NULL,150,NULL,'complete','NO_ATTRIBUTION_FOUND')")
    evidence = FundingAnalytics(main, ops).launch_funding_evidence('m2')
    assert evidence['selected_edge_count'] == 0
    assert evidence['walkback_selected'] == []
    assert evidence['creator_funding_retained']
    assert evidence['evidence_status'] == 'CREATOR_FUNDING_ONLY'
    assert {item['funding_relation'] for item in evidence['creator_funding_retained']} == {'BEFORE_LAUNCH', 'AFTER_LAUNCH'}
