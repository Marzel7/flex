#!/usr/bin/env python3
"""Read-only exact selected-route matcher qualification for Potential candidates."""
from __future__ import annotations
import hashlib,json,sqlite3,time
from collections import Counter,defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; DB=ROOT/'database/wt_ops_v2.db'
MEM=ROOT/'docs/agent_handoff/p3r/v2/p3r-v2-2dec1d40604c1f7c08c8/p3r_v2_candidate_membership.v1.json'
SNAP=ROOT/'docs/audits/potential_route_activity_snapshot_v2/candidate_census.json'
OUT=ROOT/'docs/audits/potential_operations_incremental_candidate_matcher_qualification.v1.json'
def signature(c,mint):
 return tuple(c.execute("SELECT hop_depth,mechanism,amount_lamports FROM wt_walkback_edge_candidates WHERE mint=? AND selection_status='SELECTED' AND amount_lamports IS NOT NULL ORDER BY hop_depth,signature",(mint,)).fetchall())
def main():
 snap={x['candidate_id'] for x in json.load(open(SNAP))}; fam={x['candidate_id']:x for x in json.load(open(MEM))['families'] if x['candidate_id'] in snap}
 c=sqlite3.connect(f'file:{DB}?mode=ro',uri=True); c.execute('PRAGMA query_only=ON'); now=int(time.time())
 specs={}; inventory=[]
 for cid in sorted(snap):
  ms=fam.get(cid,{}).get('mints',[]); sigs=[signature(c,m) for m in ms]; counts=Counter(s for s in sigs if s); mode,n=counts.most_common(1)[0] if counts else ((),0); coverage=n/len(ms) if ms else 0
  typ='DERIVABLE_CANONICAL_SIGNATURE' if coverage==1 and mode else 'AMBIGUOUS_SIGNATURE' if mode else 'NO_MATCH_DEFINITION'
  inventory.append({'candidate_id':cid,'member_mints':len(ms),'definition_type':typ,'definition_source':'frozen member selected-route evidence','mode_coverage':coverage,'signature':mode})
  if typ=='DERIVABLE_CANONICAL_SIGNATURE': specs[cid]=mode
 groups=defaultdict(list)
 for cid,s in specs.items():groups[s].append(cid)
 collisions=[v for v in groups.values() if len(v)>1]
 qualified={cid:s for cid,s in specs.items() if len(groups[s])==1}
 all_members={m:cid for cid,x in fam.items() for m in x['mints']}; replay=[]
 for cid,s in qualified.items():
  own=[m for m,x in all_members.items() if x==cid]; rec=sum(signature(c,m)==s for m in own); wrong=sum(signature(c,m)==s for m,x in all_members.items() if x!=cid)
  replay.append({'candidate_id':cid,'members':len(own),'recovered':rec,'misses':len(own)-rec,'wrong_candidate_matches':wrong})
 recent=[r[0] for r in c.execute('SELECT mint FROM wt_walkback_queue WHERE funder_block_time>?',(now-86400,))]; assigned=[]; with_inputs=0
 for m in recent:
  s=signature(c,m); with_inputs+=bool(s); hits=[cid for cid,v in qualified.items() if v==s]
  assigned.append({'mint':m,'matches':hits})
 unique=[x for x in assigned if len(x['matches'])==1]; multi=[x for x in assigned if len(x['matches'])>1]; no=[x for x in assigned if not x['matches']]
 by=Counter(x['matches'][0] for x in unique); c.close()
 states=Counter(x['definition_type'] for x in inventory); payload={'schema_version':'potential_operations_incremental_candidate_matcher_qualification.v1','verdict':'POTENTIAL_OPERATIONS_INCREMENTAL_MATCHER_QUALIFIED_PARTIAL','candidate_count':len(snap),'definition_inventory':inventory,'qualification_counts':{'MATCHER_QUALIFIED':len(qualified),'MATCHER_PARTIAL':0,'MATCHER_AMBIGUOUS':states['AMBIGUOUS_SIGNATURE'],'MATCHER_UNAVAILABLE':states['NO_MATCH_DEFINITION']},'collision_groups':collisions,'historical_replay':replay,'negative_control':{'wrong_candidate_matches':sum(x['wrong_candidate_matches'] for x in replay)},'current_24h':{'total':len(recent),'with_matcher_inputs':with_inputs,'unique':len(unique),'multi':len(multi),'no_match':len(no),'by_candidate':dict(by),'coverage_percent':len(unique)*100/len(recent) if recent else 0},'wsol_potential_matcher_status':'MATCHER_QUALIFIED' if 'p3r-v2-c357da9d0d4d560311e4' in qualified else 'NOT_QUALIFIED','eight_hop_matcher_status':'MATCHER_QUALIFIED' if 'p3r-v2-dc4953db7adb853337c4' in qualified else 'NOT_QUALIFIED','p3r_vs_wsol':'SEPARATE: P3R active matcher is an operation contract; this matcher derives candidate routes from frozen evidence.','recommended_architecture':'B: read-only live assignment only for unique MATCHER_QUALIFIED candidates; UNKNOWN for others.','safety':{'db_writes':0,'membership_writes':0,'living_publications':0,'network_calls':0}}
 OUT.write_text(json.dumps(payload,sort_keys=True,indent=2)+'\n');print(json.dumps({'q':len(qualified),'current':payload['current_24h']},indent=2))
if __name__=='__main__':main()
