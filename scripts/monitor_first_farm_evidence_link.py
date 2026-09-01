#!/usr/bin/env python3
"""Read-only hourly monitor for the first organic farm-role evidence link.

It never invokes a scheduler, RPC client, classifier transport, or database write.
No output means no notification condition occurred.
"""
from __future__ import annotations
import json, sqlite3, sys
from pathlib import Path
from src.ops.unknown_funder_edge_quality import FundingObservation, classify_unknown_funder_edge

ROOT=Path(__file__).resolve().parents[1]; DB=ROOT/'database/wt_ops_v2.db'
def unknown(c):
 return c.execute('''WITH u AS (SELECT fl.funder FROM wt_farm_launches fl LEFT JOIN wt_confirmed_treasuries t ON t.treasury=fl.funder LEFT JOIN wt_discovered_subprovs sp ON sp.subprov=fl.funder WHERE t.treasury IS NULL AND sp.subprov IS NULL), p AS (SELECT funder,COUNT(*) n FROM u GROUP BY funder HAVING n>=3) SELECT COUNT(*),SUM(n) FROM p''').fetchone()
def main():
 if not DB.exists(): raise RuntimeError('MONITOR_DB_MISSING')
 c=sqlite3.connect(f'file:{DB}?mode=ro',uri=True); c.row_factory=sqlite3.Row
 if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='wt_farm_launch_evidence'").fetchone(): return 0
 row=c.execute('''SELECT e.*,r.transfer_source,r.transfer_destination,r.transfer_lamports,r.fee_payer,r.signers_json,r.route_semantics,r.provenance_digest
 FROM wt_farm_launch_evidence e JOIN wt_walkback_transaction_roles r ON r.evidence_key=e.role_evidence_key
 ORDER BY e.retained_at,e.mint LIMIT 1''').fetchone()
 if not row: return 0
 direct=row['transfer_source']==row['funder'] and row['transfer_destination']==row['creator'] and row['route_semantics']=='DIRECT'
 result,reasons=classify_unknown_funder_edge(FundingObservation(proven_funding_role=direct,launch_coupling=direct,transaction_role_consistent=direct,amount_lamports=row['transfer_lamports']))
 count=c.execute('SELECT COUNT(*) FROM wt_farm_launch_evidence WHERE mint=?',(row['mint'],)).fetchone()[0]
 repeat,rows=unknown(c)
 print(json.dumps({'FIRST_PROSPECTIVE_FARM_LINK':'PASS' if direct and count==1 else 'HOLD','FARM_LAUNCH_ID':row['mint'],'MINT':row['mint'],'FUNDER':row['funder'],'FUNDING_SIGNATURE':row['funding_signature'],'LINKED_EVIDENCE_ID':row['role_evidence_key'],'JOIN_UNAMBIGUOUS':count==1,'ROLE_EVIDENCE_PRESENT':True,'TIMING_EVIDENCE_PRESENT':row['transfer_block_time'] is not None,'CLASSIFIER_RESULT':result.value,'CLASSIFIER_REASON_CODES':sorted(reasons),'INCREMENTAL_EDGE_QUALITY_RPC_CALLS':0,'EXTRA_GETTRANSACTION_FOR_CLASSIFIER':0,'IDEMPOTENCY_RESULT':'ONE_LINK_ROW' if count==1 else 'HOLD_DUPLICATE_LINK','FILTER_ACTIVATED':False,'UNKNOWN_REPEAT_FUNDER_COUNT':repeat,'UNKNOWN_FARM_ROW_COUNT':rows,'SQLITE_BUSY_COUNT':0,'SQLITE_LOCKED_COUNT':0},sort_keys=True))
 return 0
if __name__=='__main__':
 try: raise SystemExit(main())
 except Exception as e: print(json.dumps({'MONITOR_ERROR':type(e).__name__,'detail':str(e)})); raise SystemExit(1)
