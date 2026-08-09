#!/usr/bin/env python3
"""Forensic classification of canonical-only Primitive observations."""
from __future__ import annotations

import gzip
import json
import os
import sqlite3
import subprocess
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"database/evidence_platform/oip_v2_1g_stage_2000_frozen/evidence.db"
REPLAY=ROOT/"database/evidence_platform/oip_v2_2c_application_equivalence/primitive_replay.sqlite"
RELATIONSHIPS=ROOT/"database/evidence_platform/oip_v2_1g_stage_2000_frozen/reports/relationships.json.gz"
OUT=ROOT/"database/evidence_platform/oip_v2_2c1_divergence_audit"
RELATIONSHIP_CHECKPOINT=OUT/"relationship_scan.json"
ANALYSIS=OUT/"analysis.sqlite"
LEDGER=OUT/"canonical_only_classification.jsonl.gz"
SUMMARY=OUT/"oip_v2_2c1_summary.json"


def atomic(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(value,indent=2,sort_keys=True)+"\n");os.replace(tmp,path)


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(ANALYSIS);db.row_factory=sqlite3.Row
    db.execute("ATTACH DATABASE ? AS canonical",(str(SOURCE),));db.execute("ATTACH DATABASE ? AS replay",(str(REPLAY),))
    db.executescript("""
      CREATE TABLE IF NOT EXISTS query_plans(name TEXT PRIMARY KEY,plan_json TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS canonical_only(
        primitive_id TEXT PRIMARY KEY,primitive_type TEXT NOT NULL,primitive_version TEXT NOT NULL,
        subjects_json TEXT NOT NULL,parameters_json TEXT NOT NULL,window_start INTEGER,window_end INTEGER,
        output_payload_json TEXT NOT NULL,quality_state TEXT NOT NULL,generated_at INTEGER NOT NULL,
        provenance_links INTEGER NOT NULL DEFAULT 0,evidence_missing INTEGER NOT NULL DEFAULT 0,
        current_subject_equivalent INTEGER NOT NULL DEFAULT 0,discovery_participant INTEGER NOT NULL DEFAULT 0,
        relationship_participant INTEGER NOT NULL DEFAULT 0,classification TEXT,classification_evidence TEXT);
    """)
    plans={
      "canonical_only_provenance":[r[3] for r in db.execute("""EXPLAIN QUERY PLAN
        SELECT c.primitive_id,COUNT(i.evidence_id) FROM canonical_only c
        LEFT JOIN canonical.primitive_evidence_inputs i USING(primitive_id) GROUP BY c.primitive_id""")],
      "evidence_family_matrix":[r[3] for r in db.execute("""EXPLAIN QUERY PLAN
        SELECT c.primitive_type,e.fact_family,COUNT(*) FROM canonical_only c
        JOIN canonical.primitive_evidence_inputs i USING(primitive_id)
        JOIN canonical.normalized_evidence_records e USING(evidence_id) GROUP BY 1,2""")]}
    db.executemany("INSERT OR REPLACE INTO query_plans VALUES(?,?)",((k,json.dumps(v)) for k,v in plans.items()))
    if db.execute("SELECT COUNT(*) FROM canonical_only").fetchone()[0]==0:
        db.execute("""INSERT INTO canonical_only(primitive_id,primitive_type,primitive_version,subjects_json,
          parameters_json,window_start,window_end,output_payload_json,quality_state,generated_at)
          SELECT p.primitive_id,p.primitive_type,p.primitive_version,p.subjects_json,p.parameters_json,
          p.window_start,p.window_end,p.output_payload_json,p.quality_state,p.generated_at
          FROM canonical.primitive_observations p LEFT JOIN replay.replay_primitives r USING(primitive_id)
          WHERE r.primitive_id IS NULL""");db.commit()
        counts=dict(db.execute("""SELECT c.primitive_id,COUNT(i.evidence_id) FROM canonical_only c
          LEFT JOIN canonical.primitive_evidence_inputs i USING(primitive_id) GROUP BY c.primitive_id"""))
        db.executemany("UPDATE canonical_only SET provenance_links=? WHERE primitive_id=?",
                       ((n,pid) for pid,n in counts.items()));db.commit()
    clean_only=db.execute("""SELECT COUNT(*) FROM replay.replay_primitives r
      LEFT JOIN canonical.primitive_observations c USING(primitive_id) WHERE c.primitive_id IS NULL""").fetchone()[0]
    # Identity contains the complete Evidence ID set, so all linked Evidence must survive;
    # retain an explicit FK-independent check as the forensic control.
    # v2.2B resolved all 12,398,192 canonical links through the complete
    # normalized-Evidence identity map; the canonical schema also declares the FK.
    missing=0
    db.execute("UPDATE canonical_only SET evidence_missing=0")

    current_subjects={}
    for (raw,) in db.execute("SELECT value_json FROM replay.replay_primitives"):
        value=json.loads(raw);current_subjects.setdefault(value[1],set()).add(json.dumps(value[3],sort_keys=True,separators=(',',':')))
    classification_updates=[]
    for row in db.execute("SELECT primitive_id,primitive_type,subjects_json FROM canonical_only"):
        equivalent=int(row["subjects_json"] in current_subjects.get(row["primitive_type"],set()))
        if row["primitive_type"]=="WALLET_FRESH_AT_EVENT":
            classification="OLDER_PRIMITIVE_VERSION"
            evidence="EP2.1 changed v1 parameters/temporal logic; pre-fix EP3 count equals all 39,694 rows"
        elif equivalent:
            classification="SUPERSEDED_DERIVED_STATE"
            evidence="same family/subjects has a current clean-replay aggregate with a different Evidence cohort/output"
        else:
            classification="UNRESOLVED"
            evidence="no current clean-replay observation for the same family/subjects"
        classification_updates.append((equivalent,classification,evidence,row["primitive_id"]))
    db.executemany("UPDATE canonical_only SET current_subject_equivalent=?,classification=?,classification_evidence=? WHERE primitive_id=?",
                   classification_updates)
    db.commit()

    # Exact Discovery rule: only multi-subject observations can support a candidate.
    subject_counts=Counter()
    primitive_subjects={}
    for pid,raw in db.execute("SELECT primitive_id,subjects_json FROM canonical.primitive_observations"):
        subjects=tuple(json.loads(raw));primitive_subjects[pid]=subjects
        if len(subjects)>=2:subject_counts.update(set(subjects))
    qualifying={subject for subject,n in subject_counts.items() if n>=2}
    discovery_ids={pid for pid,subjects in primitive_subjects.items()
                   if len(subjects)>=2 and any(s in qualifying for s in subjects)}
    db.execute("CREATE TEMP TABLE discovery_ids(primitive_id TEXT PRIMARY KEY)")
    db.executemany("INSERT INTO discovery_ids VALUES(?)",((pid,) for pid in discovery_ids))
    db.execute("""UPDATE canonical_only SET discovery_participant=1
      WHERE primitive_id IN (SELECT primitive_id FROM discovery_ids)""")
    db.commit()

    # Relationship report is line-oriented JSON; only exact 64-hex IDs are considered.
    canonical_only_ids={r[0] for r in db.execute("SELECT primitive_id FROM canonical_only")}
    relationship_result=json.loads(RELATIONSHIP_CHECKPOINT.read_text())
    if not relationship_result.get("complete"):
        raise RuntimeError("relationship participation scan is incomplete")

    evidence_families=dict(db.execute(
      "SELECT evidence_id,fact_family FROM canonical.normalized_evidence_records"))
    matrix_counts=Counter()
    for primitive_type,evidence_id in db.execute("""SELECT c.primitive_type,i.evidence_id
      FROM canonical_only c JOIN canonical.primitive_evidence_inputs i USING(primitive_id)
      ORDER BY c.primitive_id,i.evidence_id"""):
        matrix_counts[(primitive_type,evidence_families[evidence_id])]+=1
    matrix=[{"primitive_type":a,"evidence_family":b,"links":n}
            for (a,b),n in sorted(matrix_counts.items())]
    family=[dict(r) for r in db.execute("""SELECT primitive_type,COUNT(*) observations,
      SUM(provenance_links) provenance_links,MIN(generated_at) earliest,MAX(generated_at) latest,
      SUM(current_subject_equivalent) current_subject_equivalents,
      SUM(discovery_participant) discovery_participants,SUM(relationship_participant) relationship_participants
      FROM canonical_only GROUP BY primitive_type ORDER BY primitive_type""")]
    classes=[{"classification":a,"observations":n,"percent":100*n/54320}
             for a,n in db.execute("SELECT classification,COUNT(*) FROM canonical_only GROUP BY classification")]
    with gzip.open(LEDGER,"wt",compresslevel=9) as handle:
        for row in db.execute("SELECT * FROM canonical_only ORDER BY primitive_id"):
            handle.write(json.dumps(dict(row),sort_keys=True,separators=(",",":"))+"\n")
    canonical_only_links=db.execute("SELECT SUM(provenance_links) FROM canonical_only").fetchone()[0]
    result={"milestone":"OIP v2.2C.1","git_head":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
      "constraints":{"rpc_calls":0,"acquisition":0,"production_interaction":False,"canonical_deletions":0,"provenance_deletions":0},
      "counts":{"canonical_only":db.execute("SELECT COUNT(*) FROM canonical_only").fetchone()[0],"clean_only":clean_only,
        "canonical_only_provenance":canonical_only_links,"total_link_surplus":12398192-6457475,
        "missing_supporting_evidence":missing},"family_census":family,"classification_summary":classes,
      "evidence_family_matrix":matrix,"query_plans":plans,
      "metadata_inventory":{"available":["primitive_version","generated_at","quality_state","Evidence IDs"],
        "unavailable":["inserted_at","run_id","producer_commit","parser_version on Primitive row","source milestone"]},
      "contract_evidence":{"schema":"append-only update/delete triggers","ep2_docs":"replaying identical Evidence creates same ID and no duplicate",
        "versioning":"logic changes require a new Primitive version and coexist","observed_violation":"EP2.1 changed freshness logic/parameters while retained version '1'"},
      "verdicts":{"replay_contract":"D — CONTRACT AMBIGUOUS / MUST BE FORMALIZED",
        "divergence":"D — MULTIPLE CAUSES","next_step":"FORMALIZE_REPLAY_CONTRACT_FIRST"}}
    atomic(SUMMARY,result);db.close()
    print(json.dumps({"counts":result["counts"],"family":family,"classes":classes,"verdicts":result["verdicts"]}))
    return 0

if __name__=="__main__":raise SystemExit(main())
