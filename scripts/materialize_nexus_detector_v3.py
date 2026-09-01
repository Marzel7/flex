import json,sqlite3
from pathlib import Path
from src.ops.direct_10k_creator_provisioning import detect_direct_10k_creator_provisioning as detect
r=Path(__file__).resolve().parents[1]; a=r/'docs/audits'; p=json.load(open(a/'direct_10k_creator_provisioning_detector_results.v2.json'))
c=sqlite3.connect(f'file:{r}/database/wt_ops_v2.db?mode=ro',uri=True)
for raw in [json.loads(x) for x in open(a/'nexus_current_detector_raw_cache.v1.jsonl')]:
 m=raw['mint']; creator,funder=c.execute('select creator,funder_wallet from wt_walkback_queue where mint=?',(m,)).fetchone(); ts=[]
 for ix in raw['provider_result']['transaction']['message']['instructions']:
  q=ix.get('parsed') or {}; i=q.get('info') or {}
  if q.get('type')=='transfer' and 'lamports' in i: ts.append((i.get('source'),i.get('destination'),int(i['lamports'])))
 x=next(x for x in ts if x[0]==funder and x[1]==creator); e={'mint':m,'creator':creator,'direct_funder':funder,'defining_signature':raw['signature'],'transfer_source':x[0],'transfer_destination':x[1],'transfer_amount_lamports':x[2],'launch_coupled':True}; d=detect(e); p['rows'].append({'mint':m,'population':'CURRENT','detector_result':d['result'],'reason':d['reason'],**e})
p['schema_version']='direct-10k-detector-results.v3';p['supersedes']='direct_10k_creator_provisioning_detector_results.v2.json';p['rows']=sorted(p['rows'],key=lambda x:x['mint']);open(a/'direct_10k_creator_provisioning_detector_results.v3.json','w').write(json.dumps(p,indent=2,sort_keys=True)+'\n')
