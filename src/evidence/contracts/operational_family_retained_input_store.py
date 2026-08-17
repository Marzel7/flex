"""PSI0F-F13 isolated fixture retention store and bounded query-only F9 export."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Callable, Mapping, Sequence
from urllib.parse import quote

from .operational_family_retention_bundle import (
    OperationalFamilyRetentionBundle,
    build_fixture_operational_family_retention_bundle,
    build_operational_family_retention_bundle_contract,
    replay_fixture_operational_family_retention_bundle,
)


SCHEMA_VERSION = "psi0f-f13.retained-input-store.v1"
ENGINEERING_REVISION = "17d30faf"
PSI0F_F12_DIGEST = "70a5d98982bee953796168d2aded36a15917198e1877fad92541ca8d174a2787"
PSI0F_F9_DIGEST = "52e24914afb8871943baa0826d43878ed3b73cd5521af1298de62eab2320df2b"
SCHEMA_DIGEST = "55366e106c9b2f1cd3dccfc37eff16193bc3ba374ffe931243a7992dcda18b75"
SCHEMA_PATH = Path(__file__).with_name("operational_family_retention_schema.sql")
MAX_QUERY_SECONDS = 30.0
CEILINGS = {
    "operation_cohort": 5_000,
    "evaluation_summaries": 5_000,
    "normalized_runtime_projections": 10_000,
    "candidate_payloads": 5_000,
    "nomination_dispositions": 5_000,
    "nomination_disposition_members": 50_000,
}
REVIEW_METADATA_FIELDS = frozenset(("review_id", "reviewer_class", "reason_codes", "reviewed_sequence"))
REVIEWER_CLASSES = frozenset(("FIXTURE_REVIEW", "HUMAN_REVIEW"))
REASON_CODES = frozenset((
    "CONFLICT_PRESENT", "EVIDENCE_COMPLETE", "EVIDENCE_INCOMPLETE",
    "MANUAL_HOLD", "RECURRING_BEHAVIOUR",
))
TABLE_COLUMNS = {
    "retention_manifest": (
        "retention_id", "schema_version", "f9_contract_digest", "source_engineering_revision",
        "vocabulary_json", "vocabulary_digest", "logical_capture_sequence", "manifest_digest",
    ),
    "operation_cohort": ("retention_id", "position", "operation_id"),
    "evaluation_summaries": ("retention_id", "operation_id", "payload_json", "payload_digest"),
    "normalized_runtime_projections": (
        "retention_id", "operation_id", "input_digest", "payload_json", "payload_digest",
    ),
    "candidate_payloads": ("retention_id", "candidate_id", "payload_json", "payload_digest"),
    "nomination_dispositions": (
        "retention_id", "review_id", "candidate_id", "group_id", "nomination_state",
        "supporting_identity_digest", "reviewer_class", "reason_codes_json", "reviewed_sequence",
        "authority_json", "payload_digest",
    ),
    "nomination_disposition_members": ("retention_id", "review_id", "position", "operation_id"),
}


class OperationalFamilyRetainedInputStoreError(RuntimeError):
    """Named fail-closed PSI0F-F13 violation."""


@dataclass(frozen=True)
class OperationalFamilyRetainedInputStoreContract:
    schema_version: str
    engineering_revision: str
    psi0f_f12_digest: str
    psi0f_f9_digest: str
    schema_digest: str
    fixture_publisher_only: bool
    publisher_requires_new_path: bool
    exporter_query_only: bool
    exporter_writes_files: bool
    real_store_capture_authorized: bool
    authority: Mapping[str, bool]
    contract_digest: str


@dataclass(frozen=True)
class PublishedOperationalFamilyRetainedInputs:
    path: Path
    retention_id: str
    manifest_digest: str
    bundle_digest: str
    source_digest: str


@dataclass(frozen=True)
class ExportedOperationalFamilyRetainedInputs:
    retention_id: str
    manifest_digest: str
    bundle: OperationalFamilyRetentionBundle
    query_count: int


def _fail(code: str) -> None:
    raise OperationalFamilyRetainedInputStoreError(f"PSI0F_F13_{code}")


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def _digest(value: object) -> str:
    return sha256(_canonical(value).rstrip(b"\n")).hexdigest()


def _payload(value: object) -> tuple[str, str]:
    encoded = _canonical(value)
    return encoded.decode(), sha256(encoded.rstrip(b"\n")).hexdigest()


def build_operational_family_retained_input_store_contract() -> OperationalFamilyRetainedInputStoreContract:
    authority = {
        key: False for key in (
            "operator_identity", "policy", "ranking", "attribution", "integration",
            "deployment", "activation", "evidence_mirror", "cohort_mode", "eb2",
        )
    }
    body = {
        "schema_version": SCHEMA_VERSION, "engineering_revision": ENGINEERING_REVISION,
        "psi0f_f12_digest": PSI0F_F12_DIGEST, "psi0f_f9_digest": PSI0F_F9_DIGEST,
        "schema_digest": SCHEMA_DIGEST, "fixture_publisher_only": True,
        "publisher_requires_new_path": True, "exporter_query_only": True,
        "exporter_writes_files": False, "real_store_capture_authorized": False,
        "authority": authority,
    }
    return OperationalFamilyRetainedInputStoreContract(**body, contract_digest=_digest(body))


def verify_operational_family_retained_input_store_contract(
    contract: OperationalFamilyRetainedInputStoreContract,
) -> bool:
    if contract != build_operational_family_retained_input_store_contract():
        _fail("CONTRACT_REPLAY_MISMATCH")
    if (not contract.fixture_publisher_only or not contract.publisher_requires_new_path or
            not contract.exporter_query_only or contract.exporter_writes_files or
            contract.real_store_capture_authorized or any(contract.authority.values())):
        _fail("AUTHORITY_DRIFT")
    if sha256(SCHEMA_PATH.read_bytes()).hexdigest() != contract.schema_digest:
        _fail("SCHEMA_IDENTITY_DRIFT")
    return True


def _document(bundle: OperationalFamilyRetentionBundle, name: str, key: str) -> object:
    return json.loads(bundle.files[name])[key]


def _review_metadata(values: object, dispositions: Sequence[Mapping[str, object]]) -> dict[str, dict[str, object]]:
    if not isinstance(values, (list, tuple)):
        _fail("REVIEW_METADATA_TYPE_INVALID")
    result: dict[str, dict[str, object]] = {}
    for value in values:
        if not isinstance(value, Mapping) or frozenset(value) != REVIEW_METADATA_FIELDS:
            _fail("REVIEW_METADATA_SCHEMA_DRIFT")
        row = dict(value)
        review_id = row["review_id"]
        reviewer_class = row["reviewer_class"]
        reasons = row["reason_codes"]
        sequence = row["reviewed_sequence"]
        if (not isinstance(review_id, str) or not review_id or review_id in result or
                reviewer_class not in REVIEWER_CLASSES or not isinstance(sequence, int) or sequence < 0 or
                not isinstance(reasons, (list, tuple)) or not reasons or
                any(reason not in REASON_CODES for reason in reasons) or len(reasons) != len(set(reasons))):
            _fail("REVIEW_METADATA_INVALID")
        row["reason_codes"] = sorted(reasons)
        result[review_id] = row
    expected = {row["review_id"] for row in dispositions}
    if set(result) != expected or len({row["reviewed_sequence"] for row in result.values()}) != len(result):
        _fail("REVIEW_METADATA_COVERAGE_DRIFT")
    return result


def publish_fixture_operational_family_retained_inputs(
    destination: Path, *, cohort: object, evaluations: object, runtime: object,
    candidates: object, dispositions: object, vocabulary: object,
    review_metadata: object, logical_capture_sequence: int,
) -> PublishedOperationalFamilyRetainedInputs:
    verify_operational_family_retained_input_store_contract(
        build_operational_family_retained_input_store_contract()
    )
    if not isinstance(logical_capture_sequence, int) or logical_capture_sequence < 0:
        _fail("LOGICAL_CAPTURE_SEQUENCE_INVALID")
    contract = build_operational_family_retention_bundle_contract()
    bundle = build_fixture_operational_family_retention_bundle(
        contract, cohort=cohort, evaluations=evaluations, runtime=runtime,
        candidates=candidates, dispositions=dispositions, vocabulary=vocabulary,
    )
    replay_fixture_operational_family_retention_bundle(contract, bundle.files)
    normalized = {
        name: _document(bundle, f"{name}.json", "items")
        for name in ("cohort", "evaluations", "runtime", "candidates", "dispositions")
    }
    normalized["vocabulary"] = _document(bundle, "vocabulary.json", "value")
    metadata = _review_metadata(review_metadata, normalized["dispositions"])
    vocabulary_json, vocabulary_digest = _payload(normalized["vocabulary"])
    manifest_identity = {
        "schema_version": SCHEMA_VERSION,
        "f9_contract_digest": contract.contract_digest,
        "source_engineering_revision": ENGINEERING_REVISION,
        "vocabulary_digest": vocabulary_digest,
        "logical_capture_sequence": logical_capture_sequence,
        "bundle_digest": bundle.bundle_digest,
        "source_digest": bundle.source_digest,
    }
    retention_id = _digest(manifest_identity)
    manifest_digest = _digest({"retention_id": retention_id, **manifest_identity})
    path = Path(destination)
    if path.exists() or path.is_symlink() or not path.parent.is_dir():
        _fail("DESTINATION_NOT_NEW")
    descriptor = None
    connection = None
    created = False
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        descriptor = None
        created = True
        connection = sqlite3.connect(path, isolation_level=None)
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.executescript(SCHEMA_PATH.read_text())
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO retention_manifest VALUES(?,?,?,?,?,?,?,?)",
            (retention_id, SCHEMA_VERSION, contract.contract_digest, ENGINEERING_REVISION,
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
                values = (retention_id, *(row[key] for key in keys), payload_json, payload_digest)
                connection.execute(f"INSERT INTO {table} VALUES({','.join('?' for _ in values)})", values)
        for disposition in normalized["dispositions"]:
            review = metadata[disposition["review_id"]]
            reason_json, _ = _payload(review["reason_codes"])
            authority_json, _ = _payload(disposition["authority"])
            ledger_body = {
                "disposition": disposition, "reviewer_class": review["reviewer_class"],
                "reason_codes": review["reason_codes"], "reviewed_sequence": review["reviewed_sequence"],
            }
            connection.execute(
                "INSERT INTO nomination_dispositions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (retention_id, disposition["review_id"], disposition["candidate_id"],
                 disposition["group_id"], disposition["nomination_state"],
                 disposition["supporting_identity_digest"], review["reviewer_class"], reason_json,
                 review["reviewed_sequence"], authority_json, _digest(ledger_body)),
            )
            connection.executemany(
                "INSERT INTO nomination_disposition_members VALUES(?,?,?,?)",
                ((retention_id, disposition["review_id"], position, operation)
                 for position, operation in enumerate(disposition["operation_ids"])),
            )
        connection.commit()
        connection.close()
        connection = None
        descriptor = os.open(path, os.O_RDONLY)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = os.open(path.parent, os.O_RDONLY)
        os.fsync(descriptor)
        os.close(descriptor)
        return PublishedOperationalFamilyRetainedInputs(
            path=path, retention_id=retention_id, manifest_digest=manifest_digest,
            bundle_digest=bundle.bundle_digest, source_digest=bundle.source_digest,
        )
    except BaseException:
        if connection is not None:
            connection.close()
        if descriptor is not None:
            os.close(descriptor)
        if created:
            for partial in (path, Path(f"{path}-wal"), Path(f"{path}-shm"), Path(f"{path}-journal")):
                if partial.exists() and not partial.is_dir():
                    partial.unlink()
        raise


def _timed(connection: sqlite3.Connection, sql: str, params: tuple[object, ...],
           *, clock: Callable[[], float], limit: float) -> list[sqlite3.Row]:
    deadline = clock() + limit
    reached = False
    def interrupt() -> int:
        nonlocal reached
        reached = clock() >= deadline
        return int(reached)
    connection.set_progress_handler(interrupt, 1_000)
    try:
        rows = connection.execute(sql, params).fetchall()
        if clock() >= deadline:
            _fail("QUERY_TIMEOUT")
        return rows
    except sqlite3.OperationalError as exc:
        if reached and "interrupted" in str(exc).lower():
            _fail("QUERY_TIMEOUT")
        raise OperationalFamilyRetainedInputStoreError(f"PSI0F_F13_QUERY_FAILED:{exc}") from exc
    finally:
        connection.set_progress_handler(None, 0)


def _decode(payload: object, digest: object) -> object:
    if not isinstance(payload, str) or not isinstance(digest, str):
        _fail("PAYLOAD_INVALID")
    try:
        value = json.loads(payload)
    except json.JSONDecodeError:
        _fail("PAYLOAD_JSON_INVALID")
    if payload.encode() != _canonical(value) or digest != sha256(payload.encode().rstrip(b"\n")).hexdigest():
        _fail("PAYLOAD_REPLAY_MISMATCH")
    return value


def export_operational_family_retained_inputs(
    source: Path, retention_id: str, *, max_query_seconds: float = MAX_QUERY_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> ExportedOperationalFamilyRetainedInputs:
    verify_operational_family_retained_input_store_contract(
        build_operational_family_retained_input_store_contract()
    )
    path = Path(source)
    if (not path.is_file() or path.is_symlink() or not isinstance(retention_id, str) or not retention_id or
            max_query_seconds <= 0 or max_query_seconds > MAX_QUERY_SECONDS):
        _fail("SOURCE_OR_BOUND_INVALID")
    if any(Path(f"{path}{suffix}").exists() for suffix in ("-wal", "-shm", "-journal")):
        _fail("SOURCE_COMPANION_PRESENT")
    before = path.stat()
    connection = sqlite3.connect(
        f"file:{quote(str(path.resolve()), safe='/')}?mode=ro", uri=True, timeout=0.25,
    )
    connection.row_factory = sqlite3.Row
    query_count = 0
    try:
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
            _fail("QUERY_ONLY_NOT_ENFORCED")
        objects = connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
        expected_tables = set(TABLE_COLUMNS)
        actual_tables = {row["name"] for row in objects if row["type"] == "table"}
        actual_triggers = {row["name"] for row in objects if row["type"] == "trigger"}
        expected_triggers = {f"{table}_no_{action}" for table in expected_tables for action in ("update", "delete")}
        if actual_tables != expected_tables or actual_triggers != expected_triggers or len(objects) != 21:
            _fail("SCHEMA_OBJECT_MISMATCH")
        for table, columns in TABLE_COLUMNS.items():
            actual = tuple(row[1] for row in connection.execute(f"PRAGMA table_info({table})"))
            if actual != columns:
                _fail("SCHEMA_COLUMN_MISMATCH")
        def query(table: str, order: str) -> list[sqlite3.Row]:
            nonlocal query_count
            ceiling = 1 if table == "retention_manifest" else CEILINGS[table]
            rows = _timed(
                connection, f"SELECT * FROM {table} WHERE retention_id=? ORDER BY {order} LIMIT ?",
                (retention_id, ceiling + 1), clock=clock, limit=max_query_seconds,
            )
            query_count += 1
            if not rows or len(rows) > ceiling:
                _fail(f"{table.upper()}_COUNT_INVALID")
            return rows
        manifest_rows = query("retention_manifest", "retention_id")
        if len(manifest_rows) != 1:
            _fail("MANIFEST_COUNT_INVALID")
        manifest = dict(manifest_rows[0])
        cohort_rows = query("operation_cohort", "position")
        evaluation_rows = query("evaluation_summaries", "operation_id")
        runtime_rows = query("normalized_runtime_projections", "operation_id,input_digest")
        candidate_rows = query("candidate_payloads", "candidate_id")
        disposition_rows = query("nomination_dispositions", "reviewed_sequence,review_id")
        member_rows = query("nomination_disposition_members", "review_id,position")
        vocabulary = _decode(manifest["vocabulary_json"], manifest["vocabulary_digest"])
        evaluations = [_decode(row["payload_json"], row["payload_digest"]) for row in evaluation_rows]
        runtime = [_decode(row["payload_json"], row["payload_digest"]) for row in runtime_rows]
        candidates = [_decode(row["payload_json"], row["payload_digest"]) for row in candidate_rows]
        members: dict[str, list[sqlite3.Row]] = {}
        for row in member_rows:
            members.setdefault(row["review_id"], []).append(row)
        dispositions = []
        sequences = []
        for row in disposition_rows:
            reason_codes = _decode(row["reason_codes_json"], sha256(row["reason_codes_json"].encode().rstrip(b"\n")).hexdigest())
            authority = _decode(row["authority_json"], sha256(row["authority_json"].encode().rstrip(b"\n")).hexdigest())
            if (row["reviewer_class"] not in REVIEWER_CLASSES or not isinstance(reason_codes, list) or
                    not reason_codes or any(reason not in REASON_CODES for reason in reason_codes)):
                _fail("REVIEW_METADATA_INVALID")
            ordered = members.get(row["review_id"], [])
            if [item["position"] for item in ordered] != list(range(len(ordered))):
                _fail("MEMBERSHIP_SEQUENCE_INVALID")
            disposition = {
                "review_id": row["review_id"], "candidate_id": row["candidate_id"],
                "group_id": row["group_id"], "operation_ids": [item["operation_id"] for item in ordered],
                "nomination_state": row["nomination_state"],
                "supporting_identity_digest": row["supporting_identity_digest"], "authority": authority,
            }
            ledger_body = {
                "disposition": disposition, "reviewer_class": row["reviewer_class"],
                "reason_codes": reason_codes, "reviewed_sequence": row["reviewed_sequence"],
            }
            if row["payload_digest"] != _digest(ledger_body):
                _fail("DISPOSITION_PAYLOAD_REPLAY_MISMATCH")
            dispositions.append(disposition)
            sequences.append(row["reviewed_sequence"])
        if len(members) != len(dispositions) or len(sequences) != len(set(sequences)):
            _fail("DISPOSITION_COVERAGE_DRIFT")
        cohort = [{"position": row["position"], "operation_id": row["operation_id"]} for row in cohort_rows]
        contract = build_operational_family_retention_bundle_contract()
        if manifest["schema_version"] != SCHEMA_VERSION or manifest["f9_contract_digest"] != contract.contract_digest:
            _fail("MANIFEST_CONTRACT_DRIFT")
        bundle = build_fixture_operational_family_retention_bundle(
            contract, cohort=cohort, evaluations=evaluations, runtime=runtime,
            candidates=candidates, dispositions=dispositions, vocabulary=vocabulary,
        )
        replay_fixture_operational_family_retention_bundle(contract, bundle.files)
        manifest_identity = {
            "schema_version": SCHEMA_VERSION, "f9_contract_digest": contract.contract_digest,
            "source_engineering_revision": manifest["source_engineering_revision"],
            "vocabulary_digest": manifest["vocabulary_digest"],
            "logical_capture_sequence": manifest["logical_capture_sequence"],
            "bundle_digest": bundle.bundle_digest, "source_digest": bundle.source_digest,
        }
        if (_digest(manifest_identity) != retention_id or
                _digest({"retention_id": retention_id, **manifest_identity}) != manifest["manifest_digest"]):
            _fail("MANIFEST_REPLAY_MISMATCH")
    finally:
        connection.close()
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        _fail("SOURCE_IDENTITY_CHANGED")
    return ExportedOperationalFamilyRetainedInputs(
        retention_id=retention_id, manifest_digest=manifest["manifest_digest"],
        bundle=bundle, query_count=query_count,
    )
