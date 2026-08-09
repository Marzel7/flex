"""Deterministic OIP v2.1 census and bounded recovery planning.

This module is read-only.  It classifies the frozen eligible-migrated
population and produces an acquisition plan; execution is a separate, explicit
step so a measured RPC budget always exists before provider traffic begins.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

CONTRACT_VERSION = "OIP_V2_COVERAGE_V1"
STATES = ("COMPLETE", "PENDING", "UNAVAILABLE", "FAILED", "INELIGIBLE", "STALE")
REASONS = (
    "COMPLETE_EVIDENCE", "MISSING_CREATION_TRANSACTION", "MISSING_MIGRATION_TRANSACTION",
    "MISSING_CREATION_AND_MIGRATION_TRANSACTION", "MISSING_CREATION_SIGNATURE",
    "MISSING_LAUNCH_FACT", "NORMALIZATION_FAILED", "STALE_SIGNATURE_PROJECTION",
)


@dataclass(frozen=True)
class LaunchCoverage:
    mint: str
    state: str
    reason: str
    creation_signature: str | None
    migration_signature: str | None
    creation_transaction_present: bool
    migration_transaction_present: bool
    launch_fact_present: bool
    recovery: str
    creator: str | None = None
    launch_timestamp: int | None = None
    provider_source: str = "UNKNOWN"
    watchtower_population: bool = False
    discovery_participation: str = "UNAVAILABLE_IN_SUMMARY_SNAPSHOT"


def _ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _evidence_index(path: Path) -> tuple[set[str], dict[str, str], set[str]]:
    signatures: set[str] = set()
    launches: dict[str, str] = {}
    failed_artifacts: set[str] = set()
    with _ro(path) as conn:
        for row in conn.execute("SELECT natural_key FROM normalized_evidence_records WHERE fact_family='TransactionFact'"):
            signatures.add(row[0].split("/", 1)[-1])
        for row in conn.execute("SELECT payload_json FROM normalized_evidence_records WHERE fact_family='LaunchFact'"):
            payload = json.loads(row[0]); launches[payload["mint"]] = payload["creation_signature"]
        columns = {row[1] for row in conn.execute("PRAGMA table_info(normalization_status)")}
        if {"raw_artifact_digest", "status"} <= columns:
            failed_artifacts.update(row[0] for row in conn.execute(
                "SELECT raw_artifact_digest FROM normalization_status WHERE status='FAILED'"
            ))
    return signatures, launches, failed_artifacts


def census(
    production_db: Path,
    evidence_db: Path,
    *,
    max_source_rowid: int | None = None,
    max_migration_signal_updated_at: int | None = None,
) -> list[LaunchCoverage]:
    tx_signatures, launch_facts, _failed = _evidence_index(evidence_db)
    output: list[LaunchCoverage] = []
    with _ro(production_db) as conn:
        source_rows = conn.execute("""
            SELECT mint, NULLIF(create_tx_signature,''), NULLIF(migration_tx,''),
                   COALESCE(NULLIF(pf_ws_creator,''),NULLIF(earliest_tx_creator,'')),
                   COALESCE(migrated_at,created_at), COALESCE(NULLIF(migration_source,''),
                   NULLIF(source_platform,''),'UNKNOWN'), COALESCE(watchtower_related,0)
            FROM token_analysis
            WHERE (COALESCE(migration_tx,'')<>'' OR lifecycle_stage='migrated')
              AND (? IS NULL OR rowid <= ?)
              AND (? IS NULL OR COALESCE(migration_signal_updated_at,0) <= ?)
        """, (max_source_rowid, max_source_rowid, max_migration_signal_updated_at,
              max_migration_signal_updated_at)).fetchall()
        for mint, creation, migration, creator, timestamp, provider, watchtower in sorted(source_rows, key=lambda row: row[0]):
            create_present = bool(creation and creation in tx_signatures)
            migration_present = bool(migration and migration in tx_signatures)
            launch_present = mint in launch_facts
            if launch_present and creation and launch_facts[mint] != creation:
                state, reason, recovery = "STALE", "STALE_SIGNATURE_PROJECTION", "REPLAY"
            elif create_present and migration_present and launch_present:
                state, reason, recovery = "COMPLETE", "COMPLETE_EVIDENCE", "NONE"
            elif not creation:
                state, reason, recovery = "UNAVAILABLE", "MISSING_CREATION_SIGNATURE", "PERMANENTLY_UNAVAILABLE"
            elif not create_present and not migration_present:
                state, reason, recovery = "PENDING", "MISSING_CREATION_AND_MIGRATION_TRANSACTION", "BOUNDED_ACQUISITION"
            elif not create_present:
                state, reason, recovery = "PENDING", "MISSING_CREATION_TRANSACTION", "BOUNDED_ACQUISITION"
            elif not migration_present:
                state, reason, recovery = "PENDING", "MISSING_MIGRATION_TRANSACTION", "BOUNDED_ACQUISITION"
            else:
                state, reason, recovery = "PENDING", "MISSING_LAUNCH_FACT", "REPLAY"
            try:
                launch_timestamp = int(timestamp) if timestamp is not None else None
            except (TypeError, ValueError):
                launch_timestamp = None
            output.append(LaunchCoverage(mint, state, reason, creation, migration,
                                         create_present, migration_present, launch_present, recovery,
                                         creator, launch_timestamp, str(provider), bool(watchtower)))
    return output


def recovery_plan(rows: Iterable[LaunchCoverage], *, hard_call_limit: int | None = None) -> dict:
    rows = list(rows)
    signatures: set[str] = set()
    for row in rows:
        if row.recovery != "BOUNDED_ACQUISITION":
            continue
        if not row.creation_transaction_present and row.creation_signature:
            signatures.add(row.creation_signature)
        if not row.migration_transaction_present and row.migration_signature:
            signatures.add(row.migration_signature)
    ordered = sorted(signatures)
    selected = ordered if hard_call_limit is None else ordered[:max(0, hard_call_limit)]
    states = Counter(row.state for row in rows); reasons = Counter(row.reason for row in rows)
    return {
        "contract_version": CONTRACT_VERSION, "population": len(rows),
        "states": {key: states.get(key, 0) for key in STATES},
        "root_causes": {key: reasons.get(key, 0) for key in REASONS},
        "unique_missing_signatures": len(ordered), "planned_calls": len(selected),
        "hard_call_limit": hard_call_limit, "deferred_calls": len(ordered) - len(selected),
        "rpc_credits": {"calls": len(selected), "provider_rate_not_assumed": True},
        "signatures": selected,
        "no_duplicate_rpc": len(selected) == len(set(selected)),
    }


def serialise_census(rows: Iterable[LaunchCoverage]) -> list[dict]:
    return [asdict(row) for row in rows]


def reclassify_census_snapshot(
    snapshot: Iterable[dict], evidence_db: Path
) -> list[LaunchCoverage]:
    """Re-evaluate Evidence presence without rereading mutable source projections."""
    tx_signatures, launch_facts, _failed = _evidence_index(evidence_db)
    output: list[LaunchCoverage] = []
    for item in snapshot:
        creation = item.get("creation_signature")
        migration = item.get("migration_signature")
        mint = item["mint"]
        create_present = bool(creation and creation in tx_signatures)
        migration_present = bool(migration and migration in tx_signatures)
        launch_present = mint in launch_facts
        if launch_present and creation and launch_facts[mint] != creation:
            state, reason, recovery = "STALE", "STALE_SIGNATURE_PROJECTION", "REPLAY"
        elif create_present and migration_present and launch_present:
            state, reason, recovery = "COMPLETE", "COMPLETE_EVIDENCE", "NONE"
        elif not creation:
            state, reason, recovery = "UNAVAILABLE", "MISSING_CREATION_SIGNATURE", "PERMANENTLY_UNAVAILABLE"
        elif not create_present and not migration_present:
            state, reason, recovery = "PENDING", "MISSING_CREATION_AND_MIGRATION_TRANSACTION", "BOUNDED_ACQUISITION"
        elif not create_present:
            state, reason, recovery = "PENDING", "MISSING_CREATION_TRANSACTION", "BOUNDED_ACQUISITION"
        elif not migration_present:
            state, reason, recovery = "PENDING", "MISSING_MIGRATION_TRANSACTION", "BOUNDED_ACQUISITION"
        else:
            state, reason, recovery = "PENDING", "MISSING_LAUNCH_FACT", "REPLAY"
        output.append(LaunchCoverage(
            mint, state, reason, creation, migration, create_present, migration_present,
            launch_present, recovery, item.get("creator"), item.get("launch_timestamp"),
            item.get("provider_source", "UNKNOWN"), bool(item.get("watchtower_population")),
            item.get("discovery_participation", "UNAVAILABLE_IN_SUMMARY_SNAPSHOT"),
        ))
    return output
