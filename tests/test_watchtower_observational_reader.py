import sqlite3

import src.ops.watchtower_fingerprint_observational_reader as reader



def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE operators (operator_id TEXT PRIMARY KEY,display_name TEXT,status TEXT);
        CREATE TABLE operator_launch_membership (operator_id TEXT,mint TEXT);
        CREATE TABLE wt_walkback_queue (mint TEXT PRIMARY KEY,status TEXT,intelligence_outcome TEXT,creator TEXT,subprov TEXT,treasury TEXT,funder_sig TEXT,funding_mechanism TEXT);
        CREATE TABLE wt_provisioning_sessions (source_mint TEXT,treasury TEXT,subprov TEXT,creator TEXT,subprov_to_creator_mechanism TEXT,treasury_to_subprov_mechanism TEXT);
        CREATE TABLE wt_watchtower_launches (mint TEXT PRIMARY KEY);
        CREATE TABLE wt_confirmed_treasuries (treasury TEXT PRIMARY KEY);
        CREATE TABLE wt_treasury_review (treasury TEXT PRIMARY KEY,status TEXT);
        CREATE TABLE wt_discovered_subprovs (subprov TEXT PRIMARY KEY,treasury TEXT,state TEXT,treasury_known INTEGER,creator_count INTEGER);
        """
    )
    conn.execute("INSERT INTO operators VALUES (?,'WATCHTOWER','CONFIRMED')", (reader.WATCHTOWER_OPERATOR_ID,))
    return conn


def test_observational_state_is_read_from_persisted_route_and_not_created():
    conn = _db()
    conn.executemany("INSERT INTO wt_walkback_queue VALUES (?,?,?,?,?,?,?,?)", [
        ("a","complete","WATCHTOWER_CONFIRMED","creator1","sub1","t1","sig1","WSOL_WRAP_CLOSE"),
    ])
    conn.execute(
        "INSERT INTO wt_provisioning_sessions VALUES (?,?,?,?,?,?)",
        ("a","t1","sub1","creator1","WSOL_WRAP_CLOSE","WSOL_WRAP_CLOSE"),
    )
    assert reader.read_watchtower_observational_state(conn, "a") == reader.STATE_CONFIRMED_VERIFIED_ROUTE
    result = reader.watchtower_observational_summary_for_mint(conn, "a")
    assert result["monitoring_state"] == reader.STATE_CONFIRMED_VERIFIED_ROUTE


def test_verified_state_does_not_mutate_queue_or_queue_session():
    conn = _db()
    conn.execute(
        "INSERT INTO wt_walkback_queue VALUES (?,?,?,?,?,?,?,?)",
        ("b","complete","WATCHTOWER_CONFIRMED","creator","sub","t","sig","WSOL_WRAP_CLOSE"),
    )
    conn.execute(
        "INSERT INTO wt_provisioning_sessions VALUES (?,?,?,?,?,?)",
        ("b","t","sub","creator","WSOL_WRAP_CLOSE","WSOL_WRAP_CLOSE"),
    )
    before_q = conn.execute("SELECT COUNT(*) FROM wt_walkback_queue").fetchone()[0]
    before_s = conn.execute("SELECT COUNT(*) FROM wt_provisioning_sessions").fetchone()[0]
    assert reader.read_watchtower_observational_state(conn, "b") == reader.STATE_CONFIRMED_VERIFIED_ROUTE
    assert conn.execute("SELECT COUNT(*) FROM wt_walkback_queue").fetchone()[0] == before_q
    assert conn.execute("SELECT COUNT(*) FROM wt_provisioning_sessions").fetchone()[0] == before_s


def test_pending_role_discovery_state_is_reported_without_projection():
    conn = _db()
    conn.execute(
        "INSERT INTO wt_walkback_queue VALUES (?,?,?,?,?,?,?,?)",
        ("c","running",None,"creator",None,None,None,"WSOL_WRAP_CLOSE"),
    )
    assert reader.read_watchtower_observational_state(conn, "c") == reader.STATE_PENDING_ROLE_DISCOVERY


def test_reader_does_not_promote_or_create_treasury_candidates():
    conn = _db()
    conn.execute("INSERT INTO wt_treasury_review VALUES ('t1','PENDING_REVIEW')")
    conn.execute("INSERT INTO wt_treasury_review VALUES ('t2','APPROVED')")
    before = conn.execute("SELECT COUNT(*) FROM wt_treasury_review").fetchone()[0]
    assert reader.watchtower_source_manifest(conn)["mutability_coverage"]["treasury_candidates"] == before
    conn.execute("INSERT INTO wt_confirmed_treasuries VALUES ('t3')")
    assert reader.watchtower_source_manifest(conn)["mutability_coverage"]["treasury_candidates"] == before


def test_reader_does_not_create_or_promote_subproviders():
    conn = _db()
    conn.execute("INSERT INTO wt_discovered_subprovs VALUES ('s1','t1','VALID_SUBPROVISIONER',1,1)")
    conn.execute("INSERT INTO wt_discovered_subprovs VALUES ('s2','t2','REJECTED_INFRASTRUCTURE',0,2)")
    before_candidates = conn.execute("SELECT COUNT(*) FROM wt_discovered_subprovs").fetchone()[0]
    before_rejected = reader._rejected_subprovider_count(conn)
    manifest = reader.watchtower_source_manifest(conn)
    assert manifest["mutability_coverage"]["subprovider_candidates"] == before_candidates
    assert manifest["mutability_coverage"]["rejected_subproviders"] == before_rejected
    assert conn.execute("SELECT COUNT(*) FROM wt_discovered_subprovs").fetchone()[0] == before_candidates


def test_reader_does_not_call_membership_projection():
    conn = _db()
    conn.execute("INSERT INTO operator_launch_membership VALUES (?,?)", (reader.WATCHTOWER_OPERATOR_ID, "m1"))
    conn.execute("INSERT INTO operator_launch_membership VALUES (?,?)", ("other", "m2"))
    before = conn.execute("SELECT COUNT(*) FROM operator_launch_membership").fetchone()[0]
    _ = reader.watchtower_source_manifest(conn)
    assert conn.execute("SELECT COUNT(*) FROM operator_launch_membership").fetchone()[0] == before


def test_no_sqlite_writes_in_observational_module_source():
    source = open(reader.__file__).read()
    assert "INSERT INTO operator_launch_membership" not in source
    assert "INSERT INTO wt_watchtower_launches" not in source
    assert "INSERT INTO wt_confirmed_treasuries" not in source
    assert "UPDATE operator_launch_membership" not in source
    assert "DELETE FROM" not in source


def test_observational_metadata_matches_required_contract():
    conn = _db()
    conn.execute("INSERT INTO wt_treasury_review VALUES ('t1','PENDING_REVIEW')")
    conn.execute("INSERT INTO wt_discovered_subprovs VALUES ('s1','t1','VALID_SUBPROVISIONER',1,1)")
    conn.execute("INSERT INTO wt_confirmed_treasuries VALUES ('t1')")
    conn.execute("INSERT INTO operator_launch_membership VALUES (?, 'm1')", (reader.WATCHTOWER_OPERATOR_ID,))
    payload = reader.watchtower_source_manifest(conn)
    assert payload["operation"] == "WATCHTOWER"
    assert payload["fingerprint_id"] == reader.FINGERPRINT_ID
    assert payload["monitoring_strategy"] == "DYNAMIC_ROLE_DISCOVERY"
    assert payload["near_match_monitoring"] == "DISABLED"
    assert payload["mutation_resilience"] == reader.MUTATION_RESILIENCE
    assert payload["membership_write_capability"] == "NONE"
    assert payload["mutability_coverage"]["confirmed_subproviders"] >= 0
    assert payload["provenance"] == "CURRENTLY_PERSISTED_FACTS"
    assert payload["uniqueness_capability"] == reader.UNIQUENESS_MEASURABLE


def test_known_watchtower_modules_are_not_mutated_by_observer_task_read_scope():
    with open("src/core/watchtower_registry_promotion.py", "r", encoding="utf-8") as fh:
        promotion = fh.read()
    with open("src/core/walkback_worker.py", "r", encoding="utf-8") as fh:
        walkback = fh.read()
    assert "_has_verified_queue_funding_route" in promotion
    assert "_has_verified_queue_funding_route" not in walkback
    assert "watchtower_fingerprint_observational_reader" not in promotion
