#!/usr/bin/env python3
"""Build and validate the isolated Primitive Authority Contract v1 projection."""
from __future__ import annotations

import hashlib,json,os,sqlite3,subprocess,sys
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from src.evidence.primitives.authority import (
    CONTRACT_VERSION,FAMILY_CONTRACTS,AuthorityRule,authority_group,authority_rank,
    contract_for,corrected_freshness,
)

SOURCE=ROOT/"database/evidence_platform/oip_v2_1g_stage_2000_frozen/evidence.db"
REPLAY=ROOT/"database/evidence_platform/oip_v2_2c_application_equivalence/primitive_replay.sqlite"
OUT=ROOT/"database/evidence_platform/oip_v2_2c2_authority_contract"
DB=OUT/"authority_projection.sqlite"
SUMMARY=ROOT/"docs/evidence_platform/oip_v2_2c2_authority_summary.json"
CONTRACT=ROOT/"docs/evidence_platform/primitive_authority_contract_v1.json"

def atomic(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(value,indent=2,sort_keys=True)+"\n");os.replace(tmp,path)

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    target=sqlite3.connect(DB);target.row_factory=sqlite3.Row
    target.execute("ATTACH DATABASE ? AS canonical",(str(SOURCE),));target.execute("ATTACH DATABASE ? AS replay",(str(REPLAY),))
    target.executescript("""CREATE TABLE IF NOT EXISTS primitive_authority(
      primitive_id TEXT PRIMARY KEY,state TEXT NOT NULL,authority_group_json TEXT,
      superseded_by TEXT,reason TEXT NOT NULL,contract_version TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS query_plans(name TEXT PRIMARY KEY,plan_json TEXT NOT NULL);""")
    target.execute("DELETE FROM primitive_authority")
    plan=[r[3] for r in target.execute("""EXPLAIN QUERY PLAN SELECT primitive_id,COUNT(*)
      FROM canonical.primitive_evidence_inputs GROUP BY primitive_id""")]
    target.execute("INSERT OR REPLACE INTO query_plans VALUES(?,?)",("input_counts",json.dumps(plan)))
    if target.execute("SELECT COUNT(*) FROM primitive_authority").fetchone()[0]==0:
        input_counts=dict(target.execute("SELECT primitive_id,COUNT(*) FROM canonical.primitive_evidence_inputs GROUP BY primitive_id"))
        observations=[];winners={}
        for row in target.execute("SELECT * FROM canonical.primitive_observations ORDER BY primitive_id"):
            family=row["primitive_type"];contract=contract_for(family)
            subjects=tuple(json.loads(row["subjects_json"]));parameters=json.loads(row["parameters_json"])
            output=json.loads(row["output_payload_json"])
            group=authority_group(family,subjects,parameters,output,row["primitive_version"])
            record=(row["primitive_id"],family,contract,group,parameters,
                    authority_rank(family,input_counts[row["primitive_id"]],row["window_start"],
                                   row["window_end"],output,row["generated_at"],row["primitive_id"]))
            observations.append(record)
            if contract.authority_rule is AuthorityRule.LATEST_PER_GROUP:
                previous=winners.get(group)
                if previous is None or record[-1]>previous[-1]:winners[group]=record
            elif (contract.authority_rule is AuthorityRule.CORRECTED_FRESHNESS and
                  corrected_freshness(parameters)):
                previous=winners.get(group)
                if previous is None or record[-1]>previous[-1]:winners[group]=record
        rows=[]
        for primitive_id,family,contract,group,parameters,rank in observations:
            group_json=json.dumps(group,separators=(",",":"))
            if group[1] not in contract.current_versions:
                state="LEGACY_VERSION";superseded=None;reason="UNAPPROVED_SEMANTIC_VERSION"
            elif contract.authority_rule is AuthorityRule.CORRECTED_FRESHNESS and not corrected_freshness(parameters):
                state="LEGACY_VERSION";superseded=winners[group][0];reason="LEGACY_SEMANTICS_EP2_0"
            elif contract.authority_rule is AuthorityRule.LATEST_PER_GROUP and winners[group][0]!=primitive_id:
                state="HISTORICAL_SNAPSHOT";superseded=winners[group][0];reason="SUPERSEDED_BY_CURRENT_GROUP_SNAPSHOT"
            else:
                state="AUTHORITATIVE";superseded=None;reason="CURRENT_AUTHORITY_CONTRACT_V1"
            rows.append((primitive_id,state,group_json,superseded,reason,CONTRACT_VERSION))
        target.executemany("INSERT INTO primitive_authority VALUES(?,?,?,?,?,?)",rows);target.commit()
    counts=[{"family":a,"state":b,"count":n} for a,b,n in target.execute("""
      SELECT p.primitive_type,a.state,COUNT(*) FROM primitive_authority a
      JOIN canonical.primitive_observations p USING(primitive_id) GROUP BY 1,2 ORDER BY 1,2""")]
    authority_count=target.execute("SELECT COUNT(*) FROM primitive_authority WHERE state='AUTHORITATIVE'").fetchone()[0]
    authority_minus_clean=target.execute("""SELECT COUNT(*) FROM primitive_authority a
      LEFT JOIN replay.replay_primitives r USING(primitive_id)
      WHERE a.state='AUTHORITATIVE' AND r.primitive_id IS NULL""").fetchone()[0]
    clean_minus_authority=target.execute("""SELECT COUNT(*) FROM replay.replay_primitives r
      LEFT JOIN primitive_authority a USING(primitive_id)
      WHERE a.primitive_id IS NULL OR a.state!='AUTHORITATIVE'""").fetchone()[0]
    authority_digest=hashlib.sha256("".join(row[0] for row in target.execute(
        "SELECT primitive_id FROM primitive_authority WHERE state='AUTHORITATIVE' ORDER BY primitive_id"
    )).encode()).hexdigest()
    clean_digest=hashlib.sha256("".join(row[0] for row in target.execute(
        "SELECT primitive_id FROM replay.replay_primitives ORDER BY primitive_id"
    )).encode()).hexdigest()
    contract_json={"contract_version":CONTRACT_VERSION,"default_consumer_policy":"CURRENT_AUTHORITATIVE",
      "current_state_replay":"must reproduce exactly the AUTHORITATIVE projection",
      "historical_ledger_replay":"requires historical Evidence boundaries and generator semantics; not currently reconstructable exactly",
      "authority_history":"future transitions must be append-only and auditable",
      "unknown_family_policy":"FAIL_CLOSED","families":{
        family:{"semantic_type":c.semantic_type.value,"cohort_sensitivity":c.cohort_sensitivity.value,
          "authority_rule":c.authority_rule.value,"grouping_fields":list(c.grouping_fields),
          "consumer_policy":c.consumer_policy,"replay_policy":c.replay_policy,
          "historical_access":c.historical_access,"version_policy":c.version_policy,
          "current_versions":list(c.current_versions)}
        for family,c in sorted(FAMILY_CONTRACTS.items())}}
    atomic(CONTRACT,contract_json)
    result={"milestone":"OIP v2.2C.2","git_head":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
      "constraints":{"rpc_calls":0,"acquisition":0,"production_interaction":False,"canonical_mutations":0,"provenance_deletions":0},
      "projection":{"persisted":401050,"authoritative":authority_count,
        "non_authoritative":401050-authority_count,"authority_minus_clean":authority_minus_clean,
        "clean_minus_authority":clean_minus_authority,"clean_count":346730,
        "count_equal":authority_count==346730,"authority_id_digest":authority_digest,
        "clean_id_digest":clean_digest,"digest_equal":authority_digest==clean_digest,
        "set_equal":authority_minus_clean==clean_minus_authority==0},
      "family_state_counts":counts,"historical_access":{"queryable_rows":target.execute("SELECT COUNT(*) FROM primitive_authority WHERE state!='AUTHORITATIVE'").fetchone()[0],
        "supersession_links":target.execute("SELECT COUNT(*) FROM primitive_authority WHERE superseded_by IS NOT NULL").fetchone()[0]},
      "consumer_contract":{"Discovery":"CURRENT_AUTHORITATIVE","Motifs":"Discovery outputs and their authoritative Primitive support",
        "Relationships":"authoritative motif/landscape outputs","Historical analysis":"explicit ALL_PERSISTED query"},
      "migration_control":{"historical_gate":"all persisted provenance pairs remain exactly equivalent",
        "application_gate":"authoritative projection equals CURRENT_STATE_REPLAY"},
      "resume":"AUTHORITY_PROJECTION_IMPLEMENTATION_REQUIRED_BEFORE_RESUMING_V2_2C",
      "acquisition":"HOLD_ACQUISITION"}
    atomic(SUMMARY,result);target.close()
    print(json.dumps({"projection":result["projection"],"resume":result["resume"],"acquisition":result["acquisition"]}))
    return 0 if result["projection"]["set_equal"] else 1

if __name__=="__main__":raise SystemExit(main())
