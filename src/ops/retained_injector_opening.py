"""Fail-closed one-shot import of the immutable retained INJECTOR +2 evidence."""
from __future__ import annotations
import hashlib, json, sqlite3, time
from typing import Any

MINT="3yvK6WWww1moF3qx3UvHngZCHy9kRCVd9Jva8tRrpump"
MIGRATION_TIMESTAMP=1791307928
MIGRATION_SLOT=453969437
MIGRATION_SIGNATURE="mPr6Gv6Sjag8sU6bLC4m5uejoshcqVcBhs8r5uwXMrjZkXfdnffQukgs1c64LpFhvTJXN94zc2rujmQGoAp5M5X"
POOL="5sBp2gWc4bUyMzDg57YjPtDm1PBRY5jQG2LamGpxycBv"
SOURCE_COMMIT="bb9644bbd39c8f2f8d240b348acca72ce661289e"
SOURCE_BLOB="96d87bbfdd7dc2c35adfb371b72ad8590f094969"
EVIDENCE={"operation":"watchtower","mint":MINT,"migration_timestamp":MIGRATION_TIMESTAMP,"migration_slot":MIGRATION_SLOT,"migration_signature":MIGRATION_SIGNATURE,"pool":POOL,"provider":"BIRDEYE","request_window":[1791307928,1791307932],"normalizer":"STRICT_ENTRY_NORMALIZER_V2","http_status":200,"t_present":False,"t_plus1_present":False,"selected_timestamp":1791307930,"selected_mc":134293.7821862771,"entry_method":"BOUNDED_POST_MIGRATION_MC_FALLBACK","entry_exactness":"POST_MIGRATION_OFFSET_2S_OBSERVED_MC","entry_offset_seconds":2,"source_commit":SOURCE_COMMIT,"source_blob":SOURCE_BLOB}
EVIDENCE_ID=hashlib.sha256(json.dumps(EVIDENCE,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def import_retained_injector_opening(db_path:str, evidence:dict[str,Any]=EVIDENCE, *, now:int|None=None)->dict[str,Any]:
    """Import only this pinned evidence; never calls a provider or activates LIVE."""
    if evidence != EVIDENCE or hashlib.sha256(json.dumps(evidence,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=EVIDENCE_ID: raise ValueError("RETAINED_EVIDENCE_IDENTITY_MISMATCH")
    stamp=int(time.time() if now is None else now)
    with sqlite3.connect(db_path) as con:
        columns={r[1] for r in con.execute("pragma table_info(operation_monitor_facts)")}
        if "entry_offset_seconds" not in columns: con.execute("alter table operation_monitor_facts add column entry_offset_seconds integer")
        row=con.execute("select entry_status,entry_timestamp,entry_mc_usd,monitor_state from operation_monitor_facts where operation_id='watchtower' and mint=?",(MINT,)).fetchone()
        if not row: raise ValueError("INJECTOR_FACT_REQUIRED")
        if row[1] is not None or row[2] is not None or row[0]=="QUALIFIED": raise ValueError("AUTHORITATIVE_ENTRY_ALREADY_EXISTS")
        result=con.execute("update operation_monitor_facts set entry_status='QUALIFIED',entry_timestamp=?,entry_mc_usd=?,entry_method=?,entry_exactness=?,entry_offset_seconds=2,monitor_state='HISTORICAL_RECOVERY_ACQUIRING',next_observation_at=null,evidence_status='RETAINED_INJECTOR_PLUS2_IMPORTED',provenance_digest=?,updated_at=? where operation_id='watchtower' and mint=? and entry_timestamp is null and entry_mc_usd is null",(1791307930,134293.7821862771,EVIDENCE['entry_method'],EVIDENCE['entry_exactness'],EVIDENCE_ID,stamp,MINT))
        if result.rowcount != 1: raise ValueError("RETAINED_IMPORT_CONFLICT")
    return {**EVIDENCE,"evidence_id":EVIDENCE_ID,"route":"HISTORICAL_RECONSTRUCTION"}
