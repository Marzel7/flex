import json
import sqlite3

from src.ops import wsol_10_sol_four_step_operation as subject


def _dbs(tmp_path, *, launch_time=200, conflict=False, migrated=True):
    tmp_path.mkdir(exist_ok=True)
    ops = sqlite3.connect(tmp_path / "ops.db")
    ops.executescript("""
    CREATE TABLE operators(operator_id TEXT,status TEXT);
    CREATE TABLE operator_launch_membership(mint TEXT PRIMARY KEY,operator_id TEXT,source_population_id TEXT,assigned_at INT,event_id TEXT);
    CREATE TABLE confirmed_operation_matches(match_id TEXT PRIMARY KEY,operator_id TEXT,mint TEXT,detector_version TEXT,state TEXT,evidence_json TEXT,detected_at INT);
    CREATE TABLE wt_walkback_queue(mint TEXT,creator TEXT);
    CREATE TABLE wt_walkback_edge_candidates(mint TEXT,wallet TEXT,candidate_parent TEXT,signature TEXT,block_time INT,amount_lamports INT,mechanism TEXT,hop_depth INT,selection_status TEXT);
    CREATE TABLE wt_walkback_atomic_flows(mint TEXT,signature TEXT,evidence_key TEXT,has_create INT,has_sync_native INT,has_close INT,instruction_order_json TEXT);
    CREATE TABLE operation_activity_snapshots(snapshot_id TEXT,operator_id TEXT,observed_at INT,timestamp_semantics TEXT,metrics_json TEXT,activity_state TEXT);
    """)
    ops.execute("INSERT INTO operators VALUES(?, 'CONFIRMED')", (subject.OPERATOR_ID,))
    creator = "creator"
    ops.execute("INSERT INTO operator_launch_membership VALUES('proof',?,?,1,'proof-event')", (subject.OPERATOR_ID, subject.SOURCE_CHILD_ID))
    ops.execute("INSERT INTO wt_walkback_queue VALUES('proof',?)", (creator,))
    ops.execute("INSERT INTO wt_walkback_edge_candidates VALUES('proof',?,'ByZc7RNeYowEg2jKo2giytWb9WmNyZPrQ1hXhnGSzHTY','proof-sig',100,9999985000,'WSOL_WRAP_CLOSE',1,'SELECTED')", (creator,))
    ops.execute("INSERT INTO wt_walkback_atomic_flows VALUES('proof','proof-sig','atomic',1,1,1,?)", (json.dumps(subject.ATOMIC_SEQUENCE),))
    if conflict:
        ops.execute("CREATE TABLE operator_identity_assets(operator_id TEXT,asset_type TEXT,asset_value TEXT,status TEXT)")
        ops.execute("INSERT INTO operator_identity_assets VALUES('other','CREATOR_FAMILY',?,'ACTIVE')", (creator,))
    ops.commit()
    core_path = tmp_path / "core.db"; core = sqlite3.connect(core_path)
    core.execute("CREATE TABLE token_analysis(mint TEXT,pf_ws_creator TEXT,created_at INT,create_tx_signature TEXT,migration_tx TEXT)")
    core.execute("INSERT INTO token_analysis VALUES('later',?,?, 'birth', ?)", (creator, launch_time, 'migration' if migrated else None))
    core.commit(); core.close()
    return ops, str(core_path)


def test_proven_creator_later_birth_is_admitted(monkeypatch, tmp_path):
    ops, core = _dbs(tmp_path); monkeypatch.setattr('src.ops.manual_registry.refresh_operator_activity_snapshot', lambda *a, **k: {})
    assert subject.project_completed_walkback(ops, 'later', core_db_path=core, now=300) == 'admitted'
    row = ops.execute("SELECT detector_version,source_population_id FROM confirmed_operation_matches JOIN operator_launch_membership USING(mint) WHERE mint='later'").fetchone()
    assert row == (subject.CREATOR_CONTINUITY_VERSION, 'BYZANTINE_PROVEN_CREATOR_CONTINUITY')
    assert subject.project_completed_walkback(ops, 'later', core_db_path=core, now=301) == 'already_present'


def test_creator_route_rejects_preproof_and_governance_conflict(monkeypatch, tmp_path):
    monkeypatch.setattr('src.ops.manual_registry.refresh_operator_activity_snapshot', lambda *a, **k: {})
    ops, core = _dbs(tmp_path / 'pre', launch_time=99)
    assert subject.project_completed_walkback(ops, 'later', core_db_path=core) == 'not_wsol_10_four_step'


def test_creator_continuity_requires_current_migration_and_reconciles_projection(monkeypatch, tmp_path):
    monkeypatch.setattr('src.ops.manual_registry.refresh_operator_activity_snapshot', lambda *a, **k: {})
    ops, core = _dbs(tmp_path, migrated=False)
    assert subject.project_completed_walkback(ops, 'later', core_db_path=core) == 'not_wsol_10_four_step'
    # Simulate only the formerly over-broad projection: evidence remains after withdrawal.
    ops.execute("INSERT INTO operator_launch_membership VALUES('later',?,?,1,'event')", (subject.OPERATOR_ID, 'BYZANTINE_PROVEN_CREATOR_CONTINUITY'))
    result = subject.reconcile_invalid_creator_continuity_memberships(ops, core_db_path=core)
    assert result['removed'] == ['later']
    ops, core = _dbs(tmp_path / 'conflict', conflict=True)
    assert subject.project_completed_walkback(ops, 'later', core_db_path=core) == 'not_wsol_10_four_step'
