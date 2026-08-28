#!/usr/bin/env python3
"""Create a compact value-level immutable Potential route-activity snapshot."""
from __future__ import annotations
import hashlib,json,sqlite3,time,sys
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.ops.p3r_v2_tiering import base_fingerprint,stable_candidate_id
from src.ops.potential_operations import rows

ROOT=Path(__file__).resolve().parents[1]; DB=ROOT/'database/wt_ops_v2.db'; OUT=ROOT/'docs/audits/potential_route_activity_snapshot_v2'
def canon(x): return json.dumps(x,sort_keys=True,separators=(',',':'))
def digest(x): return hashlib.sha256(canon(x).encode()).hexdigest()
def snapshot_digest(x): return digest({k:v for k,v in x.items() if k!='snapshot_digest'})
def build(db=DB, cutoff=None):
    cutoff=int(time.time()) if cutoff is None else int(cutoff)
    c=sqlite3.connect(f'file:{db}?mode=ro',uri=True);c.row_factory=sqlite3.Row
    try:
      hw={'queue':c.execute('select max(rowid) from wt_walkback_queue').fetchone()[0] or 0,'edges':c.execute('select max(rowid) from wt_walkback_edge_candidates').fetchone()[0] or 0,'atomic_flows':c.execute('select max(rowid) from wt_walkback_atomic_flows').fetchone()[0] or 0}
      population=rows(str(db)); candidates={r['candidate_id']:r for r in population}; queue={r['mint']:dict(r) for r in c.execute('select mint,creator,funder_wallet from wt_walkback_queue where rowid<=?',(hw['queue'],))}
      selected=defaultdict(list)
      for r in c.execute('select rowid,mint,hop_depth,mechanism,amount_lamports,block_time,selection_status,rejection_reason,last_observed_at,evidence_key from wt_walkback_edge_candidates where rowid<=? and selection_status="SELECTED" order by mint,hop_depth,rowid',(hw['edges'],)): selected[r['mint']].append(dict(r))
      routes=[]
      for mint,edges in selected.items():
        fp=base_fingerprint([(e['hop_depth'],e['mechanism'],e['amount_lamports']) for e in edges]); cid=stable_candidate_id(fp)
        if cid not in candidates: continue
        ts=min((e['block_time'] for e in edges if e['block_time'] is not None),default=None)
        routes.append({'candidate_id':cid,'mint':mint,'route_digest':digest(fp),'route_match_status':'SELECTED_FINGERPRINT_MATCH','hop_count':len(edges),'route_activity_timestamp':ts,'creator':queue.get(mint,{}).get('creator'),'direct_funder':queue.get(mint,{}).get('funder_wallet'),'edges':[{'rowid':e['rowid'],'evidence_key':e['evidence_key'],'hop':e['hop_depth'],'semantic':e['mechanism'],'amount':e['amount_lamports'],'block_time':e['block_time'],'selection_status':e['selection_status'],'rejection_reason':e['rejection_reason'],'last_observed_at':e['last_observed_at']} for e in edges]})
      census=[]
      for cid,r in sorted(candidates.items()):
        rr=[x for x in routes if x['candidate_id']==cid]; ts=[x['route_activity_timestamp'] for x in rr if x['route_activity_timestamp'] is not None]; edge=[e['block_time'] for x in rr for e in x['edges'] if e['block_time'] is not None]
        def count(v,d): return sum(cutoff-d*86400<=x<=cutoff for x in v)
        census.append({'candidate_id':cid,'display_name':r['display_descriptor'],'workflow_status':r['workflow_status'],'relationship':r['relationship_label'],'previous_attention_rank':r.get('current_attention_rank'),'priority_rank':r.get('priority_rank',999999),'fingerprint':r.get('fingerprint',{}),'fingerprint_digest':digest(r.get('fingerprint',{})),'activity':{'primary_unit':'MATCHED_ROUTES','matched_routes_total':len(rr),'matched_routes_24h':count(ts,1),'matched_routes_7d':count(ts,7),'matched_routes_30d':count(ts,30),'first_matched_route':min(ts) if ts else None,'latest_matched_route':max(ts) if ts else None,'technical_selected_edges_total':len(edge),'technical_selected_edge_timestamps_24h':count(edge,1),'technical_selected_edge_timestamps_7d':count(edge,7),'technical_selected_edge_timestamps_30d':count(edge,30)},'distinct_creators':len({x['creator'] for x in rr if x['creator']}),'distinct_direct_funders':len({x['direct_funder'] for x in rr if x['direct_funder']}),'status':'RESOLVED' if rr else 'ROUTE_ACTIVITY_UNRESOLVED'})
      # Existing ordering contract: only substitute the three activity inputs.
      census.sort(key=lambda x:(-x['activity']['matched_routes_24h'],-x['activity']['matched_routes_7d'],-x['activity']['matched_routes_30d'],-x['activity']['matched_routes_total'],x['priority_rank'],x['candidate_id']))
      for i,x in enumerate(census,1): x['current_attention_rank_v2']=i
      return {'schema_version':'POTENTIAL_ROUTE_ACTIVITY_SNAPSHOT_V2','lineage':'V2','snapshot_cutoff_epoch':cutoff,'snapshot_cutoff_utc':datetime.fromtimestamp(cutoff,timezone.utc).isoformat(),'high_waters':hw,'route_timestamp_contract':'minimum selected-edge transaction block_time per matched mint route','activity_contract':'MATCHED_ROUTES','candidate_census':census,'routes':routes}
    finally: c.close()
def main():
    value=build(); value['snapshot_digest']=snapshot_digest(value); OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'manifest.json').write_text(json.dumps({k:v for k,v in value.items() if k not in {'routes','candidate_census'}},indent=2,sort_keys=True)+'\n')
    (OUT/'candidate_census.json').write_text(json.dumps(value['candidate_census'],indent=2,sort_keys=True)+'\n')
    (OUT/'route_membership.jsonl').write_text(''.join(canon(x)+'\n' for x in value['routes']))
    print(json.dumps({'candidates':len(value['candidate_census']),'routes':len(value['routes']),'digest':value['snapshot_digest']}))
if __name__=='__main__': main()
