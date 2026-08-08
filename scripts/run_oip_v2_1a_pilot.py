#!/usr/bin/env python3
"""Run the single approved 1,000-call OIP v2.1A shadow acquisition pilot."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.acquisition.transaction import SharedTransactionAcquisition, acquisition_scope
from src.evidence.config import EvidenceConfig
from src.evidence.service import EvidencePlatform
from src.intelligence.migrated_coverage import census
from src.intelligence.migrated_coverage_acquisition import representative_sample

CALL_LIMIT = 1000
FROZEN_SOURCE_ROWID = 1_615_500


def counts(path: Path) -> dict[str, int]:
    conn=sqlite3.connect(f"file:{path}?mode=ro",uri=True)
    try:
        return {name:int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]) for name in
                ("artifact_references","normalized_evidence_records","primitive_observations")}
    finally: conn.close()


async def run(args) -> dict:
    source_db=args.source / "evidence.db"; target_db=args.output / "evidence.db"
    if args.output.exists():
        raise RuntimeError("pilot output already exists; automatic continuation is forbidden")
    args.output.mkdir(parents=True)
    shutil.copy2(source_db,target_db)
    before_counts=counts(target_db); before_size=target_db.stat().st_size
    before_rows=census(args.production_db,target_db,max_source_rowid=FROZEN_SOURCE_ROWID)
    sample,sampling=representative_sample(before_rows,call_limit=CALL_LIMIT)
    if len(sample)!=CALL_LIMIT:
        raise RuntimeError(f"representative sampler produced {len(sample)} calls, expected {CALL_LIMIT}")
    config=EvidenceConfig(platform_enabled=True,writer_enabled=True,queue_enabled=True,
        artifact_store_enabled=True,health_enabled=True,mirror_enabled=True,
        normalization_enabled=True,primitive_engine_enabled=True,database_path=target_db,
        queue_path=args.output/"intake",artifact_path=args.output/"artifacts",
        mirror_spool_path=args.output/"mirror_spool",writer_batch_size=100,
        queue_max_messages=2000,mirror_buffer_size=1000)
    platform=EvidencePlatform(config); platform.writer.primitive_engine=None; platform.writer.start()
    results=[]; latencies=[]; started=time.monotonic()
    try:
        async with aiohttp.ClientSession() as session:
            client=SharedTransactionAcquisition(session,semaphore=asyncio.Semaphore(args.concurrency))
            request_semaphore=asyncio.Semaphore(args.concurrency)
            async def fetch(item):
                payload={"jsonrpc":"2.0","id":1,"method":"getTransaction","params":[item.signature,
                    {"encoding":"jsonParsed","commitment":"finalized","maxSupportedTransactionVersion":0}]}
                async with request_semaphore:
                    with acquisition_scope(purpose=item.purpose,launch=item.launch):
                        response=await client.request_once(http_method="POST",url=args.rpc_url,
                            timeout_seconds=30,request_type="json_rpc",method="getTransaction",json_payload=payload)
                latencies.append(response.latency_ms)
                state="PROVIDER_UNAVAILABLE"
                if response.status==200 and isinstance(response.data,dict):
                    state="RECOVERED" if response.data.get("result") is not None else "HISTORICAL_UNAVAILABLE"
                    if not platform.mirror.publish_nowait(response,http_method="POST",url=args.rpc_url,request_payload=payload):
                        raise RuntimeError("Evidence mirror rejected pilot acquisition")
                results.append((item.signature,state)); return state
            # Exactly one shared-acquisition request per target; no retry or failover can cross the cap.
            await asyncio.gather(*(fetch(item) for item in sample))
        if not platform.mirror.drain(timeout=300): raise RuntimeError("mirror did not drain")
        while True:
            batch=platform.writer.run_once()
            if batch["claimed"]==0: break
        primitive_result=platform.primitive_engine.run_once()
    finally:
        platform.writer.stop();platform.mirror.stop()
    elapsed=time.monotonic()-started;after_counts=counts(target_db);after_size=target_db.stat().st_size
    after_rows=census(args.production_db,target_db,max_source_rowid=FROZEN_SOURCE_ROWID)
    complete_before=sum(x.state=="COMPLETE" for x in before_rows);complete_after=sum(x.state=="COMPLETE" for x in after_rows)
    states={state:sum(value==state for _,value in results) for state in sorted({v for _,v in results})}
    report={"milestone":"OIP v2.1A","call_limit":CALL_LIMIT,"rpc_calls":len(results),
        "credits_used":len(results)*10,"automatic_continuation":False,"sampling":sampling,
        "population_snapshot":{"eligible_launches":len(after_rows),"max_source_rowid":FROZEN_SOURCE_ROWID,
            "live_births_excluded":True},
        "acquisition_outcomes":states,"coverage":{"before_complete":complete_before,"after_complete":complete_after,
            "recovered_launches":complete_after-complete_before,"eligible_population":len(after_rows)},
        "records":{"before":before_counts,"after":after_counts,
            "evidence_added":after_counts["normalized_evidence_records"]-before_counts["normalized_evidence_records"],
            "primitives_added":after_counts["primitive_observations"]-before_counts["primitive_observations"]},
        "performance":{"elapsed_seconds":round(elapsed,3),"throughput_per_second":round(len(results)/elapsed,3),
            "rpc_latency_ms":{"minimum":round(min(latencies),3),"maximum":round(max(latencies),3),
                "mean":round(sum(latencies)/len(latencies),3)},"retry_count":0,"provider_failover_count":0},
        "storage":{"before_bytes":before_size,"after_bytes":after_size,"growth_bytes":after_size-before_size},
        "primitive_run":primitive_result,"production_writes":0,"governance_actions":0}
    (args.output/"pilot_report.json").write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
    return report


def main():
    load_dotenv(ROOT/".env")
    parser=argparse.ArgumentParser();parser.add_argument("--source",type=Path,default=ROOT/"database/evidence_platform/watchtower_shadow_ep3_0d")
    parser.add_argument("--output",type=Path,default=ROOT/"database/evidence_platform/oip_v2_1a_pilot")
    parser.add_argument("--production-db",type=Path,default=ROOT/"database/flex_complete_database.db")
    parser.add_argument("--rpc-url",default=os.environ.get("HELIUS_RPC_URL"));parser.add_argument("--concurrency",type=int,default=8)
    args=parser.parse_args()
    if not args.rpc_url: raise SystemExit("HELIUS_RPC_URL is required")
    report=asyncio.run(run(args));print(json.dumps(report,sort_keys=True,indent=2));return 0


if __name__=="__main__": raise SystemExit(main())
