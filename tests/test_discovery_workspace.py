"""Sprint X15 — Discovery provenance workspace."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


SCHEMA = """
CREATE TABLE migrated_tokens (mint TEXT PRIMARY KEY, creator TEXT, migration_tx TEXT,
 migration_time INTEGER, stored_at INTEGER);
CREATE TABLE wt_watchtower_launches (mint TEXT, creator_wallet TEXT, create_signature TEXT,
 create_time INTEGER, treasury_wallet TEXT, subprov_wallet TEXT, wrap_close_signature TEXT,
 funding_mechanism TEXT, creator_extraction_method TEXT, confidence TEXT, recorded_at INTEGER);
CREATE TABLE wt_wrap_close_candidates (creator TEXT PRIMARY KEY, funding_mechanism TEXT,
 creator_extraction_method TEXT, subprov_wallet TEXT, lineage_source_treasury TEXT,
 base_amount_sol REAL, tx_signature TEXT, funded_at INTEGER, confidence TEXT, detected_at INTEGER);
CREATE TABLE wt_discovered_subprovs (subprov TEXT PRIMARY KEY, creator_count INTEGER,
 treasury TEXT, immediate_funder TEXT, confidence REAL, state TEXT, wrap_close_count INTEGER,
 funding_mechanism TEXT, first_seen INTEGER, last_seen INTEGER);
CREATE TABLE wt_confirmed_treasuries (treasury TEXT PRIMARY KEY, confidence TEXT, method TEXT,
 out_sol REAL, recipients INTEGER, confirmed_at INTEGER);
CREATE TABLE wt_treasury_review (treasury TEXT PRIMARY KEY, status TEXT, confidence TEXT,
 detected_at INTEGER, reviewed_at INTEGER, detected_via TEXT, recipients INTEGER, out_sol REAL);
CREATE TABLE watchtower_token_attribution (mint TEXT PRIMARY KEY, creator TEXT, score REAL,
 tier TEXT, reasons_json TEXT, matched_treasury TEXT, matched_subprov TEXT,
 reviewed_status TEXT, scored_at INTEGER);
CREATE TABLE wt_token_lifecycle (mint TEXT PRIMARY KEY, treasury TEXT, subprov TEXT, creator TEXT,
 lifecycle_state TEXT, launched_at INTEGER, migrated_at INTEGER, recycled_at INTEGER,
 operation_uuid TEXT, updated_at INTEGER);
CREATE TABLE wt_walkback_queue (mint TEXT PRIMARY KEY, creator TEXT, subprov TEXT, treasury TEXT,
 status TEXT, completed_at INTEGER, intelligence_outcome TEXT);
CREATE TABLE wt_ops_v2_treasury_resolution (operation_uuid TEXT PRIMARY KEY,
 current_assigned_treasury TEXT, positional_root_candidate TEXT, confidence REAL,
 evidence_path TEXT, status TEXT, reason TEXT, resolved_at INTEGER);
CREATE TABLE operators (operator_id TEXT PRIMARY KEY, status TEXT, confidence TEXT,
 first_seen INTEGER, last_seen INTEGER, summary TEXT, review_state TEXT, display_name TEXT,
 created_at INTEGER, updated_at INTEGER);
CREATE TABLE operator_entities (operator_id TEXT, entity_address TEXT, entity_type TEXT,
 confidence TEXT, evidence_count INTEGER, first_seen INTEGER, last_seen INTEGER, added_at INTEGER);
CREATE TABLE operator_evidence (evidence_id TEXT PRIMARY KEY, operator_id TEXT, evidence_type TEXT,
 evidence_category TEXT, source_operation TEXT, entity_a TEXT, entity_b TEXT, weight REAL,
 details TEXT, created_at INTEGER);
CREATE TABLE operator_reviews (review_id TEXT PRIMARY KEY, operator_id TEXT, decision TEXT,
 reviewer TEXT, timestamp INTEGER, reason TEXT, related_operator_id TEXT);
"""


@pytest.fixture()
def discovery_db():
    fd, path = tempfile.mkstemp(suffix="_discovery.db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.executescript("""
    INSERT INTO migrated_tokens VALUES ('MINT','CREATOR','MIGTX',150,151);
    INSERT INTO wt_watchtower_launches VALUES
      ('MINT','CREATOR','CREATETX',140,'TREASURY','SUBPROV','WRAPTX',
       'WSOL_WRAP_CLOSE','CLOSE_ACCOUNT_DESTINATION','STRICT',141);
    INSERT INTO wt_wrap_close_candidates VALUES
      ('CREATOR','WSOL_WRAP_CLOSE','CLOSE_ACCOUNT_DESTINATION','SUBPROV','TREASURY',
       2.10203928,'WRAPTX',130,'STRICT',131);
    INSERT INTO wt_discovered_subprovs VALUES
      ('SUBPROV',4,'TREASURY','TREASURY',0.9,'PROVISIONAL_SUBPROV',4,'WSOL_WRAP_CLOSE',120,145);
    INSERT INTO wt_confirmed_treasuries VALUES
      ('TREASURY','HIGH','walkback',24.2,8,110);
    INSERT INTO watchtower_token_attribution VALUES
      ('MINT','CREATOR',95,'STRONG','["Known infrastructure ancestry"]','TREASURY','SUBPROV','CONFIRMED',160);
    INSERT INTO wt_token_lifecycle VALUES
      ('MINT','TREASURY','SUBPROV','CREATOR','MIGRATED',140,150,NULL,'OP1',161);
    INSERT INTO operators VALUES
      ('OPERATOR','CONFIRMED','CERTAIN',100,170,'Known operation','REVIEWED','Alpha',90,170);
    INSERT INTO operator_entities VALUES
      ('OPERATOR','TREASURY','TREASURY','HIGH',2,100,170,165);
    INSERT INTO operator_evidence VALUES
      ('EV1','OPERATOR','SHARED_TREASURY_ROOT','IDENTITY','watchtower','TREASURY','OTHER',1.0,'{}',166);
    INSERT INTO operator_reviews VALUES
      ('RV1','OPERATOR','CONFIRMED','analyst',169,'Shared root verified',NULL);
    """)
    conn.commit()
    conn.close()
    yield path
    os.unlink(path)


def test_token_chain_is_explainable_and_chronological(discovery_db):
    from src.discovery.service import DiscoveryService
    data = DiscoveryService(discovery_db, discovery_db).resolve("MINT", "token")
    kinds = {n["kind"] for n in data["timeline"]}
    assert data["state"] == "CONFIRMED"
    assert {"TOKEN_LAUNCH", "CREATOR_IDENTIFIED", "SUBPROVISIONER_RESOLVED",
            "TREASURY_RESOLVED", "WATCHTOWER_ATTRIBUTION"} <= kinds
    timestamps = [n["timestamp"] for n in data["timeline"] if n["timestamp"]]
    assert timestamps == sorted(timestamps)
    assert data["walkback"]["stop_reason"] == "Reached known treasury."
    assert [h["role"] for h in data["walkback"]["hops"]][:3] == [
        "CREATOR", "SUB_PROVISIONER", "TREASURY"
    ]


def test_unresolved_walk_explains_early_stop(discovery_db):
    conn = sqlite3.connect(discovery_db)
    conn.execute("DELETE FROM wt_confirmed_treasuries")
    conn.execute("UPDATE wt_discovered_subprovs SET treasury=NULL, immediate_funder=NULL")
    conn.execute("UPDATE wt_watchtower_launches SET treasury_wallet=NULL")
    conn.execute("UPDATE watchtower_token_attribution SET matched_treasury=NULL, reviewed_status='AUTO'")
    conn.execute("UPDATE wt_token_lifecycle SET treasury=NULL")
    conn.commit()
    conn.close()
    from src.discovery.service import DiscoveryService
    data = DiscoveryService(discovery_db, discovery_db).resolve("MINT", "token")
    assert "Funding chain exhausted" in data["walkback"]["stop_reason"]
    assert data["walkback"]["status"] == "PROVISIONAL"


def test_operator_uses_x8_evidence_catalogue(discovery_db):
    from src.discovery.service import DiscoveryService
    data = DiscoveryService(discovery_db, discovery_db).resolve("OPERATOR", "operator")
    evidence = next(n for n in data["timeline"] if n["kind"] == "ATTRIBUTION_EVIDENCE")
    assert evidence["category"] == "IDENTITY"
    assert evidence["supporting_rule"] == "SHARED_TREASURY_ROOT"
    assert "confirmed treasury root" in evidence["reason"]
    assert data["state"] == "CONFIRMED"


def test_service_does_not_mutate_database(discovery_db):
    before = hashlib.sha256(open(discovery_db, "rb").read()).digest()
    from src.discovery.service import DiscoveryService
    service = DiscoveryService(discovery_db, discovery_db)
    service.resolve("MINT")
    service.search("MINT")
    service.recent()
    after = hashlib.sha256(open(discovery_db, "rb").read()).digest()
    assert after == before


def test_workspace_routes_and_entry_points(discovery_db):
    from flask import Flask, jsonify, render_template, request
    from src.discovery.service import DiscoveryService
    app = Flask(__name__, template_folder=os.path.join(ROOT, "templates"),
                static_folder=os.path.join(ROOT, "static"))
    service = DiscoveryService(discovery_db, discovery_db)

    @app.route("/discovery")
    def page():
        return render_template("discovery.html", entity_id=request.args.get("entity", ""),
                               entity_type=request.args.get("type", "auto"), active_page="discovery")

    @app.route("/api/discovery/entity/<entity_id>")
    def api(entity_id):
        return jsonify(service.resolve(entity_id, request.args.get("type", "auto")))

    app.config["TESTING"] = True
    with app.test_client() as client:
        assert client.get("/discovery?entity=MINT&type=token").status_code == 200
        assert client.get("/api/discovery/entity/MINT?type=token").get_json()["ok"] is True

    # Preserve the import-isolation contract exercised by the Operations OS
    # registry tests when this module runs earlier in the same pytest process.
    for module_name in list(sys.modules):
        if module_name == "flask" or module_name.startswith("flask."):
            del sys.modules[module_name]

    discovery = open(os.path.join(ROOT, "templates", "discovery.html"), encoding="utf-8").read()
    sidebar = open(os.path.join(ROOT, "templates", "partials", "sidebar.html"), encoding="utf-8").read()
    entity = open(os.path.join(ROOT, "templates", "entity_intelligence.html"), encoding="utf-8").read()
    operator = open(os.path.join(ROOT, "templates", "operator_intelligence.html"), encoding="utf-8").read()
    mission = open(os.path.join(ROOT, "templates", "ops_shell_index.html"), encoding="utf-8").read()
    assert "vertical" not in discovery.lower() or "dw-timeline" in discovery
    assert "View Discovery" in discovery
    assert 'href="/discovery"' in sidebar
    assert "View Attribution Chain" in entity
    assert "Discovery History" in operator
    assert "Meaningful Intelligence" in mission
    assert "/api/discovery/recent" in mission
