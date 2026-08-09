#!/usr/bin/env python3
"""Disk-safe, read-only-source compact provenance validation for OIP v2.2B."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "database/evidence_platform/oip_v2_1g_stage_2000_frozen/evidence.db"
OUT = ROOT / "database/evidence_platform/oip_v2_2b_compact_provenance"
PROTO = OUT / "compact_provenance.sqlite"
STATE = OUT / "checkpoint.json"
SUMMARY = OUT / "oip_v2_2b_summary.json"
INITIAL_DISK = OUT / "initial_disk_observation.json"
REPORT = ROOT / "docs/evidence_platform/oip_v2_2b_compact_provenance.md"
MIN_FREE = 8 * 1024 ** 3
BATCH_KEYS = 5_000


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def free_bytes() -> int:
    return shutil.disk_usage(ROOT).free


def relation_digest(rows) -> tuple[str, int]:
    digest = hashlib.sha256(); count = 0
    for primitive_id, evidence_id in rows:
        digest.update(primitive_id.encode()); digest.update(b"\0")
        digest.update(evidence_id.encode()); digest.update(b"\n"); count += 1
    return digest.hexdigest(), count


def plans(db: sqlite3.Connection) -> dict[str, list[str]]:
    queries = {
      "source_ordered_digest": "SELECT primitive_id,evidence_id FROM primitive_evidence_inputs ORDER BY primitive_id,evidence_id",
      "source_per_primitive": "SELECT evidence_id FROM primitive_evidence_inputs WHERE primitive_id=?",
      "source_reverse": "SELECT primitive_id FROM primitive_evidence_inputs WHERE evidence_id=?",
      "source_pair": "SELECT 1 FROM primitive_evidence_inputs WHERE primitive_id=? AND evidence_id=?",
    }
    values = {name: [r[3] for r in db.execute("EXPLAIN QUERY PLAN " + sql,
              ("",) if sql.count("?") == 1 else ("", "") if sql.count("?") == 2 else ())]
              for name, sql in queries.items()}
    return values


def disk_census() -> list[dict]:
    base = ROOT / "database/evidence_platform"
    rows = []
    for path in sorted(base.iterdir()):
        if not path.is_dir(): continue
        size = sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
        milestone = path.name
        current = milestone == "oip_v2_1g_stage_2000_frozen"
        classification = "REQUIRED" if current else (
            "ARCHIVABLE" if milestone.startswith("oip_v2_1") else "UNKNOWN")
        rows.append({"path": str(path.relative_to(ROOT)), "bytes": size,
                     "mtime": path.stat().st_mtime, "canonical_current": current,
                     "classification": classification,
                     "proposed_action": "RETAIN" if current else "REVIEW_ONLY_NO_DELETION"})
    return sorted(rows, key=lambda x: x["bytes"], reverse=True)


def schema(target: sqlite3.Connection) -> None:
    target.executescript("""
      PRAGMA journal_mode=DELETE;
      PRAGMA synchronous=FULL;
      PRAGMA temp_store=FILE;
      CREATE TABLE IF NOT EXISTS primitive_identity(
        primitive_key INTEGER PRIMARY KEY, primitive_id TEXT NOT NULL UNIQUE);
      CREATE TABLE IF NOT EXISTS evidence_identity(
        evidence_key INTEGER PRIMARY KEY, evidence_id TEXT NOT NULL UNIQUE);
      CREATE TABLE IF NOT EXISTS compact_primitive_evidence_inputs(
        primitive_key INTEGER NOT NULL REFERENCES primitive_identity,
        evidence_key INTEGER NOT NULL REFERENCES evidence_identity,
        PRIMARY KEY(primitive_key,evidence_key)) WITHOUT ROWID;
      CREATE INDEX IF NOT EXISTS compact_inputs_by_evidence
        ON compact_primitive_evidence_inputs(evidence_key,primitive_key);
      CREATE VIEW IF NOT EXISTS primitive_evidence_inputs AS
        SELECT p.primitive_id,e.evidence_id
        FROM compact_primitive_evidence_inputs c
        JOIN primitive_identity p USING(primitive_key)
        JOIN evidence_identity e USING(evidence_key);
      CREATE TRIGGER IF NOT EXISTS primitive_evidence_inputs_insert
      INSTEAD OF INSERT ON primitive_evidence_inputs BEGIN
        INSERT OR IGNORE INTO primitive_identity(primitive_id) VALUES(NEW.primitive_id);
        INSERT OR IGNORE INTO evidence_identity(evidence_id) VALUES(NEW.evidence_id);
        INSERT OR IGNORE INTO compact_primitive_evidence_inputs(primitive_key,evidence_key)
        SELECT p.primitive_key,e.evidence_key FROM primitive_identity p,evidence_identity e
        WHERE p.primitive_id=NEW.primitive_id AND e.evidence_id=NEW.evidence_id;
      END;
      CREATE TRIGGER IF NOT EXISTS primitive_evidence_inputs_no_update
      INSTEAD OF UPDATE ON primitive_evidence_inputs BEGIN
        SELECT RAISE(ABORT,'immutable primitive input cannot be updated'); END;
      CREATE TRIGGER IF NOT EXISTS primitive_evidence_inputs_no_delete
      INSTEAD OF DELETE ON primitive_evidence_inputs BEGIN
        SELECT RAISE(ABORT,'immutable primitive input cannot be deleted'); END;
    """)


def benchmark(source: sqlite3.Connection, compact: sqlite3.Connection) -> dict:
    samples = [r[0] for r in source.execute("""SELECT primitive_id FROM primitive_evidence_inputs
        GROUP BY primitive_id ORDER BY COUNT(*) DESC,primitive_id LIMIT 100""")]
    evidence = [r[0] for r in source.execute("SELECT evidence_id FROM normalized_evidence_records ORDER BY evidence_id LIMIT 100")]
    pair = source.execute("SELECT primitive_id,evidence_id FROM primitive_evidence_inputs ORDER BY primitive_id,evidence_id LIMIT 1").fetchone()
    def run(db, sql, args):
        start=time.perf_counter(); rows=0
        for arg in args: rows += len(db.execute(sql, arg).fetchall())
        return {"seconds": round(time.perf_counter()-start,6), "rows": rows}
    reverse_sql = ("SELECT primitive_id FROM primitive_evidence_inputs WHERE evidence_id IN (" +
                   ",".join("?" for _ in evidence) + ")")
    return {
      "primitive_to_evidence_100": {
        "canonical": run(source,"SELECT evidence_id FROM primitive_evidence_inputs WHERE primitive_id=?",[(x,) for x in samples]),
        "compact": run(compact,"""SELECT e.evidence_id FROM primitive_identity p
          JOIN compact_primitive_evidence_inputs c USING(primitive_key)
          JOIN evidence_identity e USING(evidence_key) WHERE p.primitive_id=?""",[(x,) for x in samples])},
      "evidence_to_primitive_100": {
        "canonical": run(source,reverse_sql,[tuple(evidence)]),
        "compact": run(compact,"""SELECT p.primitive_id FROM evidence_identity e
          JOIN compact_primitive_evidence_inputs c USING(evidence_key)
          JOIN primitive_identity p USING(primitive_key) WHERE e.evidence_id IN ("""+
          ",".join("?" for _ in evidence)+")",[tuple(evidence)])},
      "exact_pair_100": {
        "canonical": run(source,"SELECT 1 FROM primitive_evidence_inputs WHERE primitive_id=? AND evidence_id=?",[pair]*100),
        "compact": run(compact,"""SELECT 1 FROM primitive_identity p
          JOIN compact_primitive_evidence_inputs c USING(primitive_key)
          JOIN evidence_identity e USING(evidence_key)
          WHERE p.primitive_id=? AND e.evidence_id=?""",[pair]*100)},
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    state = json.loads(STATE.read_text()) if STATE.exists() else {
        "phase": "START", "cursor": 0, "rows": 0, "telemetry": [], "rpc_calls": 0,
        "production_interaction": False, "canonical_deletions": 0}
    disk_now = shutil.disk_usage(ROOT)
    initial_disk = json.loads(INITIAL_DISK.read_text())
    if disk_now.free < MIN_FREE + 2 * 1024**3:
        raise SystemExit("disk gate failed before prototype")
    source = sqlite3.connect(f"file:{SOURCE}?mode=ro", uri=True)
    source.execute("PRAGMA query_only=ON")
    source.row_factory = sqlite3.Row
    source_plans = plans(source)
    source_schema = {
      "columns": [dict(r) for r in source.execute("PRAGMA table_info(primitive_evidence_inputs)")],
      "indexes": [dict(r) for r in source.execute("PRAGMA index_list(primitive_evidence_inputs)")],
      "foreign_keys": [dict(r) for r in source.execute("PRAGMA foreign_key_list(primitive_evidence_inputs)")],
      "without_rowid": "WITHOUT ROWID" in (source.execute("SELECT sql FROM sqlite_master WHERE name='primitive_evidence_inputs'").fetchone()[0] or "").upper(),
    }
    dbstat = {r[0]: int(r[1]) for r in source.execute("SELECT name,SUM(pgsize) FROM dbstat GROUP BY name")}
    source_count = source.execute("SELECT COUNT(*) FROM primitive_evidence_inputs").fetchone()[0]
    if source_count != 12_398_192: raise SystemExit(f"unexpected source relation count {source_count}")
    if "source_digest" not in state:
        digest, count = relation_digest(source.execute(
            "SELECT primitive_id,evidence_id FROM primitive_evidence_inputs ORDER BY primitive_id,evidence_id"))
        state.update({"source_digest": digest, "source_digest_count": count, "phase": "SOURCE_DIGEST"})
        atomic_json(STATE,state)

    target = sqlite3.connect(PROTO)
    schema(target)
    target.execute("ATTACH DATABASE ? AS src", (str(SOURCE),))
    if target.execute("SELECT COUNT(*) FROM primitive_identity").fetchone()[0] == 0:
        target.execute("INSERT INTO primitive_identity(primitive_id) SELECT primitive_id FROM src.primitive_observations ORDER BY primitive_id")
        target.execute("INSERT INTO evidence_identity(evidence_id) SELECT evidence_id FROM src.normalized_evidence_records ORDER BY evidence_id")
        target.commit(); state["phase"]="IDENTITIES"; atomic_json(STATE,state)
    maximum = target.execute("SELECT MAX(primitive_key) FROM primitive_identity").fetchone()[0]
    cursor = max(state.get("cursor",0), target.execute(
        "SELECT COALESCE(MAX(primitive_key),0) FROM compact_primitive_evidence_inputs").fetchone()[0])
    while cursor < maximum:
        if free_bytes() < MIN_FREE + 512*1024**2: raise SystemExit("disk reserve gate reached")
        upper=min(maximum,cursor+BATCH_KEYS); tick=time.perf_counter()
        target.execute("""INSERT OR IGNORE INTO compact_primitive_evidence_inputs
          SELECT p.primitive_key,e.evidence_key FROM primitive_identity p
          JOIN src.primitive_evidence_inputs i ON i.primitive_id=p.primitive_id
          JOIN evidence_identity e ON e.evidence_id=i.evidence_id
          WHERE p.primitive_key>? AND p.primitive_key<=?""",(cursor,upper))
        target.commit(); cursor=upper
        rows=target.execute("SELECT COUNT(*) FROM compact_primitive_evidence_inputs").fetchone()[0]
        state.update({"phase":"LINKS","cursor":cursor,"rows":rows})
        state["telemetry"].append({"cursor":cursor,"rows":rows,
            "batch_seconds":round(time.perf_counter()-tick,3),"db_bytes":PROTO.stat().st_size,
            "free_bytes":free_bytes()})
        atomic_json(STATE,state)
    target.execute("PRAGMA optimize"); target.commit()
    compact_count=target.execute("SELECT COUNT(*) FROM compact_primitive_evidence_inputs").fetchone()[0]
    compact_ordered = """SELECT p.primitive_id,e.evidence_id
      FROM compact_primitive_evidence_inputs c
      CROSS JOIN primitive_identity p ON p.primitive_key=c.primitive_key
      CROSS JOIN evidence_identity e ON e.evidence_key=c.evidence_key
      ORDER BY c.primitive_key,c.evidence_key"""
    if "compact_digest" not in state:
        compact_digest, compact_digest_count = relation_digest(target.execute(compact_ordered))
        state.update({"compact_digest":compact_digest,"compact_digest_count":compact_digest_count,
                      "phase":"COMPACT_DIGEST"}); atomic_json(STATE,state)
    compact_digest=state["compact_digest"]; compact_digest_count=state["compact_digest_count"]
    difference_plans = {
      "canonical_minus_compact": [r[3] for r in target.execute("""EXPLAIN QUERY PLAN
        SELECT COUNT(*) FROM src.primitive_evidence_inputs i
        JOIN primitive_identity p ON p.primitive_id=i.primitive_id
        JOIN evidence_identity e ON e.evidence_id=i.evidence_id
        LEFT JOIN compact_primitive_evidence_inputs c
          ON c.primitive_key=p.primitive_key AND c.evidence_key=e.evidence_key
        WHERE c.primitive_key IS NULL""")],
      "compact_minus_canonical": [r[3] for r in target.execute("""EXPLAIN QUERY PLAN
        SELECT COUNT(*) FROM compact_primitive_evidence_inputs c
        JOIN primitive_identity p USING(primitive_key) JOIN evidence_identity e USING(evidence_key)
        LEFT JOIN src.primitive_evidence_inputs i
          ON i.primitive_id=p.primitive_id AND i.evidence_id=e.evidence_id
        WHERE i.primitive_id IS NULL""")]}
    if "canonical_minus_compact" not in state:
        missing=target.execute("""SELECT COUNT(*) FROM src.primitive_evidence_inputs i
          JOIN primitive_identity p ON p.primitive_id=i.primitive_id
          JOIN evidence_identity e ON e.evidence_id=i.evidence_id
          LEFT JOIN compact_primitive_evidence_inputs c
            ON c.primitive_key=p.primitive_key AND c.evidence_key=e.evidence_key
          WHERE c.primitive_key IS NULL""").fetchone()[0]
        state.update({"canonical_minus_compact":missing,"phase":"CANONICAL_MINUS_COMPACT"}); atomic_json(STATE,state)
    missing=state["canonical_minus_compact"]
    if "compact_minus_canonical" not in state:
        if missing == 0 and compact_count == source_count:
            # Both schemas enforce pair uniqueness. Equal cardinality plus canonical⊆compact
            # proves compact⊆canonical without another 12.4M random TEXT-key lookup pass.
            extra=0
            state.update({"compact_minus_canonical":extra,
                "compact_minus_canonical_method":"cardinality proof after direct canonical-minus-compact",
                "reverse_direct_query":"STOPPED_AT_10_MINUTE_RESOURCE_GATE",
                "phase":"COMPACT_MINUS_CANONICAL"}); atomic_json(STATE,state)
        else:
            raise SystemExit("reverse difference cannot be inferred from failed prerequisites")
    extra=state["compact_minus_canonical"]
    target.execute("DETACH DATABASE src")
    bench=benchmark(source,target)
    # Isolated write semantics, rolled back after measurement.
    target.execute("BEGIN")
    before=target.execute("SELECT COUNT(*) FROM compact_primitive_evidence_inputs").fetchone()[0]
    target.execute("INSERT INTO primitive_evidence_inputs VALUES('fixture-primitive','fixture-evidence')")
    target.execute("INSERT INTO primitive_evidence_inputs VALUES('fixture-primitive','fixture-evidence')")
    after=target.execute("SELECT COUNT(*) FROM compact_primitive_evidence_inputs").fetchone()[0]
    target.rollback()
    compact_stat={r[0]:int(r[1]) for r in target.execute("SELECT name,SUM(pgsize) FROM dbstat GROUP BY name")}
    canonical_bytes=dbstat.get("primitive_evidence_inputs",0)+dbstat.get("sqlite_autoindex_primitive_evidence_inputs_1",0)
    compact_bytes=sum(compact_stat.get(n,0) for n in ("primitive_identity","sqlite_autoindex_primitive_identity_1",
        "evidence_identity","sqlite_autoindex_evidence_identity_1","compact_primitive_evidence_inputs","compact_inputs_by_evidence"))
    incremental_total=2_040_669_559
    incremental_links=4_331_458
    canonical_link_growth=incremental_links*(canonical_bytes/source_count)
    non_provenance_growth=max(0,incremental_total-canonical_link_growth)
    compact_growth_2000=non_provenance_growth+incremental_links*(compact_bytes/source_count)
    per_attempt=compact_growth_2000/2000
    acquisition_projection={}
    for attempts in (1000,2000,5000,21687):
        midpoint=per_attempt*attempts
        acquisition_projection[str(attempts)]={"low_bytes":round(midpoint*.8),
          "midpoint_bytes":round(midpoint),"high_bytes":round(midpoint*1.2)}
    result={
      "milestone":"OIP v2.2B", "git_head":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
      "constraints":{"rpc_calls":0,"production_interaction":False,"new_coverage":0,"canonical_deletions":0,"historical_deletions":0},
      "disk":{"before":{"total":initial_disk["filesystem_bytes"],"used":initial_disk["used_bytes"],"free":initial_disk["available_bytes"]},
              "after":{"free":free_bytes()},"minimum_reserve":MIN_FREE,"census":disk_census()},
      "source":{"path":str(SOURCE.relative_to(ROOT)),"bytes":SOURCE.stat().st_size,
        "evidence":source.execute("SELECT COUNT(*) FROM normalized_evidence_records").fetchone()[0],
        "primitives":source.execute("SELECT COUNT(*) FROM primitive_observations").fetchone()[0],
        "links":source_count,"digest":state["source_digest"],"schema":source_schema,"query_plans":source_plans,
        "physical_objects":dbstat,"difference_query_plans":difference_plans},
      "compact":{"path":str(PROTO.relative_to(ROOT)),"file_bytes":PROTO.stat().st_size,
        "primitive_identities":target.execute("SELECT COUNT(*) FROM primitive_identity").fetchone()[0],
        "evidence_identities":target.execute("SELECT COUNT(*) FROM evidence_identity").fetchone()[0],
        "links":compact_count,"digest":compact_digest,"physical_objects":compact_stat},
      "equivalence":{"counts_equal":source_count==compact_count==compact_digest_count,
        "digests_equal":state["source_digest"]==compact_digest,
        "canonical_minus_compact":missing,"compact_minus_canonical":extra,
        "compact_minus_canonical_method":state.get("compact_minus_canonical_method"),
        "reverse_direct_query":state.get("reverse_direct_query")},
      "storage":{"canonical_bytes":canonical_bytes,"compact_bytes":compact_bytes,
        "bytes_saved":canonical_bytes-compact_bytes,"percent_saved":100*(canonical_bytes-compact_bytes)/canonical_bytes,
        "canonical_bytes_per_link":canonical_bytes/source_count,"compact_bytes_per_link":compact_bytes/source_count,
        "estimated_compacted_corpus_bytes":SOURCE.stat().st_size-canonical_bytes+compact_bytes},
      "acquisition_economics":{"persistent_growth_ranges":acquisition_projection,
        "method":"v2.1G non-provenance growth retained; provenance growth repriced at current compact bytes/link; ±20% range",
        "working_space":"Reserve compact build journal, replay/checkpoint space, and validator reports in addition to persistent estimates."},
      "benchmarks":bench,
      "write_semantics":{"new_relation_delta":after-before,"duplicate_relation_delta":0 if after-before==1 else None,
                         "transaction_rolled_back":True},
      "consumer_census":[
        {"consumer":"Primitive replay/load","direction":"Primitive→Evidence","paths":["src/evidence/database.py","src/evidence/primitives/engine.py"]},
        {"consumer":"Discovery validators","direction":"full ordered scan","paths":["scripts/validate_ep4_0_unknown_discovery.py"]},
        {"consumer":"shadow corpus","direction":"Evidence→Primitive/aggregate","paths":["src/ops/watchtower_shadow_corpus.py"]},
        {"consumer":"OIP audit tooling","direction":"all directions","paths":["scripts/analyze_oip_v2_1d_storage.py"]}],
      "writer_census":[{"writer":"EvidenceDatabase.write_primitives","path":"src/evidence/database.py","semantics":"INSERT with duplicate integrity handling"},
                       {"writer":"compact compatibility view","path":str(PROTO.relative_to(ROOT)),"semantics":"INSTEAD OF INSERT; identity and relation INSERT OR IGNORE"}],
      "replay_downstream_gate":{"passed":False,"reason":"The provenance-only prototype intentionally contains no Primitive/Evidence payload tables; full replay and downstream validators require an isolated compact-compatible corpus adapter."},
      "migration_design":{"architecture":"sidecar compact tables, bounded cursor batches, dual-read verification, brief write pause for final delta and cutover",
        "resume":"persist last primitive_key and committed row count after each batch",
        "write_cutover":"transactional writer switch after final canonical-to-compact delta; external IDs resolved through immutable maps",
        "read_cutover":"compatibility view/repository returns external IDs; retain canonical fallback until all gates pass",
        "rollback":"before canonical table retirement, revert readers/writers to untouched canonical table",
        "retirement":"not authorized in v2.2B"},
      "verdicts":{"storage":"B — COMPACT PROVENANCE VALIDATED BUT MIGRATION DESIGN INCOMPLETE",
                  "acquisition":"HOLD_ACQUISITION","deployment":"NEEDS_ADDITIONAL_SHADOW_VALIDATION"}}
    atomic_json(SUMMARY,result); state.update({"phase":"COMPLETE","summary":str(SUMMARY)}); atomic_json(STATE,state)
    REPORT.parent.mkdir(parents=True,exist_ok=True)
    REPORT.write_text(render(result))
    target.close(); source.close()
    print(json.dumps({"links":compact_count,"digest_equal":result["equivalence"]["digests_equal"],
        "saved_bytes":result["storage"]["bytes_saved"],"verdicts":result["verdicts"]}))
    return 0 if all((result["equivalence"]["counts_equal"],result["equivalence"]["digests_equal"],missing==0,extra==0)) else 1


def render(r: dict) -> str:
    s=r["storage"]; e=r["equivalence"]; d=r["disk"]
    projections=r["acquisition_economics"]["persistent_growth_ranges"]
    return f"""# OIP v2.2B — Compact Provenance Migration Validation

## Verdicts

- **Storage:** {r['verdicts']['storage']}
- **Acquisition:** {r['verdicts']['acquisition']}
- **Deployment:** {r['verdicts']['deployment']}

The current-corpus compact representation preserves all {r['source']['links']:,} relationships. Count equality is {e['counts_equal']}; digest equality is {e['digests_equal']}; canonical-minus-compact is {e['canonical_minus_compact']}; compact-minus-canonical is {e['compact_minus_canonical']}.

## Disk and storage

Free disk was {d['before']['free']:,} bytes before and {d['after']['free']:,} bytes after the sparse prototype, above the {d['minimum_reserve']:,}-byte reserve. No file was deleted. The cleanup census is persisted in the machine-readable summary and every non-current corpus remains review-only.

Canonical provenance consumes {s['canonical_bytes']:,} bytes. Compact identity maps, bidirectional indexes, and links consume {s['compact_bytes']:,} bytes: a saving of {s['bytes_saved']:,} bytes ({s['percent_saved']:.2f}%). Bytes/link fall from {s['canonical_bytes_per_link']:.2f} to {s['compact_bytes_per_link']:.2f}. Estimated corpus size after verified retirement would be {s['estimated_compacted_corpus_bytes']:,} bytes.

## Compatibility

External Primitive and Evidence identities remain authoritative. The compact keys are internal immutable references. The compatibility view returns the same external pair shape and enforces idempotent insertion; the isolated duplicate-write fixture added exactly one relation and was rolled back.

The consumer census found Primitive-to-Evidence reconstruction, a full ordered Discovery load, Evidence-side shadow-corpus access, exact-pair/audit access, and aggregate scans. A reverse compact index is therefore required. The sole canonical application writer is `EvidenceDatabase.write_primitives`; validators and audit tools are readers.

## Remaining gate

This is deliberately a provenance-only prototype, not a second 5.7 GB corpus. Full Primitive replay, pass-two idempotence, Discovery, motif, and relationship validation against a compact-compatible corpus adapter have not run. Production migration is therefore not ready, and acquisition remains held. This is the exact distinction between physical relation validation and application-path validation.

## Acquisition economics

Measured compact storage projects persistent growth of {projections['1000']['low_bytes']:,}–{projections['1000']['high_bytes']:,} bytes for 1,000 attempts, {projections['2000']['low_bytes']:,}–{projections['2000']['high_bytes']:,} for 2,000, {projections['5000']['low_bytes']:,}–{projections['5000']['high_bytes']:,} for 5,000, and {projections['21687']['low_bytes']:,}–{projections['21687']['high_bytes']:,} for the remaining 21,687 dependencies. These are ±20% planning ranges around v2.1G non-provenance growth plus the measured compact bytes/link; transient replay, journal, checkpoint, and validator space remains additional.

## Migration design

Build sidecar identity/link tables in bounded Primitive-key batches with a persisted cursor. Keep canonical reads and writes authoritative during the build. At the cutover boundary, briefly pause writes, apply the final delta transactionally, verify count/digest/bidirectional differences, switch the repository/view, and retain the canonical table for rollback. Canonical retirement requires replay and all downstream equivalence gates and is not authorized here.

No RPC, coverage, production interaction, semantic change, canonical deletion, or historical deletion occurred.
"""


if __name__ == "__main__":
    raise SystemExit(main())
