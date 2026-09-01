"""Disposable post-commit worker-to-Living dispatch qualification."""
from __future__ import annotations
import sqlite3
import copy
import hashlib
import json
import pytest
import src.core.walkback_worker as worker
from src.ops import living_potential_operations as living
from src.ops.generic_living_lineage_metadata import ensure_lineage_schema
from src.ops.generic_living_persisted_source_reader import read_generic_living_source_context
from src.ops.generic_living_pipeline_v2 import compute_generic_living_assessment
from src.ops.generic_living_active_components import publish_generic_assessment_atomic

WSOL = living.WSOL_POTENTIAL_OPERATION_ID
EIGHT = living.TRANSFER_POTENTIAL_OPERATION_ID
RUN = living.WSOL_RUN


def _generation(n=0):
    return f"{n:012d}:{n:012d}:{n:012d}"


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "worker-living.db")
    c = sqlite3.connect(path); c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript("""
      CREATE TABLE wt_walkback_queue (mint TEXT PRIMARY KEY, creator TEXT, funder_wallet TEXT, funding_mechanism TEXT, funder_sig TEXT, updated_at INTEGER DEFAULT 0);
      CREATE TABLE wt_walkback_edge_candidates (mint TEXT, evidence_key TEXT, selection_status TEXT);
      CREATE TABLE wt_walkback_atomic_flows (mint TEXT, evidence_key TEXT);
      CREATE TABLE p3r_v2_candidate_membership (run_id TEXT, candidate_id TEXT, mint TEXT);
    """)
    living.ensure_schema(c); ensure_lineage_schema(c)
    for op, candidate, mint in ((WSOL, living.WSOL_CANDIDATE_ID, "wsol-mint"), (EIGHT, living.TRANSFER_CANDIDATE_ID, "eight-mint")):
        c.execute("INSERT INTO p3r_v2_candidate_membership VALUES(?,?,?)", (RUN, candidate, mint))
        c.execute("INSERT INTO potential_operation_identity VALUES(?,?,?,?)", (op, "PAUSED", "{}", 0))
        c.execute("INSERT INTO potential_operation_assessment_version VALUES(?,?,?,?,?,?)", (op+"-old", op, op+"-old", _generation(), "{}", 0))
        c.execute("INSERT INTO potential_operation_current VALUES(?,?,?,?)", (op, op+"-old", _generation(), 0))
        c.execute("INSERT INTO potential_operation_evidence_association VALUES(?,?,?,?,?,?,?)", (op+"-mint", op, "mint:"+mint, "SEED", "INCLUDED", "{}", 0))
    c.execute("INSERT INTO potential_operation_evidence_association VALUES(?,?,?,?,?,?,?)", ("wsol-funder", WSOL, "funder:shared", "SEED", "UNRESOLVED", "{}", 0))
    c.commit(); monkeypatch.setattr(worker, "OPS_DB_PATH", path)
    yield c, path
    c.close()


def _persist_then_callback(c, mint, funder="funder"):
    c.execute("INSERT INTO wt_walkback_queue (mint,creator,funder_wallet,funding_mechanism,funder_sig) VALUES(?,?,?,?,?)", (mint, "creator", funder, "PLAIN_XFER", "sig"))
    c.commit()
    return worker._notify_living_after_walkback_commit(c, mint)


@pytest.mark.parametrize("mint,operation,other", [("wsol-mint", WSOL, EIGHT), ("eight-mint", EIGHT, WSOL)])
def test_worker_callback_generic_publishes_only_resolved_candidate(db, monkeypatch, mint, operation, other):
    c, _ = db; monkeypatch.setenv("LIVING_GENERIC_BOUNDED_DISPATCH_ENABLED", "true")
    out = _persist_then_callback(c, mint)
    assert out["status"] == "DISPATCHED" and out["result"]["dispatch_mode"] == "GENERIC"
    assert c.execute("SELECT COUNT(*) FROM potential_operation_assessment_lineage WHERE potential_operation_id=? AND pipeline_lineage='GENERIC_DECLARATIVE_V2'", (operation,)).fetchone()[0] == 1
    assert c.execute("SELECT assessment_id FROM potential_operation_current WHERE potential_operation_id=?", (other,)).fetchone()[0] == other+"-old"


def test_worker_callback_unrelated_and_global_irrelevant_do_not_publish(db, monkeypatch):
    c, _ = db; monkeypatch.setenv("LIVING_GENERIC_BOUNDED_DISPATCH_ENABLED", "true")
    unrelated = _persist_then_callback(c, "other-mint")
    assert unrelated["result"]["resolved_candidate_ids"] == []
    global_only = _persist_then_callback(c, "other-shared", "shared")
    candidate = global_only["result"]["candidates"][0]
    assert candidate["relevance"] == "GLOBAL_HIGH_WATER_ADVANCED_BUT_NOT_RELEVANT" and not candidate["published"]
    assert c.execute("SELECT COUNT(*) FROM potential_operation_assessment_lineage WHERE pipeline_lineage='GENERIC_DECLARATIVE_V2'").fetchone()[0] == 0


def test_worker_callback_flag_off_uses_legacy_path(db, monkeypatch):
    c, _ = db; monkeypatch.setenv("LIVING_GENERIC_BOUNDED_DISPATCH_ENABLED", "false")
    out = _persist_then_callback(c, "wsol-mint")
    assert out["result"]["automatic_global_integration"] is False
    assert c.execute("SELECT COUNT(*) FROM potential_operation_assessment_lineage WHERE pipeline_lineage='GENERIC_DECLARATIVE_V2'").fetchone()[0] == 0


def test_worker_callback_replay_is_idempotent(db, monkeypatch):
    c, _ = db; monkeypatch.setenv("LIVING_GENERIC_BOUNDED_DISPATCH_ENABLED", "true")
    _persist_then_callback(c, "wsol-mint")
    before = [c.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] for table in (
        "potential_operation_assessment_version", "potential_operation_assessment_lineage",
        "potential_operation_evidence_association", "potential_operation_assessment_association_binding")]
    current = c.execute("SELECT assessment_id FROM potential_operation_current WHERE potential_operation_id=?", (WSOL,)).fetchone()[0]
    replay = worker._notify_living_after_walkback_commit(c, "wsol-mint")
    after = [c.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] for table in (
        "potential_operation_assessment_version", "potential_operation_assessment_lineage",
        "potential_operation_evidence_association", "potential_operation_assessment_association_binding")]
    assert replay["status"] == "DISPATCHED" and before == after
    assert c.execute("SELECT assessment_id FROM potential_operation_current WHERE potential_operation_id=?", (WSOL,)).fetchone()[0] == current


def test_worker_callback_stale_generation_does_not_regress_current(db, monkeypatch):
    c, _ = db; monkeypatch.setenv("LIVING_GENERIC_BOUNDED_DISPATCH_ENABLED", "true")
    c.execute("UPDATE potential_operation_current SET freshness_key=? WHERE potential_operation_id=?", (_generation(999), WSOL)); c.commit()
    out = _persist_then_callback(c, "wsol-mint")
    assert out["status"] == "DISPATCHED"
    assert c.execute("SELECT freshness_key FROM potential_operation_current WHERE potential_operation_id=?", (WSOL,)).fetchone()[0] == _generation(999)
    assert c.execute("SELECT COUNT(*) FROM potential_operation_assessment_lineage WHERE potential_operation_id=? AND pipeline_lineage='GENERIC_DECLARATIVE_V2'", (WSOL,)).fetchone()[0] == 0


def test_worker_derived_publisher_equal_freshness_conflict_rolls_back(db):
    """Capture a naturally relevant worker source input, then test publisher conflict."""
    c, path = db
    c.execute("INSERT INTO wt_walkback_queue (mint,creator,funder_wallet,funding_mechanism,funder_sig) VALUES(?,?,?,?,?)", ("wsol-mint", "creator", "funder", "PLAIN_XFER", "sig")); c.commit()
    spec = living._generic_living_registry()[living.WSOL_CANDIDATE_ID]
    evidence = read_generic_living_source_context(spec, path, {"mint": "wsol-mint", "current_generation": _generation()})
    assert evidence["relevant_new_evidence"] is True
    result = compute_generic_living_assessment(spec, evidence)
    associations = [{"potential_operation_id": WSOL, "evidence_identity": a["evidence_key"], "evidence_type": a["evidence_type"], "association_state": a["state"], "source_key": "derived", "provenance": {}} for a in result["associations"]]
    first = publish_generic_assessment_atomic(c, result, associations)
    conflicting = copy.deepcopy(result); conflicting["payload"]["conflict_probe"] = True
    conflicting["digest"] = hashlib.sha256(json.dumps(conflicting["payload"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    counts = [c.execute("SELECT COUNT(*) FROM " + t).fetchone()[0] for t in ("potential_operation_assessment_version", "potential_operation_assessment_lineage", "potential_operation_evidence_association", "potential_operation_assessment_association_binding")]
    with pytest.raises(ValueError, match="equal freshness conflict"):
        publish_generic_assessment_atomic(c, conflicting, associations)
    assert c.execute("SELECT assessment_id FROM potential_operation_current WHERE potential_operation_id=?", (WSOL,)).fetchone()[0] == first["assessment_id"]
    assert counts == [c.execute("SELECT COUNT(*) FROM " + t).fetchone()[0] for t in ("potential_operation_assessment_version", "potential_operation_assessment_lineage", "potential_operation_evidence_association", "potential_operation_assessment_association_binding")]

def test_callback_injected_publisher_failure_is_observable_and_isolated(db, monkeypatch):
    c, _ = db; monkeypatch.setenv("LIVING_GENERIC_BOUNDED_DISPATCH_ENABLED", "true")
    c.execute("INSERT INTO wt_walkback_queue (mint,creator,funder_wallet,funding_mechanism,funder_sig) VALUES(?,?,?,?,?)", ("wsol-mint", "creator", "funder", "PLAIN_XFER", "sig")); c.commit()
    before = [c.execute("SELECT COUNT(*) FROM " + t).fetchone()[0] for t in ("potential_operation_assessment_version", "potential_operation_assessment_lineage", "potential_operation_assessment_association_binding")]
    def inject(stage, _context):
        if stage == "after_current_before_commit": raise RuntimeError("test publication failure")
    failed = worker._notify_living_after_walkback_commit(c, "wsol-mint", test_failure_injector=inject)
    assert failed["invoked"] and not failed["success"] and failed["publication_attempted"] and not failed["published_assessment_ids"] and failed["error_type"]
    assert c.execute("SELECT COUNT(*) FROM wt_walkback_queue WHERE mint='wsol-mint'").fetchone()[0] == 1
    assert before == [c.execute("SELECT COUNT(*) FROM " + t).fetchone()[0] for t in ("potential_operation_assessment_version", "potential_operation_assessment_lineage", "potential_operation_assessment_association_binding")]
    c.execute("INSERT INTO wt_walkback_queue (mint,creator,funder_wallet,funding_mechanism,funder_sig) VALUES(?,?,?,?,?)", ("unrelated", "creator", "funder", "PLAIN_XFER", "sig")); c.commit()
    noop = worker._notify_living_after_walkback_commit(c, "unrelated")
    assert noop["success"] and not noop["publication_attempted"] and noop["no_op_reason"]
