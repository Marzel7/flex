#!/usr/bin/env python3
"""Offline retained-evidence replay; performs no provider or DB writes."""
import hashlib,json
from pathlib import Path
from src.ops.direct_10k_creator_provisioning import detect_direct_10k_creator_provisioning
R=Path(__file__).resolve().parents[1]; A=R/'docs/audits'
Q='QVtWcAX3R7Cr51VhAxFSYntoCAmTQzK8Hf4R1TrKNQ4'
def lines(p): return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
def transfers(tx):
 out=[]
 for ix in ((tx.get('transaction') or {}).get('message') or {}).get('instructions',[]):
  p=ix.get('parsed') if isinstance(ix,dict) else None; i=p.get('info',{}) if isinstance(p,dict) else {}
  if isinstance(i,dict) and isinstance(p,dict) and p.get('type')=='transfer' and 'lamports' in i: out.append((i.get('source'),i.get('destination'),int(i['lamports'])))
 return out
def main():
 edges=lines(A/'potential_operations_6437_funder_creator_edges.v1.jsonl'); cache={x['signature']:x for x in lines(A/'direct_10k_fee_cu_transaction_cache.v1.jsonl')}; rows=[]
 for e in edges:
  cohort='STRICT' if e['state']=='PROVEN_ASSOCIATED_CREATOR_10K' else ('QVtW' if e['funder']==Q else 'ALTERNATE')
  ts=transfers(cache[e['signature']]['provider_result']); creator=e['creator']; direct=e['funder']
  creator_flows=[x for x in ts if x[1]==creator]
  if cohort=='QVtW':
   flow=next(x for x in creator_flows if x[0]!=direct); inter=True
  elif cohort=='STRICT':
   flow=next((x for x in creator_flows if x[0]==direct), creator_flows[0]); inter=False
  else:
   flow=next(x for x in ts if x[0]==direct and x[1]==e['destination']); inter=False
  ev={'mint':e['mint'],'creator':creator,'direct_funder':direct,'defining_signature':e['signature'],'transfer_source':flow[0],'transfer_destination':flow[1],'transfer_amount_lamports':flow[2],'launch_coupled':True,'intermediary_route':inter}
  d=detect_direct_10k_creator_provisioning(ev); rows.append({'mint':e['mint'],'population':cohort,**ev,'detector_result':d['result'],'reason':d['reason'],'provenance':{'edges':'potential_operations_6437_funder_creator_edges.v1.jsonl','raw':'direct_10k_fee_cu_transaction_cache.v1.jsonl'}})
 payload={'schema_version':'direct-10k-detector-results.v2','supersedes':'direct_10k_creator_provisioning_shadow_qualification.v1.json','reason_for_supersession':'v1 lacked resolved QVtW transfer source/destination','rows':sorted(rows,key=lambda x:x['mint'])}; payload['digest']=hashlib.sha256(json.dumps(payload['rows'],sort_keys=True,separators=(',',':')).encode()).hexdigest(); (A/'direct_10k_creator_provisioning_detector_results.v2.json').write_text(json.dumps(payload,indent=2,sort_keys=True)+'\n'); print(payload['digest'])
if __name__=='__main__': main()
