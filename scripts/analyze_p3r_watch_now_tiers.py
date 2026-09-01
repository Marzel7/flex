#!/usr/bin/env python3
"""Bounded, read-only WATCH_NOW evidence tiering and Tier-1 deep dive."""
import hashlib, json, sqlite3
from collections import Counter, defaultdict
from pathlib import Path

ROOT=Path('/tmp/p3r-clean-20260824T092959Z'); OUT=ROOT/'activity'/'tiers'; OUT.mkdir(exist_ok=True)
WATCH=ROOT/'activity/p3r_watch_now_candidate_membership.v1.json'; BASE=ROOT/'behavioural_corpus/p3r_candidate_operational_family_membership.v1.json'
FEATURES=ROOT/'behavioural_corpus/p3r_behavioural_features.jsonl'; ALT=ROOT/'enrichment/p3r_novel_candidate_alternative_recurrence.v1.json'; ATOMIC=ROOT/'enrichment/p3r_strong_alternative_atomic_recurrence.v1.json'; ADDRESS=ROOT/'enrichment/p3r_atomic_strong_address_blind.v1.json'; DB=Path('database/wt_ops_v2.db')
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(name,obj):
 p=OUT/name; p.write_text(json.dumps(obj,sort_keys=True,indent=2)+'\n'); return {'path':str(p),'sha256':sha(p)}
def main():
 code=sha(__file__); watch=json.loads(WATCH.read_text()); wm={x['candidate_id']:x for x in watch['members']}; assert len(wm)==45
 base={x['candidate_id']:x for x in json.loads(BASE.read_text())['candidates']}
 alt={x['candidate_id']:x for x in json.loads(ALT.read_text())}; atomic={x['candidate_id']:x for x in json.loads(ATOMIC.read_text())}; address={x['candidate_id']:x for x in json.loads(ADDRESS.read_text())}
 tier1=[]; tier2=[]; tier3=[]
 for cid in sorted(wm):
  a=alt.get(cid,{}).get('classification'); at=atomic.get(cid,{}).get('classification'); ad=address.get(cid,{}).get('classification')
  tier='TIER_1_ACTIVE_MULTI_LAYER' if a=='STRONGLY_RECURRENT' and at=='ATOMIC_STRONGLY_RECURRENT' and ad=='FULLY_ADDRESS_BLIND' else 'TIER_2_ACTIVE_STRUCTURAL' if a=='STRONGLY_RECURRENT' else 'TIER_3_ACTIVE_BASE'
  row={'candidate_id':cid,'tier':tier,'historical_member_count':base[cid]['launch_count'],'activity':wm[cid],'original_strength':base[cid]['strength'],'alternative_recurrence':a or 'NOT_ENRICHED','atomic_recurrence':at or 'NOT_ENRICHED','address_blind':ad or 'NOT_PROVEN'}
  {'TIER_1_ACTIVE_MULTI_LAYER':tier1,'TIER_2_ACTIVE_STRUCTURAL':tier2,'TIER_3_ACTIVE_BASE':tier3}[tier].append(row)
 assert len(tier1)==4 and len(tier2)==14 and len(tier3)==27
 mints={r['candidate_id']:base[r['candidate_id']]['mints'] for r in tier1}; allm={m for ms in mints.values() for m in ms}
 feature={}
 for line in FEATURES.open():
  x=json.loads(line)
  if x['mint'] in allm: feature[x['mint']]=x
 con=sqlite3.connect(f'file:{DB}?mode=ro',uri=True); con.execute('CREATE TEMP TABLE cohort(mint TEXT PRIMARY KEY)'); con.executemany('INSERT INTO cohort VALUES(?)',((m,) for m in allm))
 edge_rows=con.execute("SELECT mint,hop_depth,candidate_parent,amount_lamports,mechanism,selection_status FROM wt_walkback_edge_candidates WHERE mint IN (SELECT mint FROM cohort)").fetchall()
 atomic_rows=con.execute("SELECT mint,instruction_order_json,transfer_lamports,has_create,has_sync_native,has_close FROM wt_walkback_atomic_flows WHERE mint IN (SELECT mint FROM cohort)").fetchall()
 con.close()
 byedge=defaultdict(list); byatomic=defaultdict(list)
 for r in edge_rows: byedge[r[0]].append(r)
 for r in atomic_rows: byatomic[r[0]].append(r)
 bindings={'watch_now_path':str(WATCH),'watch_now_sha256':sha(WATCH),'frozen_114_membership_sha256':'cfbed26959c0956e7200a614462d9d604572e54e352a2d4a5de8341e1f22bf16','activity_artifact_sha256':'f8d3a31db93d4429b9a7eed658b5f5dbc0890244ce59217f5d5ce2f0dd3e319f','alternative_artifact_sha256':sha(ALT),'atomic_artifact_sha256':sha(ATOMIC),'address_blind_artifact_sha256':sha(ADDRESS),'analysis_code_sha256':code,'source_database':str(DB),'source_database_read_only':True}
 funding=[]; atomic_dive=[]; rotation=[]
 for tr in tier1:
  cid=tr['candidate_id']; selected=[]; alternative=[]
  for mint in mints[cid]:
   for _,hop,parent,amount,mech,status in byedge[mint]:
    (selected if status=='SELECTED' else alternative if status=='ALTERNATIVE' else []).append((mint,hop,parent,amount,mech))
  byhop=Counter(x[1] for x in alternative); altmech=Counter(x[4] for x in alternative); altamount=Counter(x[3] for x in alternative if x[3] not in (None,0))
  funding.append({'candidate_id':cid,'original_fingerprint':{'topology':base[cid]['structural_fingerprint'],'selected_nonzero_raw_lamports':base[cid]['amount_fingerprint_lamports'],'selected_mechanism_sequence':base[cid]['mechanism_fingerprint']},'selected_edge_count':len(selected),'selected_hop_depth_structure':dict(sorted(Counter(x[1] for x in selected).items())),'selected_parent_count':len({x[2] for x in selected}),'retained_alternative_edge_count':len(alternative),'alternatives_by_hop':dict(sorted(byhop.items())),'alternative_mechanisms':dict(sorted(altmech.items())),'alternative_nonzero_amounts':dict(sorted(altamount.items())),'amount_recurrence_note':'Exact selected amount vector is frozen in the original fingerprint; zero/null amounts excluded from positive evidence.','alternative_lineage_note':'Alternatives are retained competing observations, not canonical lineage.'})
  seq=Counter(); amounts=defaultdict(list)
  for mint in mints[cid]:
   for _,order,amount,create,sync,close in byatomic[mint]:
    key=json.dumps({'instruction_order_json':json.loads(order),'has_create':bool(create),'has_sync_native':bool(sync),'has_close':bool(close)},sort_keys=True,separators=(',',':'))
    seq[key]+=1; amounts[key].append(amount)
  dom,n=(seq.most_common(1)[0] if seq else (None,0)); covered=len({r[0] for r in atomic_rows if r[0] in mints[cid]})
  atomic_dive.append({'candidate_id':cid,'atomic_covered_member_count':covered,'atomic_coverage':covered/len(mints[cid]),'dominant_observed_sequence':json.loads(dom) if dom else None,'dominant_recurrence_count':n,'dominant_recurrence_share':n/sum(seq.values()) if seq else None,'sequence_variants':[{'sequence':json.loads(k),'count':v,'non_null_transfer_lamports':sorted({a for a in amounts[k] if a is not None})} for k,v in seq.most_common()],'contradictory_atomic_observations':sum(v for k,v in seq.items() if k!=dom),'missing_atomic_member_count':len(mints[cid])-covered,'interpretation':'Observed instruction ordering only; absent instructions are not inferred.'})
  creators=[feature[m].get('creator') for m in mints[cid] if m in feature]; funders=[feature[m].get('direct_funder') for m in mints[cid] if m in feature]; parents=[p for m in mints[cid] for p in feature.get(m,{}).get('parents',[])]
  rotation.append({'candidate_id':cid,'feature_coverage':len([m for m in mints[cid] if m in feature]),'distinct_creators':len(set(creators)),'distinct_direct_funders':len(set(funders)),'distinct_upstream_parents':len(set(parents)),'recurrent_creator_addresses':[{'address':k,'members':v} for k,v in Counter(creators).most_common() if v>1],'recurrent_direct_funder_addresses':[{'address':k,'members':v} for k,v in Counter(funders).most_common() if v>1],'recurrent_parent_addresses':[{'address':k,'observations':v} for k,v in Counter(parents).most_common() if v>1],'classification':'BEHAVIOURAL_WITH_ADDRESS_ROTATION','caveat':'Address diversity supports address-blind persistence; it does not establish real-world identity.'})
 activity=[{'candidate_id':r['candidate_id'],'historical_member_count':r['historical_member_count'],'activity_proxy_caveat':'Earliest retained selected-edge time, not asserted token birth','metrics':{k:r['activity'][k] for k in ['active_days','launches_per_active_day','last_1d','last_3d','last_7d','last_30d','max_1h','max_6h','max_24h','median_inter_launch_gap_seconds','longest_inactivity_gap_seconds','most_recent_observed_activity']}} for r in tier1]
 fd={x['candidate_id']:x for x in funding}; ad={x['candidate_id']:x for x in atomic_dive}; pairs=[]
 ids=[x['candidate_id'] for x in tier1]
 for i,a in enumerate(ids):
  for b in ids[i+1:]:
   same_orig=fd[a]['original_fingerprint']==fd[b]['original_fingerprint']; same_alt=(fd[a]['alternatives_by_hop']==fd[b]['alternatives_by_hop'] and fd[a]['alternative_mechanisms']==fd[b]['alternative_mechanisms']); same_atomic=ad[a]['dominant_observed_sequence']==ad[b]['dominant_observed_sequence']
   verdict='CLEARLY_DISTINCT' if not same_orig and not same_alt and not same_atomic else 'INSUFFICIENT_EVIDENCE'
   pairs.append({'candidate_a':a,'candidate_b':b,'same_original_fingerprint':same_orig,'same_alternative_summary':same_alt,'same_dominant_atomic_sequence':same_atomic,'verdict':verdict,'note':'No pair is merged; shared individual evidence layers alone cannot prove a broader family.'})
 tiers={'bindings':bindings,'definitions':{'TIER_1_ACTIVE_MULTI_LAYER':'WATCH_NOW + STRONGLY_RECURRENT alternative + ATOMIC_STRONGLY_RECURRENT + FULLY_ADDRESS_BLIND','TIER_2_ACTIVE_STRUCTURAL':'WATCH_NOW + STRONGLY_RECURRENT alternative, but not Tier 1','TIER_3_ACTIVE_BASE':'remaining WATCH_NOW'},'counts':{'tier_1':len(tier1),'tier_2':len(tier2),'tier_3':len(tier3)},'tier_1':tier1,'tier_2':tier2,'tier_3':tier3}
 promotion={'bindings':bindings,'tier_2_to_tier_1':'Retain WATCH_NOW and existing STRONGLY_RECURRENT alternative evidence; add sufficient atomic member coverage, a recurrent observed atomic sequence under the versioned contract, and FULLY_ADDRESS_BLIND persistence. Activity alone cannot promote.','tier_3_to_tier_2':'Obtain STRONGLY_RECURRENT independent alternative-edge evidence under the existing/future versioned contract. Activity alone cannot promote.','tier_1_strengthening':{x['candidate_id']:'Prospective recurrence plus versioned background prevalence; collect more atomic observations only where current atomic coverage is incomplete.' for x in tier1}}
 policy={'bindings':bindings,'policy':{'Tier 1':'Highest prospective evidence-capture and analysis priority.','Tier 2':'Continue observation; prioritize atomic evidence.','Tier 3':'Observe activity; defer deep enrichment unless independent evidence strengthens.','WATCH_LATER / DORMANT':'Retain without immediate enrichment budget.'},'v2_recommendation':'TIER_1_FIRST','rationale':'Smallest cohort with all current evidence layers; conduct background controls alongside it before any broader conclusion.','no_production_action':True}
 outputs={}
 outputs['tiers']=write('p3r_watch_now_evidence_tiers.v1.json',tiers); outputs['tier1_membership']=write('p3r_tier1_candidate_membership.v1.json',{'bindings':bindings,'members':tier1}); outputs['funding']=write('p3r_tier1_funding_deep_dive.v1.json',{'bindings':bindings,'candidates':funding}); outputs['atomic']=write('p3r_tier1_atomic_deep_dive.v1.json',{'bindings':bindings,'candidates':atomic_dive}); outputs['activity']=write('p3r_tier1_activity_deep_dive.v1.json',{'bindings':bindings,'candidates':activity}); outputs['rotation']=write('p3r_tier1_address_rotation.v1.json',{'bindings':bindings,'candidates':rotation}); outputs['distinctiveness']=write('p3r_tier1_pairwise_distinctiveness.v1.json',{'bindings':bindings,'pairs':pairs}); outputs['promotion']=write('p3r_candidate_tier_promotion_contract.v1.json',promotion); outputs['policy']=write('p3r_immediate_observation_policy.v1.json',policy); outputs['manifest']=write('p3r_watch_now_tiering_artifact_manifest.v1.json',{'bindings':bindings,'artifacts':outputs})
 print(json.dumps({'verdict':'P3R_WATCH_NOW_TIERS_QUALIFIED','tier_counts':tiers['counts'],'tier1_ids':ids,'tier1_historical_mints':sum(x['historical_member_count'] for x in tier1),'pairwise':Counter(x['verdict'] for x in pairs),'outputs':outputs},indent=2))
if __name__=='__main__': main()
