"""
Entity Intelligence — operation adapters.

Each adapter reads from one operation's data sources and produces:
  - list[OperationObservation]
  - list[TimelineEvent]

Adapters are the ONLY code that knows about specific operations.
The service and the model are operation-agnostic.

Rules:
  - No RPC.
  - No writes.
  - If a DB is missing or a query fails, return empty lists (never raise).
  - No operation should know this module exists.
"""

from __future__ import annotations

import os
import sqlite3
from typing import NamedTuple

from src.intelligence.models import (
    ENTITY_TYPE_TREASURY,
    ENTITY_TYPE_SUB_PROVISIONER,
    ENTITY_TYPE_OPERATOR,
    ENTITY_TYPE_UNKNOWN,
    OperationObservation,
    TimelineEvent,
)

_REPO = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
OPS_DB_PATH = os.environ.get(
    "OPS_V2_DB_PATH",
    os.path.join(_REPO, "database", "wt_ops_v2.db"),
)

PERSISTENCE_MIN_LAUNCHES = 3  # mirrors Launcher Observatory constant


def _ro_conn(path: str) -> sqlite3.Connection:
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


# ── Return type ────────────────────────────────────────────────────────────────

class AdapterResult(NamedTuple):
    observations: list[OperationObservation]
    timeline:     list[TimelineEvent]
    entity_type:  str   # best-guess from this adapter, UNKNOWN if no signal


# ── WATCHTOWER adapter ─────────────────────────────────────────────────────────

def _watchtower_adapter(entity_id: str) -> AdapterResult:
    """
    Checks wt_confirmed_treasuries and wt_discovered_subprovs.
    Does NOT modify WATCHTOWER data.
    """
    observations: list[OperationObservation] = []
    timeline:     list[TimelineEvent]        = []
    entity_type                              = ENTITY_TYPE_UNKNOWN

    try:
        conn = _ro_conn(OPS_DB_PATH)
        try:
            # ── Treasury? ──────────────────────────────────────────────────────
            row = conn.execute(
                "SELECT * FROM wt_confirmed_treasuries WHERE treasury = ?",
                (entity_id,),
            ).fetchone()

            if row:
                entity_type = ENTITY_TYPE_TREASURY
                facts = {
                    "confidence":    row["confidence"],
                    "method":        row["method"],
                    "out_sol":       row["out_sol"],
                    "recipients":    row["recipients"],
                    "micro_pings":   row["micro_pings"],
                    "provenance":    row["provenance"],
                    "no_subscribe":  bool(row["no_subscribe"]),
                }
                confirmed_at = row["confirmed_at"]
                observations.append(OperationObservation(
                    operation_id = "watchtower",
                    display_name = "WATCHTOWER",
                    role         = "TREASURY",
                    facts        = facts,
                    first_seen   = confirmed_at,
                    last_seen    = confirmed_at,
                    provenance   = "wt_confirmed_treasuries",
                ))
                if confirmed_at:
                    timeline.append(TimelineEvent(
                        ts          = confirmed_at,
                        event_type  = "TREASURY_CONFIRMED",
                        description = (
                            f"Confirmed as WATCHTOWER treasury "
                            f"({row['confidence']} confidence, "
                            f"method={row['method']}, "
                            f"{row['recipients']} recipients, "
                            f"{row['out_sol']} SOL out)"
                        ),
                        source      = "WATCHTOWER",
                        provenance  = "wt_confirmed_treasuries.confirmed_at",
                        metadata    = {
                            "method":     row["method"],
                            "confidence": row["confidence"],
                            "out_sol":    row["out_sol"],
                        },
                    ))

            # ── Sub-provisioner? ───────────────────────────────────────────────
            row = conn.execute(
                "SELECT * FROM wt_discovered_subprovs WHERE subprov = ?",
                (entity_id,),
            ).fetchone()

            if row:
                # Can be both treasury AND subprov in the mesh model
                if entity_type == ENTITY_TYPE_UNKNOWN:
                    entity_type = ENTITY_TYPE_SUB_PROVISIONER
                sp_facts = {
                    "state":             row["state"],
                    "confidence":        row["confidence"],
                    "subprov_type":      row["subprov_type"],
                    "creator_count":     row["creator_count"],
                    "wrap_close_count":  row["wrap_close_count"],
                    "funding_mechanism": row["funding_mechanism"],
                    "treasury":          row["treasury"],
                    "treasury_known":    bool(row["treasury_known"]),
                }
                first_seen = row["first_seen"]
                last_seen  = row["last_seen"]
                observations.append(OperationObservation(
                    operation_id = "watchtower",
                    display_name = "WATCHTOWER",
                    role         = "SUB_PROVISIONER",
                    facts        = sp_facts,
                    first_seen   = first_seen,
                    last_seen    = last_seen,
                    provenance   = "wt_discovered_subprovs",
                ))
                if first_seen:
                    timeline.append(TimelineEvent(
                        ts          = first_seen,
                        event_type  = "SUBPROV_DISCOVERED",
                        description = (
                            f"Discovered as WATCHTOWER sub-provisioner "
                            f"(state={row['state']}, "
                            f"{row['creator_count']} creators seeded)"
                        ),
                        source      = "WATCHTOWER",
                        provenance  = "wt_discovered_subprovs.first_seen",
                        metadata    = {
                            "state":        row["state"],
                            "creator_count": row["creator_count"],
                        },
                    ))

        finally:
            conn.close()

    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"[INTELLIGENCE] WATCHTOWER adapter error for {entity_id}: {exc}")

    return AdapterResult(observations=observations, timeline=timeline, entity_type=entity_type)


# ── Launcher Observatory adapter ───────────────────────────────────────────────

def _launcher_observatory_adapter(entity_id: str) -> AdapterResult:
    """
    Checks wt_farm_launches for persistent-funder status.
    Does NOT modify Launcher Observatory data.
    """
    observations: list[OperationObservation] = []
    timeline:     list[TimelineEvent]        = []
    entity_type                              = ENTITY_TYPE_UNKNOWN

    try:
        conn = _ro_conn(OPS_DB_PATH)
        try:
            rows = conn.execute(
                """
                SELECT mint, funder, detected_at, wrap_close, seed_sol, peak_mc, migrated_at
                FROM wt_farm_launches
                WHERE funder = ?
                ORDER BY detected_at ASC
                """,
                (entity_id,),
            ).fetchall()

            if not rows:
                return AdapterResult(observations=[], timeline=[], entity_type=ENTITY_TYPE_UNKNOWN)

            launch_count = len(rows)
            first_seen   = rows[0]["detected_at"]
            last_seen    = rows[-1]["detected_at"]

            # Build per-launch timeline events
            for r in rows:
                if r["detected_at"]:
                    timeline.append(TimelineEvent(
                        ts          = r["detected_at"],
                        event_type  = "LAUNCH_OBSERVED",
                        description = (
                            f"Token launch observed by Launcher Observatory "
                            f"(mint={r['mint'][:8]}…, "
                            f"seed={r['seed_sol']}◎"
                            + (f", peak_mc=${r['peak_mc']:,.0f}" if r["peak_mc"] else "")
                            + ")"
                        ),
                        source      = "LAUNCHER_OBSERVATORY",
                        provenance  = "wt_farm_launches.detected_at",
                        metadata    = {
                            "mint":       r["mint"],
                            "seed_sol":   r["seed_sol"],
                            "peak_mc":    r["peak_mc"],
                            "migrated":   bool(r["migrated_at"]),
                            "wrap_close": bool(r["wrap_close"]),
                        },
                    ))

            is_persistent = launch_count >= PERSISTENCE_MIN_LAUNCHES
            if is_persistent:
                entity_type = ENTITY_TYPE_OPERATOR

            migrated_count = sum(1 for r in rows if r["migrated_at"])
            peak_mcs       = [r["peak_mc"] for r in rows if r["peak_mc"]]
            avg_peak_mc    = (sum(peak_mcs) / len(peak_mcs)) if peak_mcs else None

            facts: dict = {
                "launch_count":     launch_count,
                "is_persistent":    is_persistent,
                "migrated_count":   migrated_count,
                "migration_rate":   round(migrated_count / launch_count, 3) if launch_count else 0,
                "avg_peak_mc_usd":  round(avg_peak_mc, 0) if avg_peak_mc else None,
                "wrap_close_used":  any(r["wrap_close"] for r in rows),
            }

            role = "PERSISTENT_FUNDER" if is_persistent else "SINGLE_FUNDER"
            observations.append(OperationObservation(
                operation_id = "launcher-observatory",
                display_name = "Launcher Observatory",
                role         = role,
                facts        = facts,
                first_seen   = first_seen,
                last_seen    = last_seen,
                provenance   = "wt_farm_launches",
            ))

            # Add a single high-level observation event at the LAST launch
            if last_seen and is_persistent:
                timeline.append(TimelineEvent(
                    ts          = last_seen,
                    event_type  = "OPERATOR_BECAME_PERSISTENT",
                    description = (
                        f"Operator reached persistent threshold "
                        f"({launch_count} launches ≥ {PERSISTENCE_MIN_LAUNCHES} required)"
                    ),
                    source      = "LAUNCHER_OBSERVATORY",
                    provenance  = "wt_farm_launches (aggregate)",
                    metadata    = {"launch_count": launch_count},
                ))

        finally:
            conn.close()

    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"[INTELLIGENCE] Launcher Observatory adapter error for {entity_id}: {exc}")

    return AdapterResult(observations=observations, timeline=timeline, entity_type=entity_type)


# ── Buy Swarm Observatory adapter ─────────────────────────────────────────────

def _buy_swarm_observatory_adapter(entity_id: str) -> AdapterResult:
    """
    Checks wt_swarm_buys for two roles:
      SWARM_PARTICIPANT — entity is a swarm_wallet that participated in buys
      SWARM_SUBPROV     — entity is a subprov_wallet that provisioned a qualified swarm

    Does NOT modify Buy Swarm Observatory data.
    """
    observations: list[OperationObservation] = []
    timeline:     list[TimelineEvent]        = []
    entity_type                              = ENTITY_TYPE_UNKNOWN

    try:
        from src.ops.buy_swarm_observatory_routes import (
            get_swarm_buys_for_wallet,
            get_swarm_buys_for_subprov,
        )

        # ── Role 1: SWARM_PARTICIPANT ──────────────────────────────────────────
        buys = get_swarm_buys_for_wallet(entity_id)
        if buys:
            mints      = list({b["mint"] for b in buys})
            first_seen = min(b["observed_at"] for b in buys)
            last_seen  = max(b["observed_at"] for b in buys)

            observations.append(OperationObservation(
                operation_id = "buy-swarm-observatory",
                display_name = "Buy Swarm Observatory",
                role         = "SWARM_PARTICIPANT",
                facts        = {
                    "buy_count":      len(buys),
                    "target_count":   len(mints),
                    "unique_subprovs": len({b["subprov_wallet"] for b in buys if b["subprov_wallet"]}),
                },
                first_seen   = first_seen,
                last_seen    = last_seen,
                provenance   = "wt_swarm_buys",
            ))

            for b in buys:
                if b["observed_at"]:
                    timeline.append(TimelineEvent(
                        ts          = b["observed_at"],
                        event_type  = "SWARM_BUY_OBSERVED",
                        description = (
                            f"Coordinated buy observed by Buy Swarm Observatory "
                            f"(mint={b['mint'][:8]}…)"
                        ),
                        source      = "BUY_SWARM_OBSERVATORY",
                        provenance  = "wt_swarm_buys.observed_at",
                        metadata    = {
                            "mint":       b["mint"],
                            "subprov":    b["subprov_wallet"],
                            "treasury":   b["treasury_wallet"],
                            "swap_sig":   b["swap_signature"],
                        },
                    ))

        # ── Role 2: SWARM_SUBPROV ─────────────────────────────────────────────
        swarms = get_swarm_buys_for_subprov(entity_id)
        if swarms:
            first_seen_sp = min(s["first_seen"] for s in swarms if s["first_seen"])
            last_seen_sp  = max(s["last_seen"]  for s in swarms if s["last_seen"])

            observations.append(OperationObservation(
                operation_id = "buy-swarm-observatory",
                display_name = "Buy Swarm Observatory",
                role         = "SWARM_SUBPROV",
                facts        = {
                    "qualified_swarm_count":  len(swarms),
                    "total_participants":     sum(s["participant_count"] for s in swarms),
                    "target_mints":           len(swarms),
                    "avg_window_seconds":     round(
                        sum(s["window_seconds"] for s in swarms) / len(swarms), 0
                    ),
                },
                first_seen   = first_seen_sp,
                last_seen    = last_seen_sp,
                provenance   = "wt_swarm_buys / qualified_swarms CTE",
            ))

            for s in swarms:
                if s["first_seen"]:
                    timeline.append(TimelineEvent(
                        ts          = s["first_seen"],
                        event_type  = "SWARM_PROVISIONED",
                        description = (
                            f"Sub-provisioner orchestrated qualified swarm "
                            f"({s['participant_count']} participants, "
                            f"window={s['window_seconds']}s, "
                            f"mint={s['mint'][:8]}…)"
                        ),
                        source      = "BUY_SWARM_OBSERVATORY",
                        provenance  = "wt_swarm_buys (qualified_swarms CTE).first_seen",
                        metadata    = {
                            "mint":              s["mint"],
                            "participant_count": s["participant_count"],
                            "window_seconds":    s["window_seconds"],
                            "treasury":          s["treasury_wallet"],
                        },
                    ))

    except Exception as exc:
        print(f"[INTELLIGENCE] Buy Swarm Observatory adapter error for {entity_id}: {exc}")

    return AdapterResult(observations=observations, timeline=timeline, entity_type=entity_type)


# ── Adapter registry ──────────────────────────────────────────────────────────
# Ordered: WATCHTOWER first (it holds stronger identity signals)

_ADAPTERS = [
    _watchtower_adapter,
    _launcher_observatory_adapter,
    _buy_swarm_observatory_adapter,
]


def aggregate(entity_id: str) -> tuple[list[OperationObservation], list[TimelineEvent], str]:
    """
    Run all adapters for entity_id.

    Returns:
      (observations, timeline_events_unsorted, best_entity_type)

    Caller is responsible for sorting the timeline.
    Entity type resolution: first non-UNKNOWN wins (adapters ordered by signal strength).
    """
    all_observations: list[OperationObservation] = []
    all_timeline:     list[TimelineEvent]        = []
    resolved_type                                = ENTITY_TYPE_UNKNOWN

    for adapter in _ADAPTERS:
        result = adapter(entity_id)
        all_observations.extend(result.observations)
        all_timeline.extend(result.timeline)
        if resolved_type == ENTITY_TYPE_UNKNOWN and result.entity_type != ENTITY_TYPE_UNKNOWN:
            resolved_type = result.entity_type

    return all_observations, all_timeline, resolved_type
