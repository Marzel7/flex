#!/usr/bin/env python3
"""Read-only OIP v2.2A retention and dematerialization audit."""
from __future__ import annotations

import hashlib
import gzip
import json
import os
import sqlite3
import subprocess
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "database/evidence_platform/oip_v2_1g_stage_2000_frozen/evidence.db"
POPULATION = ROOT / "docs/evidence_platform/oip_v2_1g_coverage_population.json.gz"
REPORTS = CORPUS.parent / "reports"
OUT = ROOT / "database/evidence_platform/oip_v2_2a_retention_audit"
ANALYSIS = OUT / "analysis.sqlite"
SUMMARY = OUT / "oip_v2_2a_retention_summary.json"
REPORT = ROOT / "docs/evidence_platform/oip_v2_2a_historical_retention_audit.md"


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def scalar(db: sqlite3.Connection, sql: str, args=()) -> int:
    return int(db.execute(sql, args).fetchone()[0])


def main() -> int:
    started = time.perf_counter()
    OUT.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{CORPUS}?mode=ro", uri=True)
    source.execute("PRAGMA query_only=ON")
    analysis = sqlite3.connect(ANALYSIS)
    analysis.executescript("""
      PRAGMA journal_mode=WAL;
      CREATE TABLE IF NOT EXISTS query_plans(name TEXT PRIMARY KEY, plan_json TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS primitive_input_counts(
        primitive_id TEXT PRIMARY KEY, input_count INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS primitive_inventory(
        primitive_type TEXT PRIMARY KEY, primitive_rows INTEGER NOT NULL,
        provenance_links INTEGER NOT NULL, payload_bytes INTEGER NOT NULL,
        first_seen INTEGER, last_seen INTEGER, discovery_rows INTEGER NOT NULL,
        isolated_rows INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS audit_metadata(key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
    """)
    plans = {
      "provenance_family_aggregate": [r[3] for r in source.execute("""
        EXPLAIN QUERY PLAN SELECT po.primitive_type,COUNT(pei.evidence_id)
        FROM primitive_observations po LEFT JOIN primitive_evidence_inputs pei USING(primitive_id)
        GROUP BY po.primitive_type""")],
      "provenance_per_primitive": [r[3] for r in source.execute("""
        EXPLAIN QUERY PLAN SELECT primitive_id,COUNT(*) FROM primitive_evidence_inputs
        GROUP BY primitive_id""")],
    }
    analysis.executemany("INSERT OR REPLACE INTO query_plans VALUES(?,?)",
                         ((k, json.dumps(v)) for k, v in plans.items()))
    analysis.commit()

    # One checkpointed scan of the large relation.
    if scalar(analysis, "SELECT COUNT(*) FROM primitive_inventory") == 0:
        if scalar(analysis, "SELECT COUNT(*) FROM primitive_input_counts") == 0:
            analysis.executemany("INSERT INTO primitive_input_counts VALUES(?,?)",
                source.execute("SELECT primitive_id,COUNT(*) FROM primitive_evidence_inputs GROUP BY primitive_id"))
            analysis.commit()
        input_counts = dict(analysis.execute("SELECT primitive_id,input_count FROM primitive_input_counts"))

        # Discovery's exact rule: a subject occurring in >=2 multi-subject primitives.
        qualifying: set[str] = set()
        primitive_subjects: dict[str, tuple[str, ...]] = {}
        subject_counts: Counter[str] = Counter()
        rows = []
        for row in source.execute("""SELECT primitive_id,primitive_type,subjects_json,
                 window_start,window_end,length(output_payload_json)
                 FROM primitive_observations ORDER BY primitive_id"""):
            pid, family, raw, ws, we, payload = row
            subjects = tuple(json.loads(raw))
            primitive_subjects[pid] = subjects
            if len(subjects) >= 2:
                subject_counts.update(set(subjects))
            rows.append((pid, family, ws, we, payload or 0))
        qualifying = {s for s, n in subject_counts.items() if n >= 2}
        consumed = {pid for pid, subjects in primitive_subjects.items()
                    if len(subjects) >= 2 and any(s in qualifying for s in subjects)}
        aggregates: dict[str, list[int | None]] = {}
        for pid, family, ws, we, payload in rows:
            item = aggregates.setdefault(family, [0, 0, 0, None, None, 0, 0])
            item[0] += 1
            item[1] += input_counts.get(pid, 0)
            item[2] += payload
            stamp = we if we is not None else ws
            if stamp is not None:
                item[3] = stamp if item[3] is None else min(item[3], stamp)
                item[4] = stamp if item[4] is None else max(item[4], stamp)
            item[5 if pid in consumed else 6] += 1
        analysis.executemany("INSERT INTO primitive_inventory VALUES(?,?,?,?,?,?,?,?)",
            ((family, *values) for family, values in sorted(aggregates.items())))
        analysis.execute("INSERT OR REPLACE INTO audit_metadata VALUES('discovery_supporting_primitive_digest',?)",
                         (json.dumps(hashlib.sha256("\n".join(sorted(consumed)).encode()).hexdigest()),))
        analysis.commit()
    inventory = [dict(zip(("primitive_type","primitive_rows","provenance_links","payload_bytes",
                           "first_seen","last_seen","discovery_rows","isolated_rows"), row))
                 for row in analysis.execute("SELECT * FROM primitive_inventory ORDER BY primitive_type")]

    objects = {name: int(size) for name, size in source.execute(
        "SELECT name,SUM(pgsize) FROM dbstat GROUP BY name")}
    discovery = json.loads((REPORTS / "discovery.json").read_text())["datasets"][0]
    motifs = json.loads((REPORTS / "motifs.json").read_text())["datasets"][0]
    counts = {
      "evidence": scalar(source, "SELECT COUNT(*) FROM normalized_evidence_records"),
      "primitives": scalar(source, "SELECT COUNT(*) FROM primitive_observations"),
      "provenance_links": scalar(source, "SELECT COUNT(*) FROM primitive_evidence_inputs"),
      "discovery_candidates": discovery["candidate_count"],
      "motifs": motifs["canonical_motifs"],
      "motif_occurrences": sum(int(k) * int(v) for k, v in motifs["occurrence_distribution"].items()),
      "relationships": 686,
      "launches": 32044,
      "completed_launches": 2498,
    }
    primitive_bytes = objects.get("primitive_observations", 0)
    provenance_bytes = objects.get("primitive_evidence_inputs", 0)
    derived_bytes = primitive_bytes + provenance_bytes
    isolated_rows = sum(x["isolated_rows"] for x in inventory)
    isolated_links = sum(x["provenance_links"] * x["isolated_rows"] // max(1, x["primitive_rows"])
                         for x in inventory)
    timing = next(x for x in inventory if x["primitive_type"] == "BEHAVIOURAL_TIMING")

    compact_old = json.loads((ROOT / "database/evidence_platform/oip_v2_1d_storage_audit/prototype_checkpoint.json").read_text())
    compact_audit = json.loads((ROOT / "docs/evidence_platform/oip_v2_1d_primitive_provenance_storage.json").read_text())
    compact_ratio = compact_audit["prototype"]["compact_subsystem_bytes"] / compact_audit["prototype"]["current_link_subsystem_bytes"]
    projected_compact = round(provenance_bytes * compact_ratio)
    remaining_attempts = 21687
    growth_per_attempt = 2040669559 / 2000
    projected_unoptimized = CORPUS.stat().st_size + round(remaining_attempts * growth_per_attempt)
    provenance_share = provenance_bytes / CORPUS.stat().st_size
    projected_compact_total = round(projected_unoptimized * (1 - provenance_share + provenance_share * compact_ratio))

    population = json.load(gzip.open(POPULATION, "rt"))
    reference_time = max(row["launch_timestamp"] for row in population if row.get("launch_timestamp"))
    ages = Counter()
    creators = Counter(row.get("creator") for row in population if row.get("creator"))
    for row in population:
        stamp = row.get("launch_timestamp")
        if stamp is None:
            ages["UNKNOWN"] += 1
        else:
            days = max(0, (reference_time - stamp) / 86400)
            ages["0-7d" if days <= 7 else "8-30d" if days <= 30 else "31-90d" if days <= 90 else ">90d"] += 1
    creator_recurrence = {"unique_creators": len(creators),
                          "single_launch_creators": sum(n == 1 for n in creators.values()),
                          "recurring_creators": sum(n > 1 for n in creators.values()),
                          "launches_by_recurring_creators": sum(n for n in creators.values() if n > 1)}

    git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
                         capture_output=True, check=True).stdout.strip()
    result = {
      "milestone": "OIP v2.2A", "generated_at": datetime.now(timezone.utc).isoformat(),
      "frozen_corpus": str(CORPUS.relative_to(ROOT)), "git_head": git,
      "constraints": {"rpc_calls": 0, "production_interaction": False,
                      "canonical_deletions": 0, "semantic_changes": 0},
      "counts": counts, "physical": {"database_bytes": CORPUS.stat().st_size,
          "primitive_table_bytes": primitive_bytes, "provenance_table_bytes": provenance_bytes,
          "derived_core_bytes": derived_bytes, "objects": objects},
      "family_inventory": inventory,
      "age_analysis": {"reference_timestamp": reference_time, "launch_age_bands": dict(ages),
                       "note": "Age is relative to the frozen population maximum timestamp, not wall-clock time."},
      "creator_recurrence": creator_recurrence,
      "consumer_graph": {
        "primitive_to_discovery": "exact engine rule reconstructed from frozen Primitive subjects",
        "discovery_to_motif": "one MotifOccurrence per DiscoveryCandidate; same supporting primitives",
        "motif_to_relationship": "relationship report aggregate only; relationship participants are protected by motif protection",
        "operation_and_investigation_consumers": "not materialized in the frozen shadow corpus",
      },
      "isolation": {"primitive_rows_without_discovery_or_motif_participation": isolated_rows,
                    "estimated_links": isolated_links,
                    "candidate_dematerialization_status": "NOT_ELIGIBLE",
                    "reason": "Operation, investigation, anomaly, governance, and complete recurring-actor exclusions cannot be proven from this shadow"},
      "behavioural_timing": timing,
      "reconstruction": {"v2_1g_discovery_deterministic": discovery["deterministic"],
          "v2_1g_motif_deterministic": motifs["deterministic"],
          "v2_1d_compact_relation_identical": compact_old["semantic_relation_identical"],
          "v2_1d_source_relation_count": compact_old["source_relation_count"],
          "scope_note": "Compact-link identity was proven on v2.1D; it is compatible but not a current-corpus migration proof."},
      "prototype_gate": {"opened": False, "shadow_copy_created": False,
                         "reason": "No objectively safe cohort remains after mandatory-but-unavailable protection layers"},
      "projection": {"remaining_acquisition_attempts": remaining_attempts,
          "unoptimized_bytes_linear": projected_unoptimized,
          "current_provenance_compact_projection_bytes": projected_compact,
          "combined_total_projection_bytes": projected_compact_total,
          "compact_ratio_reused_from_v2_1d": compact_ratio,
          "caveat": "Linear engineering projection, not a measured future corpus."},
      "retention_rules": {
        "PERMANENT": "immutable artifacts, Evidence, launch identity, transaction signatures, explicit historical relationships",
        "HOT": "all Operation, motif, relationship, recurring-actor, shared-infrastructure, active, unresolved, investigation, anomaly, predecessor/successor, and governance participants",
        "COLD": "reconstructable historical derived state not needed in active replay, with searchable retained actor/evidence indexes",
        "RECOMPUTABLE": "version-pinned Primitives and downstream structures with complete immutable Evidence and dependency manifests",
        "DEMATERIALIZABLE": "only after all safety overrides are queryable and exact rematerialization equivalence is proven on the current corpus",
      },
      "decisions": {"retention": "B — COLD TIERING JUSTIFIED, DELETION NOT JUSTIFIED",
                    "provenance": "COMPACT_PROVENANCE_FIRST",
                    "acquisition": "READY_FOR_5K_AFTER_STORAGE_MILESTONE"},
      "runtime_seconds": round(time.perf_counter() - started, 3),
    }
    atomic_json(SUMMARY, result)
    family_lines = "\n".join(
        f"| {x['primitive_type']} | {x['primitive_rows']:,} | {x['provenance_links']:,} | {x['discovery_rows']:,} | {x['isolated_rows']:,} |"
        for x in inventory)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(f"""# OIP v2.2A — Historical Intelligence Retention Audit

## Executive conclusion

The frozen v2.1G corpus supports cold tiering, but it does **not** support deletion. A measurable derived population does not participate in current Discovery/motifs, yet the corpus lacks authoritative Operation, investigation, anomaly, governance, and complete recurring-actor consumer state. Treating those absences as non-use would create permanent blindness.

**Retention verdict:** {result['decisions']['retention']}
**Provenance priority:** {result['decisions']['provenance']}
**Acquisition gate:** {result['decisions']['acquisition']}

## Frozen population

- 32,044 launches; 2,498 completed launches.
- {counts['evidence']:,} Evidence records.
- {counts['primitives']:,} Primitive observations and {counts['provenance_links']:,} provenance links.
- {counts['discovery_candidates']:,} Discovery candidates, {counts['motifs']:,} motifs, and {counts['relationships']:,} relationships.
- Corpus: {CORPUS.stat().st_size:,} bytes; Primitive/provenance core: {derived_bytes:,} bytes.
- Frozen-relative launch ages: {dict(ages)}. Creator recurrence: {creator_recurrence['recurring_creators']:,} recurring creators cover {creator_recurrence['launches_by_recurring_creators']:,} launches; {creator_recurrence['single_launch_creators']:,} creators occur once.
- Zero RPC, production reads/writes, canonical deletions, or semantic changes.

## Family inventory

| Primitive family | Rows | Provenance links | Discovery/motif supporting | No Discovery/motif participation |
|---|---:|---:|---:|---:|
{family_lines}

`BEHAVIOURAL_TIMING` contains {timing['primitive_rows']:,} rows and {timing['provenance_links']:,} links; {timing['discovery_rows']:,} rows support Discovery/motifs and {timing['isolated_rows']:,} do not. Non-participation is a query-path observation, not proof of no future value.

## Consumer and safety analysis

Discovery participation was reconstructed using the frozen `DiscoveryEngine` rule: subjects with at least two multi-subject observations produce candidates, and all observations for that subject become supporting primitives. Motif canonicalization creates one occurrence per candidate and carries those same supporting primitive IDs. Relationship participants are therefore already protected by motif protection.

The shadow database contains no canonical Discovery, motif, relationship, Operation, investigation, anomaly, or governance tables. Its validator reports establish deterministic aggregate outputs, but only Primitive/Evidence dependencies are queryable at row level. The mandatory safety overrides consequently cannot all be evaluated. The apparent isolated cohort ({isolated_rows:,} Primitive rows; approximately {isolated_links:,} links) is **not eligible** for dematerialization.

## Reconstruction

v2.1G recorded deterministic Discovery and motif replay. v2.1D proved exact compact-provenance relation identity across {compact_old['source_relation_count']:,} links. That result supports a representation migration, but it is not silently promoted to proof for the enlarged v2.1G relation. A current-corpus compact migration must repeat the digest and lookup validation.

The shadow-dematerialization gate remained closed: no corpus copy was created and no row was removed. Exact protected motif/relationship equivalence after dematerialization therefore remains untested, as required when the safe cohort cannot be established.

## Retention model

- **PERMANENT:** immutable artifacts, Evidence, launch identity, signatures, timestamps, and explicit historical relationships.
- **HOT:** every Operation/motif/relationship participant; recurring actors and shared infrastructure; active, unresolved, investigated, anomalous, governed, predecessor, and successor structures.
- **COLD:** reconstructable historical derived state outside active queries, while actor and Evidence indexes remain searchable for reactivation.
- **RECOMPUTABLE:** version-pinned derived outputs whose full Evidence dependency manifest is retained.
- **DEMATERIALIZABLE:** only after every safety override is queryable and current-corpus rematerialization proves exact Primitive, Discovery, motif, and relationship equivalence.

## Storage direction

The prior compact prototype proved an exact relation-preserving representation and remains the lower-risk first storage milestone. The linear no-change projection for the remaining 21,687 attempts is {projected_unoptimized:,} bytes. Projections are directional: changing corpus composition can change both provenance density and compression yield.

After current-corpus compact-provenance validation, the acquisition evidence supports the 5,000-call stage. Retention tiering should proceed as metadata and query-path design first; deletion remains blocked until the missing protection registries and rematerialization proof exist.
""")
    source.close(); analysis.close()
    print(json.dumps({"summary": str(SUMMARY), "report": str(REPORT),
                      "retention": result["decisions"]["retention"],
                      "runtime_seconds": result["runtime_seconds"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
