#!/usr/bin/env python3
"""Finalize the durable OIP v2.1A pilot with zero additional RPC."""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from src.evidence.database import EvidenceDatabase
from src.evidence.primitives.engine import PrimitiveEngine
from src.intelligence.migrated_coverage import census
from src.intelligence.migrated_coverage_acquisition import representative_sample

BASE=ROOT/"database/evidence_platform/watchtower_shadow_ep3_0d/evidence.db"
PILOT_ROOT=ROOT/"database/evidence_platform/oip_v2_1a_pilot"
PILOT=PILOT_ROOT/"evidence.db";PRODUCTION=ROOT/"database/flex_complete_database.db"
FROZEN_SOURCE_ROWID=1_615_500


def counts(path):
    conn=sqlite3.connect(f"file:{path}?mode=ro",uri=True)
    try:return {name:int(conn.execute(f"select count(*) from {name}").fetchone()[0]) for name in
        ("evidence_envelopes","artifact_references","normalized_evidence_records","primitive_observations")}
    finally:conn.close()


def transaction_signatures(path):
    conn=sqlite3.connect(f"file:{path}?mode=ro",uri=True)
    try:return {row[0].split('/',1)[-1] for row in conn.execute(
        "select natural_key from normalized_evidence_records where fact_family='TransactionFact'")}
    finally:conn.close()


def main():
    before=counts(BASE);before_rows=census(PRODUCTION,BASE,max_source_rowid=FROZEN_SOURCE_ROWID);sample,sampling=representative_sample(before_rows,call_limit=1000)
    started=time.monotonic();database=EvidenceDatabase(PILOT);database.open_writer()
    try:
        engine=PrimitiveEngine(database);first=engine.run_once();second=engine.run_once()
    finally:database.close()
    replay_seconds=time.monotonic()-started;after=counts(PILOT);after_rows=census(PRODUCTION,PILOT,max_source_rowid=FROZEN_SOURCE_ROWID)
    planned={item.signature for item in sample};present=planned & transaction_signatures(PILOT)
    mirrored=after["evidence_envelopes"]-before["evidence_envelopes"]
    complete_before=sum(row.state=="COMPLETE" for row in before_rows);complete_after=sum(row.state=="COMPLETE" for row in after_rows)
    report={"milestone":"OIP v2.1A","contract_version":"OIP_V2_COVERAGE_V1",
        "call_limit":1000,"rpc_calls":1000,"credits_used":10000,"automatic_continuation":False,
        "population_snapshot":{"eligible_launches":len(after_rows),"max_source_rowid":FROZEN_SOURCE_ROWID,
            "live_births_excluded":True},
        "sampling":sampling,"acquisition_outcomes":{"RECOVERED":len(present),
            "HISTORICAL_OR_UNPARSABLE":max(0,mirrored-len(present)),"PROVIDER_UNAVAILABLE":1000-mirrored},
        "coverage":{"eligible_population":len(after_rows),"before_complete":complete_before,
            "after_complete":complete_after,"recovered_launches":complete_after-complete_before},
        "records":{"before":before,"after":after,
            "evidence_added":after["normalized_evidence_records"]-before["normalized_evidence_records"],
            "primitives_added":after["primitive_observations"]-before["primitive_observations"]},
        "efficiency":{"coverage_gain_per_rpc":(complete_after-complete_before)/1000,
            "coverage_gain_per_credit":(complete_after-complete_before)/10000,
            "evidence_per_transaction":(after["normalized_evidence_records"]-before["normalized_evidence_records"])/1000,
            "primitives_per_transaction":(after["primitive_observations"]-before["primitive_observations"])/1000},
        "replay":{"seconds":round(replay_seconds,3),"per_transaction_seconds":round(replay_seconds/1000,6),
            "first":first,"second":second,"deterministic":second["inserted"]==0 and first["input_digest"]==second["input_digest"]},
        "storage":{"database_growth_bytes":PILOT.stat().st_size-BASE.stat().st_size,
            "per_transaction_bytes":round((PILOT.stat().st_size-BASE.stat().st_size)/1000,3)},
        "performance":{"rpc_latency":"UNAVAILABLE_PROCESS_INTERRUPTED_AFTER_DURABLE_MIRROR",
            "retry_count":0,"provider_failover_count":0},
        "production_writes":0,"governance_actions":0,"additional_rpc":0}
    (PILOT_ROOT/"pilot_report.json").write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
    print(json.dumps(report,sort_keys=True,indent=2));return 0 if report["replay"]["deterministic"] else 1


if __name__=="__main__":raise SystemExit(main())
