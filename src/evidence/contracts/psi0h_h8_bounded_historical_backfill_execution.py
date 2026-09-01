"""PSI0H-H8 bounded historical backfill execution contract.

Execution is strictly one-shot reconstruction from H7-planned retained sources.
No provider calls, no monitoring/comparison/disposition authority, and strict
bounded ceilings are enforced at both source and row level.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "psi0h-h8.bounded-historical-backfill-execution.v1"
RUN_ID = "psi0h-h8-bounded-historical-backfill"
H7_SCHEMA_VERSION = "psi0h-h7.bounded-historical-backfill-preflight.v1"
H7_VERDICT = "READY_H7_BOUND_PLAN"
AUTHORIZATION_ENV = "PSI0H_H8_REAL_BACKFILL_AUTHORIZED"
AUTHORIZATION_VALUE = "1"

AUTHORITY = {
    "comparison": False,
    "candidate_generation": False,
    "candidate_disposition": False,
    "supported": False,
    "same_operation": False,
    "same_human": False,
    "alerting": False,
    "monitoring": False,
    "consumer": False,
    "policy": False,
    "ranking": False,
    "trading": False,
    "integration": False,
    "deployment": False,
    "activation": False,
}


class Psi0hH8BoundedHistoricalBackfillExecutionError(RuntimeError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _digest(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.loads(handle.read())
    if not isinstance(payload, Mapping):
        raise Psi0hH8BoundedHistoricalBackfillExecutionError("PSI0H_H8_H7_ARTIFACT_INVALID")
    return payload


def _file_identity(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {
        "device": int(stat.st_dev),
        "inode": int(stat.st_ino),
        "size_bytes": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def _safe_json(value: Any) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, Mapping) else None


def _to_int(value: Any) -> int | None:
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            return None
    return None


def _emit_payload_from_row(
    *,
    source_label: str,
    operation_key: str,
    source: str | None = None,
    destination: str | None = None,
    wallet: str | None = None,
    creator: str | None = None,
    funder: str | None = None,
    recipient: str | None = None,
    mechanism: str,
    event_type: str,
    window_start: int,
    window_end: int,
    amount: float | None = None,
) -> dict[str, Any]:
    subjects: list[str] = []
    for item in (source, destination, wallet, creator, funder, recipient):
        if isinstance(item, str) and item:
            subjects.append(item)

    payload: dict[str, Any] = {
        "source": source_label,
        "operation_id": f"H8:{operation_key}",
        "mechanism": mechanism,
        "event_types": [event_type],
        "window": {"start": window_start, "end": window_end},
        "window_start": window_start,
        "window_end": window_end,
        "wallet": wallet or "",
        "wallets": subjects,
        "roles": {
            "creator": creator or "",
            "funder": funder or "",
            "recipient": recipient or "",
            "source": source or "",
            "destination": destination or "",
        },
    }
    if destination is not None and destination:
        payload["destination"] = destination
    if source is not None and source:
        payload["source"] = source
    if funder is not None and funder:
        payload["funder"] = funder
    if recipient is not None and recipient:
        payload["recipient"] = recipient
    if creator is not None and creator:
        payload["creator"] = creator
    if amount is not None:
        payload["amount"] = amount
    return payload


def _record_payload_digest(payload: Mapping[str, Any]) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _collect_funding_corpus_rows(conn: sqlite3.Connection, *, source_path: str, row_budget: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if row_budget <= 0:
        return [], []

    evidence_rows: list[dict[str, Any]] = []
    primitive_rows: list[dict[str, Any]] = []
    rows_left = row_budget
    tables = {str(name) for name, in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

    def maybe_emit(
        *,
        table: str,
        row_id: Any,
        kind: str,
        payload: Mapping[str, Any],
        emitted_event_time: int,
        mechanism: str,
    ) -> None:
        nonlocal rows_left
        if rows_left <= 0:
            return
        normalized_source = str(source_path)
        row_key = _to_int(row_id) or 0
        row_key_text = f"{table}:{kind}:{row_key}"
        payload_with_observed = dict(payload)
        payload_with_observed["observed_at"] = emitted_event_time

        evidence_rows.append(
            {
                "source_path": normalized_source,
                "evidence_id": row_key_text + ":e",
                "event_time": emitted_event_time,
                "fact_family": mechanism,
                "operation_id": payload.get("operation_id"),
                "payload_digest": _record_payload_digest(payload_with_observed),
                "payload": payload_with_observed,
            }
        )
        primitive_rows.append(
            {
                "source_path": normalized_source,
                "primitive_id": row_key_text + ":p",
                "primitive_type": f"{table}_{mechanism.upper()}",
                "window_start": int(payload.get("window", {}).get("start", emitted_event_time)),
                "window_end": int(payload.get("window", {}).get("end", emitted_event_time)),
                "generated_at": emitted_event_time,
                "event_time": emitted_event_time,
                "missing_inputs_json": None,
                "payload": dict(payload),
                "payload_digest": _record_payload_digest(payload),
                "operation_id": payload.get("operation_id"),
            }
        )
        rows_left -= 2

    # Launch/funding continuity candidate rows.
    if "token_analysis" in tables:
        query = """
            SELECT rowid, mint, earliest_tx_creator, migration_tx, migrated_at, first_observed_at,
                   network_funder_address, pool_address, pumpswap_pool_address, create_tx_signature,
                   source_platform
            FROM token_analysis
            ORDER BY rowid
        """
        try:
            for row in conn.execute(query).fetchmany(rows_left // 2):
                event_time = _to_int(row["migrated_at"]) or _to_int(row["first_observed_at"]) or 0
                creator = str(row["earliest_tx_creator"] or row["network_funder_address"] or "")
                funder = str(row["network_funder_address"] or "")
                destination = str(row["pool_address"] or row["pumpswap_pool_address"] or "")
                operation_key = "|".join(
                    x for x in (
                        str(row["mint"] or ""),
                        str(row["migration_tx"] or ""),
                        str(row["create_tx_signature"] or ""),
                        creator,
                        funder,
                        destination,
                    ) if x
                ) or str(row["mint"])
                payload = _emit_payload_from_row(
                    source_label="token_analysis",
                    operation_key=operation_key,
                    source=str(row["source_platform"] or ""),
                    destination=destination,
                    wallet=creator,
                    creator=creator,
                    funder=funder,
                    mechanism="LAUNCH_LINKAGE",
                    event_type="migration_launch",
                    window_start=event_time,
                    window_end=event_time,
                    amount=None,
                )
                if rows_left > 0:
                    maybe_emit(table="token_analysis", row_id=row["rowid"], kind="launch", payload=payload, emitted_event_time=event_time, mechanism="LAUNCH_LINKAGE")
        except Exception:
            pass

    if rows_left <= 0:
        return evidence_rows, primitive_rows

    if "pumpfun_migration_verification" in tables:
        query = """
            SELECT rowid, mint, migrated_at, migration_tx, dex, pumpswap_pool_address,
                   pre_signal_score, pre_migration_signal_source, final_verdict
            FROM pumpfun_migration_verification
            ORDER BY rowid
        """
        try:
            for row in conn.execute(query).fetchmany(rows_left // 2):
                event_time = _to_int(row["migrated_at"]) or 0
                funder = str(row["migration_tx"] or "")
                destination = str(row["pumpswap_pool_address"] or "")
                operation_key = "|".join((str(row["mint"] or ""), str(row["migration_tx"] or ""))) or str(row["rowid"])
                payload = _emit_payload_from_row(
                    source_label="pumpfun_migration_verification",
                    operation_key=operation_key,
                    source=str(row["dex"] or ""),
                    destination=destination,
                    wallet=funder,
                    creator=funder,
                    funder=funder,
                    mechanism="LAUNCH_VERIFICATION",
                    event_type="migration_verification",
                    window_start=event_time,
                    window_end=event_time,
                    amount=None,
                )
                if rows_left > 0:
                    maybe_emit(table="pumpfun_migration_verification", row_id=row["rowid"], kind="verification", payload=payload, emitted_event_time=event_time, mechanism="LAUNCH_VERIFICATION")
        except Exception:
            pass

    if rows_left <= 0:
        return evidence_rows, primitive_rows

    if "creator_funders" in tables:
        query = """
            SELECT rowid, creator_address, funder_address, amount_sol, unixepoch(first_detected_at) AS first_detected_at,
                   source_type, is_cex, cex_exchange, cex_type
            FROM creator_funders
            ORDER BY rowid
        """
        try:
            for row in conn.execute(query).fetchmany(rows_left // 2):
                event_time = _to_int(row["first_detected_at"]) or 0
                payload = _emit_payload_from_row(
                    source_label="creator_funders",
                    operation_key="|".join((str(row["creator_address"] or ""), str(row["funder_address"] or ""), str(row["source_type"] or ""))),
                    source=str(row["source_type"] or ""),
                    wallet=str(row["creator_address"] or ""),
                    creator=str(row["creator_address"] or ""),
                    funder=str(row["funder_address"] or ""),
                    destination=str(row["cex_exchange"] or ""),
                    mechanism="FUNDING_EDGE",
                    event_type="incoming_funding",
                    window_start=event_time,
                    window_end=event_time,
                    amount=float(row["amount_sol"]) if isinstance(row["amount_sol"], (int, float)) else None,
                )
                if rows_left > 0:
                    maybe_emit(table="creator_funders", row_id=row["rowid"], kind="funding", payload=payload, emitted_event_time=event_time, mechanism="FUNDING_EDGE")
        except Exception:
            pass

    if rows_left <= 0:
        return evidence_rows, primitive_rows

    if "creator_outgoing_transfers" in tables:
        query = """
            SELECT rowid, creator_address, recipient_address, amount_sol, block_time, recipient_type, transaction_signature
            FROM creator_outgoing_transfers
            ORDER BY rowid
        """
        try:
            for row in conn.execute(query).fetchmany(rows_left // 2):
                event_time = _to_int(row["block_time"]) or 0
                payload = _emit_payload_from_row(
                    source_label="creator_outgoing_transfers",
                    operation_key="|".join((str(row["creator_address"] or ""), str(row["recipient_address"] or ""), str(row["transaction_signature"] or ""))),
                    source=str(row["recipient_type"] or ""),
                    wallet=str(row["creator_address"] or ""),
                    creator=str(row["creator_address"] or ""),
                    recipient=str(row["recipient_address"] or ""),
                    mechanism="OUTBOUND_DISTRIBUTION",
                    event_type="creator_outgoing",
                    window_start=event_time,
                    window_end=event_time,
                    amount=float(row["amount_sol"]) if isinstance(row["amount_sol"], (int, float)) else None,
                )
                if rows_left > 0:
                    maybe_emit(table="creator_outgoing_transfers", row_id=row["rowid"], kind="outbound", payload=payload, emitted_event_time=event_time, mechanism="OUTBOUND_DISTRIBUTION")
        except Exception:
            pass

    if rows_left <= 0:
        return evidence_rows, primitive_rows

    if "creator_receivers" in tables:
        query = """
            SELECT rowid, creator_address, receiver_address, amount_sol, unixepoch(timestamp) AS timestamp, receiver_type
            FROM creator_receivers
            ORDER BY rowid
        """
        try:
            for row in conn.execute(query).fetchmany(rows_left // 2):
                event_time = _to_int(row["timestamp"]) or 0
                payload = _emit_payload_from_row(
                    source_label="creator_receivers",
                    operation_key="|".join((str(row["creator_address"] or ""), str(row["receiver_address"] or ""), str(row["receiver_type"] or ""))),
                    source=str(row["receiver_type"] or ""),
                    wallet=str(row["creator_address"] or ""),
                    creator=str(row["creator_address"] or ""),
                    recipient=str(row["receiver_address"] or ""),
                    mechanism="OUTBOUND_DISTRIBUTION",
                    event_type="creator_receiver",
                    window_start=event_time,
                    window_end=event_time,
                    amount=float(row["amount_sol"]) if isinstance(row["amount_sol"], (int, float)) else None,
                )
                if rows_left > 0:
                    maybe_emit(table="creator_receivers", row_id=row["rowid"], kind="receiver", payload=payload, emitted_event_time=event_time, mechanism="OUTBOUND_DISTRIBUTION")
        except Exception:
            pass

    if rows_left <= 0:
        return evidence_rows, primitive_rows

    if "creator_inbound_transfers" in tables:
        query = """
            SELECT rowid, creator_address, funder_address, amount_sol, timestamp, source_type
            FROM creator_inbound_transfers
            ORDER BY rowid
        """
        try:
            for row in conn.execute(query).fetchmany(rows_left // 2):
                event_time = _to_int(row["timestamp"]) or 0
                payload = _emit_payload_from_row(
                    source_label="creator_inbound_transfers",
                    operation_key="|".join((str(row["creator_address"] or ""), str(row["funder_address"] or ""), str(row["source_type"] or ""))),
                    source=str(row["source_type"] or ""),
                    wallet=str(row["creator_address"] or ""),
                    creator=str(row["creator_address"] or ""),
                    funder=str(row["funder_address"] or ""),
                    mechanism="INBOUND_EDGE",
                    event_type="creator_inbound",
                    window_start=event_time,
                    window_end=event_time,
                    amount=float(row["amount_sol"]) if isinstance(row["amount_sol"], (int, float)) else None,
                )
                if rows_left > 0:
                    maybe_emit(table="creator_inbound_transfers", row_id=row["rowid"], kind="inbound", payload=payload, emitted_event_time=event_time, mechanism="INBOUND_EDGE")
        except Exception:
            pass

    if rows_left <= 0:
        return evidence_rows, primitive_rows

    if "creator_tx_ledger" in tables:
        query = """
            SELECT id, creator_pubkey, counterparty, blockTime, tx_type, source, signature
            FROM creator_tx_ledger
            ORDER BY rowid
        """
        try:
            for row in conn.execute(query).fetchmany(rows_left // 2):
                event_time = _to_int(row["blockTime"]) or 0
                payload = _emit_payload_from_row(
                    source_label="creator_tx_ledger",
                    operation_key="|".join((str(row["creator_pubkey"] or ""), str(row["counterparty"] or ""), str(row["signature"] or ""))),
                    source=str(row["source"] or ""),
                    wallet=str(row["creator_pubkey"] or ""),
                    creator=str(row["creator_pubkey"] or ""),
                    recipient=str(row["counterparty"] or ""),
                    mechanism="TX_LEDGER",
                    event_type=str(row["tx_type"] or "tx"),
                    window_start=event_time,
                    window_end=event_time,
                )
                if rows_left > 0:
                    maybe_emit(table="creator_tx_ledger", row_id=row["id"], kind="ledger", payload=payload, emitted_event_time=event_time, mechanism="TX_LEDGER")
        except Exception:
            pass

    return evidence_rows, primitive_rows


def _collect_source_rows(path: Path, *, row_ceiling: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if row_ceiling <= 0:
        return [], []

    conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        tables = {str(name) for name, in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if not tables:
            return [], []

        evidence_rows: list[dict[str, Any]] = []
        if "normalized_evidence_records" in tables:
            for evidence_id, observed_at, payload_json in conn.execute(
                "SELECT evidence_id, observed_at, payload_json FROM normalized_evidence_records ORDER BY rowid"
            ).fetchmany(row_ceiling):
                payload = _safe_json(payload_json)
                if payload is None:
                    continue
                row = {
                    "source_path": str(path),
                    "evidence_id": str(evidence_id),
                    "event_time": int(observed_at) if isinstance(observed_at, int) else None,
                    "fact_family": payload.get("fact_family"),
                    "operation_id": payload.get("operation_id"),
                    "payload_digest": sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                }
                evidence_rows.append(row)
            if row_ceiling > len(evidence_rows):
                remaining = row_ceiling - len(evidence_rows)
            else:
                remaining = 0
        else:
            remaining = row_ceiling

        primitive_rows: list[dict[str, Any]] = []
        if "primitive_observations" in tables and remaining > 0:
            query = (
                "SELECT primitive_id, primitive_type, window_start, window_end, generated_at, "
                "missing_inputs_json, output_payload_json "
                "FROM primitive_observations ORDER BY rowid LIMIT ?"
            )
            for primitive_id, primitive_type, window_start, window_end, generated_at, missing_inputs_json, output_payload_json in conn.execute(query, (remaining,)).fetchall():
                payload = _safe_json(output_payload_json)
                row = {
                    "source_path": str(path),
                    "primitive_id": str(primitive_id),
                    "primitive_type": str(primitive_type) if primitive_type is not None else "",
                    "window_start": int(window_start) if isinstance(window_start, int) else None,
                    "window_end": int(window_end) if isinstance(window_end, int) else None,
                    "generated_at": int(generated_at) if isinstance(generated_at, int) else None,
                    "event_time": int(window_end) if isinstance(window_end, int) else None,
                    "missing_inputs_json": missing_inputs_json,
                    "payload": payload or {},
                }
                if payload is not None:
                    row["payload_digest"] = sha256(
                        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
                    ).hexdigest()
                if payload and "operation_id" in payload:
                    row["operation_id"] = payload["operation_id"]
                primitive_rows.append(row)

        if evidence_rows and remaining == 0 and primitive_rows:
            return evidence_rows, primitive_rows

        if remaining > 0:
            fallback_evidence, fallback_primitive = _collect_funding_corpus_rows(
                conn, source_path=str(path), row_budget=remaining
            )
            evidence_rows.extend(fallback_evidence)
            primitive_rows.extend(fallback_primitive)

        return evidence_rows, primitive_rows
    finally:
        conn.close()


@dataclass(frozen=True)
class _CandidateSource:
    path: str
    identity: dict[str, int]
    row_ceiling: int


def _build_authorization_binding(*, h7_artifact: Mapping[str, Any]) -> dict[str, Any]:
    destination = h7_artifact.get("destination", {})
    source_plan = h7_artifact.get("source_plan", {})
    if not isinstance(destination, Mapping):
        raise Psi0hH8BoundedHistoricalBackfillExecutionError("PSI0H_H8_H7_DESTINATION_INVALID")
    if not isinstance(source_plan, Mapping):
        raise Psi0hH8BoundedHistoricalBackfillExecutionError("PSI0H_H8_H7_SOURCE_PLAN_INVALID")

    return {
        "h7_schema_version": h7_artifact.get("schema_version"),
        "h7_status": h7_artifact.get("status"),
        "h7_verdict": h7_artifact.get("verdict"),
        "h7_run_id": h7_artifact.get("milestone"),
        "h7_artifact_digest": h7_artifact.get("artifact_digest"),
        "h7_boundaries": h7_artifact.get("boundaries", {}),
        "h7_selection": h7_artifact.get("selection", {}),
        "destination_root": destination.get("destination_root"),
        "destination_isolation_required": destination.get("isolation_required"),
        "destination_candidate_paths_frozen": destination.get("candidate_paths_are_frozen"),
        "h7_replay_controls": h7_artifact.get("replay_tamper_controls", {}),
    }


def qualify_h8_backfill_execution(
    *, h7_artifact_path: str | Path, output_artifact_path: str | Path, stop_on_first_blocker: bool = True
) -> dict[str, Any]:
    h7_payload = _read_json(Path(h7_artifact_path))
    if h7_payload.get("schema_version") != H7_SCHEMA_VERSION:
        raise Psi0hH8BoundedHistoricalBackfillExecutionError("PSI0H_H8_H7_SCHEMA_MISMATCH")
    if h7_payload.get("status") != "PASS":
        raise Psi0hH8BoundedHistoricalBackfillExecutionError("PSI0H_H8_H7_STATUS_NOT_PASS")
    if h7_payload.get("verdict") != H7_VERDICT:
        raise Psi0hH8BoundedHistoricalBackfillExecutionError("PSI0H_H8_H7_VERDICT_NOT_READY")
    if os.environ.get(AUTHORIZATION_ENV) != AUTHORIZATION_VALUE:
        raise Psi0hH8BoundedHistoricalBackfillExecutionError("PSI0H_H8_AUTHORIZATION_MISSING")

    destination = h7_payload.get("destination", {})
    if not isinstance(destination, Mapping):
        raise Psi0hH8BoundedHistoricalBackfillExecutionError("PSI0H_H8_H7_DESTINATION_INVALID")
    destination_root = destination.get("destination_root")
    if not isinstance(destination_root, str) or not destination_root:
        raise Psi0hH8BoundedHistoricalBackfillExecutionError("PSI0H_H8_DESTINATION_ROOT_INVALID")

    output_path = Path(output_artifact_path)
    if output_path.exists():
        raise Psi0hH8BoundedHistoricalBackfillExecutionError("PSI0H_H8_OUTPUT_EXISTS")
    destination_parent = Path(destination_root)
    if not destination_parent.is_dir():
        raise Psi0hH8BoundedHistoricalBackfillExecutionError("PSI0H_H8_DESTINATION_ROOT_MISSING")
    resolved_destination_parent = destination_parent.resolve()
    output_parent = output_path.parent.resolve()
    try:
        output_parent.relative_to(resolved_destination_parent)
    except ValueError as exc:
        raise Psi0hH8BoundedHistoricalBackfillExecutionError("PSI0H_H8_DESTINATION_PATH_OUTSIDE_ROOT") from exc

    boundaries = h7_payload.get("boundaries", {})
    if not isinstance(boundaries, Mapping):
        raise Psi0hH8BoundedHistoricalBackfillExecutionError("PSI0H_H8_BOUNDARIES_INVALID")
    max_sources = boundaries.get("max_sources")
    max_rows_per_source = boundaries.get("max_rows_per_source")
    max_total_rows = boundaries.get("max_total_rows")
    for item in (max_sources, max_rows_per_source, max_total_rows):
        if not isinstance(item, int) or item <= 0:
            raise Psi0hH8BoundedHistoricalBackfillExecutionError("PSI0H_H8_BOUNDARY_INVALID")

    source_rows = h7_payload.get("source_plan", {}).get("candidate_sources", [])
    if not isinstance(source_rows, list):
        raise Psi0hH8BoundedHistoricalBackfillExecutionError("PSI0H_H8_H7_CANDIDATE_SOURCES_INVALID")
    selected = source_rows[:max_sources]

    candidates: list[_CandidateSource] = []
    for row in selected:
        if not isinstance(row, Mapping):
            if stop_on_first_blocker:
                raise Psi0hH8BoundedHistoricalBackfillExecutionError("PSI0H_H8_SOURCE_ROW_INVALID")
            continue
        source_path = row.get("source_path")
        row_ceiling = int(row.get("row_reconstruction_ceiling") or max_rows_per_source)
        if row_ceiling <= 0:
            row_ceiling = max_rows_per_source
        if not isinstance(source_path, str) or not source_path:
            if stop_on_first_blocker:
                raise Psi0hH8BoundedHistoricalBackfillExecutionError("PSI0H_H8_SOURCE_PATH_INVALID")
            continue
        source_identity = row.get("source_identity")
        if not isinstance(source_identity, Mapping):
            source_identity = {}
        candidates.append(
            _CandidateSource(
                path=source_path,
                identity={k: int(v) for k, v in source_identity.items() if k in {"device", "inode", "size_bytes", "mtime_ns"}},
                row_ceiling=min(row_ceiling, max_rows_per_source),
            )
        )

    if not candidates:
        return {
            "schema_version": SCHEMA_VERSION,
            "milestone": "PSI0H-H8",
            "run_id": RUN_ID,
            "status": "PASS",
            "execution_status": "HALT_NO_CANDIDATES",
            "h7_artifact": str(h7_artifact_path),
            "h7_binding": _build_authorization_binding(h7_artifact=h7_payload),
            "scope": {
                "source_read": False,
                "provider_access": False,
                "service_changes": False,
                "comparison": False,
                "monitoring": False,
                "candidate_generation": False,
                "candidate_disposition": False,
                "activation": False,
            },
            "authority": dict(AUTHORITY),
            "provider_request_count": 0,
            "execution": {
                "source_scanned": 0,
                "selected_source_count": 0,
                "emitted_evidence_rows": 0,
                "emitted_primitive_rows": 0,
                "source_snapshots": [],
                "selection": [],
                "stop_conditions": ["No candidate source rows in H7 source_plan"],
                "blockers": ["H7_SOURCE_PLAN_EMPTY"],
                "source_identity_drifts": [],
            },
            "artifact": str(output_path),
            "provider_runtime_semantics": {
                "max_provider_requests": 0,
                "provider_boundaries": {
                    "retries_allowed": 0,
                    "pagination_enabled": False,
                    "failover_allowed": False,
                    "requests_per_source": 0,
                },
            },
            "reconstruction_mode": {
                "event_time_semantics": "window",
                "strict_event_time": True,
                "allow_observation_time_fallback": False,
                "providerless": True,
            },
        }

    source_snapshots = []
    executed_sources: list[dict[str, Any]] = []
    evidence_out: list[dict[str, Any]] = []
    primitive_out: list[dict[str, Any]] = []
    stop_conditions: list[str] = []
    blockers: list[str] = []
    identity_drifts: list[dict[str, Any]] = []
    rows_used = 0

    for candidate in candidates:
        if Path(candidate.path).resolve() == output_path.resolve():
            raise Psi0hH8BoundedHistoricalBackfillExecutionError("PSI0H_H8_SOURCE_AND_OUTPUT_PATH_REUSED")
        if rows_used >= max_total_rows:
            break
        source_path = Path(candidate.path)
        if not source_path.exists():
            blockers.append(f"H7_SOURCE_PATH_MISSING:{candidate.path}")
            if stop_on_first_blocker:
                break
            continue

        live_identity = _file_identity(source_path)
        drifted = False
        if candidate.identity:
            for key, expected in candidate.identity.items():
                if live_identity.get(key) != int(expected):
                    blockers.append(f"SOURCE_IDENTITY_DRIFT:{candidate.path}:{key}:{expected}->{live_identity.get(key)}")
                    identity_drifts.append({
                        "source_path": candidate.path,
                        "field": key,
                        "expected": expected,
                        "observed": live_identity.get(key),
                    })
                    drifted = True
                    if stop_on_first_blocker:
                        break
            if stop_on_first_blocker and drifted:
                break
        row_budget = min(candidate.row_ceiling, max_total_rows - rows_used)
        source_snapshots.append({
            "source_path": str(source_path),
            "source_identity": live_identity,
            "row_budget": row_budget,
            "blocked_by_drift": drifted,
        })

        selected_evidence, selected_primitive = _collect_source_rows(source_path, row_ceiling=row_budget)
        rows_used += len(selected_evidence) + len(selected_primitive)
        evidence_out.extend(selected_evidence)
        primitive_out.extend(selected_primitive)
        executed_sources.append({
            "source_path": str(source_path),
            "row_budget": row_budget,
            "selected_evidence_count": len(selected_evidence),
            "selected_primitive_count": len(selected_primitive),
            "row_drifted": drifted,
        })

    if rows_used >= max_total_rows:
        stop_conditions.append("MAX_TOTAL_ROWS_CEILING_REACHED")
    if len(executed_sources) >= max_sources:
        stop_conditions.append("MAX_SOURCES_CEILING_REACHED")
    if not blockers and not evidence_out and not primitive_out:
        stop_conditions.append("NO_RECONSTRUCTABLE_ROWS")

    execution_status = "COMPLETED" if not blockers else "HALT"

    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "milestone": "PSI0H-H8",
        "run_id": RUN_ID,
        "status": "PASS" if execution_status == "COMPLETED" else "HOLD",
        "execution_status": execution_status,
        "h7_artifact": str(h7_artifact_path),
        "h7_binding": _build_authorization_binding(h7_artifact=h7_payload),
        "scope": {
            "source_read": True,
            "provider_access": False,
            "service_changes": False,
            "comparison": False,
            "monitoring": False,
            "candidate_generation": False,
            "candidate_disposition": False,
            "activation": False,
        },
        "authority": dict(AUTHORITY),
        "provider_request_count": 0,
        "execution": {
            "destination_root": str(destination_parent),
            "selected_source_count": len(executed_sources),
            "source_scanned": len(source_snapshots),
            "source_snapshots": source_snapshots,
            "selection": [c.__dict__ for c in candidates],
            "evidence_rows": evidence_out,
            "primitive_rows": primitive_out,
            "evidence_count": len(evidence_out),
            "primitive_count": len(primitive_out),
            "stop_conditions": sorted(set(stop_conditions)),
            "blockers": sorted(set(blockers)),
            "source_identity_drifts": identity_drifts,
            "execution_plan": {
                "max_sources": max_sources,
                "max_rows_per_source": max_rows_per_source,
                "max_total_rows": max_total_rows,
            },
        },
        "provider_runtime_semantics": {
            "max_provider_requests": 0,
            "provider_boundaries": {
                "retries_allowed": 0,
                "pagination_enabled": False,
                "failover_allowed": False,
                "requests_per_source": 0,
            },
        },
        "reconstruction_mode": {
            "event_time_semantics": "window",
            "strict_event_time": True,
            "allow_observation_time_fallback": False,
            "providerless": True,
        },
        "output_digest_bindings": {
            "h7_artifact_path": str(h7_artifact_path),
            "h7_artifact_digest": h7_payload.get("artifact_digest"),
            "artifact_path": str(output_path),
        },
    }
    result["artifact_digest"] = _digest(result)
    return result


def verify_h8_backfill_execution(record: Mapping[str, Any]) -> bool:
    if not isinstance(record, Mapping):
        raise Psi0hH8BoundedHistoricalBackfillExecutionError("PSI0H_H8_RECORD_INVALID")
    if record.get("schema_version") != SCHEMA_VERSION:
        raise Psi0hH8BoundedHistoricalBackfillExecutionError("PSI0H_H8_RECORD_SCHEMA_INVALID")
    digest = str(record.get("artifact_digest", ""))
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise Psi0hH8BoundedHistoricalBackfillExecutionError("PSI0H_H8_RECORD_DIGEST_INVALID")
    replay = dict(record)
    replay.pop("artifact_digest")
    if _digest(replay) != digest:
        raise Psi0hH8BoundedHistoricalBackfillExecutionError("PSI0H_H8_RECORD_DIGEST_MISMATCH")

    if record.get("execution_status") not in {"COMPLETED", "HALT", "HALT_NO_CANDIDATES"}:
        raise Psi0hH8BoundedHistoricalBackfillExecutionError("PSI0H_H8_RECORD_STATUS_INVALID")
    if any(record.get("authority", {}).values()):
        raise Psi0hH8BoundedHistoricalBackfillExecutionError("PSI0H_H8_RECORD_AUTHORITY_EXPANDED")

    if not all(isinstance(item, int) for item in (
        record.get("provider_request_count"),
        record.get("execution", {}).get("evidence_count"),
        record.get("execution", {}).get("primitive_count"),
        record.get("execution", {}).get("selected_source_count"),
    )):
        raise Psi0hH8BoundedHistoricalBackfillExecutionError("PSI0H_H8_RECORD_FIELD_INVALID")
    return True
