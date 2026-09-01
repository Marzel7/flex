#!/usr/bin/env python3
"""Read-only creator-concentration qualification for current Potential Operations."""
from __future__ import annotations
import json, math, sqlite3, time
from collections import Counter, defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'database/wt_ops_v2.db'
MEMBERSHIP=ROOT/'docs/agent_handoff/p3r/v2/p3r-v2-2dec1d40604c1f7c08c8/p3r_v2_candidate_membership.v1.json'
SNAPSHOT=ROOT/'docs/audits/potential_route_activity_snapshot_v2/candidate_census.json'
OUT=ROOT/'docs/audits/potential_operations_multi_token_creator_analysis.v1.json'

def percentile(values, p):
    values=sorted(values)
    if not values:return None
    i=(len(values)-1)*p; lo,hi=math.floor(i),math.ceil(i)
    return values[lo]+(values[hi]-values[lo])*(i-lo)

def state(a24,a7,a30):
    return 'VERY_ACTIVE' if a24>=3 else 'ACTIVE' if a24 or a7 else 'COOLING' if a30 else 'DORMANT'

def main():
    snap={x['candidate_id']:x for x in json.loads(SNAPSHOT.read_text())}
    family={x['candidate_id']:x for x in json.loads(MEMBERSHIP.read_text())['families'] if x['candidate_id'] in snap}
    assert len(snap)==62, 'unexpected current candidate population'
    c=sqlite3.connect(f'file:{DB}?mode=ro',uri=True); c.row_factory=sqlite3.Row; c.execute('PRAGMA query_only=ON')
    # Queue.creator is canonical; selected-edge wallet fills a documented
    # retained creator field only where the queue creator is absent.
    records={r['mint']:dict(r) for r in c.execute("SELECT mint,creator,COALESCE(create_anchor_block_time,funder_block_time,completed_at) AS launched_at FROM wt_walkback_queue")}
    edge={r['mint']:r['wallet'] for r in c.execute("SELECT mint,wallet FROM wt_walkback_edge_candidates WHERE selection_status='SELECTED' AND hop_depth=1")}
    for mint,row in records.items(): row['creator']=row['creator'] or edge.get(mint)
    global_by_creator=defaultdict(dict)
    for mint,row in records.items():
        if row['creator']: global_by_creator[row['creator']][mint]=row['launched_at']
    global_counts={k:len(v) for k,v in global_by_creator.items()}
    counts=list(global_counts.values()); serious=max(6,math.ceil(percentile(counts,.90) or 6)); extreme=max(21,math.ceil(percentile(counts,.99) or 21))
    def creator_class(n):
        return 'SINGLE_TOKEN' if n==1 else 'LOW_REPEAT' if n<=3 else 'MULTI_TOKEN' if n<serious else 'EXTREME_MULTI_TOKEN' if n>=extreme else 'HEAVY_MULTI_TOKEN'
    now=int(time.time()); candidates=[]
    for cid in sorted(snap, key=lambda x:snap[x]['current_attention_rank_v2']):
        mints=family.get(cid,{}).get('mints',[]); creators=[records.get(m,{}).get('creator') for m in mints]; known=[x for x in creators if x]
        per=Counter(known); serious_creators={x for x in per if global_counts[x]>=serious}; extreme_creators={x for x in per if global_counts[x]>=extreme}
        kept=[m for m in mints if records.get(m,{}).get('creator') not in serious_creators]
        kept_creators={records.get(m,{}).get('creator') for m in kept if records.get(m,{}).get('creator')}
        times=[records.get(m,{}).get('launched_at') for m in mints if records.get(m,{}).get('launched_at')]
        ktimes=[records.get(m,{}).get('launched_at') for m in kept if records.get(m,{}).get('launched_at')]
        windows=lambda xs:[sum(t>now-d*86400 for t in xs) for d in (1,7,30)]
        a=windows(times); ka=windows(ktimes); n=len(mints); shares=sorted((v/n for v in per.values()),reverse=True)
        serious_share=sum(per[x] for x in serious_creators)/n if n else 0
        independent=1-serious_share
        risk='INSUFFICIENT_DATA' if not n or len(known)<n else 'CREATOR_DOMINATED' if serious_share>=.8 or len(kept)<2 else 'HEAVILY_CREATOR_DRIVEN' if serious_share>=.5 else 'PARTIALLY_CREATOR_DRIVEN' if serious_share>=.2 else 'ROBUST_TO_MULTI_CREATOR_FILTER'
        candidates.append({'candidate_id':cid,'current_attention_rank':snap[cid]['current_attention_rank_v2'],'activity_state':state(*[snap[cid]['activity'][f'matched_routes_{d}'] for d in ('24h','7d','30d')]),'member_mints':n,'creator_coverage':len(known)/n if n else 0,'distinct_creators':len(per),'single_token_creators':sum(global_counts[x]==1 for x in per),'repeat_creators':sum(global_counts[x]>1 for x in per),'serious_multi_token_creators':len(serious_creators),'extreme_creators':len(extreme_creators),'serious_creator_member_share':serious_share,'independent_creator_support_members':len(kept),'independent_creator_support_share':independent,'largest_creator_share':shares[0] if shares else None,'top_3_creator_share':sum(shares[:3]),'top_5_creator_share':sum(shares[:5]),'distinct_creator_ratio':len(per)/n if n else 0,'max_global_creator_token_count':max((global_counts[x] for x in per),default=None),'median_global_creator_token_count':percentile([global_counts[x] for x in per],.5),'p90_global_creator_token_count':percentile([global_counts[x] for x in per],.9),'original_activity':dict(zip(('24h','7d','30d'),a)),'creator_filtered_activity':dict(zip(('24h','7d','30d'),ka)),'surviving_member_mints':len(kept),'surviving_distinct_creators':len(kept_creators),'creator_risk_classification':risk})
    penalty={'ROBUST_TO_MULTI_CREATOR_FILTER':0,'PARTIALLY_CREATOR_DRIVEN':1,'HEAVILY_CREATOR_DRIVEN':2,'CREATOR_DOMINATED':3,'INSUFFICIENT_DATA':4}
    adjusted=sorted(candidates,key=lambda x:(penalty[x['creator_risk_classification']],x['current_attention_rank'],x['candidate_id']))
    for rank,x in enumerate(adjusted,1): x['creator_adjusted_rank']=rank; x['rank_delta']=x['current_attention_rank']-rank
    c.close()
    dist={'1':sum(n==1 for n in counts),'2':sum(n==2 for n in counts),'3':sum(n==3 for n in counts),'4-5':sum(4<=n<=5 for n in counts),'6-10':sum(6<=n<=10 for n in counts),'11-20':sum(11<=n<=20 for n in counts),'>20':sum(n>20 for n in counts),'percentiles':{k:percentile(counts,p) for k,p in {'median':.5,'p75':.75,'p90':.9,'p95':.95,'p99':.99}.items()},'max':max(counts)}
    payload={'schema_version':'potential_operations_multi_token_creator_analysis.v1','verdict':'POTENTIAL_OPERATIONS_MULTI_TOKEN_CREATOR_ANALYSIS_COMPLETE','authoritative_creator_sources':['wt_walkback_queue.creator (canonical creator)','wt_walkback_edge_candidates.wallet at selected hop 1 only when queue.creator is absent'],'population':{'candidates':62,'global_creator_population':len(counts),'global_launch_universe':'wt_walkback_queue distinct mint rows'},'creator_token_count_distribution':dist,'creator_class_thresholds':{'SINGLE_TOKEN':1,'LOW_REPEAT':'2-3','MULTI_TOKEN':f'4-{serious-1}','HEAVY_MULTI_TOKEN':f'{serious}-{extreme-1}','EXTREME_MULTI_TOKEN':f'>={extreme}','SERIOUS_MULTI_TOKEN_CREATOR':f'>={serious} (empirical p90, floor 6)'},'candidate_risk_census':dict(Counter(x['creator_risk_classification'] for x in candidates)),'candidates':candidates,'focus_next':candidates[0],'wsol':next(x for x in candidates if x['candidate_id']=='p3r-v2-c357da9d0d4d560311e4'),'eight_hop':next(x for x in candidates if x['candidate_id']=='p3r-v2-dc4953db7adb853337c4'),'top_20':candidates[:20],'biggest_risers':sorted(candidates,key=lambda x:x['rank_delta'],reverse=True)[:10],'biggest_fallers':sorted(candidates,key=lambda x:x['rank_delta'])[:10],'would_exclude_from_operation_priority':[x['candidate_id'] for x in candidates if x['creator_risk_classification']=='CREATOR_DOMINATED'],'top_creator_diverse_live_operations':[x['candidate_id'] for x in candidates if x['creator_risk_classification']=='ROBUST_TO_MULTI_CREATOR_FILTER' and x['activity_state'] in {'VERY_ACTIVE','ACTIVE'}][:20],'read_only_verification':{'real_db_writes':0,'source_table_writes':0,'living_publications':0,'ranking_writes':0,'promotions':0,'detector_changes':0}}
    OUT.write_text(json.dumps(payload,indent=2,sort_keys=True)+'\n')
    print(json.dumps({'output':str(OUT),'census':payload['candidate_risk_census'],'threshold':serious},indent=2))
if __name__=='__main__': main()
