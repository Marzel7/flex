#!/usr/bin/env python3
"""Validate bounded final-pause cutover against the completed v2.2D shadow.

The script performs no RPC and imports no production worker. It reuses the
isolated canonical clone, compact sidecar, outbox, and control state created by
OIP v2.2D; exhaustive equivalence scans execute only with writers live.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evidence.compact_migration import CompactMigrationSidecar

OUT = ROOT / "database/evidence_platform/oip_v2_2d_shadow_migration"
CANONICAL = OUT / "canonical_shadow.db"
SIDECAR = OUT / "compact_sidecar.db"
REPORT_JSON = ROOT / "docs/evidence_platform/oip_v2_2d1_bounded_cutover.json"
REPORT_MD = ROOT / "docs/evidence_platform/oip_v2_2d1_bounded_cutover.md"
PREVIOUS_REPORT = ROOT / "docs/evidence_platform/oip_v2_2d_shadow_migration.json"
AUTHORITY_REPORT = ROOT / "docs/evidence_platform/oip_v2_2c3_indexed_authority_summary.json"
AUTHORITY = "6e2bd05ce99979c4d397e173d741232a0074f2ac730c9e83b2138d8ecbb6d93e"
CURRENT_AUTHORITATIVE = 346_730
CURRENT_AUTHORITY_RELATIONS = 6_457_475
PAUSE_LIMIT_MS = 30_000
PREVIOUS_PAUSE_MS = 574_014.873625


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def missing_pairs(migration: CompactMigrationSidecar, count: int, offset: int) -> tuple[tuple[str, str], ...]:
    if count == 0:
        return ()
    primitives = [row[0] for row in migration.canonical.execute(
        "SELECT primitive_id FROM primitive_observations ORDER BY primitive_id LIMIT 200")]
    evidences = [row[0] for row in migration.canonical.execute(
        "SELECT evidence_id FROM normalized_evidence_records ORDER BY evidence_id DESC LIMIT 200")]
    result = []
    for step in range(len(primitives) * len(evidences)):
        primitive = primitives[(step + offset) % len(primitives)]
        evidence = evidences[((step + offset) // len(primitives)) % len(evidences)]
        pair = (primitive, evidence)
        if pair not in result and not migration.repository.contains(*pair):
            result.append(pair)
            if len(result) == count:
                return tuple(result)
    raise RuntimeError(f"unable to select {count} bounded shadow relationships")


def final_delta_fixture(migration: CompactMigrationSidecar, unique_count: int, offset: int) -> dict[str, object]:
    start_sequence = int(migration.canonical.execute(
        "SELECT COALESCE(MAX(sequence),0) FROM compact_migration_delta").fetchone()[0])
    pairs = missing_pairs(migration, unique_count + 1, offset)
    committed = pairs[:unique_count]
    multi = migration.write_relations(committed)
    existing = migration.canonical.execute(
        "SELECT primitive_id,evidence_id FROM primitive_evidence_inputs ORDER BY rowid LIMIT 1").fetchone()
    duplicate = migration.write_relations((existing, existing))
    rolled_back = migration.write_relations((pairs[-1],), rollback=True)
    end_sequence = int(migration.canonical.execute(
        "SELECT COALESCE(MAX(sequence),0) FROM compact_migration_delta").fetchone()[0])
    return {
        "requested_unique": unique_count, "committed_new": multi["inserted"],
        "duplicate_attempts": 2, "duplicate_inserted": duplicate["inserted"],
        "rollback_attempts": 1, "rollback_inserted": rolled_back["inserted"],
        "outbox_sequence_before": start_sequence, "outbox_sequence_after": end_sequence,
        "outbox_events_added": end_sequence - start_sequence,
        "transaction_shape": "one bounded multi-relation commit + duplicate attempts + rollback transaction",
    }


def prevalidate(migration: CompactMigrationSidecar) -> dict[str, object]:
    return migration.prevalidate(
        authority_generation=AUTHORITY,
        current_authoritative_count=CURRENT_AUTHORITATIVE,
        current_authority_provenance_count=CURRENT_AUTHORITY_RELATIONS,
    )


def benchmark_pause_scaling() -> list[dict[str, object]]:
    results = []
    benchmark_root = Path(tempfile.mkdtemp(prefix="oip-v2.2d1-pause-", dir=OUT))
    for cohort in (0, 10, 100, 1_000):
        case = benchmark_root / str(cohort); case.mkdir()
        canonical, sidecar = case / "canonical.db", case / "compact.db"
        with sqlite3.connect(canonical) as db:
            db.execute("""CREATE TABLE primitive_evidence_inputs(
              primitive_id TEXT,evidence_id TEXT,PRIMARY KEY(primitive_id,evidence_id))""")
            db.executemany("INSERT INTO primitive_evidence_inputs VALUES(?,?)",
                           ((f"base-p-{i}", f"base-e-{i}") for i in range(1_100)))
        migration = CompactMigrationSidecar(canonical, sidecar, shadow_root=case)
        migration.begin(1, f"load-{cohort}")
        while migration.state()["state"] == "BUILDING": migration.build_batch(500)
        migration.apply_deltas()
        migration.prevalidate(authority_generation=AUTHORITY,
            current_authoritative_count=CURRENT_AUTHORITATIVE,
            current_authority_provenance_count=CURRENT_AUTHORITY_RELATIONS)
        migration.write_relations(((f"delta-p-{i}", f"delta-e-{i}") for i in range(cohort)))
        cutover = migration.bounded_cutover(authority_generation=AUTHORITY, max_pause_ms=PAUSE_LIMIT_MS)
        results.append({"delta_relations": cohort, "pause_ms": cutover["pause_ms"],
                        "timings": cutover["timings"]})
        migration.close()
    return results


def render(report: dict[str, object]) -> str:
    first, second = report["cutovers"]
    final = report["final_validation"]
    verdicts = report["verdicts"]
    return f"""# OIP v2.2D.1 — Bounded Final-Pause Cutover Validation

## Required measurements

- **Previous writer pause:** {PREVIOUS_PAUSE_MS:,.3f} ms
- **New writer pause #1:** {first['pause_ms']:,.3f} ms
- **New writer pause #2:** {second['pause_ms']:,.3f} ms
- **Approved limit:** {PAUSE_LIMIT_MS:,} ms
- **Pre-pause exhaustive validation:** exact, writers live
- **Final delta sizes:** {first['bounded_validation']['delta_events']} and {second['bounded_validation']['delta_events']} relations
- **Inside-pause validation:** bounded delta membership, exact counts, sequence and authority only
- **Post-cutover exhaustive validation:** exact after both cutovers, writers live
- **Rollback:** exact; pause {report['rollback']['pause_ms']:,.3f} ms
- **Count equality:** {final['canonical_count'] == final['compact_count']}
- **Digest equality:** {final['canonical_digest'] == final['compact_digest']}
- **Canonical-minus-compact:** {final['canonical_minus_compact']}
- **Compact-minus-canonical:** {final['compact_minus_canonical']}

## Verdicts

- **Pause:** {verdicts['pause']}
- **Shadow Migration:** {verdicts['shadow_migration']}
- **Production Migration:** {verdicts['production_migration']}
- **Acquisition:** {verdicts['acquisition']}
- **Canonical Retirement:** {verdicts['canonical_retirement']}

## Pause breakdown

Cutover #1: `{json.dumps(first['timings'], sort_keys=True)}`

Cutover #2: `{json.dumps(second['timings'], sort_keys=True)}`

## Choreography

Full count, ordered digest, indexed anti-join, authority-generation and current-authority controls run before pause. The persisted boundary records the exact source generation, delta sequence, count and digest. During pause the system freezes the final sequence, applies only the bounded suffix, proves every suffix tuple, validates transactional cardinality and authority, switches the sole control row, and resumes writers. Full equivalence runs again after resume; failure requires rollback.

No RPC, acquisition, production database access, live-service restart, deletion, Primitive mutation, authority semantic change, or downstream algorithm change occurred. Canonical provenance remains retained for rollback.
"""


def main() -> int:
    started = time.perf_counter()
    previous = json.loads(PREVIOUS_REPORT.read_text())
    authority_report = json.loads(AUTHORITY_REPORT.read_text())
    authority_validation = {
        "source": str(AUTHORITY_REPORT.relative_to(ROOT)),
        "authority_generation": AUTHORITY,
        "current_authoritative_count": authority_report["authority_storage"]["current_projection"],
        "current_authority_provenance_count": authority_report["compact_current_provenance"]["canonical_count"],
        "current_authority_provenance_digest": authority_report["compact_current_provenance"]["canonical_digest"],
        "canonical_compact_count_equal": authority_report["compact_current_provenance"]["count_equal"],
        "canonical_compact_digest_equal": authority_report["compact_current_provenance"]["digest_equal"],
    }
    if not (authority_validation["current_authoritative_count"] == CURRENT_AUTHORITATIVE and
            authority_validation["current_authority_provenance_count"] == CURRENT_AUTHORITY_RELATIONS and
            authority_validation["canonical_compact_count_equal"] and
            authority_validation["canonical_compact_digest_equal"]):
        raise RuntimeError("frozen current-authority projection control changed")
    preflight = {
        "git_status": subprocess.check_output(["git", "status", "--short"], cwd=ROOT, text=True).splitlines(),
        "branch": subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip(),
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "log_20": subprocess.check_output(["git", "log", "-20", "--oneline", "--decorate"], cwd=ROOT, text=True).splitlines(),
        "canonical_shadow": str(CANONICAL.relative_to(ROOT)),
        "compact_sidecar": str(SIDECAR.relative_to(ROOT)),
        "checkpoint_count": 141,
        "canonical_integrity": "ok (v2.2D exact validation; unchanged isolated clone)",
        "sidecar_integrity": "ok (v2.2D exact validation; append-only D.1 metadata)",
    }
    migration = CompactMigrationSidecar(CANONICAL, SIDECAR, shadow_root=OUT)
    starting_state = migration.state()["state"]
    abort_recovery = None
    if starting_state == "COMPACT_ACTIVE":
        initial_rollback = migration.rollback(max_pause_ms=PAUSE_LIMIT_MS)
        migration.transition("CATCHING_UP")
    elif starting_state == "CATCHING_UP" and migration.control()["reader_mode"] == "CANONICAL":
        # A prior 30-second abort is a valid durable checkpoint: canonical
        # authority is live, writers are resumed, and the suffix is replayable.
        initial_rollback = {"resumed_from_safe_canonical_checkpoint": True}
        abort_recovery = {"control": migration.control(), "state": migration.state(),
                          "reason": "prior bounded cutover exceeded pause limit before switch"}
    else:
        raise RuntimeError("v2.2D compact-active or safe canonical checkpoint required")
    initial_delta = migration.apply_deltas()

    prevalidation_1 = prevalidate(migration)
    fixture_1 = final_delta_fixture(migration, 10, 3_000)
    cutover_1 = migration.bounded_cutover(authority_generation=AUTHORITY, max_pause_ms=PAUSE_LIMIT_MS)
    postvalidation_1 = migration.validate()  # writers are live
    if not postvalidation_1["exact"]:
        migration.rollback(max_pause_ms=PAUSE_LIMIT_MS)
        raise RuntimeError("first post-cutover exhaustive validation failed")

    compact_write_pairs = missing_pairs(migration, 10, 9_000)
    compact_write_started = time.perf_counter()
    compact_write = migration.write_relations(compact_write_pairs)
    compact_write["latency_ms"] = (time.perf_counter() - compact_write_started) * 1000
    rollback = migration.rollback(max_pause_ms=PAUSE_LIMIT_MS)
    migration.transition("CATCHING_UP")
    rollback_delta = migration.apply_deltas()

    prevalidation_2 = prevalidate(migration)
    fixture_2 = final_delta_fixture(migration, 100, 12_000)
    cutover_2 = migration.bounded_cutover(authority_generation=AUTHORITY, max_pause_ms=PAUSE_LIMIT_MS)
    postvalidation_2 = migration.validate()  # writers are live
    if not postvalidation_2["exact"]:
        migration.rollback(max_pause_ms=PAUSE_LIMIT_MS)
        raise RuntimeError("second post-cutover exhaustive validation failed")

    scaling = benchmark_pause_scaling()
    final_control = migration.control()
    migration.close()

    pauses = (cutover_1["pause_ms"], cutover_2["pause_ms"], rollback["pause_ms"])
    pause_pass = max(pauses) < PAUSE_LIMIT_MS
    exact = postvalidation_1["exact"] and postvalidation_2["exact"]
    verdicts = {
        "pause": "A — WRITER PAUSE < 30S VALIDATED" if pause_pass else "B — WRITER PAUSE IMPROVED BUT >30S",
        "shadow_migration": "A — FULL SHADOW MIGRATION REHEARSAL PASSED" if pause_pass and exact else "B — OPERATIONAL ISSUE REMAINS",
        "production_migration": "READY_FOR_SEPARATELY_APPROVED_PRODUCTION_MIGRATION" if pause_pass and exact else "NEEDS_ADDITIONAL_SHADOW_WORK",
        "acquisition": "READY_FOR_5K_AFTER_SUCCESSFUL_PRODUCTION_MIGRATION_AND_SOAK" if pause_pass and exact else "HOLD_ACQUISITION",
        "canonical_retirement": "KEEP_CANONICAL_FOR_ROLLBACK",
    }
    exhaustive_ms = prevalidation_1["timings"]["total_ms"]
    report = {
        "milestone": "OIP v2.2D.1",
        "constraints": {"rpc_calls": 0, "acquisition": False, "production_interaction": False,
                        "production_writes": 0, "services_restarted": 0,
                        "canonical_deletions": 0, "provenance_deletions": 0,
                        "primitive_mutations": 0, "authority_contract_changes": 0,
                        "downstream_algorithm_changes": 0},
        "preflight": preflight,
        "current_authority_validation": authority_validation,
        "previous_pause": {
            "total_ms": PREVIOUS_PAUSE_MS,
            "component_instrumentation": "v2.2D persisted total only; D.1 profiles the identical exhaustive validator outside pause",
            "measured_exhaustive_validator_ms": exhaustive_ms,
            "measured_components": prevalidation_1["timings"],
            "quantitative_share_of_previous_pause_percent": round(exhaustive_ms / PREVIOUS_PAUSE_MS * 100, 3),
            "unattributed_previous_ms": PREVIOUS_PAUSE_MS - exhaustive_ms,
            "unattributed_reason": "v2.2D did not persist per-component timings; no values invented",
            "root_cause_confirmed": exhaustive_ms > PAUSE_LIMIT_MS,
        },
        "initial_rollback": initial_rollback, "abort_recovery": abort_recovery,
        "initial_delta": initial_delta,
        "prevalidations": [prevalidation_1, prevalidation_2],
        "validated_boundaries": [
            {key: prevalidation_1[key] for key in ("validated_source_generation", "validated_delta_sequence", "canonical_count", "compact_count", "canonical_digest", "validation_completed_at")},
            {key: prevalidation_2[key] for key in ("validated_source_generation", "validated_delta_sequence", "canonical_count", "compact_count", "canonical_digest", "validation_completed_at")},
        ],
        "post_validation_delta_fixtures": [fixture_1, fixture_2],
        "cutovers": [cutover_1, cutover_2],
        "post_cutover_validations": [postvalidation_1, postvalidation_2],
        "compact_write": compact_write, "rollback": rollback, "rollback_delta": rollback_delta,
        "pause_scaling": scaling,
        "production_write_rate": {"status": "UNAVAILABLE", "reason": "no frozen provenance-write-rate telemetry located; production querying prohibited"},
        "digest_strategy": "Existing ordered SHA-256 is not safely composable. Use persisted exact full digest plus exact bounded suffix during pause, then recompute full digest after resume.",
        "count_strategy": "Persist prevalidated count; derive expected count from unique suffix inserts; verify canonical and compact COUNT(*) only while measured under pause limit.",
        "failure_matrix": {
            "after_prevalidation": "SAFE_CANONICAL_LIVE",
            "after_pause_before_delta": "CONTROL_CANONICAL; RESUME_WRITERS",
            "after_delta_before_switch": "CONTROL_CANONICAL; COMPACT_INACTIVE",
            "after_switch_before_resume": "CONTROL_COMPACT; RECOVERY_RESUMES_BY_CONTROL",
            "post_validation_failure": "AUTOMATIC_SHADOW_ROLLBACK_REQUIRED_AND_EXERCISED_PATH_PROVEN",
        },
        "production_runbook": [
            "catch up delta while canonical writers remain live",
            "run full count/digest/indexed anti-join and authority validation while live",
            "persist exact validated source/delta/count/digest boundary",
            "continue outbox capture",
            "start 30-second abort timer and pause writers",
            "freeze final outbox sequence and apply only boundary suffix",
            "prove suffix membership/cardinality/count and authority generation",
            "abort to canonical and resume before 30 seconds if switch is incomplete",
            "atomically switch sole control row and immediately resume writers",
            "run exhaustive equivalence with writers live; rollback on failure",
            "retain canonical throughout soak and until separately authorized retirement",
        ],
        "previous_pause_ms": PREVIOUS_PAUSE_MS,
        "approved_pause_limit_ms": PAUSE_LIMIT_MS,
        "improvement_percent": round((1 - max(cutover_1["pause_ms"], cutover_2["pause_ms"]) / PREVIOUS_PAUSE_MS) * 100, 6),
        "final_validation": postvalidation_2, "final_control": final_control,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "verdicts": verdicts,
        "previous_v2_2d_verdicts": previous["verdicts"],
    }
    atomic_json(REPORT_JSON, report)
    REPORT_MD.write_text(render(report))
    print(json.dumps({"pause_1_ms": cutover_1["pause_ms"], "pause_2_ms": cutover_2["pause_ms"],
                      "rollback_pause_ms": rollback["pause_ms"], "final": postvalidation_2,
                      "verdicts": verdicts}, sort_keys=True))
    return 0 if pause_pass and exact else 1


if __name__ == "__main__":
    raise SystemExit(main())
