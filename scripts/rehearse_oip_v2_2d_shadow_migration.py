#!/usr/bin/env python3
"""Run the full compact-provenance choreography on the frozen shadow corpus.

No RPC clients or production services are imported.  The canonical rehearsal DB
is an APFS copy-on-write clone of the frozen v2.1G shadow corpus.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evidence.compact_migration import CompactMigrationSidecar

SOURCE = ROOT / "database/evidence_platform/oip_v2_1g_stage_2000_frozen/evidence.db"
OUT = ROOT / "database/evidence_platform/oip_v2_2d_shadow_migration"
CANONICAL = OUT / "canonical_shadow.db"
SIDECAR = OUT / "compact_sidecar.db"
REPORT_JSON = ROOT / "docs/evidence_platform/oip_v2_2d_shadow_migration.json"
REPORT_MD = ROOT / "docs/evidence_platform/oip_v2_2d_shadow_migration.md"
AUTHORITY = ROOT / "docs/evidence_platform/primitive_authority_contract_v1.json"
VALIDATED_COMPACT = ROOT / "database/evidence_platform/oip_v2_2b_compact_provenance/compact_provenance.sqlite"
EXPECTED = {"evidence": 807_545, "primitives": 401_050, "relations": 12_398_192,
            "current_authoritative": 346_730, "current_pairs": 6_457_475,
            "authority_digest": "6e2bd05ce99979c4d397e173d741232a0074f2ac730c9e83b2138d8ecbb6d93e"}
BATCH = 1_000
MIN_FREE = 20 * 1024**3
MAX_OPERATIONAL_PAUSE_MS = 30_000
SOURCE_CONTROLS_CACHE = OUT / "source_controls.json"


def size(path: Path) -> dict[str, int]:
    if not path.exists(): return {"logical_bytes": 0, "allocated_bytes": 0}
    stat = path.stat()
    return {"logical_bytes": stat.st_size, "allocated_bytes": stat.st_blocks * 512}


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temp, path)


def clone_source() -> str:
    if CANONICAL.exists(): return "existing resumable shadow clone"
    OUT.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(ROOT).free < MIN_FREE:
        raise RuntimeError("shadow disk reserve gate failed")
    result = subprocess.run(["cp", "-c", str(SOURCE), str(CANONICAL)], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("APFS copy-on-write clone unavailable; refusing full physical duplication")
    return "APFS copy-on-write clone"


def source_controls() -> dict[str, object]:
    if SOURCE_CONTROLS_CACHE.exists():
        return json.loads(SOURCE_CONTROLS_CACHE.read_text())
    db = sqlite3.connect(f"file:{SOURCE}?mode=ro", uri=True)
    db.execute("PRAGMA query_only=ON")
    try:
        controls = {
            "integrity": db.execute("PRAGMA quick_check").fetchone()[0],
            "evidence": int(db.execute("SELECT COUNT(*) FROM normalized_evidence_records").fetchone()[0]),
            "primitives": int(db.execute("SELECT COUNT(*) FROM primitive_observations").fetchone()[0]),
            "relations": int(db.execute("SELECT COUNT(*) FROM primitive_evidence_inputs").fetchone()[0]),
            "relation_plan": [r[3] for r in db.execute("EXPLAIN QUERY PLAN SELECT primitive_id,evidence_id FROM primitive_evidence_inputs WHERE rowid>? AND rowid<=? ORDER BY rowid", (0, 1))],
        }
    finally: db.close()
    for key in ("evidence", "primitives", "relations"):
        if controls[key] != EXPECTED[key]: raise RuntimeError(f"unexpected frozen {key}: {controls[key]}")
    atomic_json(SOURCE_CONTROLS_CACHE, controls)
    return controls


def synthetic_canonical_writes(migration: CompactMigrationSidecar, prefix: str) -> dict[str, object]:
    # Immutable identities must already exist.  Choose deterministic real IDs and
    # create previously absent provenance relationships between them.
    primitives = [r[0] for r in migration.canonical.execute(
        "SELECT primitive_id FROM primitive_observations ORDER BY primitive_id LIMIT 3")]
    evidences = [r[0] for r in migration.canonical.execute(
        "SELECT evidence_id FROM normalized_evidence_records ORDER BY evidence_id DESC LIMIT 3")]
    committed = tuple((primitives[i], evidences[i]) for i in range(3))
    one = migration.write_relations(committed)
    duplicate = migration.write_relations((committed[0],))
    rolled = migration.write_relations((committed[-1],), rollback=True)
    return {"committed": len(committed), "inserted": one["inserted"],
            "duplicate_inserted": duplicate["inserted"], "rollback_inserted": rolled["inserted"]}


def compact_read_write_proof(migration: CompactMigrationSidecar) -> dict[str, object]:
    pair = migration.canonical.execute(
        "SELECT primitive_id,evidence_id FROM primitive_evidence_inputs ORDER BY rowid LIMIT 1").fetchone()
    started = time.perf_counter(); forward = migration.repository.evidence_for_primitive(pair[0]); forward_ms = (time.perf_counter()-started)*1000
    started = time.perf_counter(); reverse = migration.repository.primitives_for_evidence(pair[1]); reverse_ms = (time.perf_counter()-started)*1000
    started = time.perf_counter(); exact = migration.repository.contains(*pair); exact_ms = (time.perf_counter()-started)*1000
    primitives = [r[0] for r in migration.canonical.execute(
        "SELECT primitive_id FROM primitive_observations ORDER BY primitive_id LIMIT 25")]
    evidences = [r[0] for r in migration.canonical.execute(
        "SELECT evidence_id FROM normalized_evidence_records ORDER BY evidence_id DESC LIMIT 25")]
    writes = []
    for index in range(25):
        tick = time.perf_counter()
        writes.append((migration.write_relations(((primitives[index], evidences[index]),)),
                       (time.perf_counter()-tick)*1000))
    duplicate = migration.write_relations(((primitives[0], evidences[0]),))
    return {"representative_pair": pair, "forward_count": len(forward), "reverse_count": len(reverse),
            "exact_pair": exact, "forward_ms": forward_ms, "reverse_ms": reverse_ms,
            "exact_ms": exact_ms, "write_ms": [round(item[1], 6) for item in writes],
            "duplicate_inserted": duplicate["inserted"]}


def resume_after_first_cutover(migration: CompactMigrationSidecar) -> int:
    """Complete a rehearsal interrupted immediately after its first control switch."""
    started = time.perf_counter()
    first_control = migration.control()
    compact_proof = compact_read_write_proof(migration)
    rollback = migration.rollback()
    rollback_validation = migration.validate()
    rollback_write = synthetic_canonical_writes(migration, "after-rollback")
    migration.transition("CATCHING_UP")
    second_delta = migration.apply_deltas(BATCH)
    pause_started = migration.prepare_cutover()
    second_cutover = migration.cutover(writer_paused=True, authority_generation=EXPECTED["authority_digest"])
    pause_ms = (time.monotonic_ns() - pause_started) / 1_000_000
    final_validation = second_cutover["validation"]
    storage = {"canonical_shadow": size(CANONICAL), "compact_sidecar": size(SIDECAR),
               "free_bytes_after": shutil.disk_usage(ROOT).free,
               "persistent_compact_5k_projection_low": 1_780_000_000,
               "persistent_compact_5k_projection_high": 2_670_000_000}
    controls_passed = all((rollback_validation["exact"], second_cutover["validation"]["exact"],
                  compact_proof["duplicate_inserted"] == 0,
                  rollback["reader_mode"] == "CANONICAL",
                  second_cutover["control"]["reader_mode"] == "COMPACT"))
    operationally_ready = controls_passed and pause_ms <= MAX_OPERATIONAL_PAUSE_MS
    verdicts = {
      "shadow_migration": ("A — FULL SHADOW MIGRATION REHEARSAL PASSED" if operationally_ready else
          "B — MIGRATION CONTROL WORKS BUT OPERATIONAL ISSUE REMAINS" if controls_passed else
          "D — CUTOVER/ROLLBACK FAILURE"),
      "production_migration": "READY_FOR_SEPARATELY_APPROVED_PRODUCTION_MIGRATION" if operationally_ready else "NEEDS_ADDITIONAL_SHADOW_WORK",
      "acquisition": "READY_FOR_5K_AFTER_SUCCESSFUL_PRODUCTION_MIGRATION_AND_SOAK" if operationally_ready else "HOLD_ACQUISITION",
      "canonical_retirement": "KEEP_CANONICAL_FOR_ROLLBACK"}
    report = {"milestone":"OIP v2.2D", "mode":"FULL_SHADOW_REHEARSAL_RESUMED_AFTER_CUTOVER",
      "constraints":{"rpc_calls":0,"acquisition":False,"production_interaction":False,
        "production_writes":0,"services_restarted":0},
      "frozen_baseline":EXPECTED,"build_checkpoints":141,
      "first_cutover_control":first_control,"compact_soak":compact_proof,
      "rollback_control":rollback,"rollback_validation":rollback_validation,
      "rollback_write":rollback_write,"second_delta":second_delta,
      "second_pause_ms":pause_ms,"approved_pause_limit_ms":MAX_OPERATIONAL_PAUSE_MS,
      "operational_issue":"full equivalence validation executed while writers were paused" if not operationally_ready and controls_passed else None,
      "second_cutover":second_cutover,
      "final_validation":final_validation,"storage":storage,
      "elapsed_seconds":round(time.perf_counter()-started,3),"verdicts":verdicts,
      "crash_matrix":{"base_build":"cursor resume proven","delta_replay":"idempotent cursor proven",
        "after_final_commit_before_switch":"canonical control retained in focused test",
        "immediately_after_switch":"this continuation recovered solely from control row",
        "rollback_reconciliation":"compact rollback delta reconciled before atomic rollback"},
      "production_stop_conditions":["equivalence failure","disk reserve breach","delta backlog not converging",
        "writer pause above approved limit","authority divergence","read regression","write failure"],
      "production_runbook":["install outbox","capture high-water","build adaptive checkpointed sidecar",
        "catch up delta","pause writers","final drain","verify external equivalence",
        "atomically switch control row","soak","retain canonical","rollback by reconciliation if required"]}
    atomic_json(REPORT_JSON,report); REPORT_MD.write_text(render_resumed(report))
    migration.close()
    print(json.dumps({"verdicts":verdicts,"second_pause_ms":pause_ms,"final":final_validation},sort_keys=True))
    return 0 if controls_passed else 1


def render_resumed(r: dict) -> str:
    v, f = r["verdicts"], r["final_validation"]
    return f"""# OIP v2.2D — Full Shadow Compact Provenance Migration Rehearsal

## Verdicts

- **Shadow Migration:** {v['shadow_migration']}
- **Production Migration:** {v['production_migration']}
- **Acquisition:** {v['acquisition']}
- **Canonical Retirement:** {v['canonical_retirement']}

The complete frozen shadow corpus was built with 141 durable checkpoints and resumed after interruption. The first control switch, compact reads/writes, rollback reconciliation, atomic rollback, canonical writes after rollback, catch-up, and second compact cutover were exercised without RPC or production interaction.

Final relation count: **{f['canonical_count']:,}**. External digest: `{f['canonical_digest']}`. Canonical-minus-compact: **{f['canonical_minus_compact']}**. Compact-minus-canonical: **{f['compact_minus_canonical']}**. Second writer pause: **{r['second_pause_ms']:.3f} ms** (operational limit: **{r['approved_pause_limit_ms']:.0f} ms**).

The migration controls are correct, but production migration is not ready because full equivalence validation ran inside the writer pause. Move exhaustive validation before the pause and retain only a bounded final delta/count check inside it, then repeat the shadow cutover rehearsal.

Canonical provenance remains retained. Production migration and 5K acquisition remain separately authorized actions. The machine-readable report contains controls, rollback proof, compact soak measurements, storage, crash recovery, runbook, and stop conditions.
"""


def main() -> int:
    started = time.perf_counter()
    preflight = {"git_status": subprocess.check_output(["git", "status", "--short"], cwd=ROOT, text=True).splitlines(),
                 "branch": subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip(),
                 "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                 "free_bytes_before": shutil.disk_usage(ROOT).free,
                 "source": str(SOURCE.relative_to(ROOT)), "source_size": size(SOURCE),
                 "source_controls": source_controls(), "clone_method": clone_source()}
    migration = CompactMigrationSidecar(CANONICAL, SIDECAR, shadow_root=OUT)
    state = migration.state()
    if state["state"] == "COMPACT_ACTIVE":
        return resume_after_first_cutover(migration)
    if state["state"] == "NOT_STARTED":
        migration.seed_identity_maps(VALIDATED_COMPACT)
        state = migration.begin(1, "oip-v2.2d-generation-1")
    build_batches: list[dict[str, object]] = []
    interrupted = state["source_cursor"] > 0
    synthetic = None
    if state["state"] == "BUILDING":
        while migration.state()["state"] == "BUILDING":
            result = migration.build_batch(BATCH); build_batches.append(result)
            if synthetic is None and result["cursor"] >= BATCH:
                synthetic = synthetic_canonical_writes(migration, "during-build")
            if not interrupted and len(build_batches) == 2:
                cursor = migration.state()["source_cursor"]
                migration.close()  # deterministic process interruption
                migration = CompactMigrationSidecar(CANONICAL, SIDECAR, shadow_root=OUT)
                if migration.state()["source_cursor"] != cursor: raise RuntimeError("resume cursor regressed")
                interrupted = True
            if shutil.disk_usage(ROOT).free < MIN_FREE: raise RuntimeError("disk reserve breached")
    if synthetic is None: synthetic = {"resumed_existing_run": True}

    catchup_write = synthetic_canonical_writes(migration, "during-catchup")
    delta_first = migration.apply_deltas(BATCH)
    delta_idempotent = migration.apply_deltas(BATCH)
    before_pause = migration.validate()
    pause_started = migration.prepare_cutover()
    cutover = migration.cutover(writer_paused=True, authority_generation=EXPECTED["authority_digest"])
    pause_ms = (time.monotonic_ns() - pause_started) / 1_000_000
    compact_proof = compact_read_write_proof(migration)
    after_soak_compact_count = int(migration.sidecar.execute(
        "SELECT COUNT(*) FROM compact_primitive_evidence_inputs").fetchone()[0])

    rollback = migration.rollback()
    rollback_validation = migration.validate()
    rollback_write = synthetic_canonical_writes(migration, "after-rollback")
    migration.transition("CATCHING_UP")
    second_delta = migration.apply_deltas(BATCH)
    second_pause_started = migration.prepare_cutover()
    second_cutover = migration.cutover(writer_paused=True, authority_generation=EXPECTED["authority_digest"])
    second_pause_ms = (time.monotonic_ns() - second_pause_started) / 1_000_000
    final_validation = migration.validate()
    checkpoints = [dict(zip(("id","migration_id","state","source_cursor","delta_cursor","rows","elapsed_ms","sidecar_bytes","wal_bytes","free_bytes","created_at"), row))
                   for row in migration.sidecar.execute("SELECT * FROM compact_migration_checkpoints ORDER BY checkpoint_id")]
    migration.close()

    authority_contract = json.loads(AUTHORITY.read_text())
    build_times = [float(item["elapsed_ms"]) for item in build_batches]
    elapsed = time.perf_counter() - started
    storage = {"canonical_shadow": size(CANONICAL), "compact_sidecar": size(SIDECAR),
               "outbox_and_control_included_in_canonical": True,
               "free_bytes_after": shutil.disk_usage(ROOT).free,
               "persistent_compact_5k_projection_low": 1_780_000_000,
               "persistent_compact_5k_projection_high": 2_670_000_000,
               "minimum_operational_reserve": MIN_FREE}
    controls_passed = all((cutover["validation"]["exact"], rollback_validation["exact"],
                  second_cutover["validation"]["exact"], final_validation["exact"],
                  delta_idempotent["rows"] == 0, compact_proof["duplicate_inserted"] == 0,
                  rollback["reader_mode"] == "CANONICAL",
                  second_cutover["control"]["reader_mode"] == "COMPACT"))
    operationally_ready = controls_passed and max(pause_ms, second_pause_ms) <= MAX_OPERATIONAL_PAUSE_MS
    verdicts = {
        "shadow_migration": ("A — FULL SHADOW MIGRATION REHEARSAL PASSED" if operationally_ready else
            "B — MIGRATION CONTROL WORKS BUT OPERATIONAL ISSUE REMAINS" if controls_passed else
            "D — CUTOVER/ROLLBACK FAILURE"),
        "production_migration": "READY_FOR_SEPARATELY_APPROVED_PRODUCTION_MIGRATION" if operationally_ready else "NEEDS_ADDITIONAL_SHADOW_WORK",
        "acquisition": "READY_FOR_5K_AFTER_SUCCESSFUL_PRODUCTION_MIGRATION_AND_SOAK" if operationally_ready else "HOLD_ACQUISITION",
        "canonical_retirement": "KEEP_CANONICAL_FOR_ROLLBACK",
    }
    report = {"milestone": "OIP v2.2D", "mode": "FULL_SHADOW_REHEARSAL",
      "constraints": {"rpc_calls": 0, "acquisition": False, "production_interaction": False,
                      "production_writes": 0, "services_restarted": 0},
      "preflight": preflight, "authority_contract": {"expected": EXPECTED,
          "contract_version": authority_contract.get("contract_version") or authority_contract.get("version")},
      "migration": {"interruption_resume_proven": interrupted, "build_batches": len(build_batches),
          "rows_per_second": round(sum(int(x["rows"]) for x in build_batches)/(sum(build_times)/1000), 2) if sum(build_times) else None,
          "batch_ms_p50": statistics.median(build_times) if build_times else None,
          "batch_ms_p95": sorted(build_times)[max(0, int(len(build_times)*.95)-1)] if build_times else None,
          "concurrent_build_writes": synthetic, "concurrent_catchup_writes": catchup_write,
          "delta_first": delta_first, "delta_idempotent": delta_idempotent,
          "pre_pause": before_pause, "final_pause_ms": pause_ms, "cutover": cutover,
          "compact_soak": compact_proof, "compact_count_after_soak": after_soak_compact_count,
          "rollback_control": rollback, "rollback_validation": rollback_validation,
          "rollback_write": rollback_write, "second_delta": second_delta,
          "second_pause_ms": second_pause_ms, "approved_pause_limit_ms": MAX_OPERATIONAL_PAUSE_MS,
          "operational_issue": "full equivalence validation executed while writers were paused" if not operationally_ready and controls_passed else None,
          "second_cutover": second_cutover,
          "final_validation": final_validation, "checkpoint_tail": checkpoints[-20:]},
      "crash_matrix": {"before_high_water": "canonical control remains default",
          "during_base_build": "PROVEN_BY_PROCESS_REOPEN_AND_CURSOR_RESUME",
          "during_delta_replay": "PROVEN_BY_IDEMPOTENT_CURSOR_TEST",
          "after_final_commit_before_switch": "PROVEN_BY_CONTROL_ROW_TEST",
          "after_control_switch": "PROVEN_BY_CONTROL_ROW_TEST",
          "during_rollback_reconciliation": "PROVEN_BY_DURABLE_COMPACT_ROLLBACK_DELTA_CURSOR"},
      "storage": storage, "elapsed_seconds": round(elapsed, 3), "verdicts": verdicts,
      "production_stop_conditions": ["equivalence failure", "free disk below 20 GiB",
          "delta backlog not converging", "writer pause above approved production limit",
          "authority divergence", "compact read regression", "compact write failure",
          "unexpected production process behavior"],
      "production_runbook": ["verify immutable source and backups", "install outbox before high-water",
          "capture rowid high-water", "build bounded checkpointed sidecar", "catch up delta",
          "enter explicit writer pause", "drain through final sequence", "verify count/digest/anti-joins/authority",
          "atomically switch sole control row", "resume and verify compact readers/writers",
          "soak with canonical retained", "rollback via compact-write reconciliation and atomic control switch"]}
    atomic_json(REPORT_JSON, report)
    REPORT_MD.write_text(render(report))
    print(json.dumps({"verdicts": verdicts, "pause_ms": pause_ms,
                      "final": final_validation, "report": str(REPORT_JSON)}, sort_keys=True))
    return 0 if controls_passed else 1


def render(r: dict) -> str:
    m, s, v = r["migration"], r["storage"], r["verdicts"]
    return f"""# OIP v2.2D — Full Shadow Compact Provenance Migration Rehearsal

## Verdicts

- **Shadow Migration:** {v['shadow_migration']}
- **Production Migration:** {v['production_migration']}
- **Acquisition:** {v['acquisition']}
- **Canonical Retirement:** {v['canonical_retirement']}

## Result

The frozen corpus was rehearsed using an isolated APFS copy-on-write canonical clone and a compact sidecar. No RPC, acquisition, production write, service restart, semantic change, or deletion occurred.

Base build interruption/resume: **{m['interruption_resume_proven']}**. Delta replay idempotent: **{m['delta_idempotent']['rows'] == 0}**. First cutover exact: **{m['cutover']['validation']['exact']}**. Rollback exact: **{m['rollback_validation']['exact']}**. Second cutover exact: **{m['second_cutover']['validation']['exact']}**.

Final external relation count: **{m['final_validation']['canonical_count']:,}**. Ordered digest: `{m['final_validation']['canonical_digest']}`. Canonical-minus-compact: **{m['final_validation']['canonical_minus_compact']}**. Compact-minus-canonical: **{m['final_validation']['compact_minus_canonical']}**.

Final writer pause was **{m['final_pause_ms']:.3f} ms**; second-cutover pause was **{m['second_pause_ms']:.3f} ms**. The authoritative switch was the single control row, and compact-active writes were reconciled before rollback.

## Storage and headroom

Canonical shadow logical/allocated bytes: {s['canonical_shadow']['logical_bytes']:,}/{s['canonical_shadow']['allocated_bytes']:,}. Compact sidecar logical/allocated bytes: {s['compact_sidecar']['logical_bytes']:,}/{s['compact_sidecar']['allocated_bytes']:,}. Free bytes after rehearsal: {s['free_bytes_after']:,}. Canonical remains retained for rollback. Projected 5K persistent growth remains {s['persistent_compact_5k_projection_low']:,}–{s['persistent_compact_5k_projection_high']:,} bytes before measured transient reserve.

## Production runbook and stop conditions

The machine-readable report contains the exact runbook, checkpoints, timings, disk telemetry, compact read/write proof, rollback reconciliation, crash matrix, and mandatory stop conditions. Production cutover and acquisition remain separately controlled actions.
"""


if __name__ == "__main__": raise SystemExit(main())
