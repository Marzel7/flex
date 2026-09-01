#!/usr/bin/env python3
"""Provider-free interpretation and replay materialization of frozen funding facts."""
from __future__ import annotations
import hashlib,json,sqlite3,time
from collections import Counter,defaultdict
from pathlib import Path
from audit_confirmed_operation_funding_architecture import family_key
ROOT=Path('docs/audits'); C=ROOT/'funding_architecture_unassigned_census.v1.json'; D=ROOT/'funding_architecture_potential_discovery.v1.json'; F=ROOT/'funding_architecture_unassigned_cluster_freeze.v1.json'
def digest(x): return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def semantic(r):
 x=json.loads(family_key(r));x['sequence']=[v for v in x.get('sequence',[]) if v not in ('None:unparsed','spl-memo:unparsed','advanceNonce')];return json.dumps(x,sort_keys=True,separators=(',',':'))
def main():
 census=json.load(open(C)); facts=census['normalized_facts']; clusters=defaultdict(list); sem=defaultdict(list)
 for r in facts: clusters[family_key(r)].append(r);sem[semantic(r)].append(r)
 db=sqlite3.connect('database/wt_ops_v2.db'); cand={m:c for _,c,m in db.execute('SELECT run_id,candidate_id,mint FROM p3r_v2_candidate_membership')};db.close(); now=int(time.time())
 freeze=[]
 for key,rs in clusters.items():
  cs={r.get('creator') for r in rs if r.get('creator')};fs={r.get('direct_funder') for r in rs if r.get('direct_funder')};ts=[r.get('launch_timestamp') or 0 for r in rs]; seq=json.loads(key).get('sequence',[]); mechanism=rs[0]['retained_mechanism']; n=len(rs)
  generic=(mechanism=='PLAIN_XFER' and [x for x in seq if x!='None:unparsed']==['transfer']) or (n>=20 and all(not r['ordered_transfer_lamports'] for r in rs) and set(seq)<= {'createAccount','createAccountWithSeed','initializeAccount','closeAccount','None:unparsed'})
  cls='GENERIC_INFRASTRUCTURE' if generic else ('DISTINCTIVE_ARCHITECTURE' if n>=3 and len(cs)>=3 and len(fs)>=3 else 'AMBIGUOUS')
  freeze.append({'family_id':'FAU_'+hashlib.sha256(key.encode()).hexdigest()[:16],'architecture_digest':hashlib.sha256(key.encode()).hexdigest()[:16],'architecture':json.loads(key),'launches':n,'creators':len(cs),'funders':len(fs),'member_mint_digest':digest(sorted(r['mint'] for r in rs)),'first_seen':min(ts),'latest':max(ts),'activity_24h':sum(t>=now-86400 for t in ts),'activity_7d':sum(t>=now-604800 for t in ts),'activity_30d':sum(t>=now-2592000 for t in ts),'candidate_ids':sorted({cand[r['mint']] for r in rs if r['mint'] in cand}),'generic_classification':cls,'semantic_digest':hashlib.sha256(semantic(rs[0]).encode()).hexdigest()[:16]})
 freeze.sort(key=lambda x:(-x['launches'],x['family_id'])); fp={'schema':'FUNDING_ARCHITECTURE_UNASSIGNED_CLUSTER_FREEZE_V1','census_digest':census['digest'],'clusters':freeze};fp['digest']=digest(fp);F.write_text(json.dumps(fp,indent=2,sort_keys=True)+'\n')
 recurrent=[x for x in freeze if x['launches']>=3 and x['creators']>=3]; high=[x for x in recurrent if x['funders']>=3]; novel=[x for x in high if x['generic_classification']=='DISTINCTIVE_ARCHITECTURE' and not x['candidate_ids']]
 disc=json.load(open(D));disc.update({'cluster_freeze_digest':fp['digest'],'generic_counts':dict(Counter(x['generic_classification'] for x in recurrent)),'raw_cluster_count':len(freeze),'semantic_cluster_count':len(sem),'raw_recurrent_count':len(recurrent),'semantic_recurrent_count':sum(len(v)>=3 and len({r.get('creator') for r in v if r.get('creator')})>=3 for v in sem.values()),'semantic_merged_clusters':len(freeze)-len(sem),'new_funding_only_clusters':novel,'top_raw_clusters':freeze[:20],'existing_families_split_by_funding':0,'existing_families_merged_by_funding':0,'UNASSIGNED_24H':sum(x['activity_24h'] for x in freeze),'FUNDING_CLUSTERED_24H':sum(x['activity_24h'] for x in recurrent),'STRONG_CANDIDATE_CLUSTERED_24H':sum(x['activity_24h'] for x in novel),'FUNDING_ARCHITECTURE_DISCOVERY_GAP':'MEANINGFUL' if novel else 'INSUFFICIENT_EVIDENCE','SHOULD_FUNDING_ARCHITECTURE_BECOME_CANONICAL_DISCOVERY_FEATURE':'YES_AS_SUPPORTING_FEATURE','recommended_integration':['CANDIDATE_CONSTRUCTION','CANDIDATE_ENRICHMENT','COLLISION_DISAMBIGUATION'],'provider_calls_interpretation':0});disc.pop('digest',None);disc['digest']=digest(disc);D.write_text(json.dumps(disc,indent=2,sort_keys=True)+'\n');print(json.dumps({'raw':len(freeze),'semantic':len(sem),'recurrent':len(recurrent),'high':len(high),'novel':len(novel),'digest':disc['digest']}))
if __name__=='__main__':main()
