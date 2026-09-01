"""Reusable 80-member disposable fixture for future worker/Living WAL stress."""
from __future__ import annotations
import sqlite3
import pytest
import src.core.walkback_worker as worker
from src.ops import living_potential_operations as living
from src.ops.generic_living_lineage_metadata import ensure_lineage_schema
from src.ops.generic_living_persisted_source_reader import read_generic_living_source_context
from src.ops.generic_living_reverse_resolver import LivingCandidateReverseIndex
from src.ops.generic_living_active_components import classify_relevance

WSOL, EIGHT, RUN = living.WSOL_POTENTIAL_OPERATION_ID, living.TRANSFER_POTENTIAL_OPERATION_ID, living.WSOL_RUN
GEN0 = "000000000000:000000000000:000000000000"

def members(prefix): return [f"test-{prefix}-mint-{i:04d}" for i in range(1, 41)]

def build_fixture(path):
 c=sqlite3.connect(path); c.row_factory=sqlite3.Row; c.execute("PRAGMA journal_mode=WAL")
 c.executescript("CREATE TABLE wt_walkback_queue (mint TEXT PRIMARY KEY,creator TEXT,funder_wallet TEXT,funding_mechanism TEXT,funder_sig TEXT,updated_at INTEGER DEFAULT 0);CREATE TABLE wt_walkback_edge_candidates (mint TEXT,evidence_key TEXT,selection_status TEXT);CREATE TABLE wt_walkback_atomic_flows (mint TEXT,evidence_key TEXT);CREATE TABLE p3r_v2_candidate_membership (run_id TEXT,candidate_id TEXT,mint TEXT);")
 living.ensure_schema(c); ensure_lineage_schema(c)
 for op,candidate,prefix in ((WSOL,living.WSOL_CANDIDATE_ID,"wsol"),(EIGHT,living.TRANSFER_CANDIDATE_ID,"eight")):
  for mint in members(prefix):
   c.execute("INSERT INTO p3r_v2_candidate_membership VALUES(?,?,?)",(RUN,candidate,mint)); c.execute("INSERT INTO potential_operation_evidence_association VALUES(?,?,?,?,?,?,?)",(op+mint,op,"mint:"+mint,"SEED","INCLUDED","{}",0))
  c.execute("INSERT INTO potential_operation_identity VALUES(?,?,?,?)",(op,"PAUSED","{}",0)); c.execute("INSERT INTO potential_operation_assessment_version VALUES(?,?,?,?,?,?)",(op+"-old",op,op+"-old",GEN0,"{}",0)); c.execute("INSERT INTO potential_operation_current VALUES(?,?,?,?)",(op,op+"-old",GEN0,0))
 c.execute("INSERT INTO potential_operation_evidence_association VALUES(?,?,?,?,?,?,?)",("shared-w",WSOL,"funder:test-shared","SEED","UNRESOLVED","{}",0)); c.execute("INSERT INTO potential_operation_evidence_association VALUES(?,?,?,?,?,?,?)",("shared-e",EIGHT,"funder:test-shared","SEED","UNRESOLVED","{}",0)); c.commit(); return c

def append_disposable_walkback_event(c,mint,kind="relevant"):
 c.execute("INSERT INTO wt_walkback_queue (mint,creator,funder_wallet,funding_mechanism,funder_sig) VALUES(?,?,?,?,?)",(mint,"test-creator","test-shared" if kind=="global" else "test-funder","PLAIN_XFER","test-sig")); c.commit(); return mint

@pytest.fixture
def multi(tmp_path,monkeypatch):
 c=build_fixture(str(tmp_path/"multi-member.db")); monkeypatch.setattr(worker,"OPS_DB_PATH",str(tmp_path/"multi-member.db")); yield c,str(tmp_path/"multi-member.db"); c.close()

def test_multi_member_fixture_contract(multi,monkeypatch):
 c,path=multi; monkeypatch.setenv("LIVING_GENERIC_BOUNDED_DISPATCH_ENABLED","true")
 registry=living._generic_living_registry(); index=LivingCandidateReverseIndex.from_association_ledger(registry,path)
 assert len(members("wsol"))==40 and len(members("eight"))==40
 assert index.resolve({"mint":members("wsol")[0]})==[WSOL] and index.resolve({"mint":members("eight")[0]})==[EIGHT]
 assert index.resolve({"mint":"test-unrelated"})==[] and index.resolve({"funder":"test-shared"})==[EIGHT,WSOL]
 mint=append_disposable_walkback_event(c,members("wsol")[0]); spec=registry[living.WSOL_CANDIDATE_ID]
 evidence=read_generic_living_source_context(spec,path,{"mint":mint,"current_generation":GEN0})
 assert evidence["relevant_new_evidence"] and classify_relevance(spec,{"mint":mint},lambda *_:evidence)=="RELEVANT_NEW_EVIDENCE"
 out=worker._notify_living_after_walkback_commit(c,mint); assert out["status"]=="DISPATCHED" and out["result"]["resolved_candidate_ids"]==[WSOL]
