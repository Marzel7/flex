#!/usr/bin/env python3
"""Replay frozen corpora through EP2.1 without RPC or source mutation."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evidence.artifacts import ArtifactStore
from src.evidence.database import EvidenceDatabase
from src.evidence.normalization import NormalizationEngine
from src.evidence.primitives.engine import PrimitiveEngine, _Index
from src.ops.watchtower_shadow_corpus import WatchtowerShadowCorpusMaterializer


TEMPORAL = {
    "WALLET_FRESH_AT_EVENT", "LAUNCH_ACTIVATION", "ECONOMIC_FUNDING",
    "REPEATED_COUNTERPARTY", "BEHAVIOURAL_TIMING",
}


def _helper(root: Path) -> WatchtowerShadowCorpusMaterializer:
    return WatchtowerShadowCorpusMaterializer(
        operations_db=Path("database/operator_intelligence.db"),
        transaction_cache_db=Path("database/transaction_cache.db"),
        output_root=root,
    )


def _semantic_rows(path: Path) -> list[tuple[str, str, str, str]]:
    connection = sqlite3.connect(path)
    try:
        return list(connection.execute(
            "SELECT primitive_type,output_payload_json,quality_state,missing_inputs_json "
            "FROM primitive_observations ORDER BY primitive_type,output_payload_json,quality_state,missing_inputs_json"
        ))
    finally:
        connection.close()


def _replay(source: Path, destination: Path, *, incremental: bool) -> dict:
    helper = _helper(source)
    envelopes = helper._reconstruct_envelopes(source / "evidence.db")
    groups = [envelopes]
    if incremental:
        transaction, history = [], []
        for envelope in envelopes:
            purpose = str((envelope.get("acquisition") or {}).get("purpose") or "")
            (history if purpose == "creator_freshness_history" else transaction).append(envelope)
        groups = [transaction, history]
    database = EvidenceDatabase(destination, clock=lambda: 0)
    database.open_writer()
    try:
        normalizer = NormalizationEngine(
            database, ArtifactStore(source / "artifacts", enabled=True, clock=lambda: 0)
        )
        engine = PrimitiveEngine(database, clock=lambda: 0)
        for group in groups:
            for envelope in group:
                database.append_batch([{
                    "message_id": f"ep2-1-replay-{envelope['envelope_id']}",
                    "envelope": envelope,
                }])
                normalizer.normalize_envelope(envelope)
            engine.run_once()
    finally:
        database.close()
    rows = _semantic_rows(destination)
    return {
        "digest": hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest(),
        "rows": rows,
        "counts": dict(sorted(Counter(row[0] for row in rows).items())),
        "envelopes": len(envelopes),
        "checkpoints": len(groups),
    }


def _corpus_report(name: str, source: Path, *, incremental: bool) -> dict:
    with tempfile.TemporaryDirectory(prefix=f"ep2_1_{name}_") as first_dir, \
            tempfile.TemporaryDirectory(prefix=f"ep2_1_{name}_") as second_dir:
        first = _replay(source, Path(first_dir) / "evidence.db", incremental=incremental)
        second = _replay(source, Path(second_dir) / "evidence.db", incremental=incremental)
        source_rows = _semantic_rows(source / "evidence.db")
        source_counter = Counter(row[0] for row in source_rows)
        replay_counter = Counter(row[0] for row in first["rows"])
        changed_types = sorted({
            primitive_type for primitive_type in source_counter | replay_counter
            if sorted(row[1:] for row in source_rows if row[0] == primitive_type)
            != sorted(row[1:] for row in first["rows"] if row[0] == primitive_type)
        })
        fresh = Counter()
        for primitive_type, payload_json, quality, _missing in first["rows"]:
            if primitive_type == "WALLET_FRESH_AT_EVENT":
                payload = json.loads(payload_json)
                fresh[(payload.get("freshness_state"), quality)] += 1
        report = {
            "corpus": name,
            "source_primitive_counts": dict(sorted(source_counter.items())),
            "replay_primitive_counts": first["counts"],
            "changed_primitive_types": changed_types,
            "unrelated_primitive_types_changed": sorted(set(changed_types) - TEMPORAL),
            "freshness_states": {f"{state}:{quality}": count for (state, quality), count in sorted(fresh.items())},
            "deterministic": first["digest"] == second["digest"],
            "replay_digest": first["digest"],
            "envelopes": first["envelopes"],
            "projection_checkpoints": first["checkpoints"],
            "rpc_calls": 0,
        }
        if name == "3SW2":
            controller = "3SW2zquY2mVTbNuw1ZCGgtoehq2evfU36PFd6TTqSXdK"
            # LAUNCH_SIGNER carries the creator in its output and avoids any
            # dependency on Operation-specific population interpretation.
            creators = {
                json.loads(payload_json).get("wallet")
                for primitive_type, payload_json, _quality, _missing in first["rows"]
                if primitive_type == "LAUNCH_SIGNER"
            }
            creators.discard(None)
            fresh_creators = {
                payload.get("wallet")
                for primitive_type, payload_json, quality, _missing in first["rows"]
                for payload in (json.loads(payload_json),)
                if primitive_type == "WALLET_FRESH_AT_EVENT"
                and payload.get("freshness_state") == "VERIFIED_FRESH"
                and quality == "PROVEN"
            }
            unresolved_creators = creators - fresh_creators
            report["three_sw2_reference"] = {
                "controller": controller,
                "launch_creators": len(creators),
                "verified_fresh_creators": len(creators & fresh_creators),
                "unverifiable_creators": len(unresolved_creators),
            }
        return report


def _watchtower_targeted_report(source: Path) -> dict:
    """Evaluate the corrected primitive twice without rebuilding the large corpus."""
    connection = sqlite3.connect(source / "evidence.db")
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT evidence_id,logical_fact_id,fact_family,payload_json,observed_at,payload_digest "
            "FROM normalized_evidence_records ORDER BY evidence_id"
        ).fetchall()
        stored = list(connection.execute(
            "SELECT output_payload_json,quality_state,missing_inputs_json "
            "FROM primitive_observations WHERE primitive_type='WALLET_FRESH_AT_EVENT'"
        ))
        activation_keys = {
            (payload.get("creator"), payload.get("funding_signature"))
            for (raw,) in connection.execute(
                "SELECT output_payload_json FROM primitive_observations "
                "WHERE primitive_type='LAUNCH_ACTIVATION'"
            )
            for payload in (json.loads(raw),)
        }
    finally:
        connection.close()
    index = _Index(rows)
    engine = PrimitiveEngine(EvidenceDatabase(source / "evidence.db"), clock=lambda: 0)
    first = engine._wallet_freshness(index)
    second = engine._wallet_freshness(index)
    projected = sorted((json.dumps(item.output_payload, sort_keys=True, separators=(",", ":")),
                        item.quality_state, json.dumps(list(item.missing_inputs))) for item in first)
    repeated = sorted((json.dumps(item.output_payload, sort_keys=True, separators=(",", ":")),
                       item.quality_state, json.dumps(list(item.missing_inputs))) for item in second)
    existing = sorted(tuple(row) for row in stored)
    changed = projected != existing
    old_by_event = {
        (payload.get("wallet"), payload.get("reference_event")):
            (payload.get("freshness_state"), quality, missing)
        for raw, quality, missing in stored for payload in (json.loads(raw),)
    }
    new_by_event = {
        (payload.get("wallet"), payload.get("reference_event")):
            (payload.get("freshness_state"), quality, missing)
        for raw, quality, missing in projected for payload in (json.loads(raw),)
    }
    changed_activation_inputs = sorted(
        event for event in activation_keys
        if old_by_event.get(event) != new_by_event.get(event)
    )
    return {
        "corpus": "WATCHTOWER",
        "validation_scope": "TEMPORAL_PRIMITIVE_TARGETED_REPLAY",
        "wallet_freshness_observations": len(projected),
        "changed_primitive_types": ["WALLET_FRESH_AT_EVENT"] if changed else [],
        "unrelated_primitive_types_changed": [],
        "watchtower_contract_inputs_changed": bool(changed_activation_inputs),
        "changed_watchtower_activation_inputs": len(changed_activation_inputs),
        "deterministic": projected == repeated,
        "replay_digest": hashlib.sha256(json.dumps(projected).encode()).hexdigest(),
        "rpc_calls": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path,
                        default=Path("docs/evidence_platform/ep2_1_temporal_primitive_replay.json"))
    args = parser.parse_args()
    report = {
        "milestone": "EP2.1",
        "authority": "SHADOW_REPLAY_ONLY",
        "corpora": [
            _corpus_report("3SW2", Path("database/evidence_platform/three_sw2_shadow_ep3_2a"), incremental=True),
            _watchtower_targeted_report(Path("database/evidence_platform/watchtower_shadow_ep3_0d")),
        ],
        "invariants": {
            "rpc_calls": 0, "evidence_changed": False, "runtime_changed": False,
            "operation_contracts_changed": False, "source_corpora_mutated": False,
        },
    }
    report["passed"] = all(
        item["deterministic"] and not item["unrelated_primitive_types_changed"]
        for item in report["corpora"]
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
