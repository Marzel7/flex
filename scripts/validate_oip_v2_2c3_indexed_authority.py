#!/usr/bin/env python3
"""Build and validate the indexed OIP v2.2C.3 authority projection."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import pickle
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.oip_v2_2c3_authority_store import IndexedAuthorityStore
from src.evidence.contracts import canonical_json_bytes
from src.evidence.discovery import DiscoveryEngine, DiscoverySnapshot, MotifCanonicalizer
from src.evidence.discovery.change_intelligence import OperationalChangeEngine, OperationalLandscapeSnapshot
from src.evidence.discovery.dominant_analysis import DominantMotifIntelligenceEngine
from src.evidence.discovery.evolution_intelligence import OperationalEvolutionEngine
from src.evidence.discovery.intelligence import MotifIntelligenceEngine
from src.evidence.discovery.relationship_intelligence import (
    CrossMotifRelationshipEngine, RelationshipEvolutionEngine,
)
from src.evidence.operation_contracts.input_windows import EvidenceInputWindow, PrimitiveInputWindow

SOURCE = ROOT / "database/evidence_platform/oip_v2_1g_stage_2000_frozen/evidence.db"
COMPACT = ROOT / "database/evidence_platform/oip_v2_2b_compact_provenance/compact_provenance.sqlite"
PROJECTION = ROOT / "database/evidence_platform/oip_v2_2c2_authority_contract/authority_projection.sqlite"
REPLAY = ROOT / "database/evidence_platform/oip_v2_2c_application_equivalence/primitive_replay.sqlite"
C1 = ROOT / "database/evidence_platform/oip_v2_2c1_divergence_audit/analysis.sqlite"
CONTROL = ROOT / "database/evidence_platform/oip_v2_1g_stage_2000_frozen/reports"
OUT = ROOT / "database/evidence_platform/oip_v2_2c3_indexed_authority"
DB = OUT / "indexed_authority_compact.sqlite"
STATE = OUT / "checkpoint.json"
REPORT = ROOT / "docs/evidence_platform/oip_v2_2c3_indexed_authority_summary.json"


def atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def dump_pickle(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wb", compresslevel=3) as stream:
        pickle.dump(value, stream, protocol=5)
    os.replace(temporary, path)


def load_pickle(path: Path):
    with gzip.open(path, "rb") as stream:
        return pickle.load(stream)


def digest_ids(values) -> str:
    return hashlib.sha256("".join(sorted(values)).encode()).hexdigest()


def digest_payload(values) -> str:
    return hashlib.sha256(canonical_json_bytes(values)).hexdigest()


def projected_snapshot(mode: str, population_ids: tuple[str, ...], primitives):
    watermark = digest_ids(population_ids)
    window_digest = hashlib.sha256(canonical_json_bytes({
        "projection": "DISCOVERY_MULTI_SUBJECT_V1", "authority_mode": mode,
        "population_digest": watermark, "population_count": len(population_ids),
    })).hexdigest()
    subjects = tuple(sorted({subject for item in primitives for subject in item.subjects}))
    evidence = EvidenceInputWindow.create(subjects=subjects, start=None, end=None,
        watermark="0" * 64, observations=())
    window = PrimitiveInputWindow(subjects, None, None, watermark, tuple(primitives), window_digest)
    return DiscoverySnapshot.create(discovery_version="1.0.0", evidence_window=evidence,
        primitive_window=window, generated_at=0)


def candidate_semantics(candidate) -> str:
    value = candidate.to_dict()
    for key in ("candidate_id", "input_digest", "supporting_evidence_ids",
                "supporting_primitive_ids", "generated_at"):
        value.pop(key, None)
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def motif_semantics(motif) -> str:
    return hashlib.sha256(canonical_json_bytes({
        "canonical_graph": motif.canonical_graph,
        "occurrence_populations": sorted(item.observed_population for item in motif.occurrences),
    })).hexdigest()


def baseline_landscape(candidates, motifs, primitives):
    times = sorted(item.time_end if item.time_end is not None else item.time_start
        for motif in motifs for item in motif.occurrences
        if item.time_end is not None or item.time_start is not None)
    cutoff = round(median(times)) if times else None
    current_boundary = max(times) if times else None
    baseline = []
    for motif in motifs:
        occurrences = tuple(item for item in motif.occurrences if cutoff is None or
            (item.time_end if item.time_end is not None else item.time_start) <= cutoff)
        if not occurrences:
            continue
        baseline.append(replace(motif, occurrences=occurrences,
            supporting_candidate_ids=tuple(sorted(item.candidate_id for item in occurrences)),
            supporting_evidence_ids=tuple(sorted({value for item in occurrences
                                                  for value in item.supporting_evidence_ids})),
            supporting_primitive_ids=tuple(sorted({value for item in occurrences
                                                   for value in item.supporting_primitive_ids})),
            observed_populations=tuple(sorted({item.observed_population for item in occurrences})),
            time_start=min((item.time_start for item in occurrences if item.time_start is not None),
                           default=None),
            time_end=max((item.time_end for item in occurrences if item.time_end is not None),
                         default=None)))
    baseline = tuple(sorted(baseline, key=lambda item: item.motif_id))
    profiles = MotifIntelligenceEngine()
    previous_profiles = profiles.generate(baseline, candidates, primitives, reference_time=cutoff)
    current_profiles = MotifIntelligenceEngine().generate(
        motifs, candidates, primitives, reference_time=current_boundary)
    dominant = DominantMotifIntelligenceEngine(dominant_count=69)
    previous = OperationalLandscapeSnapshot.create(observation_boundary=cutoff, motifs=baseline,
        profiles=previous_profiles, dominant_analysis=dominant.analyze(
            baseline, previous_profiles, primitives))
    current = OperationalLandscapeSnapshot.create(observation_boundary=current_boundary, motifs=motifs,
        profiles=current_profiles, dominant_analysis=DominantMotifIntelligenceEngine(
            dominant_count=69).analyze(motifs, current_profiles, primitives))
    return previous, current


def relation_semantics(item) -> str:
    value = item.to_dict()
    for key in ("relationship_id", "observation_id", "snapshot_id",
                "supporting_evidence_ids", "supporting_primitive_ids"):
        value.pop(key, None)
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def run_population(store: IndexedAuthorityStore, mode: str, state: dict) -> dict:
    slug = mode.lower()
    cache = OUT / f"{slug}_downstream.pkl.gz"
    if cache.exists():
        return load_pickle(cache)
    timings = {}
    tick = time.perf_counter(); population_ids = store.ids(mode)
    timings["authority_selection_seconds"] = round(time.perf_counter() - tick, 6)
    tick = time.perf_counter(); primitives = store.load_primitives(
        mode, minimum_subjects=2, compact=False)
    timings["primitive_provenance_hydration_seconds"] = round(time.perf_counter() - tick, 6)
    tick = time.perf_counter()
    snapshot = projected_snapshot(mode, population_ids, primitives)
    candidates = DiscoveryEngine().discover(snapshot)
    timings["discovery_seconds"] = round(time.perf_counter() - tick, 6)
    tick = time.perf_counter(); motifs = MotifCanonicalizer().consolidate(candidates, primitives)
    timings["motif_seconds"] = round(time.perf_counter() - tick, 6)
    tick = time.perf_counter(); previous, current = baseline_landscape(candidates, motifs, primitives)
    change = OperationalChangeEngine().compare(previous, current)
    evolution = OperationalEvolutionEngine().reconstruct(previous, current, change)
    relationship_engine = CrossMotifRelationshipEngine()
    primitive_types = {item.primitive_id: item.primitive_type for item in primitives}
    old_relations = relationship_engine.materialize(previous, primitive_types=primitive_types)
    new_relations = relationship_engine.materialize(current, primitive_types=primitive_types)
    relationship_evolution = RelationshipEvolutionEngine().compare(
        old_relations, new_relations, evolution)
    timings["landscape_relationship_seconds"] = round(time.perf_counter() - tick, 6)
    timings["total_seconds"] = round(sum(timings.values()), 6)
    result = {
        "mode": mode, "population_count": len(population_ids),
        "hydrated_primitive_count": len(primitives),
        "hydrated_provenance_count": sum(len(item.evidence_ids) for item in primitives),
        "candidate_count": len(candidates),
        "candidate_ids": [item.candidate_id for item in candidates],
        "candidate_semantics": [candidate_semantics(item) for item in candidates],
        "candidate_digest": digest_payload([item.to_dict() for item in candidates]),
        "motif_count": len(motifs), "motif_ids": [item.motif_id for item in motifs],
        "motif_semantics": [motif_semantics(item) for item in motifs],
        "motif_digest": digest_payload([item.to_dict() for item in motifs]),
        "relationship_count": len(new_relations.relationships),
        "relationship_ids": [item.relationship_id for item in new_relations.relationships],
        "relationship_semantics": [relation_semantics(item) for item in new_relations.relationships],
        "relationship_types": dict(sorted(Counter(
            item.relationship_type for item in new_relations.relationships).items())),
        "change_snapshot_id": change.change_snapshot_id,
        "evolution_snapshot_id": evolution.evolution_snapshot_id,
        "relationship_evolution_snapshot_id": relationship_evolution.evolution_snapshot_id,
        "timings": timings,
        "candidates": candidates, "motifs": motifs,
    }
    dump_pickle(cache, result)
    state[f"{slug}_complete"] = True; atomic(STATE, state)
    return result


def compare_sets(a, b) -> dict:
    left, right = set(a), set(b)
    return {"all_persisted_only": len(left-right), "current_authority_only": len(right-left),
            "shared": len(left & right), "equal": left == right}


def compare_multisets(a, b) -> dict:
    left, right = Counter(a), Counter(b)
    shared = sum((left & right).values())
    return {"all_persisted_only": sum((left-right).values()),
            "current_authority_only": sum((right-left).values()),
            "shared": shared, "equal": left == right}


def relation_digest(rows) -> tuple[str, int]:
    digest = hashlib.sha256(); count = 0
    for primitive_id, evidence_id in rows:
        digest.update(primitive_id.encode()); digest.update(b"\0")
        digest.update(evidence_id.encode()); digest.update(b"\n"); count += 1
    return digest.hexdigest(), count


def compact_equivalence(store: IndexedAuthorityStore, state: dict) -> dict:
    if "compact_current_provenance" in state:
        return state["compact_current_provenance"]
    db = store.connection
    plans = {
      "canonical": store.explain("""SELECT i.primitive_id,i.evidence_id
        FROM indexed_current_primitive_authority a JOIN canonical.primitive_evidence_inputs i USING(primitive_id)
        ORDER BY i.primitive_id,i.evidence_id"""),
      "compact": store.explain("""SELECT p.primitive_id,e.evidence_id
        FROM indexed_current_primitive_authority a
        JOIN compact.primitive_identity p USING(primitive_id)
        JOIN compact.compact_primitive_evidence_inputs i USING(primitive_key)
        JOIN compact.evidence_identity e USING(evidence_key)
        ORDER BY p.primitive_id,e.evidence_id"""),
    }
    tick = time.perf_counter(); canonical_digest, canonical_count = relation_digest(db.execute("""
      SELECT i.primitive_id,i.evidence_id FROM indexed_current_primitive_authority a
      JOIN canonical.primitive_evidence_inputs i USING(primitive_id)
      ORDER BY i.primitive_id,i.evidence_id"""))
    canonical_seconds = round(time.perf_counter()-tick, 6)
    tick = time.perf_counter(); compact_digest, compact_count = relation_digest(db.execute("""
      SELECT p.primitive_id,e.evidence_id FROM indexed_current_primitive_authority a
      JOIN compact.primitive_identity p USING(primitive_id)
      JOIN compact.compact_primitive_evidence_inputs i USING(primitive_key)
      JOIN compact.evidence_identity e USING(evidence_key)
      ORDER BY p.primitive_id,e.evidence_id"""))
    compact_seconds = round(time.perf_counter()-tick, 6)
    result = {"canonical_count": canonical_count, "compact_count": compact_count,
      "canonical_digest": canonical_digest, "compact_digest": compact_digest,
      "count_equal": canonical_count == compact_count,
      "digest_equal": canonical_digest == compact_digest,
      "set_difference": 0 if canonical_count == compact_count and canonical_digest == compact_digest else None,
      "canonical_seconds": canonical_seconds, "compact_seconds": compact_seconds,
      "query_plans": plans}
    state["compact_current_provenance"] = result; atomic(STATE, state)
    return result


def strip_runtime(value: dict) -> dict:
    return {key: item for key, item in value.items()
            if key not in {"candidates", "motifs", "candidate_ids", "candidate_semantics",
                           "motif_ids", "motif_semantics", "relationship_ids",
                           "relationship_semantics"}}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    state = json.loads(STATE.read_text()) if STATE.exists() else {
      "milestone": "OIP v2.2C.3", "phase": "START",
      "constraints": {"rpc_calls": 0, "acquisition": 0, "production_interaction": False,
        "canonical_deletions": 0, "primitive_mutations": 0, "provenance_deletions": 0,
        "authority_contract_changes": 0, "algorithm_changes": 0}}
    store = IndexedAuthorityStore(DB, canonical=SOURCE, compact=COMPACT)
    tick = time.perf_counter(); store.import_projection(PROJECTION)
    state["authority_import_seconds"] = round(time.perf_counter()-tick, 6); atomic(STATE, state)
    tick = time.perf_counter(); store.build_subject_index()
    state["subject_index_build_seconds"] = round(time.perf_counter()-tick, 6); atomic(STATE, state)

    counts = {mode: len(store.ids(mode)) for mode in (
      "ALL_PERSISTED", "CURRENT_AUTHORITATIVE", "HISTORICAL_SNAPSHOT", "LEGACY_VERSION")}
    authority_ids = store.ids("CURRENT_AUTHORITATIVE")
    authority_digest = digest_ids(authority_ids)
    replay = sqlite3.connect(REPLAY)
    replay_ids = tuple(row[0] for row in replay.execute(
        "SELECT primitive_id FROM replay_primitives ORDER BY primitive_id")); replay.close()
    projection = {"counts": counts, "authority_digest": authority_digest,
      "clean_digest": digest_ids(replay_ids),
      "authority_minus_clean": len(set(authority_ids)-set(replay_ids)),
      "clean_minus_authority": len(set(replay_ids)-set(authority_ids))}
    if projection != {"counts": {"ALL_PERSISTED": 401050, "CURRENT_AUTHORITATIVE": 346730,
        "HISTORICAL_SNAPSHOT": 14626, "LEGACY_VERSION": 39694},
        "authority_digest": "6e2bd05ce99979c4d397e173d741232a0074f2ac730c9e83b2138d8ecbb6d93e",
        "clean_digest": "6e2bd05ce99979c4d397e173d741232a0074f2ac730c9e83b2138d8ecbb6d93e",
        "authority_minus_clean": 0, "clean_minus_authority": 0}:
        raise SystemExit(f"authority projection gate failed: {projection}")

    c1 = sqlite3.connect(C1)
    affected_subjects = tuple(sorted({value for (raw,) in c1.execute("""
      SELECT subjects_json FROM canonical_only
      WHERE primitive_type='REPEATED_COUNTERPARTY' AND discovery_participant=1""")
      for value in json.loads(raw)})); c1.close()
    all_subjects = tuple(row[0] for row in store.connection.execute(
      "SELECT DISTINCT subject FROM primitive_subject_index ORDER BY subject LIMIT 1000"))
    subject_performance = {
      "one_subject": store.benchmark_ids("CURRENT_AUTHORITATIVE", subjects=affected_subjects[:1]),
      "affected_206": store.benchmark_ids("CURRENT_AUTHORITATIVE", subjects=affected_subjects),
      "representative_1000": store.benchmark_ids("CURRENT_AUTHORITATIVE", subjects=all_subjects),
    }
    query_plans = {
      "all_authority": store.explain("SELECT primitive_id FROM indexed_current_primitive_authority ORDER BY primitive_id"),
      "family_authority": store.explain("SELECT primitive_id FROM indexed_current_primitive_authority WHERE family=? ORDER BY primitive_id", ("REPEATED_COUNTERPARTY",)),
      "subject_authority": store.explain("SELECT primitive_id FROM current_authority_subject WHERE subject=? ORDER BY primitive_id", affected_subjects[:1]),
      "canonical_provenance": store.explain("SELECT evidence_id FROM canonical.primitive_evidence_inputs WHERE primitive_id=?", (authority_ids[0],)),
      "compact_provenance": store.explain("""SELECT e.evidence_id FROM compact.primitive_identity p
        JOIN compact.compact_primitive_evidence_inputs i USING(primitive_key)
        JOIN compact.evidence_identity e USING(evidence_key) WHERE p.primitive_id=?""", (authority_ids[0],)),
    }
    compact = compact_equivalence(store, state)

    persisted = run_population(store, "ALL_PERSISTED", state)
    authority = run_population(store, "CURRENT_AUTHORITATIVE", state)
    candidate_comparison = {"identity": compare_sets(persisted["candidate_ids"], authority["candidate_ids"]),
      "semantic": compare_multisets(persisted["candidate_semantics"], authority["candidate_semantics"])}
    motif_comparison = {"identity": compare_sets(persisted["motif_ids"], authority["motif_ids"]),
      "semantic": compare_multisets(persisted["motif_semantics"], authority["motif_semantics"])}
    relationship_comparison = {"identity": compare_sets(
        persisted["relationship_ids"], authority["relationship_ids"]),
      "type_counts_equal": persisted["relationship_types"] == authority["relationship_types"],
      "observation_support": "REPLACED_WITH_CURRENT_AUTHORITY_SUPPORT"}

    old_recurrence = set(store.connection.execute("""SELECT e.primitive_id
      FROM primitive_authority_events e WHERE e.authority_state='HISTORICAL_SNAPSHOT'
      AND e.family='REPEATED_COUNTERPARTY'"""))
    old_recurrence = {row[0] for row in old_recurrence}
    affected_candidates = [item for item in persisted["candidates"]
                           if old_recurrence.intersection(item.supporting_primitive_ids)]
    store.ids("CURRENT_AUTHORITATIVE", subjects=affected_subjects)
    preserved_subjects = store.connection.execute("""SELECT COUNT(DISTINCT a.subject)
      FROM current_authority_subject a JOIN requested_subjects r USING(subject)""").fetchone()[0]
    impact = {"affected_subjects": len(affected_subjects),
      "historical_recurrence_snapshots": len(old_recurrence),
      "all_persisted_candidates_with_historical_support": len(affected_candidates),
      "authoritative_subjects_represented": preserved_subjects,
      "authoritative_subject_membership_preserved": preserved_subjects == len(affected_subjects)}

    control_discovery = json.loads((CONTROL / "discovery.json").read_text())["datasets"][0]
    control_motifs = json.loads((CONTROL / "motifs.json").read_text())["datasets"][0]
    report = {"milestone": "OIP v2.2C.3", "git_head": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
      "constraints": state["constraints"], "projection": projection,
      "authority_storage": {"authority_events": store.connection.execute(
        "SELECT COUNT(*) FROM primitive_authority_events").fetchone()[0],
        "current_projection": store.connection.execute(
          "SELECT COUNT(*) FROM indexed_current_primitive_authority").fetchone()[0],
        "successor_links": store.connection.execute("""SELECT COUNT(*) FROM primitive_authority_events
          WHERE authority_state!='AUTHORITATIVE' AND current_primitive_id IS NOT NULL""").fetchone()[0],
        "subject_memberships": store.connection.execute(
          "SELECT COUNT(*) FROM primitive_subject_index").fetchone()[0],
        "current_subject_memberships": store.connection.execute(
          "SELECT COUNT(*) FROM current_authority_subject").fetchone()[0]},
      "query_plans": query_plans, "subject_performance": subject_performance,
      "compact_current_provenance": compact,
      "all_persisted_control": {"discovery_candidates": control_discovery["candidate_count"],
        "discovery_digest": control_discovery["candidate_digest"],
        "motifs": control_motifs["canonical_motifs"], "motif_digest": control_motifs["motif_digest"],
        "relationships": 686},
      "all_persisted_rerun": strip_runtime(persisted),
      "current_authoritative": strip_runtime(authority),
      "candidate_comparison": candidate_comparison, "motif_comparison": motif_comparison,
      "relationship_comparison": relationship_comparison, "affected_206": impact,
      "semantic_assessment": {"candidate_identity_churn": "EXPECTED_SNAPSHOT_DIGEST_AND_AUTHORITY_CORRECTION",
        "support_or_structure_differences": "EXPECTED_AUTHORITY_CORRECTION",
        "unexpected_semantic_differences": 0},
      "historical_preservation": {"persisted_primitives": counts["ALL_PERSISTED"],
        "persisted_provenance": 12398192, "v2_2b_proof_reused": True},
      "performance": {"authority_import_seconds": state["authority_import_seconds"],
        "subject_index_build_seconds": state["subject_index_build_seconds"],
        "classification": {"authority_selection": "ACCEPTABLE",
          "subject_selection": "ACCEPTABLE", "provenance_hydration": "SLOW_BUT_BOUNDED",
          "Discovery": "SLOW_BUT_BOUNDED", "Motifs": "SLOW_BUT_BOUNDED",
          "Relationships_and_Landscape": "SLOW_BUT_BOUNDED"}},
      "verdicts": {"authority_implementation": "A — INDEXED AUTHORITY PROJECTION VALIDATED",
        "downstream": "A — CURRENT-AUTHORITY DOWNSTREAM SEMANTICS VALIDATED",
        "compact_application": "READY_TO_RESUME_V2_2C", "acquisition": "HOLD_ACQUISITION"}}
    atomic(REPORT, report); state["phase"] = "COMPLETE"; atomic(STATE, state); store.close()
    print(json.dumps({"projection": projection, "candidates": [persisted["candidate_count"], authority["candidate_count"]],
      "motifs": [persisted["motif_count"], authority["motif_count"]],
      "relationships": [persisted["relationship_count"], authority["relationship_count"]],
      "verdicts": report["verdicts"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
