"""X29.1 — Operational Topology Intelligence Framework: the canonical model.

Combines the three independent classifiers (funding_topology.py,
operational_behaviour_tags.py, funding_mechanism.py) into ONE per-mint
record:

    {mint: {topology: str, behaviours: [str, ...], mechanisms: [str, ...]}}

This is the ONLY storage shape. Per the brief: "Do not store the hierarchy
itself. Instead store independent classifications... The hierarchy is
generated dynamically by the UI." No tree is persisted or computed here as a
data structure to store -- build_hierarchy() below computes a drill-down
VIEW on demand from this flat per-mint map, entirely derivable and never a
second source of truth.

Classifier execution order (per the brief's Stage 1/2/3): Topology first
(exactly one result), then Behaviour (additive), then Mechanism (additive).
This module does not change that order or add cross-dimension inference --
each classifier is computed independently and their results are merely
zipped together by mint.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

from src.ops.funding_topology import build_topology_classification, TOPOLOGY_ORDER, TOPOLOGY_LABELS
from src.ops.operational_behaviour_tags import build_behaviour_classification, BEHAVIOUR_ORDER, BEHAVIOUR_LABELS
from src.ops.funding_mechanism import build_mechanism_classification, MECHANISM_ORDER, MECHANISM_LABELS
from src.ops.watchtower_alignment import WATCHTOWER_OPERATOR_ID
from src.ops.detection_reconciliation import _LIVE_DETECTION_SOURCES
from src.ops.discovery_window import WINDOW_ALL, window_seconds_for
from src.utils.infra_mapping import is_known_account

# X67.37 — the public boundary (via discovery_window's own accessor, not a
# duplicated literal) above which a request is treated as "all-time" for
# population-source purposes: only at this point is the full canonical
# registry unioned into the population (see build_operational_intelligence).
_WINDOW_ALL_SECONDS = window_seconds_for(WINDOW_ALL)

# X61 — quick birth->create->migration window thresholds, and the derived
# per-mint diagnostic (evidence-only; no new detection).
QUICK_BIRTH_MAX_AGE_SECONDS = 5
QUICK_MIGRATION_MAX_SECONDS = 900
QUICK_BIRTH_MIGRATION = "QUICK_BIRTH_MIGRATION"

CREATOR_FUNDING_PROXY_ENABLED = os.environ.get(
    "DISCOVERY_CREATOR_FUNDING_PROXY_ENABLED", "1"
).strip().lower() not in {"0", "no", "false", "off"}

_LOG = logging.getLogger(__name__)


def _normalise_unix_seconds(value: Any) -> int | None:
    """Normalise persisted Unix seconds/milliseconds/microseconds."""
    if value is None:
        return None
    try:
        timestamp = int(float(value))
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            timestamp = int(parsed.timestamp())
        except (TypeError, ValueError, OverflowError):
            return None
    absolute = abs(timestamp)
    if absolute >= 100000000000000:
        timestamp //= 1000000
    elif absolute >= 100000000000:
        timestamp //= 1000
    return timestamp


def classify_quick_birth_migration(creator_birth_at: Any, create_at: Any, migration_at: Any) -> dict[str, Any]:
    """Classify the stored birth -> CREATE -> migration intervals."""
    birth = _normalise_unix_seconds(creator_birth_at)
    create = _normalise_unix_seconds(create_at)
    migration = _normalise_unix_seconds(migration_at)

    creator_age = (create - birth) if (birth is not None and create is not None) else None
    migration_delay = (migration - create) if (migration is not None and create is not None) else None

    if birth is None:
        reason = "MISSING_CREATOR_BIRTH"
    elif create is None:
        reason = "MISSING_CREATE"
    elif migration is None:
        reason = "MISSING_MIGRATION"
    elif creator_age is None or migration_delay is None:
        reason = "UNKNOWN"
    elif creator_age < 0 or migration_delay < 0:
        reason = "NEGATIVE_INTERVAL"
    elif creator_age > QUICK_BIRTH_MAX_AGE_SECONDS:
        reason = "BIRTH_TOO_OLD"
    elif migration_delay > QUICK_MIGRATION_MAX_SECONDS:
        reason = "MIGRATION_TOO_SLOW"
    else:
        reason = "OK"

    evaluable = reason in {"BIRTH_TOO_OLD", "OK", "MIGRATION_TOO_SLOW"}

    return {
        "creator_birth_at": birth,
        "creator_birth_time": birth,
        "create_at": create,
        "create_time": create,
        "migration_at": migration,
        "migration_time": migration,
        "creator_age_at_create_seconds": creator_age,
        "creator_age_seconds": creator_age,
        "create_to_migration_seconds": migration_delay,
        "migration_seconds": migration_delay,
        "quick_birth_evaluable": evaluable,
        "quick_birth_reason": reason,
        "is_quick_birth_migration": reason == "OK",
    }


def select_creator_birth(
    confirmed_first_transaction: Any = None,
    confirmed_wallet_birth: Any = None,
    earliest_creator_signature: Any = None,
    persisted_first_seen: Any = None,
    funding_proxies: list[tuple[Any, str]] | None = None,
    allow_funding_proxy: bool = False,
) -> tuple[Any, str | None, str]:
    """Select creator birth evidence in the documented strongest-first order."""
    candidates = (
        (confirmed_first_transaction, "confirmed_first_transaction", "CONFIRMED_FIRST_TRANSACTION"),
        (confirmed_wallet_birth, "confirmed_wallet_birth", "EXHAUSTIVE_HISTORY_CONFIRMED"),
        (earliest_creator_signature, "creator_tx_ledger.earliest_signature", "OBSERVED_EARLIEST_SIGNATURE"),
        (persisted_first_seen, "creator_watch.first_seen_ts", "PERSISTED_FIRST_SEEN"),
    )

    for value, source, quality in candidates:
        if value is not None:
            return value, source, quality

    if allow_funding_proxy:
        for value, source in (funding_proxies or []):
            if value is not None:
                return value, source, "CREATOR_FUNDING_PROXY"

    return None, None, "UNKNOWN"


def summarise_quick_birth_diagnostics(records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    reason_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}

    for record in records.values():
        reason = record["quick_birth_reason"]
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

        source = record.get("creator_birth_source") or "UNKNOWN"
        source_counts[source] = source_counts.get(source, 0) + 1

    return {
        "evaluable": sum(r["quick_birth_evaluable"] for r in records.values()),
        "reasons": reason_counts,
        "creator_birth_sources": source_counts,
        "creator_funding_proxy_enabled": CREATOR_FUNDING_PROXY_ENABLED,
    }


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def _enrich_discovery_records(
    ops_db_path: str, core_db_path: str, records: dict[str, dict[str, Any]], now: int
) -> dict[str, Any]:
    """Attach operation and timing dimensions from persisted evidence only."""
    if not records:
        return {"watchtower": {}, "timing_sources": {}}

    mints = list(records)
    placeholders = ",".join("?" for _ in mints)

    ops = sqlite3.connect(f"file:{ops_db_path}?mode=ro", uri=True, timeout=5)
    ops.row_factory = sqlite3.Row

    watchtower_source_audit = []
    try:
        confirmed_treasuries = set()
        if _table_exists(ops, "wt_confirmed_treasuries"):
            confirmed_treasuries = {
                row[0] for row in ops.execute("SELECT treasury FROM wt_confirmed_treasuries")
            }

        explicit_operations: dict[str, Any] = {}
        canonical_explicit_tokens_all: set[str] = set()
        attributed_treasuries: dict[str, set[str]] = {}

        cex_exchange_name: dict[str, str] = {}

        if _table_exists(ops, "wt_attribution_outcomes"):
            canonical_explicit_tokens_all = {
                row[0]
                for row in ops.execute(
                    "SELECT mint FROM wt_attribution_outcomes WHERE operator_id=?",
                    (WATCHTOWER_OPERATOR_ID,),
                )
            }

            for row in ops.execute(
                f"SELECT mint,operator_id,outcome_type,evidence_json FROM wt_attribution_outcomes WHERE mint IN ({placeholders})",
                mints,
            ):
                if row["operator_id"]:
                    explicit_operations[row["mint"]] = row["operator_id"]

                try:
                    evidence = json.loads(row["evidence_json"] or "{}")
                except (TypeError, ValueError):
                    evidence = {}

                attributed_treasuries.setdefault(row["mint"], set()).update(
                    evidence.get("treasuries") or []
                )

                if row["outcome_type"] == "KNOWN_CEX_REACHED":
                    boundary_name = (evidence.get("boundary") or {}).get("name")
                    if boundary_name:
                        cex_exchange_name[row["mint"]] = boundary_name

            source_row = ops.execute(
                "SELECT COUNT(*) rows,MIN(completed_at) first_at,MAX(completed_at) last_at FROM wt_attribution_outcomes WHERE operator_id=?",
                (WATCHTOWER_OPERATOR_ID,),
            ).fetchone()
            watchtower_source_audit.append({
                "table": "wt_attribution_outcomes",
                "assignment_field": "operator_id",
                "confidence_field": "confidence",
                "rows": source_row["rows"],
                "first_at": source_row["first_at"],
                "last_at": source_row["last_at"],
                "used_by_discovery": True,
            })

        if _table_exists(ops, "wt_walkback_queue"):
            for row in ops.execute(
                f"SELECT mint,treasury FROM wt_walkback_queue WHERE mint IN ({placeholders}) AND treasury IS NOT NULL",
                mints,
            ):
                attributed_treasuries.setdefault(row["mint"], set()).add(row["treasury"])

        confirmed_tokens: set[str] = set()
        confirmed_tokens_all: set[str] = set()

        if _table_exists(ops, "watchtower_token_attribution"):
            confirmed_tokens_all = {
                row[0]
                for row in ops.execute(
                    "SELECT mint FROM watchtower_token_attribution WHERE reviewed_status='CONFIRMED'"
                )
            }

            confirmed_tokens = {
                row[0]
                for row in ops.execute(
                    f"SELECT mint FROM watchtower_token_attribution WHERE mint IN ({placeholders}) AND reviewed_status='CONFIRMED'",
                    mints,
                )
            }

            source_row = ops.execute(
                "SELECT COUNT(*) rows,MIN(scored_at) first_at,MAX(scored_at) last_at FROM watchtower_token_attribution WHERE reviewed_status='CONFIRMED'"
            ).fetchone()
            watchtower_source_audit.append({
                "table": "watchtower_token_attribution",
                "assignment_field": "reviewed_status=CONFIRMED",
                "confidence_field": "tier",
                "rows": source_row["rows"],
                "first_at": source_row["first_at"],
                "last_at": source_row["last_at"],
                "used_by_discovery": True,
            })

        canonical_registry_tokens: set[str] = set()
        canonical_registry_all: dict[str, Any] = {}
        launch_rows: dict[str, Any] = {}

        if _table_exists(ops, "wt_watchtower_launches"):
            for row in ops.execute(
                "SELECT mint,create_time FROM wt_watchtower_launches WHERE mint IS NOT NULL"
            ):
                canonical_registry_tokens.add(row["mint"])
                canonical_registry_all[row["mint"]] = _normalise_unix_seconds(row["create_time"])

            source_row = ops.execute(
                "SELECT COUNT(DISTINCT mint) rows,MIN(create_time) first_at,MAX(create_time) last_at FROM wt_watchtower_launches WHERE mint IS NOT NULL"
            ).fetchone()
            watchtower_source_audit.append({
                "table": "wt_watchtower_launches",
                "assignment_field": "mint",
                "confidence_field": "confidence",
                "rows": source_row["rows"],
                "first_at": source_row["first_at"],
                "last_at": source_row["last_at"],
                "used_by_discovery": True,
            })

            for row in ops.execute(
                f"SELECT mint,creator_wallet,create_time,treasury_wallet,birth_to_launch_seconds,create_to_migration_secs,detection_source,creator_extraction_method,confidence FROM wt_watchtower_launches WHERE mint IN ({placeholders}) ORDER BY recorded_at DESC",
                mints,
            ):
                launch_rows.setdefault(row["mint"], row)

        confirmation_completed_at: dict[str, int] = {}
        if canonical_registry_tokens and _table_exists(ops, "wt_attribution_outcomes"):
            registry_placeholders = ",".join("?" for _ in canonical_registry_tokens)
            for row in ops.execute(
                f"SELECT mint, completed_at FROM wt_attribution_outcomes WHERE mint IN ({registry_placeholders})",
                list(canonical_registry_tokens),
            ):
                if row["completed_at"] is not None:
                    confirmation_completed_at[row["mint"]] = int(row["completed_at"])

        lifecycle_rows: dict[str, Any] = {}
        if _table_exists(ops, "wt_token_lifecycle"):
            for row in ops.execute(
                f"SELECT mint,funded_at,launched_at,migrated_at,operation_uuid,treasury FROM wt_token_lifecycle WHERE mint IN ({placeholders})",
                mints,
            ):
                lifecycle_rows[row["mint"]] = row

        birth_rows: dict[str, Any] = {}
        if _table_exists(ops, "wt_creator_birth_launch"):
            for row in ops.execute(
                f"SELECT token_mint,funded_at,launched_at FROM wt_creator_birth_launch WHERE token_mint IN ({placeholders})",
                mints,
            ):
                birth_rows[row["token_mint"]] = row

        wrap_births: dict[str, Any] = {}
        if _table_exists(ops, "wt_wrap_close_candidates"):
            creators = sorted({r.get("creator") for r in records.values() if r.get("creator")})
            if creators:
                creator_placeholders = ",".join("?" for _ in creators)
                for row in ops.execute(
                    f"SELECT creator,funded_at FROM wt_wrap_close_candidates WHERE creator IN ({creator_placeholders}) AND funded_at IS NOT NULL",
                    creators,
                ):
                    wrap_births[row["creator"]] = row["funded_at"]

        walkback_rows: dict[str, Any] = {}
        if _table_exists(ops, "wt_walkback_queue"):
            for row in ops.execute(
                f"SELECT mint,funder_block_time,funding_mechanism,treasury FROM wt_walkback_queue WHERE mint IN ({placeholders})",
                mints,
            ):
                walkback_rows[row["mint"]] = row

        reviewed_family_tokens: set[str] = set()
        reviewed_family_tokens_all: set[str] = set()
        if _table_exists(ops, "wt_ops_v2_creators") and _table_exists(ops, "wt_ops_v2"):
            reviewed_family_tokens_all = {
                row[0]
                for row in ops.execute(
                    "SELECT DISTINCT c.token_mint FROM wt_ops_v2_creators c JOIN wt_ops_v2 o ON o.operation_uuid=c.operation_uuid WHERE o.op_type='WATCHTOWER' AND o.status IN ('CONFIRMED','ACTIVE')"
                )
            }

            reviewed_family_tokens = {
                row[0]
                for row in ops.execute(
                    f"SELECT DISTINCT c.token_mint FROM wt_ops_v2_creators c JOIN wt_ops_v2 o ON o.operation_uuid=c.operation_uuid WHERE c.token_mint IN ({placeholders}) AND o.op_type='WATCHTOWER' AND o.status IN ('CONFIRMED','ACTIVE')",
                    mints,
                )
            }

            source_row = ops.execute(
                "SELECT COUNT(DISTINCT c.token_mint) rows,MIN(c.migration_time) first_at,MAX(c.migration_time) last_at FROM wt_ops_v2_creators c JOIN wt_ops_v2 o ON o.operation_uuid=c.operation_uuid WHERE o.op_type='WATCHTOWER' AND o.status IN ('CONFIRMED','ACTIVE')"
            ).fetchone()
            watchtower_source_audit.append({
                "table": "wt_ops_v2_creators+wt_ops_v2",
                "assignment_field": "operation_uuid",
                "confidence_field": "wt_ops_v2.confidence",
                "rows": source_row["rows"],
                "first_at": source_row["first_at"],
                "last_at": source_row["last_at"],
                "used_by_discovery": True,
            })
    finally:
        ops.close()

    core_rows: dict[str, Any] = {}
    creator_first_transactions: dict[str, Any] = {}
    creator_first_seen: dict[str, Any] = {}

    canonical_tokens_all = (
        canonical_explicit_tokens_all | canonical_registry_tokens | confirmed_tokens_all | reviewed_family_tokens_all
    )

    canonical_create_times = dict(canonical_registry_all)

    core = sqlite3.connect(f"file:{core_db_path}?mode=ro", uri=True, timeout=5)
    core.row_factory = sqlite3.Row
    try:
        if _table_exists(core, "token_analysis"):
            for row in core.execute(
                f"SELECT mint,created_at,migrated_at FROM token_analysis WHERE mint IN ({placeholders})",
                mints,
            ):
                core_rows[row["mint"]] = row

            if canonical_tokens_all:
                canonical_placeholders = ",".join("?" for _ in canonical_tokens_all)
                for row in core.execute(
                    f"SELECT mint,created_at FROM token_analysis WHERE mint IN ({canonical_placeholders})",
                    list(canonical_tokens_all),
                ):
                    canonical_create_times[row["mint"]] = _normalise_unix_seconds(row["created_at"])

        creators = sorted({r.get("creator") for r in records.values() if r.get("creator")})
        if creators and _table_exists(core, "creator_tx_ledger"):
            creator_placeholders = ",".join("?" for _ in creators)
            for row in core.execute(
                f"SELECT creator_pubkey,MIN(blockTime) first_tx_at FROM creator_tx_ledger WHERE creator_pubkey IN ({creator_placeholders}) AND blockTime IS NOT NULL GROUP BY creator_pubkey",
                creators,
            ):
                creator_first_transactions[row["creator_pubkey"]] = row["first_tx_at"]

        if creators and _table_exists(core, "creator_watch"):
            creator_placeholders = ",".join("?" for _ in creators)
            for row in core.execute(
                f"SELECT creator_pubkey,first_seen_ts FROM creator_watch WHERE creator_pubkey IN ({creator_placeholders})",
                creators,
            ):
                creator_first_seen[row["creator_pubkey"]] = row["first_seen_ts"]
    finally:
        core.close()

    for mint, record in records.items():
        launch = launch_rows.get(mint)
        birth = birth_rows.get(mint)
        lifecycle = lifecycle_rows.get(mint)
        walkback = walkback_rows.get(mint)
        core_row = core_rows.get(mint)
        explicit_op = explicit_operations.get(mint)

        treasury = launch["treasury_wallet"] if launch else None

        if treasury:
            treasury_source = "wt_watchtower_launches.treasury_wallet"
        elif walkback_rows.get(mint) and walkback_rows[mint]["treasury"]:
            treasury = walkback_rows[mint]["treasury"]
            treasury_source = "wt_walkback_queue.treasury"
        else:
            treasury_source = None

        is_watchtower = explicit_op == WATCHTOWER_OPERATOR_ID
        operation_source = "explicit_confirmed_operation" if is_watchtower else None

        if not is_watchtower and mint in canonical_registry_tokens:
            is_watchtower, operation_source = True, "canonical_watchtower_launch_registry"

        if not is_watchtower and mint in confirmed_tokens:
            is_watchtower, operation_source = True, "confirmed_registry_token"

        has_confirmed_path = treasury in confirmed_treasuries or bool(
            attributed_treasuries.get(mint, set()) & confirmed_treasuries
        )

        if not is_watchtower and has_confirmed_path:
            is_watchtower, operation_source = True, "confirmed_treasury_path"

        if not is_watchtower and mint in reviewed_family_tokens:
            is_watchtower, operation_source = True, "reviewed_watchtower_family"

        create_at = lifecycle["launched_at"] if lifecycle and lifecycle["launched_at"] is not None else None
        create_source = "wt_token_lifecycle.launched_at" if create_at is not None else None

        if create_at is None and birth and birth["launched_at"] is not None:
            create_at = birth["launched_at"]
            create_source = "wt_creator_birth_launch.launched_at"

        if create_at is None and launch:
            create_at = launch["create_time"]
            create_source = "wt_watchtower_launches.create_time"

        if create_at is None and core_row:
            create_at = core_row["created_at"]
            create_source = "token_analysis.created_at"

        creator = record.get("creator")

        proxy_candidates = [
            (lifecycle["funded_at"] if lifecycle else None, "wt_token_lifecycle.funded_at"),
            (birth["funded_at"] if birth else None, "wt_creator_birth_launch.funded_at"),
            (wrap_births.get(creator), "wt_wrap_close_candidates.funded_at"),
            (walkback["funder_block_time"] if walkback else None, "wt_walkback_queue.funder_block_time"),
        ]

        creator_birth_at, creator_birth_source, creator_birth_quality = select_creator_birth(
            creator_first_transactions.get(creator),
            creator_first_seen.get(creator),
            proxy_candidates,
            allow_funding_proxy=CREATOR_FUNDING_PROXY_ENABLED,
        )

        if (
            creator_birth_at is None
            and launch
            and launch["birth_to_launch_seconds"] is not None
            and create_at is not None
            and CREATOR_FUNDING_PROXY_ENABLED
        ):
            creator_birth_at = _normalise_unix_seconds(create_at) - int(launch["birth_to_launch_seconds"])
            creator_birth_source = "wt_watchtower_launches.birth_to_launch_seconds"
            creator_birth_quality = "DERIVED_FUNDING_PROXY"

        migration_at = lifecycle["migrated_at"] if lifecycle else None
        migration_source = "wt_token_lifecycle.migrated_at" if migration_at is not None else None

        if migration_at is None and core_row:
            migration_at = core_row["migrated_at"]
            migration_source = "token_analysis.migrated_at"

        if migration_at is None and launch and launch["create_to_migration_secs"] is not None and create_at is not None:
            migration_at = _normalise_unix_seconds(create_at) + int(launch["create_to_migration_secs"])
            migration_source = "wt_watchtower_launches.create_to_migration_secs"

        timing = classify_quick_birth_migration(creator_birth_at, create_at, migration_at)

        detection_source = launch["detection_source"] if launch else None
        creator_extraction_method = launch["creator_extraction_method"] if launch else None
        launch_confidence = launch["confidence"] if launch else None
        canonicalisation = classify_canonicalisation_source(
            creator_extraction_method, launch_confidence)
        live_detection_status = classify_live_detection_status(
            detection_source, creator_extraction_method, launch_confidence)

        record.update(timing)
        record.update({
            "detection_source": detection_source,
            "caught_live": detection_source in _LIVE_DETECTION_SOURCES,
            "creator_extraction_method": creator_extraction_method,
            "confidence": launch_confidence,
            "canonicalisation_source": canonicalisation["canonicalisation_source"],
            "canonicalisation_label": canonicalisation["canonicalisation_label"],
            "live_detection_status": live_detection_status["live_detection_status"],
            "live_detection_label": live_detection_status["live_detection_label"],
            "live_detection_tooltip": live_detection_status["live_detection_tooltip"],
            "operation_id": "WATCHTOWER" if is_watchtower else explicit_op,
            "operation_confidence": "CONFIRMED" if (is_watchtower or explicit_op) else None,
            "operation_source": operation_source or ("explicit_operation" if explicit_op else None),
            "is_watchtower": is_watchtower,
            "is_cascade_confirmed": mint in canonical_registry_tokens,
            "confirmation_completed_at": confirmation_completed_at.get(mint),
            "creator_birth_source": creator_birth_source,
            "creator_birth_quality": creator_birth_quality or "UNKNOWN",
            "create_source": create_source,
            "migration_source": migration_source,
            "treasury_wallet": treasury,
            "treasury_wallet_source": treasury_source,
            "cex_exchange_name": cex_exchange_name.get(mint),
        })

    known_inside = {
        mint
        for mint in canonical_tokens_all
        if (create_time := canonical_create_times.get(mint)) is not None and create_time >= now - 86400
    }

    displayed = {mint for mint, record in records.items() if record["is_watchtower"]}

    missing = sorted(known_inside - displayed)
    for mint in missing:
        _LOG.warning(
            "WATCHTOWER assignment mismatch missing_join mint=%s expected_source=wt_watchtower_launches", mint
        )

    return {
        "watchtower": {
            "canonical_source": "explicit canonical operator assignment, then canonical launch registry",
            "known_total": len(canonical_tokens_all),
            "inside_window": len(known_inside),
            "matched": len(displayed & known_inside),
            "displayed": len(displayed),
            "excluded": len(missing),
            "excluded_mints": missing,
            "sources": watchtower_source_audit,
            "non_durable_audit_sources": [
                {
                    "source": "X51/X53/X55 audit artifacts",
                    "used_by_discovery": False,
                    "reason": "Not persisted as an approved canonical assignment",
                }
            ],
        },
        "quick_birth": summarise_quick_birth_diagnostics(records),
    }


OUTCOME_GROUP_KNOWN_OPERATION = "KNOWN_OPERATION"
OUTCOME_GROUP_CEX_REACHED = "CEX_REACHED"
OUTCOME_GROUP_KNOWN_INFRASTRUCTURE = "KNOWN_INFRASTRUCTURE"
OUTCOME_GROUP_REPEAT_CREATOR = "REPEAT_CREATOR"
OUTCOME_GROUP_UNKNOWN_INFRASTRUCTURE = "UNKNOWN_INFRASTRUCTURE"
OUTCOME_GROUP_LINEAGE_GAP = "LINEAGE_GAP"
OUTCOME_GROUP_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
OUTCOME_GROUP_UNATTRIBUTED = "UNATTRIBUTED"

OUTCOME_GROUP_LABELS = {
    OUTCOME_GROUP_KNOWN_OPERATION: "Known Operation",
    OUTCOME_GROUP_CEX_REACHED: "CEX Reached",
    OUTCOME_GROUP_KNOWN_INFRASTRUCTURE: "Known Infrastructure",
    OUTCOME_GROUP_REPEAT_CREATOR: "Repeat Creator",
    OUTCOME_GROUP_UNKNOWN_INFRASTRUCTURE: "Unknown Infrastructure",
    OUTCOME_GROUP_LINEAGE_GAP: "Lineage Gap",
    OUTCOME_GROUP_INSUFFICIENT_EVIDENCE: "Insufficient Evidence",
    OUTCOME_GROUP_UNATTRIBUTED: "Unattributed",
}

# Ordered most-specific-first, matching investigation_pipeline.py's own
# priority discipline; each outcome_type maps to exactly one group.
#
# CANONICAL_OPERATOR_REACHED gets its OWN group (Known Operation), separate
# from KNOWN_BRIDGE_REACHED/KNOWN_RELAY_REACHED (Known Infrastructure) --
# reaching a reviewed bridge/relay/protocol boundary is not the same
# strength of result as fully resolving a launch to a confirmed, named
# operator entity. Collapsing the two under one label hid that difference;
# this is presentation-mapping only, outcome_type/pipeline priority/
# detection logic are unchanged.
_OUTCOME_TYPE_TO_GROUP = {
    "CANONICAL_OPERATOR_REACHED": OUTCOME_GROUP_KNOWN_OPERATION,
    "KNOWN_CEX_REACHED": OUTCOME_GROUP_CEX_REACHED,
    "KNOWN_BRIDGE_REACHED": OUTCOME_GROUP_KNOWN_INFRASTRUCTURE,
    "KNOWN_RELAY_REACHED": OUTCOME_GROUP_KNOWN_INFRASTRUCTURE,
    "KNOWN_MULTI_TOKEN_CREATOR": OUTCOME_GROUP_REPEAT_CREATOR,
    "UNKNOWN_INFRASTRUCTURE": OUTCOME_GROUP_UNKNOWN_INFRASTRUCTURE,
    "LINEAGE_GAP": OUTCOME_GROUP_LINEAGE_GAP,
    "AMBIGUOUS_BRANCH": OUTCOME_GROUP_LINEAGE_GAP,
    "MAX_DEPTH": OUTCOME_GROUP_LINEAGE_GAP,
    "INSUFFICIENT_EVIDENCE": OUTCOME_GROUP_INSUFFICIENT_EVIDENCE,
}

# Known Operation first -- the strongest attribution result -- forming a
# clean attribution ladder from strongest to weakest.
OUTCOME_GROUP_ORDER = (
    OUTCOME_GROUP_KNOWN_OPERATION,
    OUTCOME_GROUP_CEX_REACHED,
    OUTCOME_GROUP_KNOWN_INFRASTRUCTURE,
    OUTCOME_GROUP_REPEAT_CREATOR,
    OUTCOME_GROUP_UNKNOWN_INFRASTRUCTURE,
    OUTCOME_GROUP_LINEAGE_GAP,
    OUTCOME_GROUP_INSUFFICIENT_EVIDENCE,
    OUTCOME_GROUP_UNATTRIBUTED,
)


# X67.10 -- canonicalisation provenance (HOW a launch entered
# wt_watchtower_launches) is a genuinely separate axis from detection
# provenance (HOW WATCHTOWER first observed it), per X67.9's design
# decision: detection_source must never be repurposed to answer this
# question. This classifier reads ONLY creator_extraction_method/confidence
# -- never detection_source -- so it cannot reintroduce the conflation
# X67.9 rejected. See watchtower_registry_promotion.py (WALKBACK_RECOVERED/
# WALKBACK), ws_cascade_store.py (CLOSE_ACCOUNT_DESTINATION/STRICT default),
# and watchtower_backfill.py (BACKFILL) for the real, already-written values
# this mapping is built against.
_CANONICALISATION_LABELS = {
    "BACKFILL":            "Historical Backfill",
    "WALKBACK_CONFIRMATION": "Walkback Confirmed",
    "MANUAL_ATTESTATION":  "Manual Attestation",
    "LIVE_DETECTION":      "Live Detection",
    "UNKNOWN":             "Legacy / Unknown",
    "CONFLICT":            "Provenance Conflict",
}


def classify_canonicalisation_source(
    creator_extraction_method: str | None, confidence: str | None,
) -> dict[str, str]:
    """Deterministic mapping from the two EXISTING promotion-provenance
    fields to a single, presentation-ready canonicalisation_source/label
    pair. Never reads or derives from detection_source (X67.9's explicit
    constraint) -- detection and canonicalisation are independent axes and
    a row can legitimately combine any detection status with any
    canonicalisation source (e.g. detection_source=NULL with
    canonicalisation_source=LIVE_DETECTION cannot happen given today's
    writers, but this function does not assume that; it classifies
    canonicalisation purely on its own two inputs).

    Precedence (checked in this exact order, validated against production
    values in X67.10's own audit before being finalised):
      1. confidence == 'BACKFILL'                          -> BACKFILL
      2. creator_extraction_method == 'WALKBACK_RECOVERED'
         or confidence == 'WALKBACK'                        -> WALKBACK_CONFIRMATION
      3. (reserved for verified manual-attestation provenance --
         no real stored value maps here today; not implemented per the
         task's "only implement where supported by real stored values"
         constraint)
      4. creator_extraction_method == 'CLOSE_ACCOUNT_DESTINATION'
         or confidence == 'STRICT'                          -> LIVE_DETECTION
      5. both fields present but agree with none of the above -> CONFLICT
      6. both fields absent/unrecognised                     -> UNKNOWN
    """
    method = creator_extraction_method
    conf = confidence

    if conf == "BACKFILL":
        source = "BACKFILL"
    elif method == "WALKBACK_RECOVERED" or conf == "WALKBACK":
        source = "WALKBACK_CONFIRMATION"
    elif method == "CLOSE_ACCOUNT_DESTINATION" or conf == "STRICT":
        source = "LIVE_DETECTION"
    elif method is None and conf is None:
        source = "UNKNOWN"
    else:
        # A non-null combination that doesn't match any known-good pairing
        # (e.g. method/confidence disagree about which path produced this
        # row). Fail closed to CONFLICT rather than guess -- per the task's
        # explicit "do not silently choose a label" instruction.
        source = "CONFLICT"

    return {
        "canonicalisation_source": source,
        "canonicalisation_label": _CANONICALISATION_LABELS[source],
    }


# X67.11 -- presentation-only reframing of "Caught Live" into an explicit
# live-detection status that directly answers the operator's actual
# question ("was this canonical launch detected while WATCHTOWER was
# ARMED?") instead of requiring a dash to be mentally translated into "no
# evidence." Reads detection_source (X67.10's untouched detection axis)
# AND creator_extraction_method/confidence (X67.10's canonicalisation axis)
# together, but only to DISTINGUISH "no detection because walkback-
# recovered" from "no detection because this predates detection provenance
# entirely" (the 13 legacy CLOSE_ACCOUNT_DESTINATION+NULL rows from X67.10's
# audit) -- it does not write, persist, or alter either underlying field.
_LIVE_DETECTION_STATUS_LABELS = {
    "LIVE":            "Live",
    "DETECTED_LATE":   "Detected Later",
    "NOT_DETECTED":    "Not Detected",
    "LEGACY_UNKNOWN":  "Legacy",
    "CONFLICT":        "Conflict",
}

_LIVE_DETECTION_STATUS_TOOLTIPS = {
    "LIVE": "Observed by WATCHTOWER while ARMED.",
    "DETECTED_LATE": "Observed after launch through replay, retry, logs or reconciliation.",
    "NOT_DETECTED": (
        "This launch became Canonical WATCHTOWER through retrospective "
        "walkback confirmation. No evidence exists that WATCHTOWER "
        "detected it while ARMED."
    ),
    "LEGACY_UNKNOWN": (
        "This launch predates detection provenance. It is unknown "
        "whether WATCHTOWER detected it live."
    ),
    "CONFLICT": "detection_source, creator_extraction_method and confidence disagree -- needs investigation.",
}

_DETECTED_LATE_SOURCES = (
    "PROGRAM_LOGS", "PENDING_CREATE_RETRY", "PROGRAM_REPLAY_BUFFER",
    "OPENING_CATCHUP", "EXPIRE_PROBE", "CANDIDATE_CATCHUP",
    "MANUAL_USER_ATTESTATION",
)

# The exact 13-row legacy population X67.10's audit identified: rows that
# predate detection_source's introduction entirely. Distinguished from
# NOT_DETECTED (a walkback-recovered row with no detection evidence) by
# creator_extraction_method, since both cases share detection_source IS NULL.
_LEGACY_CANONICALISATION_METHOD = "CLOSE_ACCOUNT_DESTINATION"


def classify_live_detection_status(
    detection_source: str | None,
    creator_extraction_method: str | None,
    confidence: str | None,
) -> dict[str, str]:
    """UI-only classification answering "was this canonical launch detected
    while WATCHTOWER was ARMED?" directly, instead of leaving a bare dash
    for detection_source IS NULL. Never persisted; never derives
    canonicalisation_source or detection_source from this result (the
    reverse of X67.9/X67.10's rule, preserved here: this reads both axes
    but writes neither).
    """
    if detection_source in _LIVE_DETECTION_SOURCES:
        status = "LIVE"
    elif detection_source in _DETECTED_LATE_SOURCES:
        status = "DETECTED_LATE"
    elif detection_source is None:
        if creator_extraction_method == _LEGACY_CANONICALISATION_METHOD:
            status = "LEGACY_UNKNOWN"
        elif creator_extraction_method == "WALKBACK_RECOVERED" or confidence == "WALKBACK":
            status = "NOT_DETECTED"
        else:
            status = "LEGACY_UNKNOWN"
    else:
        # A non-null detection_source that matches neither known list --
        # fail closed to CONFLICT rather than guess.
        status = "CONFLICT"

    return {
        "live_detection_status": status,
        "live_detection_label": _LIVE_DETECTION_STATUS_LABELS[status],
        "live_detection_tooltip": _LIVE_DETECTION_STATUS_TOOLTIPS[status],
    }


def outcome_group_for(outcome_type: str | None) -> str:
    """Maps a raw wt_attribution_outcomes.outcome_type onto one presentation
    group. A mint with no attribution outcome at all (e.g. never reached by
    the attribution pipeline) maps to UNATTRIBUTED, not silently dropped."""
    if not outcome_type:
        return OUTCOME_GROUP_UNATTRIBUTED
    return _OUTCOME_TYPE_TO_GROUP.get(outcome_type, OUTCOME_GROUP_UNATTRIBUTED)


def _outcome_types_by_mint(ops_db_path: str, mints: list[str]) -> dict[str, str]:
    """Read-only lookup of wt_attribution_outcomes.outcome_type for exactly
    the given mints -- no new detection, just fetching an already-persisted
    fact. Uses the most recently completed outcome per mint if more than one
    row exists (rare; matches the same recency-preference convention used
    elsewhere in this codebase)."""
    if not mints:
        return {}
    conn = sqlite3.connect(f"file:{ops_db_path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    try:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wt_attribution_outcomes'"
        ).fetchone():
            return {}
        placeholders = ",".join("?" for _ in mints)
        rows = conn.execute(
            f"SELECT mint, outcome_type FROM wt_attribution_outcomes "
            f"WHERE mint IN ({placeholders}) "
            f"ORDER BY completed_at DESC",
            mints,
        ).fetchall()
    finally:
        conn.close()
    result: dict[str, str] = {}
    for r in rows:
        result.setdefault(r["mint"], r["outcome_type"])  # first (most recent) wins
    return result


def group_mints_by_outcome(ops_db_path: str, mints: list[str]) -> dict[str, Any]:
    """Groups an already-filtered mint list (e.g. the result of query()) by
    attribution-outcome presentation group. Pure presentation grouping over
    an existing, already-computed classification -- no new detection.
    Returns {"groups": [{"group":..., "label":..., "count":..., "mints":[...]}, ...]}
    ordered by OUTCOME_GROUP_ORDER, omitting empty groups."""
    outcome_by_mint = _outcome_types_by_mint(ops_db_path, mints)
    buckets: dict[str, list[str]] = {g: [] for g in OUTCOME_GROUP_ORDER}
    for mint in mints:
        group = outcome_group_for(outcome_by_mint.get(mint))
        buckets[group].append(mint)
    return {
        "groups": [
            {"group": g, "label": OUTCOME_GROUP_LABELS[g], "count": len(buckets[g]), "mints": buckets[g]}
            for g in OUTCOME_GROUP_ORDER
            if buckets[g]
        ],
    }


def build_operational_intelligence(
    ops_db_path: str,
    core_db_path: str,
    *,
    window_seconds: int = 86400,
    now: int | None = None,
) -> dict[str, Any]:
    """Runs all three classifiers and combines them into the canonical
    per-mint record. Read-only, zero writes -- this function does not
    persist anything; callers decide whether/how to cache the result.

    Returns:
      {
        "generated_at": ..., "window_seconds": ...,
        "total_launches": N,
        "topology_summary": [...], "behaviour_summary": [...], "mechanism_summary": [...],
        "records": {mint: {"topology": ..., "behaviours": [...], "mechanisms": [...]}},
      }
    """
    now = int(now or time.time())

    _log = logging.getLogger(__name__)
    _stage_start = time.perf_counter()
    _t0 = _stage_start

    def _mark(stage: str) -> None:
        nonlocal _t0
        now_t = time.perf_counter()
        _log.debug(
            "operational_intelligence stage=%s elapsed_ms=%.1f cumulative_ms=%.1f",
            stage, (now_t - _t0) * 1000, (now_t - _stage_start) * 1000,
        )
        _t0 = now_t

    topology = build_topology_classification(ops_db_path, core_db_path, window_seconds=window_seconds, now=now)
    _mark("topology_classification")
    behaviour = build_behaviour_classification(ops_db_path, core_db_path, window_seconds=window_seconds, now=now)
    _mark("behaviour_classification")
    mechanism = build_mechanism_classification(ops_db_path, window_seconds=window_seconds, now=now)
    _mark("mechanism_classification")

    windowed_mints = set(topology["assignments"])

    # X67.37 — three distinct populations were previously collapsed into one
    # unconditional union (X61): (1) windowed launches from the topology/
    # behaviour/mechanism classifiers' own Stage-1 population
    # (wt_attribution_outcomes, filtered by each mint's resolved create_time
    # >= now-window_seconds); (2) canonical registry launches that DO have
    # attribution-pipeline evidence (already inside (1) for a wide-enough
    # window); (3) canonical registry launches that exist ONLY in
    # wt_watchtower_launches with no corresponding wt_attribution_outcomes
    # row at all (X65.40/X65.44: 21 of 163 registry mints, predating or
    # having bypassed the attribution pipeline) -- these can NEVER enter (1)
    # under any window_seconds value, since they have no Stage-1 evidence
    # to be time-filtered from in the first place.
    #
    # X61's bug was using (2)+(3) [the FULL registry] to widen EVERY window,
    # so a 24h request's `records` silently included every historical
    # canonical launch (X67.36: 907 rows instead of the true ~760; 164
    # is_watchtower=True rows instead of the Canonical panel's own
    # correctly-windowed 20).
    #
    # Fix: population source is now explicit per request shape, not a
    # blanket exception --
    #   finite window (24h/7d/30d): population = windowed launches ONLY.
    #     Registry membership never adds a mint; it only ANNOTATES mints
    #     already present (is_watchtower/is_cascade_confirmed, computed
    #     separately and unchanged inside _enrich_discovery_records below).
    #   all-time window: population = windowed launches UNION the full
    #     registry. "All" means "everything we know about" -- which
    #     includes the 21 registry-only launches with no attribution-outcome
    #     row, since there is no other window under which they could ever
    #     appear. This is the ONE place a union is architecturally correct,
    #     and it is now written as an explicit population-source branch
    #     rather than a silent widening applied to every request.
    is_all_time_request = window_seconds >= _WINDOW_ALL_SECONDS

    canonical_registry_mints: set[str] = set()
    canonical_registry_creators: dict[str, str] = {}
    ops_conn_for_registry = sqlite3.connect(f"file:{ops_db_path}?mode=ro", uri=True, timeout=5)
    ops_conn_for_registry.row_factory = sqlite3.Row
    try:
        if ops_conn_for_registry.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wt_watchtower_launches'"
        ).fetchone():
            for row in ops_conn_for_registry.execute(
                "SELECT mint, creator_wallet FROM wt_watchtower_launches WHERE mint IS NOT NULL"
            ):
                if is_all_time_request:
                    canonical_registry_mints.add(row["mint"])
                if row["creator_wallet"]:
                    canonical_registry_creators[row["mint"]] = row["creator_wallet"]
    finally:
        ops_conn_for_registry.close()
    _mark("registry_lookup")

    all_mints = (windowed_mints | canonical_registry_mints) if is_all_time_request else windowed_mints

    records: dict[str, dict[str, Any]] = {}
    for mint in all_mints:
        t = topology["assignments"].get(mint)
        b = behaviour["assignments"].get(mint)
        m = mechanism["assignments"].get(mint)

        creator = b.get("creator") if b else None
        if creator is None:
            creator = canonical_registry_creators.get(mint)

        records[mint] = {
            "topology": t["topology"] if t else "UNKNOWN",
            "topology_derived_from": t.get("derived_from") if t else None,
            "behaviours": b["behaviours"] if b else [],
            "mechanisms": m["mechanisms"] if m else [],
            "creator": creator,
        }

    diagnostics = _enrich_discovery_records(ops_db_path, core_db_path, records, now=now)
    _mark("enrich_discovery_records")

    from src.ops.operational_behaviour_tags import canonical_behaviour_for

    for r in records.values():
        r["canonical_behaviour"] = canonical_behaviour_for(
            r["behaviours"], is_quick_birth_migration=r.get("is_quick_birth_migration", False)
        )
    _mark("canonical_behaviour")

    from src.ops.creator_identity import enrich_creator_identity

    creator_identity_summary = enrich_creator_identity(core_db_path, records)
    _mark("creator_identity")

    from src.ops.campaign_classification import build_campaign_classification

    campaign_result = build_campaign_classification(ops_db_path, records)
    _mark("campaign_classification")

    for mint, r in records.items():
        assignment = campaign_result["assignments"].get(mint, {})
        r["campaign"] = assignment.get("campaign")
        r["campaign_confidence"] = assignment.get("confidence")
        r["campaign_evidence"] = assignment.get("evidence")

        _ev_subprov = (r.get("campaign_evidence") or {}).get("subprov_wallet")
        r["is_known_exchange_boundary"] = bool(_ev_subprov) and is_known_account(_ev_subprov)

    watchtower_count = sum(r["is_watchtower"] for r in records.values())
    quick_count = sum(r["is_quick_birth_migration"] for r in records.values())
    overlap_count = sum(r["is_quick_birth_migration"] for r in records.values() if r["is_watchtower"])
    assigned_quick_count = sum(
        bool(r["operation_id"]) for r in records.values() if r["is_quick_birth_migration"]
    )

    from src.ops.operational_behaviour_tags import CANONICAL_BEHAVIOUR_ORDER, BEHAVIOUR_LABELS

    total_for_canonical = len(records)
    canonical_counts = {c: 0 for c in CANONICAL_BEHAVIOUR_ORDER}
    for r in records.values():
        canonical_counts[r["canonical_behaviour"]] += 1

    canonical_behaviour_summary = [
        {
            "behaviour": c,
            "label": BEHAVIOUR_LABELS[c],
            "count": canonical_counts[c],
            "coverage_pct": round(canonical_counts[c] / total_for_canonical * 100, 1) if total_for_canonical else 0.0,
        }
        for c in CANONICAL_BEHAVIOUR_ORDER
    ]
    _mark("canonical_behaviour_summary")

    _log.debug(
        "operational_intelligence stage=%s elapsed_ms=%.1f cumulative_ms=%.1f",
        "TOTAL", (time.perf_counter() - _stage_start) * 1000, (time.perf_counter() - _stage_start) * 1000,
    )

    return {
        "generated_at": now,
        "window_seconds": window_seconds,
        "total_launches": topology["total_launches"],
        "conserved": topology["conserved"],
        "topology_summary": topology["topologies"],
        "behaviour_summary": behaviour["behaviours"],
        "canonical_behaviour_summary": canonical_behaviour_summary,
        "canonical_behaviour_conserved": sum(canonical_counts.values()) == total_for_canonical,
        "campaign_summary": campaign_result["campaign_summary"],
        "campaign_conserved": campaign_result["campaign_conserved"],
        "mechanism_summary": mechanism["mechanisms"],
        "creator_identity_summary": creator_identity_summary["identities"],
        "disposable_creator_score_distribution": creator_identity_summary["score_distribution"],
        "operation_summary": {"watchtower": watchtower_count},
        "quick_birth_migration_summary": {
            "count": quick_count,
            "watchtower_overlap_count": overlap_count,
            "assigned_operation_count": assigned_quick_count,
            "coverage_pct": round(assigned_quick_count / quick_count * 100, 1) if quick_count else 0.0,
            "missing_timing_evidence_count": sum(
                r["quick_birth_reason"] in {"MISSING_CREATOR_BIRTH", "MISSING_CREATE", "MISSING_MIGRATION", "UNKNOWN"}
                for r in records.values()
            ),
        },
        "diagnostics": diagnostics,
        "records": records,
    }


def build_hierarchy(intelligence: dict[str, Any]) -> dict[str, Any]:
    """Computes the Topology -> Behaviour -> Mechanism drill-down VIEW on
    demand from the flat per-mint `records` map. Nothing here is stored --
    call this fresh from the flat map whenever the UI needs the tree; it is
    always reproducible from `records` alone, per the brief's storage-model
    requirement.

    A mint with zero behaviour tags is grouped under a "(none)" bucket at
    the behaviour level, so counts still conserve; same for zero mechanism
    tags. A mint with >1 behaviour or mechanism tag appears under EACH of
    its tags at that level -- this is the additive property surfacing
    correctly in the tree (a launch with both Rapid Birth and Burst Launcher
    contributes to both branches), which means node counts one level down
    from Topology can sum to MORE than the topology's own total. This is
    expected, not a conservation bug -- Topology itself is still exclusive
    (each mint appears under exactly one top-level node).
    """
    records = intelligence["records"]
    tree: dict[str, Any] = {}

    for topology in TOPOLOGY_ORDER:
        mints_here = [m for m, r in records.items() if r["topology"] == topology]
        node = {
            "topology": topology,
            "label": TOPOLOGY_LABELS[topology],
            "count": len(mints_here),
            "children": [],
        }
        behaviour_buckets: dict[str, list[str]] = {b: [] for b in BEHAVIOUR_ORDER}
        behaviour_buckets["_NONE_"] = []
        for m in mints_here:
            tags = records[m]["behaviours"]
            if not tags:
                behaviour_buckets["_NONE_"].append(m)
            else:
                for t in tags:
                    behaviour_buckets.setdefault(t, []).append(m)

        # Any behaviour tag not in the canonical BEHAVIOUR_ORDER still needs
        # a branch in the tree (additive tags are never silently dropped).
        extra_behaviours = sorted(b for b in behaviour_buckets if b not in BEHAVIOUR_ORDER and b != "_NONE_")

        for behaviour in list(BEHAVIOUR_ORDER) + extra_behaviours:
            b_mints = behaviour_buckets[behaviour]
            if not b_mints:
                continue
            b_node = {
                "behaviour": behaviour,
                "label": BEHAVIOUR_LABELS.get(behaviour, behaviour.replace("_", " ").title()),
                "count": len(b_mints),
                "children": [],
            }
            mechanism_buckets: dict[str, list[str]] = {mech: [] for mech in MECHANISM_ORDER}
            mechanism_buckets["_NONE_"] = []
            for m in b_mints:
                mechs = records[m]["mechanisms"]
                if not mechs:
                    mechanism_buckets["_NONE_"].append(m)
                else:
                    for mech in mechs:
                        mechanism_buckets[mech].append(m)
            for mechanism in MECHANISM_ORDER:
                mech_mints = mechanism_buckets[mechanism]
                if not mech_mints:
                    continue
                b_node["children"].append({
                    "mechanism": mechanism,
                    "label": MECHANISM_LABELS[mechanism],
                    "count": len(mech_mints),
                })
            if mechanism_buckets["_NONE_"]:
                b_node["children"].append({
                    "mechanism": None,
                    "label": "(no mechanism evidence)",
                    "count": len(mechanism_buckets["_NONE_"]),
                })
            node["children"].append(b_node)

        if behaviour_buckets["_NONE_"]:
            none_mints = behaviour_buckets["_NONE_"]
            none_node = {"behaviour": None, "label": "(no behaviour tags)", "count": len(none_mints), "children": []}
            mechanism_buckets2: dict[str, list[str]] = {mech: [] for mech in MECHANISM_ORDER}
            mechanism_buckets2["_NONE_"] = []
            for m in none_mints:
                mechs = records[m]["mechanisms"]
                if not mechs:
                    mechanism_buckets2["_NONE_"].append(m)
                else:
                    for mech in mechs:
                        mechanism_buckets2[mech].append(m)
            for mechanism in MECHANISM_ORDER:
                mech_mints = mechanism_buckets2[mechanism]
                if mech_mints:
                    none_node["children"].append({"mechanism": mechanism, "label": MECHANISM_LABELS[mechanism], "count": len(mech_mints)})
            if mechanism_buckets2["_NONE_"]:
                none_node["children"].append({"mechanism": None, "label": "(no mechanism evidence)", "count": len(mechanism_buckets2["_NONE_"])})
            node["children"].append(none_node)

        tree[topology] = node

    return {"generated_at": intelligence["generated_at"], "tree": [tree[t] for t in TOPOLOGY_ORDER]}


def query(
    intelligence: dict[str, Any],
    *,
    topology: str | None = None,
    behaviour: str | None = None,
    canonical_behaviour: str | None = None,
    creator_identity: str | None = None,
    campaign: str | None = None,
    mechanism: str | None = None,
    operation: str | None = None,
    quick_birth_migration: bool = False,
    is_cascade_confirmed: bool | None = None,
) -> list[str]:
    """Cross-dimensional query over the flat records map -- the brief's
    explicit requirement that "no hierarchy should prevent cross-dimensional
    searching." Any combination of filters may be supplied; omitted filters
    are unconstrained. Examples this directly supports:
      query(intel, topology="FAN_OUT")                     -- all Fan-Out
      query(intel, behaviour="RAPID_BIRTH_LAUNCH")          -- every Rapid Birth launch, any topology
      query(intel, mechanism="WSOL_WRAP_CLOSE")             -- every Wrap-Close launch
      query(intel, topology="FAN_OUT", mechanism="PLAIN_TRANSFER")  -- Fan-Out using Plain Transfer
      query(intel, topology="MESH", behaviour="BURST_LAUNCH")       -- Mesh + Burst Launcher
    """
    out = []
    for mint, r in intelligence["records"].items():
        if topology is not None and r["topology"] != topology:
            continue
        if behaviour is not None and behaviour not in r["behaviours"]:
            continue
        if canonical_behaviour is not None and r.get("canonical_behaviour") != canonical_behaviour:
            continue
        if creator_identity is not None and r.get("creator_identity") != creator_identity:
            continue
        if campaign is not None and r.get("campaign") != campaign:
            continue
        if mechanism is not None and mechanism not in r["mechanisms"]:
            continue
        if operation == "WATCHTOWER" and not r.get("is_watchtower"):
            continue
        if operation and operation != "WATCHTOWER" and r.get("operation_id") != operation:
            continue
        if quick_birth_migration and not r.get("is_quick_birth_migration"):
            continue
        if is_cascade_confirmed is not None and bool(r.get("is_cascade_confirmed")) != is_cascade_confirmed:
            continue
        out.append(mint)
    return out
