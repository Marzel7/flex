#!/usr/bin/env python3
"""Offline deterministic replay of the independently qualified 6437 census."""
import hashlib,json,time,sys
from pathlib import Path
R=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(R)); A=R/'docs/audits'
from src.ops.direct_10k_creator_provisioning import detect_direct_10k_creator_provisioning
def rows(p): return [json.loads(x) for x in (A/p).read_text().splitlines() if x]
def main():
 edges=rows('potential_operations_6437_funder_creator_edges.v1.jsonl'); shapes={x['mint']:x for x in rows('potential_operations_6437_defining_transaction_shapes.v1.jsonl')}
 q='QVtWcAX3R7Cr51VhAxFSYntoCAmTQzK8Hf4R1TrKNQ4'; out=[]; started=time.perf_counter()
 for e in edges:
  cohort='STRICT' if e['state']=='PROVEN_ASSOCIATED_CREATOR_10K' else ('QVtW' if e['funder']==q else 'ALTERNATE')
  s=shapes[e['mint']]; ev={'mint':e['mint'],'creator':e['creator'],'direct_funder':e['funder'],'defining_signature':e['signature'],'transfer_source':e['funder'],'transfer_destination':e['destination'],'transfer_amount_lamports':10000,'launch_coupled':True,'fee_payer':s['fee_payer'],'signers':[e['funder']]*s['signers'],'intermediary_route':cohort=='QVtW'}
  out.append({'mint':e['mint'],'cohort':cohort,**detect_direct_10k_creator_provisioning(ev)})
 assert {c:sum(x['cohort']==c for x in out) for c in ['STRICT','QVtW','ALTERNATE']}=={'STRICT':84,'QVtW':6,'ALTERNATE':3}
 payload={'detector':'DIRECT_10K_CREATOR_PROVISIONING','results':out,'elapsed_ms':(time.perf_counter()-started)*1000}
 payload['digest']=hashlib.sha256(json.dumps(out,sort_keys=True).encode()).hexdigest()
 (A/'direct_10k_creator_provisioning_shadow_qualification.v1.json').write_text(json.dumps(payload,indent=2,sort_keys=True)+'\n')
 print({k:sum(x['cohort']==k and x['result']=='UNIQUE_MATCH' for x in out) for k in ['STRICT','QVtW','ALTERNATE']},payload['digest'])
if __name__=='__main__':main()
