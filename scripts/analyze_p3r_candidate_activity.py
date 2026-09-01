#!/usr/bin/env python3
"""Read-only activity and recency analysis for frozen P3R novel candidates."""
import hashlib
import json
import math
import os
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/tmp/p3r-clean-20260824T092959Z")
INPUT = ROOT / "enrichment/p3r_novel_candidate_enrichment_input.v1.json"
MEMBERSHIP = ROOT / "behavioural_corpus/p3r_candidate_operational_family_membership.v1.json"
ALT = ROOT / "enrichment/p3r_novel_candidate_alternative_recurrence.v1.json"
ATOMIC = ROOT / "enrichment/p3r_strong_alternative_atomic_recurrence.v1.json"
ADDRESS = ROOT / "enrichment/p3r_atomic_strong_address_blind.v1.json"
OUT = ROOT / "activity"
DB = Path("database/wt_ops_v2.db")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def utc(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z") if ts else None


def quantile(values, p):
    if not values:
        return None
    values = sorted(values)
    if len(values) == 1:
        return values[0]
    x = (len(values) - 1) * p
    lo, hi = int(math.floor(x)), int(math.ceil(x))
    return values[lo] + (values[hi] - values[lo]) * (x - lo)


def json_write(name, payload):
    path = OUT / name
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")
    return {"path": str(path), "sha256": digest(path)}


def max_window(times, seconds):
    best = left = 0
    for right, value in enumerate(times):
        while value - times[left] > seconds:
            left += 1
        best = max(best, right - left + 1)
    return best


def main():
    OUT.mkdir(exist_ok=True)
    code_digest = digest(__file__)
    frozen = json.loads(INPUT.read_text())
    full_membership = json.loads(MEMBERSHIP.read_text())
    allowed = {x["candidate_id"]: x["mints"] for x in frozen["memberships"]}
    candidates = {x["candidate_id"]: x for x in full_membership["candidates"] if x["candidate_id"] in allowed}
    assert len(allowed) == len(candidates) == 114
    all_mints = {m for ms in allowed.values() for m in ms}
    assert len(all_mints) == 1185
    alt = {x["candidate_id"]: x["classification"] for x in json.loads(ALT.read_text())}
    atomic = {x["candidate_id"]: x["classification"] for x in json.loads(ATOMIC.read_text())}
    address = {x["candidate_id"]: x["classification"] for x in json.loads(ADDRESS.read_text())}

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.execute("CREATE TEMP TABLE cohort (mint TEXT PRIMARY KEY)")
    con.executemany("INSERT INTO cohort VALUES (?)", ((m,) for m in all_mints))
    source_rows = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name IN ('wt_watchtower_launches','wt_creator_birth_launch','wt_token_lifecycle','wt_walkback_edge_candidates') ORDER BY name").fetchall()
    source_schema_digest = hashlib.sha256("\n".join(r[0] for r in source_rows).encode()).hexdigest()
    launch = dict(con.execute("SELECT w.mint, MIN(w.create_time) FROM wt_watchtower_launches w JOIN cohort c ON c.mint=w.mint WHERE w.create_time IS NOT NULL GROUP BY w.mint"))
    creator_birth = dict(con.execute("SELECT b.token_mint, MIN(b.launched_at) FROM wt_creator_birth_launch b JOIN cohort c ON c.mint=b.token_mint WHERE b.launched_at IS NOT NULL GROUP BY b.token_mint"))
    lifecycle = dict(con.execute("SELECT l.mint, l.launched_at FROM wt_token_lifecycle l JOIN cohort c ON c.mint=l.mint WHERE l.launched_at IS NOT NULL"))
    edge = dict(con.execute("SELECT e.mint, MIN(e.block_time) FROM wt_walkback_edge_candidates e JOIN cohort c ON c.mint=e.mint WHERE e.selection_status='SELECTED' AND e.block_time IS NOT NULL GROUP BY e.mint"))
    highwaters = dict(con.execute("SELECT 'wt_walkback_edge_candidates', MAX(rowid) FROM wt_walkback_edge_candidates UNION ALL SELECT 'wt_watchtower_launches', MAX(rowid) FROM wt_watchtower_launches UNION ALL SELECT 'wt_token_lifecycle', MAX(rowid) FROM wt_token_lifecycle"))
    con.close()
    # True launch fields were checked first. No candidate has them; selected edge is an explicit activity proxy.
    chronology = []
    per_candidate = defaultdict(list)
    source_counts = Counter()
    for cid, mints in allowed.items():
        for mint in mints:
            if mint in launch:
                ts, source = launch[mint], "wt_watchtower_launches.create_time"
            elif mint in creator_birth:
                ts, source = creator_birth[mint], "wt_creator_birth_launch.launched_at"
            elif mint in lifecycle:
                ts, source = lifecycle[mint], "wt_token_lifecycle.launched_at"
            elif mint in edge:
                ts, source = edge[mint], "wt_walkback_edge_candidates.MIN(selected.block_time)"
            else:
                ts, source = None, None
            source_counts[source or "MISSING"] += 1
            row = {"candidate_id": cid, "mint": mint, "activity_timestamp": ts, "activity_timestamp_utc": utc(ts), "timestamp_source": source, "provenance": "read-only local SQLite snapshot", "missingness": None if ts else "NO_QUALIFIED_LOCAL_TIMESTAMP"}
            chronology.append(row)
            if ts:
                per_candidate[cid].append(ts)
    chronology.sort(key=lambda x: (x["candidate_id"], x["activity_timestamp"] is None, x["activity_timestamp"] or 0, x["mint"]))
    chronology_path = OUT / "p3r_114_candidate_launch_chronology.v1.jsonl"
    chronology_path.write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in chronology))
    cutoff = max(edge.values())
    thresholds = {
        "VERY_HIGH_ACTIVITY": "coverage >= 0.80, last_7d >= 7, and max_24h >= 2",
        "HIGH_ACTIVITY": "coverage >= 0.80, last_7d >= 3, and max_24h >= 1",
        "REGULAR_ACTIVITY": "coverage >= 0.80 and last_30d >= 2",
        "LOW_ACTIVITY": "coverage >= 0.80 but does not meet regular threshold",
        "DORMANT": "coverage >= 0.80 and no activity in final 30 days of local evidence",
        "ACTIVITY_INSUFFICIENT_EVIDENCE": "coverage < 0.80",
    }
    metrics = []
    for cid in sorted(allowed):
        times = sorted(per_candidate[cid]); n = len(allowed[cid]); coverage = len(times)/n
        gaps = [b-a for a,b in zip(times, times[1:])]
        days = Counter(t//86400 for t in times)
        recent = {f"last_{d}d": sum(t > cutoff-d*86400 for t in times) for d in (1,3,7,14,30)}
        active_days = len(days)
        span = (times[-1]-times[0]) if len(times)>1 else 0
        if coverage < .8: cls = "ACTIVITY_INSUFFICIENT_EVIDENCE"
        elif recent["last_30d"] == 0: cls = "DORMANT"
        elif recent["last_7d"] >= 7 and max_window(times,86400) >= 2: cls = "VERY_HIGH_ACTIVITY"
        elif recent["last_7d"] >= 3 and max_window(times,86400) >= 1: cls = "HIGH_ACTIVITY"
        elif recent["last_30d"] >= 2: cls = "REGULAR_ACTIVITY"
        else: cls = "LOW_ACTIVITY"
        # Recent trend compares the final and preceding 7-day windows where both fit the dataset.
        prev7=sum(cutoff-14*86400 < t <= cutoff-7*86400 for t in times)
        trend = "ACCELERATING" if recent['last_7d'] > prev7 else "SLOWING" if recent['last_7d'] < prev7 else "STABLE"
        m = {"candidate_id": cid, "historical_launches": n, "timestamped_launches": len(times), "timestamp_coverage": coverage, "first_observed_activity": utc(times[0]) if times else None, "most_recent_observed_activity": utc(times[-1]) if times else None, "activity_span_seconds": span, "active_days": active_days, "launches_per_active_day": len(times)/active_days if active_days else None, "launches_per_calendar_day": len(times)/(span/86400+1) if times else None, "median_inter_launch_gap_seconds": quantile(gaps,.5), "mean_inter_launch_gap_seconds": statistics.mean(gaps) if gaps else None, "p25_inter_launch_gap_seconds": quantile(gaps,.25), "p75_inter_launch_gap_seconds": quantile(gaps,.75), "minimum_gap_seconds": min(gaps) if gaps else None, "maximum_gap_seconds": max(gaps) if gaps else None, "longest_inactivity_gap_seconds": max(gaps) if gaps else None, "max_1h": max_window(times,3600), "max_6h": max_window(times,21600), "max_24h": max_window(times,86400), "max_3d": max_window(times,259200), "days_ge_2": sum(v>=2 for v in days.values()), "days_ge_3": sum(v>=3 for v in days.values()), "days_ge_5": sum(v>=5 for v in days.values()), "days_ge_10": sum(v>=10 for v in days.values()), "pct_launches_multi_day": sum(v for v in days.values() if v>=2)/len(times) if times else None, "typical_launches_active_day": quantile(list(days.values()),.5), **recent, "time_since_recent_seconds": cutoff-times[-1] if times else None, "recent_active_days_7d": len({t//86400 for t in times if t>cutoff-7*86400}), "recent_trend": trend, "activity_class": cls}
        metrics.append(m)
    cls_counts = Counter(m["activity_class"] for m in metrics)
    watch_now = [m for m in metrics if m["activity_class"] in {"VERY_HIGH_ACTIVITY","HIGH_ACTIVITY"}]
    watch_later = [m for m in metrics if m["activity_class"] in {"REGULAR_ACTIVITY","LOW_ACTIVITY"}]
    dormant = [m for m in metrics if m["activity_class"]=="DORMANT"]
    unknown = [m for m in metrics if m["activity_class"]=="ACTIVITY_INSUFFICIENT_EVIDENCE"]
    matrix=[]
    byid={x['candidate_id']:x for x in metrics}
    for cid in sorted(allowed):
        m=byid[cid]
        matrix.append({"candidate_id":cid,"activity_class":m["activity_class"],"behavioural_strength":candidates[cid]["strength"],"alternative_recurrence":alt.get(cid,"NOT_ENRICHED"),"atomic_recurrence":atomic.get(cid,"NOT_ENRICHED"),"address_blind":address.get(cid,"NOT_PROVEN"),"immediate_cohort":"WATCH_NOW" if cid in {x['candidate_id'] for x in watch_now} else "WATCH_LATER" if cid in {x['candidate_id'] for x in watch_later} else "DORMANT_RETAINED" if cid in {x['candidate_id'] for x in dormant} else "ACTIVITY_UNKNOWN"})
    observation=[]
    for x in watch_now:
        rate=x['last_7d']/7
        observation.append({"candidate_id":x['candidate_id'],"historical_recent_daily_rate":rate,"descriptive_expected_opportunities_1d":rate,"descriptive_expected_opportunities_3d":rate*3,"descriptive_expected_opportunities_7d":rate*7})
    bindings={"frozen_enrichment_input_manifest":str(INPUT),"frozen_enrichment_input_manifest_sha256":digest(INPUT),"frozen_membership_digest_claim":"cfbed26959c0956e7200a614462d9d604572e54e352a2d4a5de8341e1f22bf16","analysis_code_sha256":code_digest,"source_database":{"path":str(DB),"bytes":DB.stat().st_size,"schema_sha256":source_schema_digest,"highwaters":highwaters,"read_only":True},"evidence_cutoff_utc":utc(cutoff),"timestamp_source_counts":source_counts}
    contract={"schema_version":"v1","bindings":bindings,"canonical_activity_timestamp":{"field":"MIN(wt_walkback_edge_candidates.block_time) where selection_status=SELECTED","semantics":"earliest retained selected Walkback attribution/lineage evidence; observed-activity proxy, not asserted token birth","unit":"epoch seconds","coverage":len(edge),"appropriate_for":"relative activity and recency inside the scoped Walkback-observed population","limitations":"not an independently qualified token creation timestamp"},"rejected_or_unavailable_sources":[{"field":"wt_watchtower_launches.create_time","coverage":len(launch),"semantics":"create observation event time"},{"field":"wt_creator_birth_launch.launched_at","coverage":len(creator_birth),"semantics":"token CREATE block time"},{"field":"wt_token_lifecycle.launched_at","coverage":len(lifecycle),"semantics":"generic lifecycle launch time"}]}
    distribution={"bindings":bindings,"thresholds":thresholds,"empirical":{"launches_per_active_day":{"p25":quantile([x['launches_per_active_day'] for x in metrics],.25),"p50":quantile([x['launches_per_active_day'] for x in metrics],.5),"p75":quantile([x['launches_per_active_day'] for x in metrics],.75)},"median_gap_seconds":{"p25":quantile([x['median_inter_launch_gap_seconds'] for x in metrics if x['median_inter_launch_gap_seconds'] is not None],.25),"p50":quantile([x['median_inter_launch_gap_seconds'] for x in metrics if x['median_inter_launch_gap_seconds'] is not None],.5),"p75":quantile([x['median_inter_launch_gap_seconds'] for x in metrics if x['median_inter_launch_gap_seconds'] is not None],.75)},"max_24h":{"p25":quantile([x['max_24h'] for x in metrics],.25),"p50":quantile([x['max_24h'] for x in metrics],.5),"p75":quantile([x['max_24h'] for x in metrics],.75)},"last_7d":{"p25":quantile([x['last_7d'] for x in metrics],.25),"p50":quantile([x['last_7d'] for x in metrics],.5),"p75":quantile([x['last_7d'] for x in metrics],.75)}}}
    outputs={}
    outputs['timestamp_contract']=json_write('p3r_candidate_activity_timestamp_contract.v1.json',contract)
    outputs['chronology']={"path":str(chronology_path),"sha256":digest(chronology_path)}
    outputs['metrics']=json_write('p3r_114_candidate_activity_metrics.v1.json',{"bindings":bindings,"metrics":metrics})
    outputs['distribution']=json_write('p3r_114_candidate_activity_distribution.v1.json',distribution)
    outputs['classification']=json_write('p3r_candidate_activity_classification.v1.json',{"bindings":bindings,"thresholds":thresholds,"counts":cls_counts,"candidates":[{"candidate_id":x['candidate_id'],"activity_class":x['activity_class']} for x in metrics]})
    outputs['matrix']=json_write('p3r_candidate_activity_behaviour_matrix.v1.json',{"bindings":bindings,"matrix":matrix})
    outputs['watch_now']=json_write('p3r_watch_now_candidate_membership.v1.json',{"bindings":bindings,"cohort":"WATCH_NOW","members":watch_now,"observation_opportunities":observation})
    outputs['watch_later']=json_write('p3r_watch_later_candidate_membership.v1.json',{"bindings":bindings,"cohort":"WATCH_LATER","members":watch_later})
    decision={"bindings":bindings,"verdict":"P3R_ACTIVITY_PRIORITY_COHORT_QUALIFIED","recommendation":"FOCUS_V2_QUALIFICATION_ON_WATCH_NOW","rationale":"Activity proxy coverage is complete inside the frozen Walkback-observed candidate set. This is a resource-prioritization decision, not operation identity proof.","counts":cls_counts,"watch_now_ids":[x['candidate_id'] for x in watch_now],"watch_now_historical_launches":sum(x['historical_launches'] for x in watch_now),"strong_alternative_in_watch_now":sum(alt.get(x['candidate_id'])=='STRONGLY_RECURRENT' for x in watch_now),"atomic_strong_address_blind_in_watch_now":sum(address.get(x['candidate_id'])=='FULLY_ADDRESS_BLIND' for x in watch_now)}
    outputs['decision']=json_write('p3r_candidate_activity_priority_decision.v1.json',decision)
    outputs['artifact_manifest']=json_write('p3r_candidate_activity_artifact_manifest.v1.json',{"bindings":bindings,"artifacts":outputs})
    print(json.dumps({"verdict":decision['verdict'],"cutoff":utc(cutoff),"counts":cls_counts,"watch_now":decision['watch_now_ids'],"watch_now_launches":decision['watch_now_historical_launches'],"outputs":outputs},indent=2,default=dict))

if __name__ == '__main__': main()
