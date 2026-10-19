"""Explicit, non-automatic manual V1 proposal workflow."""
import json, time
from hashlib import sha256
from src.ops.operation_attribution_evidence import build_decision

WORKFLOW_VERSION="MANUAL_OPERATION_ATTRIBUTION_WORKFLOW_V1"
def ensure_schema(c):
 c.executescript("CREATE TABLE IF NOT EXISTS proposed_operations(proposal_id TEXT PRIMARY KEY,candidate_source TEXT,detector_contract TEXT,candidate_member_refs TEXT,created_at INT,validation_state TEXT,latest_v1_decision_ref TEXT,promotion_state TEXT);CREATE TABLE IF NOT EXISTS manual_attribution_decisions(decision_id TEXT PRIMARY KEY,proposal_id TEXT,decision_json TEXT,created_at INT);CREATE INDEX IF NOT EXISTS ix_manual_attribution_decisions_proposal ON manual_attribution_decisions(proposal_id)")
def propose(c,proposal_id,candidate_source,detector_contract,members=()):
 ensure_schema(c);c.execute("INSERT OR IGNORE INTO proposed_operations VALUES(?,?,?,?,?,'NOT_VALIDATED',NULL,'NOT_PROMOTED')",(proposal_id,candidate_source,detector_contract,json.dumps(list(members)),0));c.commit()
def validate(c,proposal_id,families,**kw):
 row=c.execute("SELECT detector_contract FROM proposed_operations WHERE proposal_id=?",(proposal_id,)).fetchone()
 if not row: raise ValueError('proposal not found')
 if not families:
  d={'validation_state':'ADDITIONAL_EVIDENCE_REQUIRED','additional_evidence_manifest':{'missing_evidence_families':['independent attribution family'],'why_required':'V1 proof gate needs retained independent corroboration','provider_required':False,'authorization_required':True}}
 else:
  d=build_decision(kw.pop('candidate_id',proposal_id),kw.pop('proposed_operation_id',proposal_id),row[0],families,**kw)
 raw=json.dumps(d,sort_keys=True,separators=(',',':')); key=sha256(raw.encode()).hexdigest();d['decision_id']=key;ensure_schema(c);c.execute('BEGIN IMMEDIATE');c.execute("INSERT OR IGNORE INTO manual_attribution_decisions VALUES(?,?,?,?)",(key,proposal_id,raw,0));c.execute("UPDATE proposed_operations SET validation_state=?,latest_v1_decision_ref=? WHERE proposal_id=?",(d.get('validation_state',d.get('attribution_state')),key,proposal_id));c.commit();return d
def promote(c,proposal_id):
 raise RuntimeError('manual workflow state cannot persist canonical membership')
