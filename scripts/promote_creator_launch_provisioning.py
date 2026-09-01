#!/usr/bin/env python3
"""One-way, idempotent promotion of the frozen strict 6437 population."""
import json,sqlite3,time,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; DB=ROOT/'database/wt_ops_v2.db'; A=ROOT/'docs/audits'
OID=str(uuid.uuid5(uuid.NAMESPACE_URL,'CREATOR_LAUNCH_PROVISIONING')); CID='p3r-v2-6437acd385e566e301a7'; now=int(time.time())
edges=[json.loads(x) for x in (A/'potential_operations_6437_funder_creator_edges.v1.jsonl').read_text().splitlines() if x]
strict=[x for x in edges if x['state']=='PROVEN_ASSOCIATED_CREATOR_10K']; assert len(strict)==84
c=sqlite3.connect(DB); c.execute('BEGIN IMMEDIATE')
try:
 c.execute("INSERT INTO operators(operator_id,status,confidence,first_seen,last_seen,summary,review_state,display_name,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(operator_id) DO UPDATE SET updated_at=excluded.updated_at",(OID,'ACTIVE','HIGH',now,now,'Direct funder sends exactly 10,000 lamports to the associated token creator in the qualified launch transaction.','REVIEWED','Creator Launch Provisioning',now,now))
 event=str(uuid.uuid5(uuid.NAMESPACE_URL,OID+':promotion:v1'))
 c.execute("INSERT OR IGNORE INTO operator_identity_events VALUES(?,?,?,?,?,?,?,?)",(event,OID,'PROMOTION',now,'codex','direct_10k_shadow_qualification','Promoted strict 84 only',json.dumps({'source_candidate_id':CID,'detector':'DIRECT_10K_CREATOR_PROVISIONING','members':84})))
 for x in strict:c.execute("INSERT OR IGNORE INTO operator_launch_membership VALUES(?,?,?,?,?)",(x['mint'],OID,CID,now,event))
 c.execute("INSERT INTO operation_registry_dispositions VALUES(?,?,?,?,?,?) ON CONFLICT(operator_id) DO UPDATE SET disposition=excluded.disposition,reason=excluded.reason,source_candidate_id=excluded.source_candidate_id,updated_at=excluded.updated_at",(OID,'ACTIVE_MANUAL','codex','Promoted from strict qualified 6437 core',CID,now))
 c.execute("INSERT OR IGNORE INTO operation_behavioural_profiles VALUES(?,?,?,?,?,?,?,?,?,?)",(str(uuid.uuid5(uuid.NAMESPACE_URL,OID+':profile:1')),OID,CID,1,'ACTIVE',json.dumps({'detector':'DIRECT_10K_CREATOR_PROVISIONING'}),json.dumps(sorted(x['mint'] for x in strict)),now,now,'codex'))
 c.commit(); print(OID,c.execute('select count(*) from operator_launch_membership where operator_id=?',(OID,)).fetchone()[0])
except: c.rollback();raise
finally:c.close()
