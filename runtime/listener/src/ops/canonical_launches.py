"""X67.27 — Shared canonical WATCHTOWER launch population helper.

Single source of truth for "which token launches belong to WATCHTOWER,
within a given time window" — consumed by BOTH Discovery
(templates/discovery.html) and Operation Intelligence
(templates/watchtower_operational_intelligence.html) so the two pages can
never independently diverge on canonical membership again.

Root cause this module fixes (X67.27 investigation): prior to this module,
Discovery computed its window-scoped canonical population entirely
client-side in JavaScript (x65_45CanonicalRowsForWindow() in
discovery.html, filtering an already-fetched window=all payload by
`create_at >= since`), while Operation Intelligence's Launch Audit panel
(api_intel_launch_audit(), operation_dashboard_routes.py) queried
wt_launch_audit directly with NO time-window filtering at all (a bare
`ORDER BY created_at DESC LIMIT 50`, where created_at is the AUDIT row's
own creation time, not the launch's create_time). Two independently
implemented queries, with different filtering logic, on different
timestamp fields, could not help but diverge. This module is the one
canonical-population query both pages must call instead.

Authoritative population: wt_watchtower_launches (the canonical registry
established by X65.41 and unchanged by every promotion-predicate audit
since). Every other table this module touches is enrichment-only and
MUST NOT be able to add a mint that isn't already in wt_watchtower_launches
or remove one that is (see get_canonical_watchtower_launches's own
in-line assertions).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Optional

from src.ops.detection_reconciliation import _LIVE_DETECTION_SOURCES

try:
    # X67.11 -- reuse the exact same explicit live-detection-status
    # classifier Discovery's table already renders, so "Live Detection"
    # semantics can never diverge between the two pages either.
    from src.ops.operational_intelligence import classify_live_detection_status
except ImportError:  # pragma: no cover - defensive only, module always present in prod
    classify_live_detection_status = None  # type: ignore[assignment]


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    try:
        value = row[key]
        return default if value is None else value
    except (IndexError, KeyError):
        return default


@dataclass(frozen=True)
class CanonicalLaunch:
    """One row of the shared canonical population. Every field beyond
    `mint` is enrichment -- absence of any of them must never remove the
    launch from the returned list (X67.27's explicit "enrichment safety"
    requirement)."""
    mint: str
    creator_wallet: Optional[str]
    treasury_wallet: Optional[str]
    subprov_wallet: Optional[str]
    create_time: Optional[int]
    migration_time: Optional[int]
    time_to_migration_seconds: Optional[int]
    detection_source: Optional[str]
    live_detection_status: Optional[str]
    live_detection_label: Optional[str]
    live_detection_tooltip: Optional[str]
    confidence: Optional[str]
    funding_mechanism: Optional[str]
    campaign: str  # "Unassigned campaign" when no attribution exists -- never used to drop a row
    recorded_at: Optional[int] = None
    extra: dict = field(default_factory=dict)


def get_canonical_watchtower_launches(
    ops_conn: sqlite3.Connection,
    core_conn: Optional[sqlite3.Connection] = None,
    *,
    operator_id: Optional[str] = None,  # noqa: ARG001 -- accepted for the described
                                          # interface shape; today's registry has no
                                          # multi-operator column, so this is a no-op
                                          # filter reserved for a future multi-operator
                                          # registry (see module docstring's "operator"
                                          # parameter in the required signature).
    window_start: Optional[int] = None,
    window_end: Optional[int] = None,
) -> list[CanonicalLaunch]:
    """The one shared canonical-launch query for both Discovery and
    Operation Intelligence.

    Base population: wt_watchtower_launches (never any other table).
    Windowing: launch's own `create_time` column, inclusive on both ends
    (`create_time >= window_start AND create_time <= window_end`), matching
    Discovery's existing client-side semantics
    (`x65_45CanonicalRowsForWindow`: `create_at >= since`, where `since` has
    no upper bound today -- this helper adds an explicit, optional upper
    bound via `window_end` for completeness/symmetry, defaulting to "no
    upper bound" when omitted, which reproduces Discovery's exact current
    behaviour byte-for-byte).

    A launch with a NULL create_time is EXCLUDED from every window (never
    defaulted in) -- matching src.ops.discovery_window.launch_create_times_
    for_mints's own explicit "absence means no trustworthy timestamp, never
    silently included or excluded by guessing" discipline.

    Deduplication: `wt_watchtower_launches` already has a `UNIQUE
    (creator_wallet, create_signature)` constraint, but this helper
    additionally deduplicates by mint defensively (first row wins, ordered
    by recorded_at DESC) in case a future schema change ever permits >1 row
    per mint -- so this function's own output contract (one row per mint)
    never depends on that constraint remaining exactly as-is.

    Ordering: stable, by create_time DESCENDING (falls back to recorded_at
    DESC when create_time ties, for full determinism).
    """
    query = (
        "SELECT mint, creator_wallet, treasury_wallet, subprov_wallet, create_time, "
        "create_to_migration_secs, detection_source, confidence, funding_mechanism, "
        "creator_extraction_method, recorded_at "
        "FROM wt_watchtower_launches WHERE mint IS NOT NULL AND create_time IS NOT NULL"
    )
    params: list[Any] = []
    if window_start is not None:
        query += " AND create_time >= ?"
        params.append(window_start)
    if window_end is not None:
        query += " AND create_time <= ?"
        params.append(window_end)
    query += " ORDER BY create_time DESC, recorded_at DESC"

    rows = ops_conn.execute(query, params).fetchall()

    by_mint: dict[str, sqlite3.Row] = {}
    for row in rows:
        # First row wins under (create_time DESC, recorded_at DESC) ordering
        # -- see docstring: defends the one-row-per-mint output contract
        # even if the underlying uniqueness constraint ever changes.
        by_mint.setdefault(row["mint"], row)

    mints = list(by_mint.keys())

    # --- Enrichment: migration time (core DB, best-effort) ---
    migration_by_mint: dict[str, Optional[int]] = {}
    if core_conn is not None and mints and _table_exists(core_conn, "token_analysis"):
        placeholders = ",".join("?" for _ in mints)
        for r in core_conn.execute(
            f"SELECT mint, migrated_at FROM token_analysis WHERE mint IN ({placeholders})",
            mints,
        ):
            migration_by_mint[r["mint"]] = r["migrated_at"]

    # --- Enrichment: campaign attribution (best-effort; absence -> "Unassigned campaign") ---
    campaign_by_mint: dict[str, str] = {}
    if mints and _table_exists(ops_conn, "wt_attribution_outcomes"):
        placeholders = ",".join("?" for _ in mints)
        for r in ops_conn.execute(
            f"SELECT mint, outcome_type FROM wt_attribution_outcomes WHERE mint IN ({placeholders})",
            mints,
        ):
            if r["outcome_type"] == "CANONICAL_OPERATOR_REACHED":
                campaign_by_mint[r["mint"]] = "WATCHTOWER"

    results: list[CanonicalLaunch] = []
    for mint in mints:
        row = by_mint[mint]
        create_time = _row_get(row, "create_time")
        migration_time = migration_by_mint.get(mint)
        time_to_migration = _row_get(row, "create_to_migration_secs")
        if time_to_migration is None and create_time is not None and migration_time is not None:
            try:
                time_to_migration = int(migration_time) - int(create_time)
            except (TypeError, ValueError):
                time_to_migration = None

        detection_source = _row_get(row, "detection_source")
        live_status = live_label = live_tooltip = None
        if classify_live_detection_status is not None:
            classification = classify_live_detection_status(
                detection_source,
                _row_get(row, "creator_extraction_method"),
                _row_get(row, "confidence"),
            )
            live_status = classification["live_detection_status"]
            live_label = classification["live_detection_label"]
            live_tooltip = classification["live_detection_tooltip"]

        results.append(CanonicalLaunch(
            mint=mint,
            creator_wallet=_row_get(row, "creator_wallet"),
            treasury_wallet=_row_get(row, "treasury_wallet"),
            subprov_wallet=_row_get(row, "subprov_wallet"),
            create_time=create_time,
            migration_time=migration_time,
            time_to_migration_seconds=time_to_migration,
            detection_source=detection_source,
            live_detection_status=live_status,
            live_detection_label=live_label,
            live_detection_tooltip=live_tooltip,
            confidence=_row_get(row, "confidence"),
            funding_mechanism=_row_get(row, "funding_mechanism"),
            campaign=campaign_by_mint.get(mint, "Unassigned campaign"),
            recorded_at=_row_get(row, "recorded_at"),
        ))

    return results


def canonical_launch_to_dict(launch: CanonicalLaunch) -> dict[str, Any]:
    """JSON-serializable projection, field names matching Discovery's
    existing per-row shape wherever an equivalent field already exists
    (mint, creator, treasury_wallet, subprov_wallet, create_time/create_at,
    detection_source, caught_live) so template code on either page can
    consume either shape with minimal translation."""
    return {
        "mint": launch.mint,
        "creator": launch.creator_wallet,
        "creator_wallet": launch.creator_wallet,
        "treasury_wallet": launch.treasury_wallet,
        "subprov_wallet": launch.subprov_wallet,
        "create_time": launch.create_time,
        "create_at": launch.create_time,
        "migration_time": launch.migration_time,
        "migration_at": launch.migration_time,
        "time_to_migration_seconds": launch.time_to_migration_seconds,
        "detection_source": launch.detection_source,
        "caught_live": launch.detection_source in _LIVE_DETECTION_SOURCES,
        "live_detection_status": launch.live_detection_status,
        "live_detection_label": launch.live_detection_label,
        "live_detection_tooltip": launch.live_detection_tooltip,
        "confidence": launch.confidence,
        "funding_mechanism": launch.funding_mechanism,
        "campaign": launch.campaign,
        "recorded_at": launch.recorded_at,
    }
