#!/usr/bin/env python3
"""Read-only chronological EB0.2 creator-outcome audit using local facts."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def bucket(n: int) -> str:
    return "2-4" if n <= 4 else "5-9" if n <= 9 else "10-24" if n <= 24 else "25-49" if n <= 49 else "50-99" if n <= 99 else "100+"


def chunks(items, size=300):
    for i in range(0, len(items), size): yield items[i:i+size]


def pct(n, d): return round(100*n/d, 3) if d else None


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument('--max-creators',type=int,default=400)
    args=parser.parse_args()
    db=ROOT/'database/flex_complete_database.db'; conn=sqlite3.connect(f'file:{db}?mode=ro',uri=True,timeout=5); conn.row_factory=sqlite3.Row
    now=time.time(); cutoff=now-24*3600
    recent=conn.execute("""SELECT COALESCE(NULLIF(pf_ws_creator,''),NULLIF(earliest_tx_creator,'')) creator
      FROM token_analysis INDEXED BY idx_ta_analyzed_at
      WHERE analyzed_at>=? AND source_platform='pumpfun' AND lifecycle_stage='bonding_curve' AND COALESCE(NULLIF(pf_ws_creator,''),NULLIF(earliest_tx_creator,'')) IS NOT NULL""",(cutoff,)).fetchall()
    recent_creators=sorted({str(r['creator']) for r in recent})
    recent_counts=Counter(str(r['creator']) for r in recent)
    # Pick the bounded cohort from the current birth population first.  Full
    # historical grouping is deliberately deferred until after selection so
    # this audit cannot run a multi-minute aggregation on the live database.
    candidate_creators=sorted(c for c,n in recent_counts.items() if n >= 1)
    funded=set()
    for ch in chunks(candidate_creators):
        q=','.join('?'*len(ch))
        funded.update(str(x[0]) for x in conn.execute(f'SELECT DISTINCT creator_address FROM creator_funders WHERE creator_address IN ({q})',ch))
    strata=defaultdict(list)
    for c,n in recent_counts.items():
        strata[('repeat_24h' if n > 1 else 'single_24h', 'funded' if c in funded else 'unfunded')].append(c)
    selected=[]
    per_stratum=max(1, math.ceil(args.max_creators / max(1, len(strata))))
    for key, values in sorted(strata.items()):
        selected.extend(sorted(values,key=lambda c:hashlib.sha256(c.encode()).hexdigest())[:per_stratum])
    selected=sorted(set(selected))[:max(1,args.max_creators)]
    totals=Counter(); migrated=Counter()
    launches=defaultdict(list)
    for ch in chunks(selected):
        q=','.join('?'*len(ch)); params=tuple(ch)*2
        for r in conn.execute(f"""SELECT mint, COALESCE(NULLIF(pf_ws_creator,''),NULLIF(earliest_tx_creator,'')) creator,
             COALESCE(CAST(created_at AS REAL),analyzed_at) launch_at, analyzed_at,
             CASE WHEN lifecycle_stage='migrated' OR migrated_at IS NOT NULL OR migration_tx IS NOT NULL THEN 1 ELSE 0 END migrated,
             market_cap_highest, market_cap_highest_at_ts
             FROM token_analysis WHERE (pf_ws_creator IN ({q}) OR earliest_tx_creator IN ({q}))
             AND COALESCE(CAST(created_at AS REAL),analyzed_at) > 0""",params):
            if r['creator']: launches[str(r['creator'])].append(dict(r))
    for creator, rows in launches.items():
        totals[creator]=len(rows); migrated[creator]=sum(int(row['migrated']) for row in rows)
    conn.close()
    outcomes=[]; migration_buckets=defaultdict(lambda:Counter()); size_buckets=defaultdict(lambda:Counter()); peak_values=[]
    poor_streak=defaultdict(lambda:Counter()); recent_cmp=defaultdict(lambda:Counter())
    for creator, xs in launches.items():
        xs.sort(key=lambda r:(float(r['launch_at']),str(r['mint'])))
        history=[]
        for ordinal,row in enumerate(xs,1):
            peak=float(row['market_cap_highest'] or 0)
            if peak>0: peak_values.append(peak)
            if history:
                prior=len(history); prev_mig=sum(x['migrated'] for x in history); rate=prev_mig/prior
                state='INSUFFICIENT_HISTORY' if prior<5 else 'VERY_POOR_HISTORY' if rate==0 else 'POOR_HISTORY' if rate<.05 else 'MIXED_HISTORY' if rate<.2 else 'GOOD_HISTORY'
                result={'creator':creator,'mint':row['mint'],'ordinal':ordinal,'prior_launches':prior,'prior_migration_rate':rate,'bucket':state,'migrated':int(row['migrated']),'peak_mc':peak}
                outcomes.append(result); migration_buckets[state].update([int(row['migrated'])]); size_buckets[bucket(prior)].update([int(row['migrated'])])
                streak=0
                for old in reversed(history):
                    if old['migrated']: break
                    streak+=1
                for threshold in (5,10,25):
                    if streak>=threshold: poor_streak[str(threshold)].update([int(row['migrated'])])
                recent5=sum(x['migrated'] for x in history[-5:])/min(5,prior)
                lifetime=rate
                recent_cmp['recent5_better' if recent5>lifetime else 'recent5_not_better'].update([int(row['migrated'])])
            history.append(row)
    def summary(counter):
        n=sum(counter.values()); return {'target_launches':n,'next_migration_rate_pct':pct(counter[1],n),'next_nonmigration_rate_pct':pct(counter[0],n)}
    sorted_peaks=sorted(peak_values)
    peak_cov=sum(1 for x in outcomes if x['peak_mc']>0)
    # Conservative simulation: only sufficiently observed zero-migration creators are cold.
    cold=[x for x in outcomes if x['bucket']=='VERY_POOR_HISTORY' and x['prior_launches']>=10]
    immediate=[x for x in outcomes if x not in cold]
    x78=json.loads((ROOT/'docs/audits/x78_34_qualification.json').read_text()); calls_per_full=x78['metrics']['rpc_calls']/x78['metrics']['full']
    overview={
      'milestone':'EB0.2 — Creator Historical Outcome & GMGN Feasibility Audit','mode':'READ_ONLY_SHADOW','observed_at':now,
      'baseline':{'eb0_1':str(ROOT/'docs/audits/eb0_1_birth_latency_creator_audit.json'),'git_head':'cb1fc110e105436c4baa9fe15f956628f80db3ce','worktree':'dirty pre-existing'},
      'gmgn_capability':{'integration_located':False,'credentials_located':False,'cache_table_located':False,'documented_endpoint_located':False,'bounded_experiment':{'budget':0,'used':0,'reason':'No actual configured client/endpoint/authentication tier exists; undocumented API probing is not a feasibility test.'},'verdict':'D — UNSUITABLE_OR_UNAVAILABLE'},
      'local_outcome_source':{'creator_token_relationship':'token_analysis.pf_ws_creator/earliest_tx_creator','migration':'lifecycle_stage/migrated_at/migration_tx','market_peak':'market_cap_highest','outcome_limitations':['No retained price/candlestick history table exists.','market_cap_highest is a legacy observed maximum without fixed 1m/5m/15m/1h windows.','Quick death and survival windows cannot be reconstructed honestly.']},
      'sample':{'recent_24h_creators':len(recent_creators),'eligible_repeat_creators':sum(n>=2 for n in totals.values()),'selected_creators':len(selected),'strata':{str(k):len(v) for k,v in strata.items()},'launches_with_chronology':sum(len(v) for v in launches.values()),'chronological_next_launch_targets':len(outcomes)},
      'outcomes':{'migration_bucket_next_launch':{k:summary(v) for k,v in migration_buckets.items()},'prior_sample_size_next_migration':{k:summary(v) for k,v in size_buckets.items()},'poor_streak_next_migration':{k:summary(v) for k,v in poor_streak.items()},'recent_vs_lifetime_next_migration':{k:summary(v) for k,v in recent_cmp.items()},'peak_coverage_pct':pct(peak_cov,len(outcomes)),'peak_distribution_observed':{'count':len(sorted_peaks),'p50':sorted_peaks[len(sorted_peaks)//2] if sorted_peaks else None,'p90':sorted_peaks[math.ceil(len(sorted_peaks)*.9)-1] if sorted_peaks else None,'p99':sorted_peaks[math.ceil(len(sorted_peaks)*.99)-1] if sorted_peaks else None}},
      'simulation':{'policy':'DEPRIORITIZE_ONLY zero-migration creators with >=10 strictly prior launches; capture all births and do not delete','cold_target_launches':len(cold),'immediate_target_launches':len(immediate),'immediate_pct':pct(len(immediate),len(outcomes)),'deferred_next_migrations':sum(x['migrated'] for x in cold),'false_deprioritization_migration_pct':pct(sum(x['migrated'] for x in cold),len(cold)),'full_creator_calls_per_target_proxy':round(calls_per_full,3),'cohort_full_creator_call_proxy_avoided':round(len(cold)*calls_per_full,1),'operational_hourly_load_model':'NOT_COMPUTED: this historical cohort is not a live birth rate and must not be treated as an hourly forecast','activation':'HOLD'},
      'recommended_profile_schema':['creator','launch_count','observed_outcome_count','migration_count','migration_rate','median_peak_mc','p90_peak_mc','max_peak_mc','recent_5_migration_rate','recent_10_migration_rate','last_success_at','last_launch_at','profile_version','outcome_source','updated_at'],
      'verdicts':{'creator_history_predictiveness':'C — WEAK pending outcome coverage beyond migration','poor_creator_deprioritization':'C — HIGH_FALSE_DEPRIORITIZATION_RISK until market/survival outcomes exist','successful_creator_prioritization':'D — NOT_PROVEN','helius_load_reduction':'C — LIMITED from local migration-only prior','production_activation':'HOLD'},
      'next_milestone':'EB0.3 should establish a configured, documented market-outcome source and bounded historical-window validation before any creator-performance priority policy.'
    }
    Path(ROOT/'docs/audits/eb0_2_creator_historical_outcomes.json').write_text(json.dumps(overview,sort_keys=True,indent=2)+'\n')
    Path(ROOT/'docs/audits/eb0_2_creator_outcome_sample.json').write_text(json.dumps({'sample_size':min(500,len(outcomes)),'rows':outcomes[:500]},sort_keys=True,indent=2)+'\n')
    Path(ROOT/'docs/audits/eb0_2_policy_simulation.json').write_text(json.dumps(overview['simulation'],sort_keys=True,indent=2)+'\n')
    print(json.dumps({'selected_creators':len(selected),'targets':len(outcomes),'cold':len(cold)},sort_keys=True)); return 0

if __name__=='__main__': raise SystemExit(main())
