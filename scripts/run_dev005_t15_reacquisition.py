#!/usr/bin/env python3
"""One-shot, bounded DEV-005 T+15 research acquisition.

This is deliberately a research-only tool: it reads the named Monitor facts
database, writes only artifacts under this checkout, makes at most one OHLCV
request for each pre-frozen control, and never retains a provider payload.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
AUDITS = ROOT / "docs/audits"
CONTRACTS = ROOT / "docs/contracts"
ENV_FILE = Path("/Users/kevinkeaveney/Dev/claude/flex/.env")
AUTHORITY_DB = Path("/Users/kevinkeaveney/Dev/claude/flex/.dev_runtime/monitor/dev_005a/wt_ops_v2.dev.db")
HORIZON = 604800
MAX_CONTROLS = 16


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def atomic_json(path, value):
    payload = (canonical(value) + "\n").encode()
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(payload); handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)


def append_atomic(path, value):
    prior = path.read_bytes() if path.exists() else b""
    payload = prior + canonical(value).encode() + b"\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(payload); handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)


def atomic_empty(path):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)


def load_qualified_credential():
    # Same minimal shell-compatible binding as the qualified DEV-005 launcher.
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("export "): line = line[7:].lstrip()
        if line.startswith("BIRDEYE_KKHOT="):
            value = line.split("=", 1)[1].split("#", 1)[0].strip().strip("'\"")
            if value: return value
    raise RuntimeError("BIRDEYE_KKHOT_REQUIRED")


def controls(now):
    db = sqlite3.connect(f"file:{AUTHORITY_DB}?mode=ro", uri=True)
    try:
        rows = db.execute("""
          select mint, entry_timestamp, entry_mc_usd, final_proven_ath_mc
          from operation_monitor_facts
          where operation_id='watchtower' and entry_status='QUALIFIED'
            and entry_exactness='FIRST_FULL_POST_MIGRATION_SECOND_MC'
            and entry_timestamp is not null and entry_mc_usd > 1
          order by mint asc
        """).fetchall()
    finally:
        db.close()
    excluded = {"6osBBs9gQTCAYL1uxLAmrkXD5mSH8A8uUmQMSGjhpump", "8nh9t5MHFRyEWn5K9oUqx3LJayLMMHhE4BPdgno5pump"}
    eligible = [{"control_id": f"WT-{mint}", "mint": mint,
                 "migration_timestamp": int(entry) - 1,
                 "exact_entry_timestamp": int(entry),
                 "retained_exact_entry_mc_usd": float(mc),
                 "retained_ath_mc_usd": float(ath) if ath is not None else None}
                for mint, entry, mc, ath in rows if mint not in excluded and now - int(entry) <= HORIZON]
    # Assignment is deterministic and frozen before any transport: lexical mint order.
    sample = eligible[:MAX_CONTROLS]
    for index, item in enumerate(sample): item["set"] = "discovery" if index < 12 else "held_out"
    return len(rows) - sum(m in excluded for m, *_ in rows), eligible, sample


def request_once(control, key):
    parameters = {"address": control["mint"], "chart_type": "mcap", "currency": "usd", "mode": "range",
                  "padding": "false", "time_from": control["migration_timestamp"] + 1,
                  "time_to": control["migration_timestamp"] + 16, "type": "1s"}
    fingerprint = digest({"endpoint": "/defi/v3/ohlcv", "parameters": parameters, "header_names": ["accept", "X-API-KEY", "x-chain"]})
    url = "https://public-api.birdeye.so/defi/v3/ohlcv?" + urlencode(parameters)
    status, payload, result_class = 0, None, "TRANSPORT_EXCEPTION"
    try:
        with urlopen(Request(url, headers={"accept":"application/json", "X-API-KEY":key, "x-chain":"solana"}), timeout=45) as response:
            status = int(response.status); payload = json.loads(response.read()); result_class = "HTTP_200" if status == 200 else "HTTP_NON_200"
    except HTTPError as error:
        status, result_class = int(error.code), "HTTP_429" if error.code == 429 else "HTTP_NON_200"
        # Intentionally do not read error body.
    except (URLError, TimeoutError, ValueError):
        pass
    observations = []
    if status == 200 and isinstance(payload, dict):
        items = ((payload.get("data") or {}).get("items") or [])
        for item in items:
            if not isinstance(item, dict): continue
            try:
                timestamp = int(item.get("unixTime", item.get("unix_time", item.get("timestamp"))))
                mc = float(item.get("c", item.get("close", item.get("value"))))
            except (TypeError, ValueError): continue
            if control["migration_timestamp"] + 1 <= timestamp <= control["migration_timestamp"] + 15 and mc > 0:
                observations.append({"timestamp": timestamp, "offset_from_migration": timestamp-control["migration_timestamp"], "mc_usd": mc})
    observations.sort(key=lambda item: item["timestamp"])
    plus = next((item for item in observations if item["offset_from_migration"] == 1), None)
    parity = abs(plus["mc_usd"] - control["retained_exact_entry_mc_usd"]) / control["retained_exact_entry_mc_usd"] * 100 if plus else None
    parity_class = "MISSING_PLUS1" if parity is None else "EXACT" if parity <= .5 else "CLOSE" if parity <= 1 else "MATERIAL" if parity <= 5 else "MAJOR" if parity <= 25 else "SEVERE"
    fallback = next((item for item in observations if 1 < item["offset_from_migration"] <= 15), None)
    fallback_signed = ((fallback["mc_usd"] - control["retained_exact_entry_mc_usd"]) / control["retained_exact_entry_mc_usd"] * 100) if fallback else None
    evidence = {**control, "http_status": status, "provider_result_class": result_class,
                "request_profile_id": "BIRDEYE_V3_OHLCV_1S_TPLUS1_TO_TPLUS15_V1", "request_fingerprint": fingerprint,
                "acquired_at": int(time.time()), "observations": observations, "plus1_present": plus is not None,
                "acquired_plus1_mc_usd": plus["mc_usd"] if plus else None, "parity_abs_percent_error": parity, "parity_class": parity_class,
                "first_available_after_plus1_timestamp": fallback["timestamp"] if fallback else None,
                "first_available_offset": fallback["offset_from_migration"] if fallback else None,
                "first_available_mc_usd": fallback["mc_usd"] if fallback else None,
                "fallback_abs_percent_error": abs(fallback_signed) if fallback_signed is not None else None,
                "fallback_signed_percent_error": fallback_signed, "covered_within_t15": fallback is not None}
    return evidence


def percentile(values, point):
    if not values: return None
    ordered = sorted(values); return ordered[max(0, min(len(ordered)-1, int((len(ordered)-1)*point)))]


def metrics(records):
    usable = [r for r in records if r["parity_class"] in {"EXACT", "CLOSE"}]
    covered = [r for r in usable if r["covered_within_t15"]]
    errors = [r["fallback_abs_percent_error"] for r in covered]
    signed = [r["fallback_signed_percent_error"] for r in covered]
    return {"parity_usable_count":len(usable), "covered_count":len(covered), "uncovered_count":len(usable)-len(covered),
            "coverage_percent":100*len(covered)/len(usable) if usable else 0, "median_absolute_percent_error":percentile(errors,.5),
            "p95_absolute_percent_error":percentile(errors,.95), "max_absolute_percent_error":max(errors) if errors else None,
            "mean_signed_percent_error":sum(signed)/len(signed) if signed else None,
            "delay_distribution": {"+2":sum(r.get("first_available_offset")==2 for r in usable), "+3":sum(r.get("first_available_offset")==3 for r in usable), "+4-5":sum((r.get("first_available_offset") or 0) in {4,5} for r in usable), "+6-10":sum(6 <= (r.get("first_available_offset") or 0) <= 10 for r in usable), "+11-15":sum(11 <= (r.get("first_available_offset") or 0) <= 15 for r in usable), "uncovered":len(usable)-len(covered)}}


def passes(metric, minimum):
    return minimum and metric["coverage_percent"] >= 80 and metric["median_absolute_percent_error"] <= 1 and metric["p95_absolute_percent_error"] <= 2 and metric["max_absolute_percent_error"] <= 5 and abs(metric["mean_signed_percent_error"]) <= .5


def analyze_only(evidence_path, manifest_path, ledger_path, analysis_path):
    records = [json.loads(line) for line in evidence_path.read_text().splitlines() if line.strip()]
    discovery=metrics([r for r in records if r["set"]=="discovery"]); heldout=metrics([r for r in records if r["set"]=="held_out"])
    discovery_pass=passes(discovery, discovery["parity_usable_count"] >= 8)
    heldout_pass=passes(heldout, heldout["parity_usable_count"] >= 3) if discovery_pass else None
    result={"schema_version":"DEV005_T15_REPRODUCIBLE_ANALYSIS_V1","independent_provider_free_recomputation":True,"records_read_only_from":str(evidence_path.relative_to(ROOT)),"provider_calls_made":len(records),"discovery":discovery,"held_out":heldout,"discovery_t15_passes":discovery_pass,"heldout_policy_evaluated_once":discovery_pass,"heldout_t15_passes":heldout_pass,"exact_entry_remains_primary":True,"t15_implementation_added":False,"artifact_digests":{"manifest":hashlib.sha256(manifest_path.read_bytes()).hexdigest(),"evidence":hashlib.sha256(evidence_path.read_bytes()).hexdigest(),"ledger":hashlib.sha256(ledger_path.read_bytes()).hexdigest()}}
    atomic_json(analysis_path,result)


def main():
    AUDITS.mkdir(parents=True, exist_ok=True); CONTRACTS.mkdir(parents=True, exist_ok=True)
    now = int(time.time()); population, eligible, sample = controls(now)
    manifest = {"schema_version":"DEV005_T15_MANIFEST_V1", "created_at":now, "authority_db":str(AUTHORITY_DB), "authority_db_write_mode":"READ_ONLY", "isolated_state_root":str(ROOT / ".dev005_t15_research_state"), "live_state_root_referenced_for_writes":False, "eligible_exact_control_population":population, "eligible_controls_within_7_days":len(eligible), "excluded_controls":["6os","8nh"], "policy":"WATCHTOWER_FIRST_AVAILABLE_1S_T15_V1", "thresholds":{"coverage_percent_gte":80,"median_abs_percent_lte":1,"p95_abs_percent_lte":2,"max_abs_percent_lte":5,"abs_mean_signed_percent_lte":.5}, "request_profile":{"endpoint":"/defi/v3/ohlcv","interval":"1s","time_from":"migration+1","time_to":"migration+16","calls_per_control":1}, "sample":sample}
    manifest_path=AUDITS/"dev005_t15_reacquisition_manifest.v1.json"; evidence_path=AUDITS/"dev005_t15_compact_control_evidence.v1.jsonl"; ledger_path=AUDITS/"dev005_t15_acquisition_ledger.v1.jsonl"; analysis_path=AUDITS/"dev005_t15_reproducible_analysis.v1.json"
    atomic_json(manifest_path, manifest); atomic_empty(evidence_path); atomic_empty(ledger_path)
    if len(eligible) < 12:
        atomic_json(analysis_path, {"status":"HOLD_INSUFFICIENT_REPRODUCIBLE_CONTROL_SAMPLE", "manifest_digest":digest(manifest), "provider_calls_made":0})
        return
    key=load_qualified_credential(); records=[]
    for sequence, control in enumerate(sample, 1):
        evidence=request_once(control,key); append_atomic(evidence_path,evidence); append_atomic(ledger_path,{"sequence_number":sequence,"control_id":control["control_id"],"request_fingerprint":evidence["request_fingerprint"],"http_status":evidence["http_status"],"normalized_evidence_digest":digest(evidence),"timestamp":evidence["acquired_at"]}); records.append(evidence)
    # The caller invokes --analyze in a fresh provider-free process.

if __name__ == "__main__":
    paths=(AUDITS/"dev005_t15_compact_control_evidence.v1.jsonl", AUDITS/"dev005_t15_reacquisition_manifest.v1.json", AUDITS/"dev005_t15_acquisition_ledger.v1.jsonl", AUDITS/"dev005_t15_reproducible_analysis.v1.json")
    analyze_only(*paths) if "--analyze" in sys.argv else main()
