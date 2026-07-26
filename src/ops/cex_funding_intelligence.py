"""X44.0 — CEX Funding Intelligence: expands the "CEX Reached" outcome
group from a bare count into a richer, still purely presentational, view
of already-persisted funding-origin evidence.

Answers exactly what X44.0 asks and nothing more:
  - which exchange (already-identified name, never inferred)
  - which withdrawal origin (the wallet address itself)
  - how many launches/creators/Operations that origin reaches
  - the observed downstream funding path (only real hops, never invented)
  - whether the same origin/subprov is shared across multiple CEX-reached
    launches (a supporting-intelligence signal, never attribution)

This module performs NO new detection, classification, or attribution. It
is a read-only aggregation over wt_attribution_outcomes.evidence_json,
which already carries (per src/core/main.py's existing CEX-boundary
recording, using src/utils/infra_mapping.py's CEX_ACCOUNTS registry):
  evidence_json.boundary.name        -- the already-identified exchange name
  evidence_json.creator              -- the creator wallet the walkback started from
  evidence_json.funder_wallet        -- (== terminal_entity) the withdrawal
                                         origin wallet itself
  evidence_json.treasuries           -- list of treasury hops observed (may be empty)
  evidence_json.subprovisioners      -- list of subprov hops observed (may be empty)

Per the task's explicit constraints: this module never merges Operations,
never raises confidence, never classifies an exchange heuristically (a
withdrawal origin with no evidence_json.boundary.name is always labelled
"Unknown CEX", never guessed), and never invents a funding-path hop that
isn't present in the evidence_json arrays above.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional

UNKNOWN_CEX_LABEL = "Unknown CEX"


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def _short(addr: Optional[str]) -> Optional[str]:
    """Same shortened-display convention already used elsewhere in Discovery
    (first 4 + last 4 characters) -- not a new formatting rule."""
    if not addr or len(addr) <= 10:
        return addr
    return f"{addr[:4]}...{addr[-4:]}"


def _operations_reaching(conn: sqlite3.Connection, wallet: Optional[str]) -> set[str]:
    """Distinct wt_ops_v2 operation_uuids where this wallet is a known
    infrastructure wallet -- the SAME cross-reference already used by
    Discovery's Operation detail pages, not a new relationship model. An
    empty result is a real, honestly-reported fact (no Operation currently
    contains this wallet), not an error."""
    if not wallet or not _table_exists(conn, "wt_ops_v2_wallets"):
        return set()
    rows = conn.execute(
        "SELECT DISTINCT operation_uuid FROM wt_ops_v2_wallets WHERE wallet=?", (wallet,)
    ).fetchall()
    return {r[0] for r in rows}


def build_cex_funding_intelligence(ops_db_path: str, *, window_seconds: Optional[int] = None,
                                    now: Optional[int] = None) -> dict[str, Any]:
    """Read-only, zero writes. Returns the CEX-Reached population grouped by
    withdrawal origin (terminal_entity), each with its exchange label,
    observed funding path, launch/creator/Operation counts, and first/last
    seen timestamps. Also returns a `multi_cex_creators` list -- creators
    whose CEX-reached launches touched more than one distinct withdrawal
    origin (per the task's "Multiple CEX Origins" feature) -- and a
    `shared_infrastructure` section identifying any subprov/treasury wallet
    that appears across more than one distinct withdrawal origin (the
    "Shared Subprovider"/"Shared Treasury" strength indicators)."""
    conn = sqlite3.connect(f"file:{ops_db_path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    try:
        if not _table_exists(conn, "wt_attribution_outcomes"):
            return {"origins": [], "mints": {}, "multi_cex_creators": [], "shared_infrastructure": []}

        where = "outcome_type='KNOWN_CEX_REACHED'"
        params: list = []
        if window_seconds is not None:
            import time
            since = int(now or time.time()) - window_seconds
            where += " AND completed_at >= ?"
            params.append(since)

        rows = conn.execute(
            f"SELECT mint, terminal_entity, evidence_json, completed_at FROM wt_attribution_outcomes WHERE {where}",
            params,
        ).fetchall()

        # ── group by withdrawal origin (terminal_entity) ──
        by_origin: dict[str, dict[str, Any]] = {}
        by_mint: dict[str, dict[str, Any]] = {}
        creator_origins: dict[str, set[str]] = {}
        subprov_origins: dict[str, set[str]] = {}
        treasury_origins: dict[str, set[str]] = {}

        for r in rows:
            origin = r["terminal_entity"]
            if not origin:
                continue
            try:
                ev = json.loads(r["evidence_json"]) if r["evidence_json"] else {}
            except (json.JSONDecodeError, TypeError):
                ev = {}
            boundary = ev.get("boundary") or {}
            exchange = boundary.get("name") or UNKNOWN_CEX_LABEL
            creator = ev.get("creator")
            treasuries = ev.get("treasuries") or []
            # X44.0 — some evidence_json rows duplicate terminal_entity
            # inside subprovisioners (the origin wallet listing itself as
            # its own hop). Excluded here so the funding path never renders
            # the same wallet twice under two roles (Exchange AND
            # Subprovider) -- this is a real quirk in the underlying
            # evidence, not a new classification decision.
            subprovisioners = [sp for sp in (ev.get("subprovisioners") or []) if sp != origin]

            slot = by_origin.setdefault(origin, {
                "origin": origin,
                "origin_short": _short(origin),
                "exchange": exchange,
                "mints": set(),
                "creators": set(),
                "treasuries": set(),
                "subprovisioners": set(),
                "first_seen": r["completed_at"],
                "last_seen": r["completed_at"],
            })
            slot["mints"].add(r["mint"])
            if creator:
                slot["creators"].add(creator)
            slot["treasuries"].update(treasuries)
            slot["subprovisioners"].update(subprovisioners)
            slot["first_seen"] = min(slot["first_seen"], r["completed_at"])
            slot["last_seen"] = max(slot["last_seen"], r["completed_at"])

            if creator:
                creator_origins.setdefault(creator, set()).add(origin)
            for sp in subprovisioners:
                subprov_origins.setdefault(sp, set()).add(origin)
            for tr in treasuries:
                treasury_origins.setdefault(tr, set()).add(origin)

            # X44.1 — per-mint record, the actual ask: show the token address
            # WITH its CEX info attached directly, not only as a separate
            # origin-grouped summary. One evidence_json row = one mint here
            # (mint is the table's PRIMARY KEY), so this is a 1:1 carry of
            # already-computed fields, not a new query or new evidence.
            by_mint[r["mint"]] = {
                "mint": r["mint"],
                "exchange": exchange,
                "origin": origin,
                "origin_short": _short(origin),
                "creator": creator,
                "creator_short": _short(creator) if creator else None,
                "treasuries": treasuries,
                "subprovisioners": subprovisioners,
                "completed_at": r["completed_at"],
            }

        origins_out = []
        for origin, slot in by_origin.items():
            operations = _operations_reaching(conn, origin)
            # funding path: only real observed hops, longest-chain-first is
            # not attempted here (evidence_json doesn't order multi-hop
            # chains) -- render exactly what's present: CEX -> [treasury]
            # -> [subprov] -> creator, omitting any hop with no evidence.
            path = [{"role": "Exchange", "label": slot["exchange"]}]
            for tr in sorted(slot["treasuries"]):
                path.append({"role": "Treasury", "label": _short(tr), "address": tr})
            for sp in sorted(slot["subprovisioners"]):
                path.append({"role": "Subprovider", "label": _short(sp), "address": sp})
            if slot["creators"]:
                # a single origin can reach multiple creators; the path
                # diagram shows the terminal role, individual creators are
                # listed separately below rather than one path per creator
                path.append({"role": "Creator", "label": f"{len(slot['creators'])} creator"
                            + ("s" if len(slot["creators"]) != 1 else "")})

            origins_out.append({
                "origin": origin,
                "origin_short": slot["origin_short"],
                "exchange": slot["exchange"],
                "launches": len(slot["mints"]),
                "creators": len(slot["creators"]),
                "operations": len(operations),
                "operation_ids": sorted(operations),
                "first_seen": slot["first_seen"],
                "last_seen": slot["last_seen"],
                "funding_path": path,
                "strength_indicators": {
                    "exchange_match": slot["exchange"] != UNKNOWN_CEX_LABEL,
                    "shared_withdrawal_origin": len(slot["mints"]) > 1,
                    "shared_treasury": any(len(v) > 1 for k, v in treasury_origins.items() if k in slot["treasuries"]),
                    # X44.0 — "shared" covers two distinct real signals: (a) this
                    # origin's own subprov hop also appears as a hop for another
                    # origin (subprov_origins len>1), or (b) this origin's subprov
                    # hop IS ITSELF another origin's terminal_entity -- i.e. one
                    # known CEX/infra wallet observed mid-chain for a different
                    # exchange's funding path (confirmed live: KuCoin's own
                    # withdrawal wallet appears as a subprov hop on a Binance-
                    # attributed launch). Both are real structural facts already
                    # in evidence_json, never an inferred relationship.
                    "shared_subprovider": (
                        any(len(v) > 1 for k, v in subprov_origins.items() if k in slot["subprovisioners"])
                        or any(sp in by_origin and sp != origin for sp in slot["subprovisioners"])
                    ),
                    "shared_creator": False,  # no evidence this module can honestly assert -- creators are never shared ACROSS distinct CEX origins in this population (see multi_cex_creators for the one exception type actually observed)
                },
            })

        origins_out.sort(key=lambda o: o["launches"], reverse=True)

        multi_cex_creators = [
            {"creator": c, "creator_short": _short(c), "origins": sorted(origins),
             "exchanges": sorted({by_origin[o]["exchange"] for o in origins if o in by_origin})}
            for c, origins in creator_origins.items() if len(origins) > 1
        ]

        shared_infrastructure = []
        for sp, origins in subprov_origins.items():
            if len(origins) > 1:
                shared_infrastructure.append({
                    "wallet": sp, "wallet_short": _short(sp), "role": "Subprovider",
                    "origins": sorted(origins),
                    "exchanges": sorted({by_origin[o]["exchange"] for o in origins if o in by_origin}),
                })
        for tr, origins in treasury_origins.items():
            if len(origins) > 1:
                shared_infrastructure.append({
                    "wallet": tr, "wallet_short": _short(tr), "role": "Treasury",
                    "origins": sorted(origins),
                    "exchanges": sorted({by_origin[o]["exchange"] for o in origins if o in by_origin}),
                })
        # X44.0 — a subprov hop that IS ITSELF another origin's own
        # withdrawal address: a distinct cross-exchange signal from the
        # "same subprov feeds two origins" case above (that one never fires
        # for these single-hop cases, since the hop only ever appears under
        # one OTHER origin's subprov list, not two). Reported separately so
        # it isn't silently dropped by the len>1 threshold above.
        for sp, origins in subprov_origins.items():
            if len(origins) == 1 and sp in by_origin and sp not in origins:
                shared_infrastructure.append({
                    "wallet": sp, "wallet_short": _short(sp), "role": "Cross-Exchange Hop",
                    "origins": sorted(origins) + [sp],
                    "exchanges": sorted({by_origin[o]["exchange"] for o in (list(origins) + [sp]) if o in by_origin}),
                })

        return {
            "origins": origins_out,
            "mints": by_mint,
            "multi_cex_creators": multi_cex_creators,
            "shared_infrastructure": shared_infrastructure,
            "total_launches": sum(o["launches"] for o in origins_out),
            "total_origins": len(origins_out),
        }
    finally:
        conn.close()
