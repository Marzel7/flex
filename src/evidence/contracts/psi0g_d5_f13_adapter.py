"""PSI0G-D6 lossless D5 provenance to F13-compatible retained input store."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Mapping

from .operational_family_retained_input_store import (
    SCHEMA_PATH, SCHEMA_VERSION as F13_SCHEMA_VERSION,
    export_operational_family_retained_inputs,
)
from .operational_family_retention_bundle import (
    build_fixture_operational_family_retention_bundle,
    build_operational_family_retention_bundle_contract,
    replay_fixture_operational_family_retention_bundle,
)
from .psi0g_real_provenance_retention import replay_real_candidate_provenance
from .psi0g_reviewed_candidate import _bind


SCHEMA_VERSION = "psi0g-d6.d5-f13-compatible-adapter.v1"
SOURCE_ENGINEERING_REVISION = "psi0g-d6"


class Psi0gD5F13AdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class AdaptedF13CompatibleStore:
    path: Path
    retention_id: str
    manifest_digest: str
    f9_bundle_digest: str
    f5_source_digest: str
    adapter_digest: str


def _fail(code: str) -> None:
    raise Psi0gD5F13AdapterError(f"PSI0G_D6_{code}")


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def _digest(value: object) -> str:
    return sha256(_canonical(value).rstrip(b"\n")).hexdigest()


def _payload(value: object) -> tuple[str, str]:
    payload = _canonical(value)
    return payload.decode(), sha256(payload.rstrip(b"\n")).hexdigest()


def _document(files: Mapping[str, bytes], name: str, key: str) -> Any:
    return json.loads(files[name])[key]


def adapt_d5_to_f13_compatible_store(source: Path, destination: Path) -> AdaptedF13CompatibleStore:
    replay = replay_real_candidate_provenance(source)
    documents = {
        name: json.loads((replay.path / name).read_bytes())
        for name in ("projection.json", "disposition.json", "authorization.json", "preflight.json")
    }
    projection = documents["projection.json"]
    reviewed = documents["disposition.json"]
    authorization = documents["authorization.json"]
    preflight = documents["preflight.json"]
    if (reviewed["candidate_id"] != projection["candidate"]["candidate_id"] or
            authorization["candidate_id"] != reviewed["candidate_id"] or
            authorization["preflight_digest"] != preflight["preflight_digest"] or
            reviewed["nomination_state"] != "PROPOSED" or
            len(reviewed["unresolved_missing_evidence"]) != 14 or
            any(reviewed["authority"].values()) or any(preflight["downstream_authority"].values())):
        _fail("REVIEWED_PROVENANCE_DRIFT")

    bound = _bind(projection, fixture_only=True)
    values = bound["values"]
    disposition = values["dispositions"][0]
    metadata = {
        "review_id": reviewed["review_id"], "reviewer_class": reviewed["reviewer_class"],
        "reason_codes": sorted(reviewed["reason_codes"]),
        "reviewed_sequence": reviewed["reviewed_sequence"],
    }
    if (disposition["review_id"] != reviewed["review_id"] or
            disposition["group_id"] != reviewed["group_id"] or
            disposition["candidate_id"] != reviewed["candidate_id"] or
            disposition["operation_ids"] != reviewed["operation_ids"] or
            sorted(projection["candidate"]["missing_evidence"]) != sorted(reviewed["unresolved_missing_evidence"])):
        _fail("LOSSLESS_DISPOSITION_BINDING_FAILED")

    contract = build_operational_family_retention_bundle_contract()
    bundle = build_fixture_operational_family_retention_bundle(contract, **values)
    source_replay = replay_fixture_operational_family_retention_bundle(contract, bundle.files)
    normalized = {
        name: _document(bundle.files, f"{name}.json", "items")
        for name in ("cohort", "evaluations", "runtime", "candidates", "dispositions")
    }
    normalized["vocabulary"] = _document(bundle.files, "vocabulary.json", "value")
    vocabulary_json, vocabulary_digest = _payload(normalized["vocabulary"])
    logical_capture_sequence = reviewed["reviewed_sequence"]
    manifest_identity = {
        "schema_version": F13_SCHEMA_VERSION,
        "f9_contract_digest": contract.contract_digest,
        "source_engineering_revision": SOURCE_ENGINEERING_REVISION,
        "vocabulary_digest": vocabulary_digest,
        "logical_capture_sequence": logical_capture_sequence,
        "bundle_digest": bundle.bundle_digest,
        "source_digest": source_replay.source_digest,
    }
    retention_id = _digest(manifest_identity)
    manifest_digest = _digest({"retention_id": retention_id, **manifest_identity})
    adapter_identity = {
        "schema_version": SCHEMA_VERSION, "d5_retention_id": replay.retention_id,
        "candidate_id": reviewed["candidate_id"], "preflight_digest": preflight["preflight_digest"],
        "f9_bundle_digest": bundle.bundle_digest, "f5_source_digest": source_replay.source_digest,
        "retention_id": retention_id, "manifest_digest": manifest_digest,
    }
    adapter_digest = _digest(adapter_identity)

    target = Path(destination)
    if target.exists() or target.is_symlink() or not target.parent.is_dir():
        _fail("DESTINATION_NOT_NEW")
    descriptor = None
    connection = None
    created = False
    try:
        descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        descriptor = None
        created = True
        connection = sqlite3.connect(target, isolation_level=None)
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.executescript(SCHEMA_PATH.read_text())
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO retention_manifest VALUES(?,?,?,?,?,?,?,?)",
            (retention_id, F13_SCHEMA_VERSION, contract.contract_digest, SOURCE_ENGINEERING_REVISION,
             vocabulary_json, vocabulary_digest, logical_capture_sequence, manifest_digest),
        )
        connection.executemany(
            "INSERT INTO operation_cohort VALUES(?,?,?)",
            ((retention_id, row["position"], row["operation_id"]) for row in normalized["cohort"]),
        )
        for table, rows, keys in (
            ("evaluation_summaries", normalized["evaluations"], ("operation_id",)),
            ("normalized_runtime_projections", normalized["runtime"], ("operation_id", "input_digest")),
            ("candidate_payloads", normalized["candidates"], ("candidate_id",)),
        ):
            for row in rows:
                payload_json, payload_digest = _payload(row)
                columns = (retention_id, *(row[key] for key in keys), payload_json, payload_digest)
                connection.execute(f"INSERT INTO {table} VALUES({','.join('?' for _ in columns)})", columns)
        reason_json, _ = _payload(metadata["reason_codes"])
        authority_json, _ = _payload(disposition["authority"])
        ledger_body = {
            "disposition": disposition, "reviewer_class": metadata["reviewer_class"],
            "reason_codes": metadata["reason_codes"], "reviewed_sequence": metadata["reviewed_sequence"],
        }
        connection.execute(
            "INSERT INTO nomination_dispositions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (retention_id, disposition["review_id"], disposition["candidate_id"], disposition["group_id"],
             disposition["nomination_state"], disposition["supporting_identity_digest"],
             metadata["reviewer_class"], reason_json, metadata["reviewed_sequence"],
             authority_json, _digest(ledger_body)),
        )
        connection.executemany(
            "INSERT INTO nomination_disposition_members VALUES(?,?,?,?)",
            ((retention_id, disposition["review_id"], position, operation)
             for position, operation in enumerate(disposition["operation_ids"])),
        )
        connection.commit()
        connection.close()
        connection = None
        descriptor = os.open(target, os.O_RDONLY)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        descriptor = os.open(target.parent, os.O_RDONLY)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
    except BaseException:
        if connection is not None:
            connection.close()
        if descriptor is not None:
            os.close(descriptor)
        if created:
            for suffix in ("", "-wal", "-shm", "-journal"):
                partial = Path(f"{target}{suffix}")
                if partial.exists() and not partial.is_dir():
                    partial.unlink()
        raise

    exported = export_operational_family_retained_inputs(target, retention_id)
    if (exported.bundle.bundle_digest != bundle.bundle_digest or
            exported.bundle.source_digest != source_replay.source_digest or
            exported.manifest_digest != manifest_digest):
        _fail("POST_WRITE_LOSSLESS_REPLAY_FAILED")
    return AdaptedF13CompatibleStore(
        path=target, retention_id=retention_id, manifest_digest=manifest_digest,
        f9_bundle_digest=bundle.bundle_digest, f5_source_digest=source_replay.source_digest,
        adapter_digest=adapter_digest,
    )
