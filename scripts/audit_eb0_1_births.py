#!/usr/bin/env python3
"""Read-only EB0.1 PumpPortal birth and creator-population audit."""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, math.ceil(len(ordered) * pct) - 1)], 3)


def in_chunks(values: list[str], size: int = 700):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "database/flex_complete_database.db")
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/audits/eb0_1_birth_latency_creator_audit.json")
    args = parser.parse_args()
    now = time.time(); start = now - args.hours * 3600
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    births = conn.execute(
        """SELECT mint, analyzed_at, created_at, COALESCE(NULLIF(pf_ws_creator,''), NULLIF(earliest_tx_creator,'')) creator,
                  create_tx_signature, COALESCE(watchtower_related,0) watchtower_related
             FROM token_analysis INDEXED BY idx_ta_analyzed_at
             WHERE analyzed_at >= ? AND lifecycle_stage='bonding_curve' AND source_platform='pumpfun'
             ORDER BY analyzed_at, mint""", (start,)
    ).fetchall()
    creators = sorted({str(row['creator']) for row in births if row['creator']})
    totals: Counter[str] = Counter(); funded: set[str] = set(); networked: set[str] = set(); watchtower: set[str] = set()
    for chunk in in_chunks(creators):
        qs = ",".join("?" for _ in chunk)
        for row in conn.execute(
            f"""SELECT COALESCE(NULLIF(pf_ws_creator,''), NULLIF(earliest_tx_creator,'')) creator, COUNT(*) n
                  FROM token_analysis
                 WHERE pf_ws_creator IN ({qs}) OR earliest_tx_creator IN ({qs})
                 GROUP BY creator""", tuple(chunk) * 2,
        ):
            if row['creator']:
                totals[str(row['creator'])] += int(row['n'])
        funded.update(str(r[0]) for r in conn.execute(f"SELECT DISTINCT creator_address FROM creator_funders WHERE creator_address IN ({qs})", chunk))
        networked.update(str(r[0]) for r in conn.execute(f"SELECT DISTINCT creator_address FROM network_membership WHERE creator_address IN ({qs})", chunk))
        networked.update(str(r[0]) for r in conn.execute(f"SELECT DISTINCT creator_address FROM creator_networks WHERE creator_address IN ({qs})", chunk))
        watchtower.update(str(r[0]) for r in conn.execute(f"SELECT DISTINCT COALESCE(NULLIF(pf_ws_creator,''),NULLIF(earliest_tx_creator,'')) FROM token_analysis WHERE watchtower_related=1 AND (pf_ws_creator IN ({qs}) OR earliest_tx_creator IN ({qs}))", tuple(chunk) * 2) if r[0])
    conn.close()

    per_creator: Counter[str] = Counter(str(r['creator']) for r in births if r['creator'])
    minute_births: Counter[int] = Counter(int(float(r['analyzed_at']) // 60) for r in births)
    minute_creators: dict[int, set[str]] = defaultdict(set)
    times: dict[str, list[float]] = defaultdict(list)
    for r in births:
        if r['creator']:
            minute_creators[int(float(r['analyzed_at']) // 60)].add(str(r['creator']))
            times[str(r['creator'])].append(float(r['analyzed_at']))
    birth_rate = list(minute_births.values()); creator_rate = [len(v) for v in minute_creators.values()]
    prior_bucket = Counter()
    for creator, n in per_creator.items():
        total = totals[creator]
        label = "1" if total == 1 else "2-4" if total <= 4 else "5-9" if total <= 9 else "10-24" if total <= 24 else "25-49" if total <= 49 else "50-99" if total <= 99 else "100+"
        prior_bucket[label] += n
    classification = Counter()
    for creator, n in per_creator.items():
        if creator in watchtower:
            label = "E_KNOWN_WATCHTOWER_RELEVANT_CREATOR"
        elif creator in networked:
            label = "D_KNOWN_CREATOR_WITH_NETWORK_RELATIONSHIP_STATE"
        elif creator in funded:
            label = "C_KNOWN_CREATOR_WITH_CREATOR_FUNDERS"
        elif totals[creator] > n:
            label = "B_KNOWN_CREATOR_NO_FUNDING"
        else:
            label = "A_NEVER_SEEN_CREATOR"
        classification[label] += n
    repeat = {}
    for seconds, name in ((300,"5m"),(900,"15m"),(3600,"1h"),(21600,"6h")):
        active = {c for c, xs in times.items() if any(b-a <= seconds for a,b in zip(xs, xs[1:]))}
        repeat[name] = {"creators":len(active),"births_from_creators":sum(per_creator[c] for c in active)}
    ordered_counts = sorted(per_creator.values(), reverse=True)
    concentration = {}
    for pct in (1,5,10):
        take=max(1,math.ceil(len(ordered_counts)*pct/100)); concentration[f"top_{pct}_pct_creators"]={"creator_count":take,"births":sum(ordered_counts[:take]),"birth_share_pct":round(100*sum(ordered_counts[:take])/len(births),3) if births else 0}
    minutes=max(1,args.hours*60); known_funders_births=sum(n for c,n in per_creator.items() if c in funded)
    unknown_unfunded_creators={c for c in per_creator if c not in funded}
    never_creators={c for c,n in per_creator.items() if totals[c] == n}
    x78_34=json.loads((ROOT/'docs/audits/x78_34_qualification.json').read_text())
    full=x78_34['metrics']['full']; calls=x78_34['metrics']['rpc_calls']; calls_per_full=round(calls/full,3)
    report={
      "milestone":"EB0.1 — Pump.fun Birth Feed Latency & Creator Population Audit",
      "mode":"READ_ONLY_HISTORICAL_INTERIM", "implementation_revision": "working-tree",
      "observation_window":{"start_utc":start,"end_utc":now,"hours":args.hours,"population":"token_analysis pumpfun/bonding_curve rows ordered by analyzed_at"},
      "pumpportal_path":{"transport":"wss://pumpportal.fun/api/data","subscription":["subscribeNewToken","subscribeMigration"],"birth_handler":"listen_pumpportal_websocket -> _insert_bonding_curve_token","persistence":"token_analysis; birth_persist_queue fallback","reconciler":"disabled in run_listener.sh"},
      "timestamp_contract":{"historical":{"available":["token_analysis.analyzed_at (local persistence-time, millisecond-ish)","token_analysis.created_at (local wall-clock seconds)","create_tx_signature","creator"],"missing":["raw PumpPortal receive UTC","monotonic receive time","authoritative chain block time/slot"]},"future_instrumentation":{"module":"src/core/pumpportal_birth_audit.py","flag":"EB0_1_BIRTH_AUDIT_ENABLED=0","status":"implemented but not enabled or deployed","precision":"UTC and monotonic nanoseconds at ws.recv boundary; before JSON parsing"},"latency_verdict":"D — NOT_MEASURABLE from historical data; no RPC validation sample executed because this is an independent no-restart interim audit"},
      "latency_distribution": {"chain_to_pumpportal_receive":"UNAVAILABLE","chain_to_persisted":"UNAVAILABLE","pumpportal_receive_to_parsed":"UNAVAILABLE","parsed_to_persisted":"UNAVAILABLE","reason":"historical rows do not carry raw receive or authoritative on-chain time"},
      "birth_rate_per_minute":{"sampled_minutes":len(minute_births),"births":len(births),"mean":round(len(births)/minutes,3),"p50":percentile(birth_rate,.5),"p90":percentile(birth_rate,.9),"p95":percentile(birth_rate,.95),"p99":percentile(birth_rate,.99),"maximum":max(birth_rate) if birth_rate else 0,"projected_per_day":round(len(births)*24/args.hours)},
      "unique_creator_rate_per_minute":{"unique_creators":len(per_creator),"mean":round(len(per_creator)/minutes,3),"p50":percentile(creator_rate,.5),"p95":percentile(creator_rate,.95),"maximum":max(creator_rate) if creator_rate else 0,"births_per_unique_creator":round(len(births)/len(per_creator),3) if per_creator else 0},
      "known_creator_classification":{"definition":"current local state; A means no launch outside this observation window, not a historical chain claim","births":dict(sorted(classification.items())),"creator_funders_births":known_funders_births,"creator_funders_birth_share_pct":round(100*known_funders_births/len(births),3) if births else 0},
      "repeat_creator_distribution":{"births_by_current_launch_count":dict(prior_bucket),"repeat_births":sum(n for c,n in per_creator.items() if totals[c]>1),"repeat_birth_share_pct":round(100*sum(n for c,n in per_creator.items() if totals[c]>1)/len(births),3) if births else 0,"short_interval":repeat,"concentration":concentration},
      "new_expensive_creator_pressure":{"births_per_minute":round(len(births)/minutes,3),"unique_creators_per_minute":round(len(per_creator)/minutes,3),"never_seen_creators_per_minute":round(len(never_creators)/minutes,3),"known_unfunded_creators_per_minute":round(len({c for c in per_creator if c not in funded and c not in never_creators})/minutes,3),"unknown_unfunded_unique_creators":len(unknown_unfunded_creators)},
      "policy_simulation":{"A_ALL_BIRTHS":{"immediate_enrichment":len(births),"full_creator_rpc_calls_hour_proxy":round(len(births)*calls_per_full,1)},"B_UNIQUE_CREATOR":{"immediate_enrichment":len(per_creator),"full_creator_rpc_calls_hour_proxy":round(len(per_creator)*calls_per_full,1)},"C_UNKNOWN_UNFUNDED_CREATOR":{"immediate_enrichment":len(unknown_unfunded_creators),"full_creator_rpc_calls_hour_proxy":round(len(unknown_unfunded_creators)*calls_per_full,1)},"D_UNKNOWN_PLUS_WATCHTOWER_PRIORITY":{"priority_immediate":len(never_creators|watchtower),"full_creator_rpc_calls_hour_proxy":round(len(never_creators|watchtower)*calls_per_full,1),"other_captured_deferred":len(births)-sum(per_creator[c] for c in (never_creators|watchtower))},"E_CAPACITY_AWARE":{"status":"NOT_MEASURABLE while X78.40 clean capacity window is pending"}},
      "x78_real_cost_context":{"source":"x78_34_qualification.json","full_creator_extractions":full,"rpc_calls":calls,"observed_calls_per_full_creator":calls_per_full,"p50_full_seconds":x78_34['metrics']['full_elapsed_p50_s'],"p95_full_seconds":x78_34['metrics']['full_elapsed_p95_s'],"dominant_long_tail":"outgoing_transfer_scan; model must keep it separate"},
      "delivery_reliability":{"duplicates":"NOT_MEASURABLE from token_analysis because UPSERT deduplicates by mint","reconnects":"NOT_MEASURABLE from retained DB rows; log-only events are not an audit corpus","missed_births":"NOT_MEASURABLE; reconciler is disabled and no independent low-cost population exists"},
      "safety":{"additional_rpc_calls":0,"production_restarts":0,"production_database_writes":0,"listener_instrumentation_enabled":False,"x78_40_window":"unmodified"},
      "limitations":["Historical `analyzed_at` is a local post-receive/persistence timestamp, not chain or raw socket receive time.","Creator classification uses current local positive evidence and current all-time launch counts; it does not assert historical freshness.","No all-birth reconciliation is active, so delivery completeness cannot be estimated."],
      "verdicts":{"pumpportal_birth_delivery":"D — NOT_MEASURABLE","latency_measurement":"D — NOT_RELIABLE","birth_census_feasibility":"C — MATERIAL_GAPS","creator_reuse_potential":"B — MATERIAL","helius_expansion_pressure":"C — HIGH_REQUIRES_STRICT_PRIORITIZATION"}
    }
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
    print(json.dumps({"births":len(births),"unique_creators":len(per_creator),"output":str(args.output)},sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
