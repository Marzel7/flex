"""PSI0H-E2 isolated migration-census to transaction-evidence adapter."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Callable, Mapping, Sequence

from src.acquisition.transaction import AcquisitionResponse
from src.evidence.config import EvidenceConfig
from src.evidence.service import EvidencePlatform


MAX_EVENTS = 20


class Psi0hCensusTransactionAdapterError(RuntimeError):
    pass


def _identity(path: Path) -> dict[str, int]:
    value = path.stat()
    return {"device": value.st_dev, "inode": value.st_ino, "size_bytes": value.st_size,
            "mtime_ns": value.st_mtime_ns}


def _append_attempt(path: Path, value: Mapping[str, Any]) -> None:
    encoded = (json.dumps(dict(value), sort_keys=True, separators=(",", ":")) + "\n").encode()
    with path.open("ab") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def collect_census_transactions(
    *, events: Sequence[Mapping[str, Any]], interval_start: int, interval_end: int,
    staging_root: Path, transport: Callable[[str, str], AcquisitionResponse],
) -> dict[str, Any]:
    root = Path(staging_root)
    if root.exists() or not root.parent.is_dir() or not 1 <= len(events) <= MAX_EVENTS:
        raise Psi0hCensusTransactionAdapterError("PSI0H_E2_BOUND_OR_DESTINATION_INVALID")
    selected = []
    seen = set()
    for source in events:
        row = dict(source)
        if set(row) != {"event_id", "event_type", "receive_utc_ns", "signature", "mint"}:
            raise Psi0hCensusTransactionAdapterError("PSI0H_E2_CENSUS_SHAPE_INVALID")
        received = row["receive_utc_ns"] // 1_000_000_000
        pair = (row["signature"], row["mint"])
        if (row["event_type"] != "MIGRATION" or not isinstance(row["event_id"], str) or
                not all(isinstance(value, str) and value for value in pair) or pair in seen or
                not interval_start <= received <= interval_end):
            raise Psi0hCensusTransactionAdapterError("PSI0H_E2_CENSUS_EVENT_INVALID")
        seen.add(pair)
        selected.append(row)
    selected.sort(key=lambda row: (row["receive_utc_ns"], row["signature"], row["mint"]))

    config = EvidenceConfig(
        platform_enabled=True, writer_enabled=True, queue_enabled=True,
        artifact_store_enabled=True, health_enabled=True, mirror_enabled=True,
        normalization_enabled=True, primitive_engine_enabled=True,
        database_path=root / "evidence.db", queue_path=root / "intake",
        artifact_path=root / "artifacts", mirror_spool_path=root / "mirror_spool",
        retained_acquisition_database_path=root / "retained_acquisition.db",
        retained_acquisition_degraded_path=root / "retention_degraded",
        writer_batch_size=MAX_EVENTS,
    )
    platform = EvidencePlatform(config)
    attempts = []
    root.mkdir()
    ledger_path = root / "physical_attempts.jsonl"
    try:
        for number, event in enumerate(selected, 1):
            attempt = {"sequence": number, "event_id": event["event_id"],
                       "signature": event["signature"], "mint": event["mint"],
                       "method": "getTransaction", "maximum_physical_calls": 1}
            _append_attempt(ledger_path, attempt)
            attempts.append(attempt)
            response = transport(event["signature"], event["mint"])
            if (not isinstance(response, AcquisitionResponse) or response.error is not None or
                    response.status != 200 or response.raw_body is None or
                    response.artifact_representation != "EXACT_PROVIDER_ARTIFACT" or
                    response.metadata.method != "getTransaction" or
                    response.metadata.retry_count != 0):
                raise Psi0hCensusTransactionAdapterError("PSI0H_E2_RESPONSE_INVALID")
            payload = response.data
            result = payload.get("result") if isinstance(payload, Mapping) else None
            block_time = result.get("blockTime") if isinstance(result, Mapping) else None
            signatures = ((result.get("transaction") or {}).get("signatures")
                          if isinstance(result, Mapping) else None) or []
            if (not isinstance(block_time, int) or not interval_start <= block_time <= interval_end or
                    event["signature"] not in signatures):
                raise Psi0hCensusTransactionAdapterError("PSI0H_E2_EVENT_TIME_OR_SIGNATURE_DRIFT")
            item = platform.mirror.item_from_response(
                response, http_method="POST", url="https://isolated.invalid/",
                request_payload={"jsonrpc": "2.0", "id": 1, "method": "getTransaction",
                                 "params": [event["signature"], {"encoding": "jsonParsed",
                                  "maxSupportedTransactionVersion": 0}]},
                handoff_at=response.metadata.timestamp,
            )
            platform.mirror._publish(item)
        platform.writer.start()
        while any(any((root / "intake" / state).glob("*.json"))
                  for state in ("pending", "retry")):
            outcome = platform.writer.run_once()
            if outcome["failed"]:
                raise Psi0hCensusTransactionAdapterError("PSI0H_E2_WRITER_FAILED")
    finally:
        platform.writer.stop()
        platform.mirror.stop()

    connection = sqlite3.connect(f"file:{(root / 'evidence.db').resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        primitive_rows = []
        used_evidence = set()
        for row in connection.execute(
                "SELECT primitive_id,primitive_type,window_start,window_end,generated_at,missing_inputs_json "
                "FROM primitive_observations WHERE window_start>=? AND window_end<=? "
                "ORDER BY window_start,window_end,primitive_id LIMIT 20", (interval_start, interval_end)):
            evidence_ids = [value[0] for value in connection.execute(
                "SELECT evidence_id FROM primitive_evidence_inputs WHERE primitive_id=? ORDER BY evidence_id",
                (row["primitive_id"],))]
            used_evidence.update(evidence_ids)
            primitive_rows.append({"primitive_id": row["primitive_id"],
                "primitive_type": row["primitive_type"], "window_start": row["window_start"],
                "window_end": row["window_end"], "generated_at": row["generated_at"],
                "evidence_ids": evidence_ids, "missing_inputs": json.loads(row["missing_inputs_json"])})
        evidence_rows = []
        envelope_ids = set()
        for evidence_id in sorted(used_evidence):
            row = connection.execute(
                "SELECT n.evidence_id,n.fact_family,n.observed_at,n.payload_digest,p.envelope_id "
                "FROM normalized_evidence_records n JOIN normalized_evidence_provenance np USING(evidence_id) "
                "JOIN evidence_provenance p ON p.provider_request_id=np.provider_request_id "
                "WHERE n.evidence_id=? LIMIT 1", (evidence_id,)).fetchone()
            if row is None:
                raise Psi0hCensusTransactionAdapterError("PSI0H_E2_LINEAGE_INCOMPLETE")
            envelope_ids.add(row["envelope_id"])
            evidence_rows.append({"evidence_id": row["evidence_id"], "envelope_id": row["envelope_id"],
                                  "fact_family": row["fact_family"], "event_time": row["observed_at"],
                                  "payload_digest": row["payload_digest"]})
        envelopes = []
        for envelope_id in sorted(envelope_ids):
            row = connection.execute(
                "SELECT envelope_id,acquired_at,artifact_digest FROM evidence_envelopes WHERE envelope_id=?",
                (envelope_id,)).fetchone()
            times = [value["event_time"] for value in evidence_rows if value["envelope_id"] == envelope_id]
            envelopes.append({"envelope_id": envelope_id, "event_time": min(times),
                              "acquired_at": row["acquired_at"], "artifact_digest": row["artifact_digest"]})
    finally:
        connection.close()
    return {"envelopes": envelopes, "evidence_rows": evidence_rows,
            "primitive_rows": primitive_rows, "provider_request_count": len(attempts),
            "attempts": attempts, "staging_identity": _identity(root / "evidence.db"),
            "physical_attempt_ledger": str(ledger_path),
            "attempts_digest": sha256(json.dumps(attempts, sort_keys=True,
                separators=(",", ":")).encode()).hexdigest()}
