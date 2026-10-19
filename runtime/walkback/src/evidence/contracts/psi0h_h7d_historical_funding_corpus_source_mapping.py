"""PSI0H-H7D historical funding-corpus operation-boundary source mapping.

This is a planning/read-only boundary that inspects retained lower-level historical
token/funding sources and classifies whether they can be reconstructed into
operation-boundary source material for H7/H8.

No providers, no live writes, no candidate comparisons and no dispositions are
performed in this boundary.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping
import sqlite3

SCHEMA_VERSION = "psi0h-h7d.historical-funding-corpus-source-mapping.v1"
RUN_ID = "psi0h-h7d-historical-funding-corpus-source-mapping"

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

VERDICT_READY_BOUNDARY_CAPABLE = "H7D_READY_OPERATION_BOUNDARY_CORPUS"
VERDICT_HOLD_PARTIAL_CORPUS = "H7D_PARTIAL_CORPUS_SOURCE_MAPPING"
VERDICT_BLOCKED_SOURCE_ABSENT = "H7D_BLOCKED_SOURCE_ABSENT"

SOURCE_CLASS_BOUNDARY_CAPABLE = "BOUNDARY_CAPABLE"
SOURCE_CLASS_CANDIDATE_ONLY = "CANDIDATE_ONLY"
SOURCE_CLASS_NOT_RETAINED = "SOURCE_NOT_RETAINED"

BLOCKER_H7D_SOURCE_MISSING = "H7D_SOURCE_MISSING"
BLOCKER_H7D_SOURCE_NOT_SQLITE = "H7D_SOURCE_NOT_SQLITE"
BLOCKER_H7D_NO_OPERATION_BOUNDARY_SCHEMA = "H7D_NO_OPERATION_BOUNDARY_SCHEMA"
BLOCKER_H7D_ZERO_OPERATION_ROWS = "H7D_ZERO_OPERATION_ROWS"
BLOCKER_H7D_NO_TOKEN_LAUNCH_LINKAGE = "H7D_NO_TOKEN_LAUNCH_LINKAGE"
BLOCKER_H7D_NO_ROLE_TOPOLOGY = "H7D_NO_ROLE_TOPOLOGY"
BLOCKER_H7D_NO_EVENT_TIME = "H7D_NO_EVENT_TIME"
BLOCKER_H7D_NO_CONTINUITY_SIGNAL = "H7D_NO_CROSS_TOKEN_CONTINUITY_SIGNAL"
BLOCKER_H7D_ROW_LIMIT_EXCEEDED = "H7D_ROW_LIMIT_EXCEEDED"
BLOCKER_H7D_TABLE_ACCESS_ERROR = "H7D_TABLE_ACCESS_ERROR"


class Psi0hH7DHistoricalFundingCorpusSourceMappingError(RuntimeError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _digest(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def _file_identity(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {
        "device": int(stat.st_dev),
        "inode": int(stat.st_ino),
        "size_bytes": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def _exists(conn: sqlite3.Connection, table: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
            (table,),
        ).fetchone()
        return bool(row)
    except Exception:
        return False


def _count_rows(conn: sqlite3.Connection, table: str, clause: str = "") -> int:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table} {clause}").fetchone()[0] or 0)
    except Exception:
        return 0


def _collect_scalar_series(conn: sqlite3.Connection, query: str, limit: int = 5000) -> list[Any]:
    try:
        rows = conn.execute(f"{query} LIMIT ?", (limit,)).fetchall()
    except Exception:
        return []
    return [row[0] for row in rows]


def _collect_token_funding_profile(path: str, *, maximum_rows_per_source: int) -> tuple[dict[str, Any], list[str]]:
    p = Path(path)
    if not p.exists():
        return {
            "source_path": path,
            "source_identity": {},
            "source_class": SOURCE_CLASS_NOT_RETAINED,
            "source_blockers": [BLOCKER_H7D_SOURCE_MISSING],
        }, [BLOCKER_H7D_SOURCE_MISSING]

    if p.suffix.lower() not in {".db", ".sqlite", ".sqlite3"} and not p.is_file():
        return {
            "source_path": path,
            "source_identity": _file_identity(p),
            "source_class": SOURCE_CLASS_NOT_RETAINED,
            "source_blockers": [BLOCKER_H7D_SOURCE_NOT_SQLITE],
        }, [BLOCKER_H7D_SOURCE_NOT_SQLITE]

    metrics: dict[str, Any] = {
        "source_path": str(p),
        "source_identity": _file_identity(p),
        "row_counts": {},
        "source_blockers": [],
        "topology_signals": {},
        "launch_signals": {},
        "continuity_signals": {},
        "provider_signal_rows": {},
        "schema_coverage": [],
    }

    blockers: list[str] = []
    try:
        conn = sqlite3.connect(f"file:{p}?mode=ro&immutable=1", uri=True)
        conn.row_factory = sqlite3.Row
    except Exception:
        return {
            "source_path": str(p),
            "source_identity": _file_identity(p),
            "source_class": SOURCE_CLASS_NOT_RETAINED,
            "source_blockers": [BLOCKER_H7D_SOURCE_NOT_SQLITE],
        }, [BLOCKER_H7D_SOURCE_NOT_SQLITE]

    try:
        if not _exists(conn, "token_analysis") and not _exists(conn, "creator_funders"):
            blockers.append(BLOCKER_H7D_NO_OPERATION_BOUNDARY_SCHEMA)
            metrics["source_blockers"] = blockers
            metrics["source_class"] = SOURCE_CLASS_CANDIDATE_ONLY
            return metrics, blockers

        tables = {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        metrics["schema_coverage"] = sorted(tables)

        # Funding edges
        creator_funder_rows = _count_rows(conn, "creator_funders")
        creator_inbound_rows = _count_rows(conn, "creator_inbound_transfers")
        creator_outgoing_rows = _count_rows(conn, "creator_outgoing_transfers")
        token_rows = _count_rows(conn, "token_analysis")
        migration_rows = _count_rows(conn, "migrated_tokens")
        verify_rows = _count_rows(conn, "pumpfun_migration_verification")
        tx_rows = _count_rows(conn, "creator_tx_ledger")
        chain_rows = _count_rows(conn, "funding_chains")

        metrics["row_counts"] = {
            "creator_funders": creator_funder_rows,
            "creator_inbound_transfers": creator_inbound_rows,
            "creator_outgoing_transfers": creator_outgoing_rows,
            "token_analysis": token_rows,
            "migrated_tokens": migration_rows,
            "pumpfun_migration_verification": verify_rows,
            "creator_tx_ledger": tx_rows,
            "funding_chains": chain_rows,
        }

        # Source/topology signals.
        metrics["topology_signals"]["funding_rows_total"] = creator_funder_rows + creator_inbound_rows + creator_outgoing_rows
        metrics["topology_signals"]["edge_subject_fields"] = []
        sample_limit = max(1, min(maximum_rows_per_source, 5000))
        edge_subjects = set(
            _collect_scalar_series(
                conn,
                "SELECT DISTINCT creator_address FROM creator_funders WHERE creator_address IS NOT NULL",
                limit=sample_limit,
            )
        )
        if not edge_subjects:
            edge_subjects = set(
                _collect_scalar_series(
                    conn,
                    "SELECT DISTINCT funder_address FROM creator_funders WHERE funder_address IS NOT NULL",
                    limit=sample_limit,
                )
            )
        if not edge_subjects:
            edge_subjects = set(
                _collect_scalar_series(
                    conn,
                    "SELECT DISTINCT creator_pubkey FROM creator_state WHERE creator_pubkey IS NOT NULL",
                    limit=sample_limit,
                )
            )
        metrics["topology_signals"]["edge_subject_fields"] = sorted(s for s in edge_subjects if isinstance(s, str))

        # Launch/operation linkage via migration evidence.
        launch_token_mint_count = _count_rows(conn, "token_analysis", "WHERE migration_tx IS NOT NULL OR migration_slot IS NOT NULL OR migrated_at IS NOT NULL")
        launch_tx_count = _count_rows(conn, "migrated_tokens", "WHERE migration_tx IS NOT NULL")
        verify_token_count = _count_rows(conn, "pumpfun_migration_verification", "WHERE migration_tx IS NOT NULL")
        metrics["launch_signals"]["launch_linked_token_rows"] = launch_token_mint_count
        metrics["launch_signals"]["migrated_tokens_rows"] = launch_tx_count
        metrics["launch_signals"]["pumpfun_verification_rows"] = verify_token_count

        # Event-time and lineage signals.
        event_windows: list[Any] = []
        event_windows.extend(
            _collect_scalar_series(
                conn,
                "SELECT DISTINCT blockTime FROM creator_tx_ledger WHERE blockTime IS NOT NULL",
                limit=sample_limit,
            )
        )
        event_windows.extend(
            _collect_scalar_series(
                conn,
                "SELECT DISTINCT migration_time FROM migrated_tokens WHERE migration_time IS NOT NULL",
                limit=sample_limit,
            )
        )
        event_windows.extend(
            _collect_scalar_series(
                conn,
                "SELECT DISTINCT migrated_at FROM token_analysis WHERE migrated_at IS NOT NULL",
                limit=sample_limit,
            )
        )
        metrics["provider_signal_rows"]["event_rows"] = len([x for x in event_windows if isinstance(x, int)])

        # Continuity signals from recurring participants.
        recurring_funders = _collect_scalar_series(
            conn,
            """
            SELECT funder_address
            FROM creator_funders
            WHERE funder_address IS NOT NULL AND funder_address!=''
            GROUP BY funder_address
            HAVING COUNT(*) >= 2
            """,
            limit=sample_limit,
        )
        recurring_creators = _collect_scalar_series(
            conn,
            """
            SELECT creator_address
            FROM creator_funders
            WHERE creator_address IS NOT NULL AND creator_address!=''
            GROUP BY creator_address
            HAVING COUNT(*) >= 2
            """,
            limit=sample_limit,
        )
        recurring_launch_creators = _collect_scalar_series(
            conn,
            """
            SELECT earliest_tx_creator
            FROM token_analysis
            WHERE earliest_tx_creator IS NOT NULL AND earliest_tx_creator!=''
            GROUP BY earliest_tx_creator
            HAVING COUNT(*) >= 2
            """,
            limit=sample_limit,
        )
        metrics["continuity_signals"] = {
            "recurring_funders": sorted(set(str(x) for x in recurring_funders if isinstance(x, str))),
            "recurring_creators": sorted(set(str(x) for x in recurring_creators if isinstance(x, str))),
            "recurring_launch_creators": sorted(set(str(x) for x in recurring_launch_creators if isinstance(x, str))),
            "recurring_subject_count": len(set(recurring_funders + recurring_creators + recurring_launch_creators)),
        }

        topology_ok = metrics["topology_signals"]["funding_rows_total"] > 0 and bool(metrics["topology_signals"]["edge_subject_fields"])
        launch_ok = launch_token_mint_count > 0 or launch_tx_count > 0 or verify_token_count > 0
        event_ok = metrics["provider_signal_rows"]["event_rows"] > 0
        continuity_ok = metrics["continuity_signals"]["recurring_subject_count"] > 0

        if not topology_ok:
            blockers.append(BLOCKER_H7D_NO_ROLE_TOPOLOGY)
        if not launch_ok:
            blockers.append(BLOCKER_H7D_NO_TOKEN_LAUNCH_LINKAGE)
        if not event_ok:
            blockers.append(BLOCKER_H7D_NO_EVENT_TIME)
        if not continuity_ok:
            blockers.append(BLOCKER_H7D_NO_CONTINUITY_SIGNAL)

        metrics["schema_coverage"] = sorted(set(metrics["schema_coverage"]))
        if (creator_funder_rows + creator_inbound_rows + creator_outgoing_rows + tx_rows + token_rows) == 0:
            blockers.append(BLOCKER_H7D_ZERO_OPERATION_ROWS)

        # Lightweight row/sample gate: if single source explodes beyond the hard bound,
        # mark as blocked for this first pass so mapping stays bounded and replayable.
        if any(v > 25_000_000 for v in metrics["row_counts"].values()):
            blockers.append(BLOCKER_H7D_ROW_LIMIT_EXCEEDED)

        # Classify.
        source_class = SOURCE_CLASS_CANDIDATE_ONLY if blockers else SOURCE_CLASS_BOUNDARY_CAPABLE

        # If no operation-bearing schema exists, prefer explicit BLOCKER_NO_OPERATION_BOUNDARY_SCHEMA.
        if not _exists(conn, "creator_funders") and not _exists(conn, "token_analysis"):
            source_class = SOURCE_CLASS_CANDIDATE_ONLY
            if BLOCKER_H7D_NO_OPERATION_BOUNDARY_SCHEMA not in blockers:
                blockers.append(BLOCKER_H7D_NO_OPERATION_BOUNDARY_SCHEMA)

        metrics["source_class"] = source_class
        metrics["source_blockers"] = sorted(set(blockers))
        return metrics, sorted(set(blockers))
    finally:
        conn.close()


def qualify_historical_funding_corpus_source_mapping(
    *,
    funding_sources: list[str],
    maximum_rows_per_source: int = 250000,
) -> dict[str, Any]:
    if not isinstance(maximum_rows_per_source, int) or maximum_rows_per_source <= 0:
        raise Psi0hH7DHistoricalFundingCorpusSourceMappingError("H7D_MAX_ROWS_PER_SOURCE_INVALID")

    if not isinstance(funding_sources, list) or not funding_sources:
        raise Psi0hH7DHistoricalFundingCorpusSourceMappingError("H7D_SOURCE_LIST_INVALID")

    source_profiles: list[dict[str, Any]] = []
    blockers: list[str] = []
    reconstructable_count = 0
    candidate_count = 0
    boundary_capable: list[dict[str, Any]] = []

    for source in funding_sources[:40]:
        source = str(source or "")
        if not source:
            profile = {
                "source_path": "",
                "source_identity": {},
                "source_class": SOURCE_CLASS_NOT_RETAINED,
                "source_blockers": [BLOCKER_H7D_SOURCE_MISSING],
            }
            blockers.append(BLOCKER_H7D_SOURCE_MISSING)
            source_profiles.append(profile)
            continue

        profile, source_blockers = _collect_token_funding_profile(source, maximum_rows_per_source=maximum_rows_per_source)
        source_profiles.append(profile)

        if profile.get("source_class") == SOURCE_CLASS_BOUNDARY_CAPABLE:
            reconstructable_count += 1
            candidate_count += 1
            boundary_capable.append(
                {
                    "source_path": profile["source_path"],
                    "source_identity": profile.get("source_identity", {}),
                    "row_counts": profile.get("row_counts", {}),
                    "launch_rows": profile.get("launch_signals", {}).get("launch_linked_token_rows", 0),
                    "continuity_subject_count": profile.get("continuity_signals", {}).get("recurring_subject_count", 0),
                }
            )
        else:
            candidate_count += 1
            blockers.extend(source_blockers)
            if profile.get("source_class") == SOURCE_CLASS_NOT_RETAINED:
                source_profiles[-1]["source_blockers"] = profile.get("source_blockers", [BLOCKER_H7D_SOURCE_MISSING])

    unique_blockers = sorted(set(blockers))

    status = "PASS" if reconstructable_count > 0 else "HOLD"
    if reconstructable_count > 0:
        verdict = VERDICT_READY_BOUNDARY_CAPABLE
        next_decision = "RERUN_H7_USING_BOUNDARY_CAPABLE_FUNDING_SOURCE"
        instruction = (
            "At least one retained funding source appears boundary-capable. Rebuild H7 source selection "
            "from the selected source(s) and run replay-capable execution only."
        )
        stop_conditions: list[str] = []
    elif candidate_count > 0:
        verdict = VERDICT_HOLD_PARTIAL_CORPUS
        next_decision = "H7D_BOUNDARY_CAPTURE_REQUIREMENT"
        instruction = (
            "No boundary-capable source yet. Preserve blocking reasons and choose a bounded capture/rebind "
            "path for missing operation-boundary fields."
        )
        stop_conditions = ["NO_BOUNDARY_CAPABLE_SOURCE"]
    else:
        verdict = VERDICT_BLOCKED_SOURCE_ABSENT
        next_decision = "BLOCKED_SOURCE_ABSENT"
        instruction = "No candidate historical funding source was readable."
        stop_conditions = ["NO_SOURCE_ROWS"]

    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": RUN_ID,
        "milestone": "PSI0H-H7D",
        "status": status,
        "verdict": verdict,
        "funding_sources_input": [str(x) for x in funding_sources],
        "boundaries": {
            "max_sources_scanned": len(funding_sources),
            "max_rows_per_source": int(maximum_rows_per_source),
            "provider_calls": 0,
            "provider_read_only": False,
        },
        "source_inventory": {
            "source_count": len(source_profiles),
            "reconstructable_source_count": reconstructable_count,
            "candidate_source_count": candidate_count,
            "boundary_capable_sources": boundary_capable,
            "source_rows": source_profiles,
        },
        "operation_boundary_requirements": {
            "required_fields": {
                "creator_funding_edges": True,
                "launch_linkage": True,
                "role_topology": True,
                "event_time": True,
                "recurring_continuity": True,
                "lineage_provenance": True,
            },
            "provider_dependent": False,
            "no_provider_ok": True,
            "no_retry_or_failover": True,
            "no_reconciliation_authority": True,
        },
        "source_matching_plan": {
            "candidate_classes": [
                SOURCE_CLASS_BOUNDARY_CAPABLE,
                SOURCE_CLASS_CANDIDATE_ONLY,
                SOURCE_CLASS_NOT_RETAINED,
            ],
            "reconstructable_sources": [x["source_path"] for x in boundary_capable],
            "missing_fields_summary": {
                "role_topology": [x["source_path"] for x in source_profiles if "H7D_NO_ROLE_TOPOLOGY" in x.get("source_blockers", [])],
                "launch_linkage": [x["source_path"] for x in source_profiles if "H7D_NO_TOKEN_LAUNCH_LINKAGE" in x.get("source_blockers", [])],
                "event_time": [x["source_path"] for x in source_profiles if "H7D_NO_EVENT_TIME" in x.get("source_blockers", [])],
                "continuity": [x["source_path"] for x in source_profiles if "H7D_NO_CROSS_TOKEN_CONTINUITY_SIGNAL" in x.get("source_blockers", [])],
            },
        },
        "replay_tamper_controls": {
            "artifact_replay_required": True,
            "row_ceiling_enforced": True,
            "source_identity_enforced": True,
            "stop_if_schema_missing": True,
            "stop_if_provider_path_unbounded": False,
        },
        "next_action": {
            "decision": next_decision,
            "instruction": instruction,
            "required_authorization": "NONE",
        },
        "stop_conditions": stop_conditions,
        "blockers": unique_blockers,
        "authority": dict(AUTHORITY),
        "scope": {
            "comparison": False,
            "candidate_generation": False,
            "candidate_disposition": False,
            "provider_access": False,
            "source_read": True,
            "monitoring": False,
            "alerting": False,
        },
    }

    result["artifact_digest"] = _digest({k: v for k, v in result.items() if k != "artifact_digest"})
    return result


def verify_h7d_source_mapping(record: Mapping[str, Any]) -> bool:
    if not isinstance(record, Mapping):
        raise Psi0hH7DHistoricalFundingCorpusSourceMappingError("H7D_RECORD_INVALID")
    if record.get("schema_version") != SCHEMA_VERSION:
        raise Psi0hH7DHistoricalFundingCorpusSourceMappingError("H7D_RECORD_SCHEMA_MISMATCH")
    if record.get("status") not in {"PASS", "HOLD"}:
        raise Psi0hH7DHistoricalFundingCorpusSourceMappingError("H7D_RECORD_STATUS_INVALID")
    if any(record.get("authority", {}).values()):
        raise Psi0hH7DHistoricalFundingCorpusSourceMappingError("H7D_RECORD_AUTHORITY_EXPANDED")

    digest = str(record.get("artifact_digest", ""))
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise Psi0hH7DHistoricalFundingCorpusSourceMappingError("H7D_RECORD_DIGEST_INVALID")
    replay = dict(record)
    replay.pop("artifact_digest", None)
    if _digest(replay) != digest:
        raise Psi0hH7DHistoricalFundingCorpusSourceMappingError("H7D_RECORD_DIGEST_MISMATCH")
    return True
