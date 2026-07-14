"""
Cross-Operation Intelligence service.

Read-only. No RPC. No schema changes. No operation modification.

This service reads from the same tables the operation adapters read,
derives relationships that already exist in the evidence, and
produces a canonical relationship set for any entity.

Operations remain independent — this service is the only code that
knows all three exist.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from typing import Any

from src.cross_operation.models import (
    CONFIDENCE_CERTAIN,
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    OBSERVED_BY_MULTIPLE_OPS,
    OBSERVED_BY_SAME_OPERATION,
    SHARED_FUNDER,
    SHARED_OPERATOR,
    SHARED_SWARM,
    SHARED_TARGET,
    EntityOverlap,
    GlobalStats,
    Relationship,
)

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OPS_DB_PATH = os.environ.get(
    "OPS_V2_DB_PATH", os.path.join(_REPO, "database", "wt_ops_v2.db")
)


def _ro_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _rel_id(entity_a: str, entity_b: str, rel_type: str) -> str:
    key = f"{min(entity_a, entity_b)}|{max(entity_a, entity_b)}|{rel_type}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


# ── Per-entity presence lookup ─────────────────────────────────────────────────

def _presence(entity_id: str, conn: sqlite3.Connection) -> dict[str, Any]:
    """
    Determine which operations have observed entity_id and collect timestamps.
    Returns a dict of operation_id -> {role, first_seen, last_seen, ...}
    """
    presence: dict[str, dict] = {}

    # ── WATCHTOWER ─────────────────────────────────────────────────────────────
    wt_rows = []
    r = conn.execute(
        "SELECT treasury, confirmed_at, confidence, method, out_sol, recipients "
        "FROM wt_confirmed_treasuries WHERE treasury=?", (entity_id,)
    ).fetchone()
    if r:
        wt_rows.append({
            "role": "TREASURY", "first_seen": r["confirmed_at"],
            "last_seen": r["confirmed_at"], "confidence": r["confidence"],
            "method": r["method"], "out_sol": r["out_sol"],
            "recipients": r["recipients"],
        })

    r = conn.execute(
        "SELECT subprov, first_seen, last_seen, confidence, treasury, creator_count "
        "FROM wt_discovered_subprovs WHERE subprov=?", (entity_id,)
    ).fetchone()
    if r:
        wt_rows.append({
            "role": "SUB_PROVISIONER", "first_seen": r["first_seen"],
            "last_seen": r["last_seen"], "confidence": r["confidence"],
            "treasury": r["treasury"], "creator_count": r["creator_count"],
        })

    if wt_rows:
        firsts = [w["first_seen"] for w in wt_rows if w["first_seen"]]
        lasts  = [w["last_seen"]  for w in wt_rows if w["last_seen"]]
        presence["watchtower"] = {
            "roles":      [w["role"] for w in wt_rows],
            "first_seen": min(firsts) if firsts else None,
            "last_seen":  max(lasts)  if lasts  else None,
            "detail":     wt_rows,
        }

    # ── Launcher Observatory ───────────────────────────────────────────────────
    lo_rows = conn.execute(
        "SELECT mint, detected_at, wrap_close, seed_sol, peak_mc "
        "FROM wt_farm_launches WHERE funder=? ORDER BY detected_at ASC",
        (entity_id,),
    ).fetchall()
    if lo_rows:
        firsts = [r["detected_at"] for r in lo_rows if r["detected_at"]]
        lasts  = firsts
        presence["launcher-observatory"] = {
            "roles":       ["FUNDER"],
            "first_seen":  min(firsts) if firsts else None,
            "last_seen":   max(firsts) if firsts else None,
            "launch_count": len(lo_rows),
            "mints":        [r["mint"] for r in lo_rows],
        }

    # ── Buy Swarm Observatory ──────────────────────────────────────────────────
    bso_roles: list[dict] = []

    # participant role
    p_rows = conn.execute(
        "SELECT mint, subprov_wallet, treasury_wallet, observed_at "
        "FROM wt_swarm_buys WHERE swarm_wallet=? ORDER BY observed_at ASC",
        (entity_id,),
    ).fetchall()
    if p_rows:
        bso_roles.append({
            "role": "SWARM_PARTICIPANT",
            "first_seen": min(r["observed_at"] for r in p_rows if r["observed_at"]),
            "last_seen":  max(r["observed_at"] for r in p_rows if r["observed_at"]),
            "mints":      list({r["mint"] for r in p_rows}),
        })

    # subprov role (qualified swarms)
    sp_rows = conn.execute(
        """
        SELECT mint, subprov_wallet, treasury_wallet,
               COUNT(DISTINCT swarm_wallet) AS participant_count,
               MIN(observed_at) AS first_seen, MAX(observed_at) AS last_seen,
               MAX(observed_at)-MIN(observed_at) AS window_seconds
        FROM wt_swarm_buys
        WHERE subprov_wallet=?
        GROUP BY mint, subprov_wallet, treasury_wallet
        HAVING participant_count >= 3 AND window_seconds <= 7200
        ORDER BY participant_count DESC
        """,
        (entity_id,),
    ).fetchall()
    if sp_rows:
        bso_roles.append({
            "role": "SWARM_SUBPROV",
            "first_seen": min(r["first_seen"] for r in sp_rows if r["first_seen"]),
            "last_seen":  max(r["last_seen"]  for r in sp_rows if r["last_seen"]),
            "qualified_swarms": len(sp_rows),
        })

    # treasury role in BSO (any mention as treasury_wallet in a qualified swarm)
    t_rows = conn.execute(
        """
        SELECT mint, COUNT(DISTINCT swarm_wallet) AS participant_count,
               MIN(observed_at) AS first_seen, MAX(observed_at) AS last_seen,
               MAX(observed_at)-MIN(observed_at) AS window_seconds
        FROM wt_swarm_buys WHERE treasury_wallet=?
        GROUP BY mint
        HAVING participant_count >= 3 AND window_seconds <= 7200
        """,
        (entity_id,),
    ).fetchall()
    # Also check raw mentions (even un-qualified) to establish BSO presence
    t_any = conn.execute(
        "SELECT COUNT(*) FROM wt_swarm_buys WHERE treasury_wallet=?", (entity_id,)
    ).fetchone()[0]
    if t_rows or t_any > 0:
        if t_rows:
            bso_roles.append({
                "role": "SWARM_TREASURY",
                "first_seen": min(r["first_seen"] for r in t_rows if r["first_seen"]),
                "last_seen":  max(r["last_seen"]  for r in t_rows if r["last_seen"]),
                "swarm_count": len(t_rows),
            })
        else:
            # Mentioned as treasury but no qualified swarms — still establishes BSO presence
            t_ts = conn.execute(
                "SELECT MIN(observed_at), MAX(observed_at) FROM wt_swarm_buys WHERE treasury_wallet=?",
                (entity_id,),
            ).fetchone()
            bso_roles.append({
                "role": "SWARM_TREASURY",
                "first_seen": t_ts[0],
                "last_seen":  t_ts[1],
                "swarm_count": 0,
            })

    if bso_roles:
        firsts = [r["first_seen"] for r in bso_roles if r.get("first_seen")]
        lasts  = [r["last_seen"]  for r in bso_roles if r.get("last_seen")]
        presence["buy-swarm-observatory"] = {
            "roles":      [r["role"] for r in bso_roles],
            "first_seen": min(firsts) if firsts else None,
            "last_seen":  max(lasts)  if lasts  else None,
            "detail":     bso_roles,
        }

    return presence


# ── Relationship derivation ────────────────────────────────────────────────────

def _relationships_for_entity(
    entity_id: str, presence: dict, conn: sqlite3.Connection
) -> list[Relationship]:
    rels: list[Relationship] = []
    ops = list(presence.keys())

    # ── OBSERVED_BY_MULTIPLE_OPERATIONS ───────────────────────────────────────
    if len(ops) >= 2:
        all_ts = [
            t
            for p in presence.values()
            for t in (p["first_seen"], p["last_seen"])
            if t
        ]
        rels.append(Relationship(
            relationship_id       = _rel_id(entity_id, "multi-op", OBSERVED_BY_MULTIPLE_OPS),
            entity_a              = entity_id,
            entity_b              = "__multi_operation__",
            relationship_type     = OBSERVED_BY_MULTIPLE_OPS,
            confidence            = CONFIDENCE_CERTAIN,
            supporting_operations = tuple(sorted(ops)),
            supporting_evidence   = tuple(
                f"Observed by {op} as {'/'.join(presence[op]['roles'])}" for op in ops
            ),
            first_seen            = min(all_ts) if all_ts else None,
            last_seen             = max(all_ts) if all_ts else None,
            provenance            = "wt_confirmed_treasuries,wt_discovered_subprovs,wt_farm_launches,wt_swarm_buys",
        ))

    # ── SHARED_OPERATOR (entity is a WT treasury AND in BSO as treasury_wallet) ─
    if "watchtower" in presence and "buy-swarm-observatory" in presence:
        wt_p   = presence["watchtower"]
        bso_p  = presence["buy-swarm-observatory"]
        if "TREASURY" in wt_p["roles"] and "SWARM_TREASURY" in [
            d["role"] for d in bso_p["detail"]
        ]:
            swarm_detail = next(
                d for d in bso_p["detail"] if d["role"] == "SWARM_TREASURY"
            )
            all_ts = [
                t for t in [
                    wt_p["first_seen"], wt_p["last_seen"],
                    swarm_detail["first_seen"], swarm_detail["last_seen"],
                ] if t
            ]
            rels.append(Relationship(
                relationship_id       = _rel_id(entity_id, "wt+bso-treasury", SHARED_OPERATOR),
                entity_a              = entity_id,
                entity_b              = "__operator__",
                relationship_type     = SHARED_OPERATOR,
                confidence            = CONFIDENCE_CERTAIN,
                supporting_operations = ("watchtower", "buy-swarm-observatory"),
                supporting_evidence   = (
                    f"Confirmed WATCHTOWER treasury (method={wt_p['detail'][0].get('method','?')}, "
                    f"{wt_p['detail'][0].get('recipients','?')} recipients)",
                    f"Orchestrated {swarm_detail['swarm_count']} qualified buy swarms as treasury",
                ),
                first_seen            = min(all_ts) if all_ts else None,
                last_seen             = max(all_ts) if all_ts else None,
                provenance            = "wt_confirmed_treasuries + wt_swarm_buys",
            ))

    # ── SHARED_FUNDER (entity is a WT subprov AND in LO as funder) ────────────
    if "watchtower" in presence and "launcher-observatory" in presence:
        wt_p = presence["watchtower"]
        lo_p = presence["launcher-observatory"]
        if "SUB_PROVISIONER" in wt_p["roles"]:
            sp_detail = next(
                d for d in wt_p["detail"] if d["role"] == "SUB_PROVISIONER"
            )
            all_ts = [
                t for t in [
                    wt_p["first_seen"], wt_p["last_seen"],
                    lo_p["first_seen"], lo_p["last_seen"],
                ] if t
            ]
            rels.append(Relationship(
                relationship_id       = _rel_id(entity_id, "wt+lo-subprov", SHARED_FUNDER),
                entity_a              = entity_id,
                entity_b              = "__funded_tokens__",
                relationship_type     = SHARED_FUNDER,
                confidence            = CONFIDENCE_CERTAIN,
                supporting_operations = ("watchtower", "launcher-observatory"),
                supporting_evidence   = (
                    f"WATCHTOWER sub-provisioner ({sp_detail.get('creator_count','?')} creators seeded)",
                    f"Launcher Observatory: {lo_p['launch_count']} token launches funded",
                ),
                first_seen            = min(all_ts) if all_ts else None,
                last_seen             = max(all_ts) if all_ts else None,
                provenance            = "wt_discovered_subprovs + wt_farm_launches",
            ))

    # ── SHARED_SWARM (entity is a WT subprov AND in BSO as swarm_subprov) ─────
    if "watchtower" in presence and "buy-swarm-observatory" in presence:
        wt_p  = presence["watchtower"]
        bso_p = presence["buy-swarm-observatory"]
        if "SUB_PROVISIONER" in wt_p["roles"] and "SWARM_SUBPROV" in [
            d["role"] for d in bso_p["detail"]
        ]:
            sp_wt  = next(d for d in wt_p["detail"]  if d["role"] == "SUB_PROVISIONER")
            sp_bso = next(d for d in bso_p["detail"] if d["role"] == "SWARM_SUBPROV")
            all_ts = [
                t for t in [
                    wt_p["first_seen"], wt_p["last_seen"],
                    sp_bso["first_seen"], sp_bso["last_seen"],
                ] if t
            ]
            rels.append(Relationship(
                relationship_id       = _rel_id(entity_id, "wt+bso-subprov", SHARED_SWARM),
                entity_a              = entity_id,
                entity_b              = "__swarm_targets__",
                relationship_type     = SHARED_SWARM,
                confidence            = CONFIDENCE_CERTAIN,
                supporting_operations = ("watchtower", "buy-swarm-observatory"),
                supporting_evidence   = (
                    f"WATCHTOWER sub-provisioner ({sp_wt.get('creator_count','?')} creators seeded)",
                    f"Buy Swarm Observatory: provisioned {sp_bso['qualified_swarms']} qualified swarms",
                ),
                first_seen            = min(all_ts) if all_ts else None,
                last_seen             = max(all_ts) if all_ts else None,
                provenance            = "wt_discovered_subprovs + wt_swarm_buys",
            ))

    # ── SHARED_TARGET (entity funded launches in LO AND in BSO as swarm target ─
    # Entity appears as funder in wt_farm_launches AND as subprov in wt_swarm_buys
    # for overlapping mints
    if "launcher-observatory" in presence and "buy-swarm-observatory" in presence:
        lo_p  = presence["launcher-observatory"]
        bso_p = presence["buy-swarm-observatory"]
        lo_mints = set(lo_p.get("mints", []))

        # Find BSO mints where this entity is subprov (for qualified swarms)
        bso_swarm_mints: set[str] = set()
        for d in bso_p["detail"]:
            if d["role"] == "SWARM_SUBPROV":
                # re-query for the actual mints
                bso_mints_rows = conn.execute(
                    """
                    SELECT mint FROM wt_swarm_buys
                    WHERE subprov_wallet=?
                    GROUP BY mint
                    HAVING COUNT(DISTINCT swarm_wallet) >= 3
                       AND MAX(observed_at)-MIN(observed_at) <= 7200
                    """,
                    (entity_id,),
                ).fetchall()
                bso_swarm_mints = {r["mint"] for r in bso_mints_rows}

        shared_mints = lo_mints & bso_swarm_mints
        if shared_mints:
            all_ts = [
                t for t in [
                    lo_p["first_seen"], lo_p["last_seen"],
                    bso_p["first_seen"], bso_p["last_seen"],
                ] if t
            ]
            rels.append(Relationship(
                relationship_id       = _rel_id(entity_id, "lo+bso-mint", SHARED_TARGET),
                entity_a              = entity_id,
                entity_b              = "__shared_mints__",
                relationship_type     = SHARED_TARGET,
                confidence            = CONFIDENCE_CERTAIN,
                supporting_operations = ("launcher-observatory", "buy-swarm-observatory"),
                supporting_evidence   = (
                    f"Funded {len(lo_mints)} token launches via Launcher Observatory",
                    f"Provisioned buy swarms for {len(bso_swarm_mints)} mints via Buy Swarm Observatory",
                    f"{len(shared_mints)} mint(s) observed by both operations",
                ),
                first_seen            = min(all_ts) if all_ts else None,
                last_seen             = max(all_ts) if all_ts else None,
                provenance            = "wt_farm_launches + wt_swarm_buys",
                metadata              = {"shared_mints": sorted(shared_mints)[:5]},
            ))

    return rels


# ── Unified timeline ───────────────────────────────────────────────────────────

def _unified_timeline(presence: dict) -> list[dict]:
    """
    Merge all operation timestamps into a single chronological list.
    No inference — only observed timestamps.
    """
    events: list[dict] = []

    if "watchtower" in presence:
        p = presence["watchtower"]
        for d in p["detail"]:
            if d["role"] == "TREASURY" and d.get("first_seen"):
                events.append({
                    "ts":          d["first_seen"],
                    "event_type":  "TREASURY_CONFIRMED",
                    "description": f"Confirmed WATCHTOWER treasury (method={d.get('method','?')})",
                    "source":      "WATCHTOWER",
                    "provenance":  "wt_confirmed_treasuries.confirmed_at",
                })
            if d["role"] == "SUB_PROVISIONER" and d.get("first_seen"):
                events.append({
                    "ts":          d["first_seen"],
                    "event_type":  "SUBPROV_DISCOVERED",
                    "description": f"Discovered as WATCHTOWER sub-provisioner ({d.get('creator_count','?')} creators)",
                    "source":      "WATCHTOWER",
                    "provenance":  "wt_discovered_subprovs.first_seen",
                })

    if "launcher-observatory" in presence:
        p = presence["launcher-observatory"]
        if p.get("first_seen"):
            events.append({
                "ts":          p["first_seen"],
                "event_type":  "FIRST_LAUNCH_OBSERVED",
                "description": f"First token launch observed by Launcher Observatory ({p['launch_count']} total)",
                "source":      "LAUNCHER_OBSERVATORY",
                "provenance":  "wt_farm_launches.detected_at",
            })
        if p.get("last_seen") and p["last_seen"] != p.get("first_seen"):
            events.append({
                "ts":          p["last_seen"],
                "event_type":  "LATEST_LAUNCH_OBSERVED",
                "description": "Latest token launch observed by Launcher Observatory",
                "source":      "LAUNCHER_OBSERVATORY",
                "provenance":  "wt_farm_launches.detected_at",
            })

    if "buy-swarm-observatory" in presence:
        p = presence["buy-swarm-observatory"]
        for d in p["detail"]:
            if d.get("first_seen"):
                label = {
                    "SWARM_PARTICIPANT": "Participated in coordinated buy swarm",
                    "SWARM_SUBPROV":     f"Provisioned qualified buy swarms ({d.get('qualified_swarms','?')} swarms)",
                    "SWARM_TREASURY":    f"Treasury for {d.get('swarm_count','?')} qualified swarms",
                }.get(d["role"], d["role"])
                events.append({
                    "ts":          d["first_seen"],
                    "event_type":  f"BSO_{d['role']}",
                    "description": label,
                    "source":      "BUY_SWARM_OBSERVATORY",
                    "provenance":  "wt_swarm_buys.observed_at",
                })

    events.sort(key=lambda e: e["ts"] or 0)
    return events


# ── Public API ─────────────────────────────────────────────────────────────────

def entity_relationships(entity_id: str) -> EntityOverlap:
    """
    Compute all cross-operation relationships for a single entity.
    Read-only. No RPC. Everything computed on-demand.
    """
    try:
        conn = _ro_conn()
        try:
            presence = _presence(entity_id, conn)
            rels     = _relationships_for_entity(entity_id, presence, conn)
            timeline = _unified_timeline(presence)
        finally:
            conn.close()
    except Exception as exc:
        print(f"[CROSS-OP] entity_relationships error for {entity_id}: {exc}")
        presence, rels, timeline = {}, [], []

    return EntityOverlap(
        entity_id        = entity_id,
        observed_by      = list(presence.keys()),
        operation_count  = len(presence),
        relationships    = rels,
        unified_timeline = timeline,
        generated_at     = int(time.time()),
    )


def global_stats() -> GlobalStats:
    """
    Compute operation overlap statistics across all known entities.
    Read-only. Scans all three operation tables once.
    """
    try:
        conn = _ro_conn()
        try:
            # Collect all known entities per operation
            wt_entities: set[str] = set()
            for row in conn.execute("SELECT treasury FROM wt_confirmed_treasuries"):
                wt_entities.add(row[0])
            for row in conn.execute("SELECT subprov FROM wt_discovered_subprovs"):
                wt_entities.add(row[0])

            lo_entities: set[str] = set()
            for row in conn.execute("SELECT DISTINCT funder FROM wt_farm_launches"):
                lo_entities.add(row[0])

            bso_entities: set[str] = set()
            for row in conn.execute("SELECT DISTINCT swarm_wallet FROM wt_swarm_buys"):
                bso_entities.add(row[0])
            for row in conn.execute("SELECT DISTINCT subprov_wallet FROM wt_swarm_buys WHERE subprov_wallet IS NOT NULL"):
                bso_entities.add(row[0])
            for row in conn.execute("SELECT DISTINCT treasury_wallet FROM wt_swarm_buys WHERE treasury_wallet IS NOT NULL"):
                bso_entities.add(row[0])

        finally:
            conn.close()
    except Exception as exc:
        print(f"[CROSS-OP] global_stats error: {exc}")
        wt_entities = lo_entities = bso_entities = set()

    all_entities = wt_entities | lo_entities | bso_entities

    in_wt  = wt_entities
    in_lo  = lo_entities
    in_bso = bso_entities

    counts: dict[str, int] = {1: 0, 2: 0, 3: 0}
    for eid in all_entities:
        n = sum([eid in in_wt, eid in in_lo, eid in in_bso])
        counts[n] = counts.get(n, 0) + 1

    # Pair overlaps
    wt_lo  = len(wt_entities  & lo_entities)
    wt_bso = len(wt_entities  & bso_entities)
    lo_bso = len(lo_entities  & bso_entities)
    all3   = len(wt_entities & lo_entities & bso_entities)

    # Approximate relationship type distribution (OBSERVED_BY_MULTIPLE_OPS for all multi-op entities)
    multi = counts.get(2, 0) + counts.get(3, 0)
    rel_dist = {
        "OBSERVED_BY_MULTIPLE_OPERATIONS": multi,
        "SHARED_OPERATOR":  len({e for e in wt_entities & bso_entities}),
        "SHARED_FUNDER":    len({e for e in wt_entities & lo_entities}),
        "SHARED_SWARM":     len({e for e in wt_entities & bso_entities}),
        "SHARED_TARGET":    len({e for e in lo_entities & bso_entities}),
    }

    total_rels = sum(rel_dist.values())
    n = len(all_entities)

    return GlobalStats(
        total_entities           = n,
        entities_in_1_operation  = counts.get(1, 0),
        entities_in_2_operations = counts.get(2, 0),
        entities_in_3_operations = counts.get(3, 0),
        total_relationships      = total_rels,
        avg_relationships_per_entity = round(total_rels / n, 2) if n else 0.0,
        relationship_type_distribution = rel_dist,
        operation_pair_overlaps  = {
            "watchtower+launcher-observatory":       wt_lo,
            "watchtower+buy-swarm-observatory":      wt_bso,
            "launcher-observatory+buy-swarm-observatory": lo_bso,
            "all-three": all3,
        },
        generated_at = int(time.time()),
    )
