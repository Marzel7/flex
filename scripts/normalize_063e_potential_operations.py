#!/usr/bin/env python3
"""Mark only the decomposed parent provenance-only and emit the audit record."""
from __future__ import annotations
import hashlib,json,sqlite3,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; DB=ROOT/'database/wt_ops_v2.db'; OUT=ROOT/'docs/agent_handoff/p3r/v2/p3r-v2-2dec1d40604c1f7c08c8/063e_child_qualification/potential_operations_normalization/063e_potential_operations_deduplication.v1.json'; PARENT='p3r-v2-063e24a2def354f23ec5'; LEGACY='P3R_063E_B65C_LEGACY'
conn=sqlite3.connect(DB); now=int(time.time())
try:
 conn.execute("UPDATE potential_operation_workflows SET workflow_status='DECOMPOSED_PARENT',latest_verdict='DECOMPOSED_DISCOVERY_FAMILY',next_action='Provenance only: 32 confirmed current members and 9 paused legacy members are represented separately.',updated_at=? WHERE candidate_id=?",(now,PARENT)); conn.commit()
 child=conn.execute("SELECT workflow_status,member_mints_json FROM potential_operation_child_candidates WHERE child_id=?",(LEGACY,)).fetchone(); result={'schema_version':'063E_POTENTIAL_OPERATIONS_DEDUPLICATION.v1','normalized_at':now,'parent_candidate_id':PARENT,'parent_workflow_status':'DECOMPOSED_PARENT','legacy_child_id':LEGACY,'legacy_workflow_status':child[0],'legacy_member_count':len(json.loads(child[1])),'confirmed_operator_id':'d8ee4d7a-fcd6-5a5b-b897-24f6ab56e334','confirmed_member_count':32}; OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n'); print(json.dumps({'artifact':str(OUT),'sha256':hashlib.sha256(OUT.read_bytes()).hexdigest()},sort_keys=True))
finally: conn.close()
