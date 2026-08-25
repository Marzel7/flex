import sqlite3

from src.core.watchtower_registry_promotion import (
    MEMBERSHIP_SOURCE,
    remove_invalid_confirmed_membership_projection,
    project_watchtower_confirmed_membership,
    reconcile_confirmed_watchtower_memberships,
)


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE operators (operator_id TEXT PRIMARY KEY, display_name TEXT, status TEXT);
        CREATE TABLE wt_walkback_queue (
            mint TEXT PRIMARY KEY, intelligence_outcome TEXT, completed_at INTEGER,
            creator TEXT, treasury TEXT, subprov TEXT, funder_sig TEXT, funding_mechanism TEXT
        );
        CREATE TABLE wt_provisioning_sessions (
            source_mint TEXT, treasury TEXT, subprov TEXT, creator TEXT,
            treasury_to_subprov_mechanism TEXT, subprov_to_creator_mechanism TEXT
        );
        CREATE TABLE operator_launch_membership (
            mint TEXT PRIMARY KEY, operator_id TEXT, source_population_id TEXT,
            assigned_at INTEGER, event_id TEXT
        );
    """)
    conn.execute("INSERT INTO operators VALUES ('wt', 'WATCHTOWER', 'CONFIRMED')")
    return conn


def _route(conn, mint):
    conn.execute(
        "UPDATE wt_walkback_queue SET creator='creator', treasury='treasury', subprov='subprov', "
        "funder_sig='sig', funding_mechanism='WSOL_WRAP_CLOSE' WHERE mint=?", (mint,),
    )
    conn.execute(
        "INSERT INTO wt_provisioning_sessions VALUES (?, 'treasury', 'subprov', 'creator', 'PLAIN_TRANSFER', 'WSOL_WRAP_CLOSE')",
        (mint,),
    )


def test_projects_only_strict_confirmed_outcome_and_is_idempotent():
    conn = _db()
    conn.execute("INSERT INTO wt_walkback_queue(mint,intelligence_outcome,completed_at) VALUES ('confirmed', 'WATCHTOWER_CONFIRMED', 10)")
    conn.execute("INSERT INTO wt_walkback_queue(mint,intelligence_outcome,completed_at) VALUES ('false_positive', 'UNKNOWN_INFRASTRUCTURE', 11)")
    _route(conn, 'confirmed')

    assert project_watchtower_confirmed_membership(conn, 'confirmed', now=100, refresh_activity=False)["action"] == "projected"
    assert project_watchtower_confirmed_membership(conn, 'confirmed', now=101, refresh_activity=False)["action"] == "already_present"
    assert project_watchtower_confirmed_membership(conn, 'false_positive', now=102, refresh_activity=False)["action"] == "not_confirmed"

    rows = conn.execute("SELECT mint,operator_id,source_population_id,assigned_at FROM operator_launch_membership").fetchall()
    assert [tuple(row) for row in rows] == [('confirmed', 'wt', MEMBERSHIP_SOURCE, 100)]


def test_reconciler_projects_only_missing_confirmed_rows_and_reruns_cleanly(monkeypatch):
    conn = _db()
    conn.executemany("INSERT INTO wt_walkback_queue(mint,intelligence_outcome,completed_at) VALUES (?,?,?)", [
        ('one', 'WATCHTOWER_CONFIRMED', 10),
        ('two', 'WATCHTOWER_CONFIRMED', 20),
        ('false_positive', 'REJECTED_INFRASTRUCTURE', 30),
    ])
    _route(conn, 'one')
    _route(conn, 'two')
    calls = []
    monkeypatch.setattr(
        'src.ops.manual_registry.refresh_operator_activity_snapshot',
        lambda *args, **kwargs: calls.append((args[1], kwargs['now'])),
    )

    first = reconcile_confirmed_watchtower_memberships(conn, now=100)
    second = reconcile_confirmed_watchtower_memberships(conn, now=101)

    assert first['projected'] == ['one', 'two']
    assert second['eligible_missing_membership'] == 0
    assert second['projected'] == []
    assert conn.execute("SELECT count(*) FROM operator_launch_membership").fetchone()[0] == 2
    assert calls == [('wt', 100)]


def test_cleanup_removes_only_projector_rows_without_a_verified_route(monkeypatch):
    conn = _db()
    conn.executemany("INSERT INTO wt_walkback_queue(mint,intelligence_outcome,completed_at) VALUES (?,?,?)", [
        ('valid', 'WATCHTOWER_CONFIRMED', 10),
        ('known_false_positive', 'WATCHTOWER_CONFIRMED', 20),
    ])
    _route(conn, 'valid')
    monkeypatch.setattr(
        'src.ops.manual_registry.refresh_operator_activity_snapshot', lambda *args, **kwargs: None,
    )
    conn.executemany(
        "INSERT INTO operator_launch_membership VALUES (?,?,?,?,NULL)",
        [('valid', 'wt', MEMBERSHIP_SOURCE, 1), ('known_false_positive', 'wt', MEMBERSHIP_SOURCE, 1)],
    )
    result = remove_invalid_confirmed_membership_projection(conn, now=100)
    assert result['retained'] == ['valid']
    assert result['removed'] == ['known_false_positive']
