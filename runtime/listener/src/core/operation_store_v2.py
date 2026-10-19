#!/usr/bin/env python3
"""
Phase 1 — Operation-Centric Intelligence Store (parallel to the WATCH pipeline).

This is the production-shaped successor to operation_discovery_poc.py. It reuses the
PROVEN trace engine from the PoC (creator -> funder -> pass-through -> collector ->
treasury) and adds the full persistent data model plus the resolved operation/family
hierarchy.

Resolved model (from the merge test):
    operation        = a treasury-rooted infrastructure island. Keyed by treasury_root.
                       Merge two roots ONLY on HARD infrastructure overlap.
    operation_family = a SOFT grouping of operations that share playbook/tooling/topology
                       but NOT infrastructure. A family link NEVER collapses operations.

Non-negotiable merge discipline:
    Merge on:  shared collector | shared pass-through | shared terminal |
               shared upstream funder | direct treasury<->treasury edge.
    NEVER merge on: same template | same timing | same fan-out size |
                    same sweep-amount class | behavioural resemblance.
    Those create FAMILY LINKS only.

Persistence: DISCOVER / MERGE / EXPAND. Never DELETE, never full-rebuild, stable UUIDs.
Operation records are persistent intelligence objects, not query outputs — because
wallets rotate, the store must outlive every wallet in it.

Safety: writes to an ISOLATED database (database/wt_ops_v2.db) so it cannot contend
with the live app's WAL. Touches NOTHING in _discover_operations / wt_operations /
attribution / dashboard. Importing this module changes no existing behaviour.

Run:
    python -m src.core.operation_store_v2                 # runs the validation seeds
    python -m src.core.operation_store_v2 <creator> ...   # trace specific creators
"""

from __future__ import annotations

import os
import sys
import json
import time
import uuid
from collections import defaultdict
from typing import Optional

from src.utils.db_locking import db_connect

# X41.0 shadow merge ledger — additive, explanatory only, never affects the merge
# decision below. These wrappers are a hard boundary: they NEVER raise, no matter
# what goes wrong (missing module, broken schema, anything) — same Gate F
# guarantee as attribution_evidence's wrapper in treasury_bank.py. Removing this
# import/these calls must return persist() to its exact original behaviour.
import logging as _merge_ledger_logging
_merge_ledger_logger = _merge_ledger_logging.getLogger("operation_merge_ledger")


def _record_merge_event(*args, **kwargs):
    try:
        from src.core.operation_merge_ledger import record_merge_event as _rme
        return _rme(*args, **kwargs)
    except Exception as _exc:                          # pragma: no cover - defensive
        _merge_ledger_logger.error("operation_merge_ledger dual-write call failed "
                                   "(swallowed, production flow unaffected): %s", _exc)
        return None


def _determine_merge_rule(*args, **kwargs):
    try:
        from src.core.operation_merge_ledger import determine_merge_rule as _dmr
        return _dmr(*args, **kwargs)
    except Exception as _exc:                          # pragma: no cover - defensive
        _merge_ledger_logger.error("determine_merge_rule call failed "
                                   "(swallowed, production flow unaffected): %s", _exc)
        return "UNKNOWN"

# Reuse the PROVEN trace engine — do not re-implement it.
from src.core.operation_discovery_poc import (
    discover_from_creator, trace_node, _native_flows, _rpc_addr_txs,
    ATA_RENT_LAMPORTS, SWEEP_FANIN, TREASURY_FANIO,
    score_wallet_role,
)

try:
    from src.core.rpc_cache import RPCCache
except Exception:
    RPCCache = None

LIVE_DB_PATH = os.environ.get(
    "FLEX_DB_PATH",
    os.path.join(os.path.dirname(__file__), "..", "..", "database", "flex_complete_database.db"),
)
OPS_DB_PATH = os.environ.get(
    "OPS_V2_DB_PATH",
    os.path.join(os.path.dirname(__file__), "..", "..", "database", "wt_ops_v2.db"),
)

# Family playbook signature for the current WATCHTOWER-like swarm playbook.
# Operations matching this topology get FAMILY-linked (never merged).
PLAYBOOK_WATCHTOWER_LIKE = "1.11/ATA+swarm-sweep-recycle"
FAMILY_TEMPLATE_BASES = {1.10, 1.11, 2.10, 0.605, 5.10}   # known WT/ALPHA library


# ─────────────────────────── schema (Phase 1 spec, additive) ───────────────
def ensure_schema(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS wt_ops_v2 (
            operation_uuid TEXT PRIMARY KEY,
            treasury_root  TEXT NOT NULL UNIQUE,
            family_uuid    TEXT,
            status         TEXT NOT NULL DEFAULT 'FORMING',
            confidence     REAL NOT NULL DEFAULT 0.0,
            first_seen     INTEGER NOT NULL,
            last_seen      INTEGER NOT NULL,
            created_at     INTEGER NOT NULL,
            updated_at     INTEGER NOT NULL,
            source         TEXT,
            notes          TEXT,
            op_type        TEXT DEFAULT 'WATCHTOWER'   -- WATCHTOWER | MICRO_DEPLOYER | UNTEMPLATED
        );

        CREATE TABLE IF NOT EXISTS wt_ops_v2_wallets (
            operation_uuid TEXT NOT NULL,
            wallet         TEXT NOT NULL,
            role           TEXT NOT NULL,
            first_seen     INTEGER NOT NULL,
            last_seen      INTEGER NOT NULL,
            evidence_json  TEXT,
            PRIMARY KEY (operation_uuid, wallet)
        );

        CREATE TABLE IF NOT EXISTS wt_ops_v2_edges (
            operation_uuid TEXT NOT NULL,
            from_wallet    TEXT NOT NULL,
            to_wallet      TEXT NOT NULL,
            amount_sol     REAL,
            block_time     INTEGER,
            signature      TEXT,
            edge_type      TEXT,
            hop_depth      INTEGER,
            evidence_json  TEXT,
            PRIMARY KEY (signature, from_wallet, to_wallet, amount_sol)
        );

        CREATE TABLE IF NOT EXISTS wt_ops_v2_creators (
            operation_uuid    TEXT NOT NULL,
            creator_wallet    TEXT NOT NULL,
            token_mint        TEXT NOT NULL,
            migration_time    INTEGER,
            funding_amount_sol REAL,
            template_base     REAL,
            source_creator    TEXT,
            trace_id          TEXT,
            evidence_json     TEXT,
            PRIMARY KEY (operation_uuid, creator_wallet, token_mint)
        );

        CREATE TABLE IF NOT EXISTS wt_ops_v2_families (
            family_uuid        TEXT PRIMARY KEY,
            family_label       TEXT,
            playbook_signature TEXT,
            confidence         REAL,
            first_seen         INTEGER,
            last_seen          INTEGER,
            evidence_json      TEXT
        );

        CREATE TABLE IF NOT EXISTS wt_ops_v2_operation_family_links (
            operation_uuid TEXT NOT NULL,
            family_uuid    TEXT NOT NULL,
            reason         TEXT,
            confidence     REAL,
            evidence_json  TEXT,
            PRIMARY KEY (operation_uuid, family_uuid)
        );

        CREATE INDEX IF NOT EXISTS ix_ov2w_wallet ON wt_ops_v2_wallets(wallet);
        CREATE INDEX IF NOT EXISTS ix_ov2e_to ON wt_ops_v2_edges(to_wallet);
        """
    )
    # migrate: add op_type to pre-existing wt_ops_v2 tables
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(wt_ops_v2)").fetchall()]
        if "op_type" not in cols:
            conn.execute("ALTER TABLE wt_ops_v2 ADD COLUMN op_type TEXT DEFAULT 'WATCHTOWER'")
    except Exception:
        pass
    conn.commit()


# ─────────────────────────── role mapping (PoC roles -> Phase-1 roles) ──────
def _phase1_role(poc_role: str) -> str:
    return {
        "treasury": "TREASURY",
        "collector": "COLLECTOR",
        "passthrough": "PASS_THROUGH",
    }.get(poc_role, "UNKNOWN")


# ─────────────────────────── hard-merge evidence ───────────────────────────
def _operation_infra(conn, op_uuid: str) -> set:
    """All infrastructure wallets (treasury/collector/passthrough/terminal) of an op."""
    rows = conn.execute(
        """SELECT wallet FROM wt_ops_v2_wallets
           WHERE operation_uuid=? AND role IN
           ('TREASURY','COLLECTOR','PASS_THROUGH','TERMINAL','DIRECT_FUNDER')""",
        (op_uuid,),
    ).fetchall()
    return {r[0] for r in rows}


def _find_hard_merge_target(conn, treasury_root: str, chain_infra: set) -> Optional[str]:
    """Return an existing operation_uuid this trace should MERGE into, or None.

    Merge only on hard infrastructure overlap:
      (a) same treasury_root, OR
      (b) >=1 shared collector / terminal / upstream / direct treasury edge, OR
      (c) >=3 shared infra wallets of any kind.
    Template/timing are NOT consulted here by design.
    """
    row = conn.execute(
        "SELECT operation_uuid FROM wt_ops_v2 WHERE treasury_root=?", (treasury_root,)
    ).fetchone()
    if row:
        return row[0]                       # (a) same root -> same operation

    for (op_uuid,) in conn.execute("SELECT operation_uuid FROM wt_ops_v2").fetchall():
        infra = _operation_infra(conn, op_uuid)
        shared = chain_infra & infra
        # direct treasury<->treasury edge, or shared infra wallet(s)
        if treasury_root in infra or len(shared) >= 3 or (
            shared and any(  # at least one shared COLLECTOR/TERMINAL is decisive
                conn.execute(
                    "SELECT 1 FROM wt_ops_v2_wallets WHERE operation_uuid=? AND wallet=? "
                    "AND role IN ('COLLECTOR','TERMINAL','DIRECT_FUNDER')",
                    (op_uuid, w),
                ).fetchone()
                for w in shared
            )
        ):
            return op_uuid
    return None


# ─────────────────────────── family linking (soft) ─────────────────────────
def _ensure_family(conn, now: int) -> str:
    """Get-or-create the WATCHTOWER-like playbook family. Soft grouping only."""
    row = conn.execute(
        "SELECT family_uuid FROM wt_ops_v2_families WHERE playbook_signature=?",
        (PLAYBOOK_WATCHTOWER_LIKE,),
    ).fetchone()
    if row:
        return row[0]
    fam = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO wt_ops_v2_families
           (family_uuid, family_label, playbook_signature, confidence, first_seen, last_seen, evidence_json)
           VALUES (?,?,?,?,?,?,?)""",
        (fam, "WATCHTOWER-LIKE", PLAYBOOK_WATCHTOWER_LIKE, 0.6, now, now,
         json.dumps({"basis": "1.11/ATA template + swarm sweep-recycle topology"})),
    )
    return fam


def _matches_playbook(trace: dict) -> bool:
    """Playbook match = template in the known WT library AND swarm topology
    (a collector or treasury reached). Behavioural — used for FAMILY links ONLY."""
    tmpl = trace.get("template")
    has_swarm = any(c["role"] in ("collector", "treasury") for c in trace.get("chain", []))
    return tmpl in FAMILY_TEMPLATE_BASES and has_swarm


def _link_family(conn, op_uuid: str, trace: dict, now: int):
    if not _matches_playbook(trace):
        return None
    fam = _ensure_family(conn, now)
    conn.execute(
        """INSERT OR IGNORE INTO wt_ops_v2_operation_family_links
           (operation_uuid, family_uuid, reason, confidence, evidence_json)
           VALUES (?,?,?,?,?)""",
        (op_uuid, fam, "playbook: template+swarm topology (NOT infra merge)", 0.6,
         json.dumps({"template": trace.get("template")})),
    )
    conn.execute("UPDATE wt_ops_v2 SET family_uuid=? WHERE operation_uuid=? AND family_uuid IS NULL",
                 (fam, op_uuid))
    conn.execute("UPDATE wt_ops_v2_families SET last_seen=? WHERE family_uuid=?", (now, fam))
    return fam


# ─────────────────────────── enrich: mint + block_time ─────────────────────
def _creator_mint_and_migration(live_conn, creator: str) -> tuple:
    """Local-first lookup of the creator's migrated token + on-chain migration time.
    Uses token_analysis.migrated_at (on-chain time), NEVER first_detected_at."""
    row = live_conn.execute(
        """SELECT mint, migrated_at FROM token_analysis
           WHERE (earliest_tx_creator=? OR pf_ws_creator=?) AND migrated_at IS NOT NULL
           ORDER BY migrated_at DESC LIMIT 1""",
        (creator, creator),
    ).fetchone()
    return (row[0], row[1]) if row else (None, None)


# ─────────────────────────── DISCOVER / MERGE / EXPAND ──────────────────────
SERIAL_DEPLOYER_MAX_CREATES = 3   # a WATCHTOWER creator is fresh + single-use (1 CREATE).
ATA_RENT = 2_039_280
# Empirically-validated operator taxonomy (all use the …039280 ATA-rent template, the
# discriminator is the BASE magnitude + creator token-count — a clean split, no middle):
#   WATCHTOWER     : template base >= 0.2  AND creator makes 1 token  (fresh single-use)
#   MICRO_DEPLOYER : template base ~ 0     AND creator makes 100s     (mass/volume operator)
# Both are real, valuable operator activity — we CLASSIFY, never discard.
WATCHTOWER_MIN_BASE = 0.05        # below this = micro-base deployer, not a WATCHTOWER seed


def _creator_create_count(live_conn, creator: str) -> int:
    """How many tokens this wallet has CREATEd (token_analysis). 1 = single-use creator."""
    try:
        r = live_conn.execute(
            "SELECT COUNT(*) FROM token_analysis WHERE earliest_tx_creator=? OR pf_ws_creator=?",
            (creator, creator)).fetchone()
        return r[0] if r else 0
    except Exception:
        return 0


def _template_base_of(live_conn, creator: str):
    """The ATA-template base this creator was funded with (largest template funding), or
    None if not template-funded. Base size classifies the operator type."""
    try:
        best = None
        for (a,) in live_conn.execute(
                "SELECT amount_sol FROM creator_funders WHERE creator_address=?", (creator,)).fetchall():
            lam = round(a * 1e9)
            if lam % 1_000_000 == ATA_RENT % 1_000_000:
                base = round((lam - ATA_RENT) / 1e9, 4)
                if best is None or base > best:
                    best = base
        return best
    except Exception:
        return None


def _base_of_amount(amount):
    """ATA-template base of a funding amount, or None if it lacks the …039280 tail."""
    if amount is None:
        return None
    lam = round(float(amount) * 1e9)
    if lam % 1_000_000 == ATA_RENT % 1_000_000:
        return round((lam - ATA_RENT) / 1e9, 4)
    return None


def classify_operation(live_conn, creator: str, stored_amount=None) -> str:
    """Operator type from the creator's signature. CLASSIFY (never reject) — every type
    is kept and labeled. See the taxonomy constants above.

    Token-count is the STRONGEST signal: a wallet that created many tokens is a
    MICRO_DEPLOYER regardless of template-base. For the template base, PREFER the
    already-captured stored funding amount (wt_ops_v2_creators.funding_amount_sol) over a
    live creator_funders lookup — the stored value is reliable; the live join can miss it
    (those misses caused real WATCHTOWER ops to be mislabeled UNTEMPLATED)."""
    if not creator:
        return "UNKNOWN"
    ntok = _creator_create_count(live_conn, creator)
    if ntok > SERIAL_DEPLOYER_MAX_CREATES:
        return "MICRO_DEPLOYER"                     # mass token deployer (decisive)
    base = _base_of_amount(stored_amount)           # prefer the captured amount
    if base is None:
        base = _template_base_of(live_conn, creator)  # fall back to live lookup
    if base is None:
        return "UNTEMPLATED"                       # single-token, genuinely no template
    if base < WATCHTOWER_MIN_BASE:
        return "MICRO_DEPLOYER"                     # micro-base template
    return "WATCHTOWER"                             # substantial base, single-use creator


def persist(conn, live_conn, trace: dict, source: str = "watch_migration") -> dict:
    """Persist a trace with DISCOVER/MERGE/EXPAND semantics. Returns an action report."""
    chain = trace.get("chain", [])
    if not chain:
        return {"action": "SKIP", "reason": "empty trace", "creator": trace.get("creator")}

    # CLASSIFY (do not reject). The seed creator's template-base + token-count assigns the
    # operator type. WATCHTOWER and MICRO_DEPLOYER are both ingested & kept — only the label
    # differs. (Previously serial deployers were REJECTED; now they're a labeled category.)
    _creator = trace.get("creator")
    # the trace already carries the template base; reconstruct the funding amount so
    # classify_operation can read its template base without a live lookup.
    _tmpl = trace.get("template")
    _stored_amt = (_tmpl + 0.00203928) if _tmpl is not None else None
    _op_type = classify_operation(live_conn, _creator, stored_amount=_stored_amt) if _creator else "UNKNOWN"
    # GATE: a real operation uses the …039280 template. "UNTEMPLATED" is NOT a category —
    # it means the seed isn't a templated operator at all → don't create an operation.
    # (op_type is only ever WATCHTOWER or MICRO_DEPLOYER for persisted ops.)
    if _op_type == "UNTEMPLATED":
        return {"action": "REJECT", "reason": "untemplated — not a template-mechanism operator",
                "creator": _creator}

    now = int(time.time())
    treasury = trace.get("treasury") or chain[-1]["wallet"]
    chain_infra = {c["wallet"] for c in chain}

    # NOTE: a creator→treasury resolver (resolve_true_treasury) exists and reproduces the
    # hand-verified sub-prov corrections, BUT it OVERSHOOTS on direct-fanout treasuries
    # (e.g. Cgwr: the "biggest funder" walk leaves the real chain and demotes a genuine
    # treasury). It is therefore NOT auto-applied here — it feeds the positional-resolver
    # REVIEW panel only; re-rooting is a deliberate, verified action, never automatic.

    target = _find_hard_merge_target(conn, treasury, chain_infra)
    if target:
        op_uuid, action = target, "MERGE"
        # X41.0: capture pre-merge state for the shadow ledger, BEFORE the write below.
        # This never influences target/action — _find_hard_merge_target() already decided.
        _pre_merge_row = conn.execute(
            "SELECT treasury_root, confidence, last_seen FROM wt_ops_v2 WHERE operation_uuid=?",
            (op_uuid,),
        ).fetchone()
        _pre_merge_wallet_count = conn.execute(
            "SELECT COUNT(*) FROM wt_ops_v2_wallets WHERE operation_uuid=?", (op_uuid,)
        ).fetchone()[0]
        # if this is a brand-new treasury merging via shared infra, record it as a wallet
        conn.execute(
            "UPDATE wt_ops_v2 SET last_seen=?, updated_at=? WHERE operation_uuid=?",
            (now, now, op_uuid),
        )
    else:
        op_uuid, action = str(uuid.uuid4()), "DISCOVER"
        conn.execute(
            """INSERT INTO wt_ops_v2
               (operation_uuid, treasury_root, status, confidence, first_seen, last_seen,
                created_at, updated_at, source, notes, op_type)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (op_uuid, treasury, "FORMING", _score(trace), now, now, now, now, source,
             json.dumps({"seed_creator": trace["creator"]}), _op_type),
        )

    # EXPAND: wallets (idempotent upsert, role precedence treasury>collector>passthrough)
    for c in chain:
        role = _phase1_role(c["role"])
        conn.execute(
            """INSERT INTO wt_ops_v2_wallets (operation_uuid, wallet, role, first_seen, last_seen, evidence_json)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(operation_uuid, wallet) DO UPDATE SET
                 last_seen=excluded.last_seen,
                 role=CASE WHEN excluded.role='TREASURY' THEN 'TREASURY'
                           WHEN wt_ops_v2_wallets.role='TREASURY' THEN 'TREASURY'
                           WHEN excluded.role='COLLECTOR' THEN 'COLLECTOR'
                           ELSE wt_ops_v2_wallets.role END""",
            (op_uuid, c["wallet"], role, now, now,
             json.dumps({"fanin": c["fanin"], "fanout": c["fanout"],
                         "sol_in": c["sol_in"], "sol_out": c["sol_out"]})),
        )
    # the seed creator + direct funder
    conn.execute(
        """INSERT OR IGNORE INTO wt_ops_v2_wallets (operation_uuid, wallet, role, first_seen, last_seen)
           VALUES (?,?, 'CREATOR', ?, ?)""", (op_uuid, trace["creator"], now, now))
    if chain:
        conn.execute(
            """INSERT INTO wt_ops_v2_wallets (operation_uuid, wallet, role, first_seen, last_seen)
               VALUES (?,?, 'DIRECT_FUNDER', ?, ?)
               ON CONFLICT(operation_uuid, wallet) DO NOTHING""",
            (op_uuid, chain[0]["wallet"], now, now))

    # EXPAND: edges with the REAL on-chain signature captured during the trace.
    # Each chain hop b carries the signature of the transfer from its upstream a
    # (a -> b), recorded as b['in_sig'] by the hardened trace engine. The unique key
    # is (signature, from, to, amount), so re-runs never create duplicate edges.
    for depth, (a, b) in enumerate(zip(chain, chain[1:])):
        # deterministic synthetic fallback so re-runs dedupe; never NULL amount
        # (NULL != NULL in SQLite would defeat the unique key)
        sig = b.get("in_sig") or f"trace:{a['wallet']}->{b['wallet']}"
        amt = b.get("in_sig_amt")
        amt = round(amt, 9) if amt is not None else 0.0
        bt = b.get("in_sig_ts") or a.get("last_ts")
        conn.execute(
            """INSERT OR IGNORE INTO wt_ops_v2_edges
               (operation_uuid, from_wallet, to_wallet, amount_sol, block_time, signature, edge_type, hop_depth, evidence_json)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (op_uuid, a["wallet"], b["wallet"], amt, bt, sig,
             "value_forward", depth,
             json.dumps({"onchain_sig": bool(b.get("in_sig"))})))
    # the direct_funder -> creator FUNDING edge (the real 1.11 transfer signature)
    fe = trace.get("funding_edge") or {}
    if fe.get("sig"):
        conn.execute(
            """INSERT OR IGNORE INTO wt_ops_v2_edges
               (operation_uuid, from_wallet, to_wallet, amount_sol, block_time, signature, edge_type, hop_depth, evidence_json)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (op_uuid, fe["funder"], trace["creator"], round(fe.get("amt") or 0.0, 9),
             fe.get("ts"), fe["sig"], "funding", -1,
             json.dumps({"onchain_sig": True})))

    # EXPAND: creator (with on-chain migration time + mint). INSERT OR IGNORE keeps it
    # idempotent; cur.rowcount tells us whether this was a genuinely new creator row.
    mint, migrated_at = trace.get("mint"), trace.get("migrated_at")
    if mint is None:
        mint, migrated_at = _creator_mint_and_migration(live_conn, trace["creator"])
    cur = conn.execute(
        """INSERT OR IGNORE INTO wt_ops_v2_creators
           (operation_uuid, creator_wallet, token_mint, migration_time, funding_amount_sol,
            template_base, source_creator, trace_id, evidence_json)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (op_uuid, trace["creator"], mint or "UNKNOWN", migrated_at,
         (trace["template"] + 0.00203928) if trace.get("template") else None,
         trace.get("template"), trace["creator"], str(uuid.uuid4()), None))
    creator_added = cur.rowcount > 0

    # rollups + confidence
    conn.execute(
        """UPDATE wt_ops_v2 SET last_seen=?, updated_at=?, confidence=? WHERE operation_uuid=?""",
        (now, now, _score(trace), op_uuid))

    # FAMILY link (soft, behavioural — never merges)
    fam = _link_family(conn, op_uuid, trace, now)
    conn.commit()

    nwallets = conn.execute("SELECT COUNT(*) FROM wt_ops_v2_wallets WHERE operation_uuid=?",
                            (op_uuid,)).fetchone()[0]
    # action refinement: a MERGE that added no new creator is a pure no-op re-run
    if action == "MERGE" and not creator_added:
        action = "EXPAND" if treasury else "MERGE"

    # X41.0 shadow merge-ledger dual-write — AFTER the authoritative commit above,
    # purely explanatory. Never influences action/op_uuid, which are already decided.
    if action in ("MERGE", "EXPAND"):
        _rule = _determine_merge_rule(conn, treasury, chain_infra, op_uuid)
        _record_merge_event(
            conn, event_type="HARD_MERGE" if action == "MERGE" else "TREASURY_ADDED",
            target_operation_uuid=op_uuid, affected_wallet=treasury, merge_rule=_rule,
            evidence_refs={"chain_infra": sorted(chain_infra), "creator": trace["creator"]},
            reviewer_or_rule=f"AUTOMATED:{_rule}", timestamp=now,
            previous_state=({"treasury_root": _pre_merge_row[0], "confidence": _pre_merge_row[1],
                             "last_seen": _pre_merge_row[2], "wallet_count": _pre_merge_wallet_count}
                            if _pre_merge_row else None),
            resulting_state={"operation_uuid": op_uuid, "wallet_count": nwallets,
                             "confidence": _score(trace)},
        )
    else:
        _record_merge_event(
            conn, event_type="OPERATION_CREATED", target_operation_uuid=op_uuid,
            affected_wallet=treasury, merge_rule=None,
            evidence_refs={"creator": trace["creator"], "op_type": _op_type},
            reviewer_or_rule="AUTOMATED:DISCOVER", timestamp=now,
            previous_state=None,
            resulting_state={"operation_uuid": op_uuid, "wallet_count": nwallets,
                             "confidence": _score(trace)},
        )
    if fam:
        _record_merge_event(
            conn, event_type="FAMILY_LINKED", target_operation_uuid=op_uuid,
            affected_wallet=treasury, evidence_refs={"family_uuid": fam,
                                                     "template": trace.get("template")},
            reviewer_or_rule="AUTOMATED:_link_family", timestamp=now,
        )

    return {"action": action, "operation_uuid": op_uuid, "treasury": treasury,
            "wallets": nwallets, "family": fam, "creator": trace["creator"],
            "template": trace.get("template"), "mint": mint,
            "creator_added": creator_added}


def _score(trace: dict) -> float:
    s = 0.0
    if trace.get("template") is not None:
        s += 0.4
    if any(c["role"] == "collector" for c in trace.get("chain", [])):
        s += 0.3
    if trace.get("treasury"):
        s += 0.3
    return round(s, 2)


# ─────────────────────────── intake: recent migrated WATCH creators ────────
def recent_watch_migrations(live_conn, days: int = 7, limit: int = 50,
                            watch_only: bool = False) -> list[dict]:
    """Pull recent migrated WATCH creators from the live DB.

    Binding requirements (per brief): migrated_at NOT NULL, creator + mint exist,
    a funding edge exists in creator_funders. Activity time is the on-chain
    token_analysis.migrated_at — NEVER first_detected_at.

    watch_only=True restricts to watch_candidate_tokens membership (strict, but the
    candidate builder lags ~weeks so the pool is small). Default (False) uses the
    broader migrated-with-funding pool — the true bootstrap sensor — and ORDERS so
    that WATCH-template creators surface first.
    """
    since = f"strftime('%s','now') - {int(days)}*86400"
    wt_templates = "(2.10203928,1.10203928,1.11203928,0.60703928,5.10203928)"
    join = ("JOIN watch_candidate_tokens wct ON wct.mint = ta.mint" if watch_only else "")
    creator_expr = ("wct.creator_address" if watch_only
                    else "COALESCE(ta.earliest_tx_creator, ta.pf_ws_creator)")
    sql = f"""
        SELECT DISTINCT {creator_expr} AS creator, ta.mint AS mint, ta.migrated_at AS migrated_at,
               EXISTS(SELECT 1 FROM creator_funders cf
                      WHERE cf.creator_address={creator_expr}
                        AND cf.amount_sol IN {wt_templates}) AS wt_tmpl
        FROM token_analysis ta
        {join}
        WHERE ta.migrated_at IS NOT NULL
          AND ta.migrated_at >= {since}
          AND {creator_expr} IS NOT NULL
          AND {creator_expr} IN (SELECT creator_address FROM creator_funders)
        -- TIME-FIRST ordering. Was `wt_tmpl DESC, migrated_at DESC` which sorted the
        -- known template creators to the top and could starve a NEW template creator
        -- out of the LIMIT window during a burst. migrated_at DESC guarantees the most
        -- recent migrations always surface; wt_tmpl is the tiebreaker within a timestamp.
        ORDER BY ta.migrated_at DESC, wt_tmpl DESC
        LIMIT {int(limit)}
    """
    rows = live_conn.execute(sql).fetchall()
    return [{"creator": r[0], "mint": r[1], "migrated_at": r[2], "wt_template": bool(r[3])}
            for r in rows]


# ─────────────────────────── Phase-1 runner ────────────────────────────────
def run(creators: list[str], verbose=True, dry_run=False, max_rpc=None) -> dict:
    """Trace a list of creator addresses, persist, and produce a run summary."""
    return _run_candidates([{"creator": c, "mint": None, "migrated_at": None,
                             "wt_template": None} for c in creators],
                           verbose=verbose, dry_run=dry_run, max_rpc=max_rpc)


def run_intake(days=7, limit=50, watch_only=False, verbose=True, dry_run=False,
               max_rpc=None) -> dict:
    """Automatic intake: pull recent migrated WATCH creators and feed them in."""
    live = db_connect(LIVE_DB_PATH, timeout=10)
    cands = recent_watch_migrations(live, days=days, limit=limit, watch_only=watch_only)
    live.close()
    if verbose:
        print(f"[INTAKE] {len(cands)} candidate migrated WATCH creators "
              f"(days={days} limit={limit} watch_only={watch_only} "
              f"wt_template={sum(c['wt_template'] for c in cands)})")
    return _run_candidates(cands, verbose=verbose, dry_run=dry_run, max_rpc=max_rpc)


def _run_candidates(cands: list[dict], verbose=True, dry_run=False, max_rpc=None) -> dict:
    """Core batch runner. Each candidate: skip-if-known -> trace -> persist.
    One failing trace never stops the batch."""
    import time as _t
    t0 = _t.time()
    live = db_connect(LIVE_DB_PATH, timeout=10)
    conn = db_connect(OPS_DB_PATH, timeout=30)
    ensure_schema(conn)
    cache = RPCCache(OPS_DB_PATH) if RPCCache is not None else None

    # instrument RPC usage by wrapping the page fetcher
    import src.core.operation_discovery_poc as P
    rpc_counter = {"n": 0}
    _orig_page = P._rpc_addr_txs_page
    def _counted_page(addr, before, c):
        rpc_counter["n"] += 1
        return _orig_page(addr, before, c)
    P._rpc_addr_txs_page = _counted_page

    s = {"candidates": len(cands), "skipped_known": 0, "traced_ok": 0, "trace_fail": 0,
         "discovered": 0, "merged": 0, "expanded": 0, "family_linked": 0,
         "wallets_added": 0, "edges_added": 0, "creators_added": 0, "ops": [], "failures": []}

    def _counts():
        return (
            conn.execute("SELECT COUNT(*) FROM wt_ops_v2_wallets").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM wt_ops_v2_edges").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM wt_ops_v2_creators").fetchone()[0],
        )

    try:
        for cand in cands:
            creator = cand["creator"]
            # 1. skip if this creator+token already persisted
            if cand.get("mint"):
                known = conn.execute(
                    "SELECT 1 FROM wt_ops_v2_creators WHERE creator_wallet=? AND token_mint=?",
                    (creator, cand["mint"])).fetchone()
            else:
                known = conn.execute(
                    "SELECT 1 FROM wt_ops_v2_creators WHERE creator_wallet=?", (creator,)).fetchone()
            if known:
                s["skipped_known"] += 1
                continue
            if max_rpc is not None and rpc_counter["n"] >= max_rpc:
                if verbose:
                    print(f"[INTAKE] max-rpc budget {max_rpc} reached — stopping early")
                break

            w0, e0, c0 = _counts()
            try:
                trace = discover_from_creator(creator, live, cache, verbose=False)
                trace["mint"] = cand.get("mint")
                trace["migrated_at"] = cand.get("migrated_at")
                if dry_run:
                    reached = trace.get("treasury") or (trace["chain"][-1]["wallet"] if trace["chain"] else None)
                    s["traced_ok"] += 1 if trace["chain"] else 0
                    if not trace["chain"]:
                        s["trace_fail"] += 1
                    if verbose:
                        print(f"  [dry] {creator[:10]}… -> treasury={(reached or 'NONE')[:12]}… "
                              f"template={trace.get('template')}")
                    continue
                report = persist(conn, live, trace)
            except Exception as exc:               # one failure never stops the batch
                s["trace_fail"] += 1
                s["failures"].append({"creator": creator, "error": str(exc)[:160]})
                if verbose:
                    print(f"  [FAIL] {creator[:10]}…: {str(exc)[:80]}")
                continue

            action = report.get("action")
            if action == "SKIP":
                s["skipped_known"] += 1
                continue
            s["traced_ok"] += 1
            if action == "DISCOVER":
                s["discovered"] += 1
            elif action == "MERGE":
                s["merged"] += 1
            elif action == "EXPAND":
                s["expanded"] += 1
            if report.get("family"):
                s["family_linked"] += 1
            w1, e1, c1 = _counts()
            s["wallets_added"] += (w1 - w0)
            s["edges_added"] += (e1 - e0)
            s["creators_added"] += (c1 - c0)
            s["ops"].append(report)
            if verbose:
                print(f"  {action:8s} {creator[:10]}… -> op={str(report.get('operation_uuid'))[:8]}… "
                      f"treasury={(report.get('treasury') or '-')[:10]}… "
                      f"tmpl={report.get('template')} fam={'y' if report.get('family') else 'n'}")
    finally:
        P._rpc_addr_txs_page = _orig_page         # restore
        conn.close()
        live.close()

    s["rpc_calls"] = rpc_counter["n"]
    s["runtime_s"] = round(_t.time() - t0, 1)
    return s


def _print_state():
    conn = db_connect(OPS_DB_PATH, timeout=30)
    print("\n================= wt_ops_v2 STATE =================")
    for row in conn.execute(
        """SELECT substr(operation_uuid,1,8), substr(treasury_root,1,14), status, confidence,
                  substr(COALESCE(family_uuid,'-'),1,8),
                  (SELECT COUNT(*) FROM wt_ops_v2_wallets w WHERE w.operation_uuid=o.operation_uuid),
                  (SELECT COUNT(*) FROM wt_ops_v2_creators c WHERE c.operation_uuid=o.operation_uuid)
           FROM wt_ops_v2 o ORDER BY created_at""").fetchall():
        print(f"  op={row[0]}  treasury={row[1]}…  status={row[2]}  conf={row[3]}  "
              f"family={row[4]}  wallets={row[5]}  creators={row[6]}")
    fams = conn.execute("SELECT substr(family_uuid,1,8), family_label, "
                        "(SELECT COUNT(*) FROM wt_ops_v2_operation_family_links l WHERE l.family_uuid=f.family_uuid) "
                        "FROM wt_ops_v2_families f").fetchall()
    for f in fams:
        print(f"  FAMILY {f[0]} '{f[1]}'  links={f[2]} operations")
    conn.close()


def _print_summary(s: dict, dry_run: bool):
    print("\n================= RUN SUMMARY =================")
    mode = "DRY-RUN (no writes)" if dry_run else "LIVE (wt_ops_v2 only)"
    print(f"  mode               : {mode}")
    print(f"  candidates found   : {s.get('candidates')}")
    print(f"  skipped (known)    : {s.get('skipped_known')}")
    print(f"  traced ok          : {s.get('traced_ok')}")
    print(f"  trace failures     : {s.get('trace_fail')}")
    print(f"  operations DISCOVER: {s.get('discovered')}")
    print(f"  operations MERGE   : {s.get('merged')}")
    print(f"  operations EXPAND  : {s.get('expanded')}")
    print(f"  families linked    : {s.get('family_linked')}")
    print(f"  wallets added      : {s.get('wallets_added')}")
    print(f"  edges added        : {s.get('edges_added')}")
    print(f"  creators added     : {s.get('creators_added')}")
    print(f"  RPC calls used     : {s.get('rpc_calls')}")
    print(f"  runtime            : {s.get('runtime_s')}s")
    if s.get("failures"):
        print(f"  --- {len(s['failures'])} failures (logged, did not stop batch) ---")
        for f in s["failures"][:5]:
            print(f"    {f['creator'][:12]}…: {f['error']}")


def _print_top_ops(limit=10):
    conn = db_connect(OPS_DB_PATH, timeout=30)
    print("\n================= TOP OPERATIONS =================")
    rows = conn.execute(
        """SELECT substr(o.treasury_root,1,20), o.confidence,
                  (SELECT COUNT(*) FROM wt_ops_v2_creators c WHERE c.operation_uuid=o.operation_uuid),
                  (SELECT COUNT(*) FROM wt_ops_v2_wallets w WHERE w.operation_uuid=o.operation_uuid AND w.role='COLLECTOR'),
                  datetime(o.last_seen,'unixepoch'), substr(COALESCE(o.family_uuid,'-'),1,8)
           FROM wt_ops_v2 o
           ORDER BY o.confidence DESC, 3 DESC LIMIT ?""", (limit,)).fetchall()
    print(f"  {'treasury_root':22s} {'conf':4s} {'creators':8s} {'coll':4s} {'last_seen':19s} family")
    for r in rows:
        print(f"  {r[0]:22s} {r[1]:<4} {r[2]:<8} {r[3]:<4} {r[4]:19s} {r[5]}")
    conn.close()


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Phase 1.2 — automated WATCH migration intake")
    ap.add_argument("creators", nargs="*", help="explicit creator addresses (bypasses intake)")
    ap.add_argument("--intake-watch", action="store_true", help="auto-pull recent migrated WATCH creators")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--max-rpc", type=int, default=None, help="cap total RPC calls")
    ap.add_argument("--watch-only", action="store_true",
                    help="strict watch_candidate_tokens gating (small pool; default off)")
    ap.add_argument("--dry-run", action="store_true", help="trace + report, write nothing")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    verbose = not args.quiet

    if args.intake_watch:
        s = run_intake(days=args.days, limit=args.limit, watch_only=args.watch_only,
                       verbose=verbose, dry_run=args.dry_run, max_rpc=args.max_rpc)
    elif args.creators:
        s = run(args.creators, verbose=verbose, dry_run=args.dry_run, max_rpc=args.max_rpc)
    else:                                  # back-compat: validation seeds
        s = run(["887CypXkzNVUcGfajdqCwbRpUMPkHhgx4sKg1gmWScmR",
                 "8UQ35j29pTaQab2w1bUiwXRceuyG25knqEQ8tDEUhcyi"],
                verbose=verbose, dry_run=args.dry_run, max_rpc=args.max_rpc)

    _print_summary(s, args.dry_run)
    if not args.dry_run:
        _print_top_ops()
        _print_state()


if __name__ == "__main__":
    main()
