#!/usr/bin/env python3
"""
Operation-Discovery Engine — Proof of Concept (vertical slice).

Goal (the single success test):
    Start from ONE known migrated WATCH creator and AUTOMATICALLY rediscover the
    Cgwr5FAa treasury operation — funder -> pass-through -> collector -> treasury —
    with NO prior knowledge of those wallet addresses, and persist it as a
    wt_ops_v2 candidate.

Design constraints (per the redesign brief + the decisions made):
  * HYBRID local-first / RPC-fallback. Local on-chain transfer tables are stale
    (May 4), so in practice this falls back to live Helius RPC, cached via the
    existing RPCCache. The local-first check is implemented so it activates for
    free the moment ingestion is restored.
  * PERSISTENT, parallel store. Writes ONLY to wt_ops_v2 / wt_ops_v2_wallets /
    wt_ops_v2_edges. Touches NOTHING in the live WATCH pipeline (_discover_operations,
    wt_operations, attribution, dashboard). Never DELETE+rebuild — DISCOVER / MERGE /
    EXPAND only. Operations keyed by a STABLE uuid, never by corridor_amount.
  * Treats an operation like an organisation, not a query result: once a treasury is
    found, it persists whether active or dormant.

This is a PoC, not the production engine. It proves the loop end-to-end on one case.
It is NOT wired into any worker and changes no existing behaviour when imported.

Run:
    python -m src.core.operation_discovery_poc <CREATOR_ADDRESS>
    # default seed is a known June-8 1.11 creator that traces into Cgwr5FAa
"""

from __future__ import annotations

import os
import sys
import json
import time
import uuid
import urllib.request
import urllib.error
from collections import defaultdict
from typing import Optional

from src.utils.db_locking import db_connect

try:
    from src.core.rpc_cache import RPCCache
except Exception:  # cache is an optimisation, not a hard dependency
    RPCCache = None

DB_PATH = os.environ.get(
    "FLEX_DB_PATH",
    os.path.join(os.path.dirname(__file__), "..", "..", "database", "flex_complete_database.db"),
)
# PoC writes to its OWN db file so it cannot contend with the live app's WAL lock.
# (Production would write to a wt_ops_v2 attached schema or the main db via the
# single-writer thread; the PoC stays fully isolated.)
OPS_DB_PATH = os.environ.get(
    "OPS_V2_DB_PATH",
    os.path.join(os.path.dirname(__file__), "..", "..", "database", "wt_ops_v2.db"),
)
HELIUS_API_KEY = os.environ.get("HELIUS_API_KEY", "")

# --- trace tuning -----------------------------------------------------------
MIN_SOL = 0.01            # ignore dust below this when following value
BIG_SOL = 1.0            # a hop carrying >= this is "significant value flow"
MAX_HOPS = 8             # safety bound on chain depth
SWEEP_FANIN = 20         # collector signature: inbound from >= N distinct wallets
TREASURY_FANIO = 8       # treasury signature: in/out across >= N distinct wallets
# A treasury moves CAPITAL, not just touches many wallets. Without a value floor, a
# high-frequency sweep/shuffle hub (many ~1-SOL transfers) trivially passes the fan
# threshold and gets mislabeled TREASURY (observed: 5 of 9 "treasuries" were sweep hubs).
# Real treasuries move hundreds of SOL (Cgwr 900 in / 120 median out; yUpm 911/235).
TREASURY_MIN_SOL = 50.0   # treasury must move >= this in a single direction
TREASURY_MIN_MAX_TX = 20.0  # ...and have at least one large single transfer (sub-prov load)
ATA_RENT_LAMPORTS = 2_039_280   # the funding-template tail (prior audit)


# ───────────────────────────── schema (parallel, additive) ─────────────────
def ensure_v2_schema(conn) -> None:
    """Create the persistent operation store. Idempotent. Never drops anything."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS wt_ops_v2 (
            operation_uuid   TEXT PRIMARY KEY,
            label            TEXT,                       -- human/auto name, nullable
            status           TEXT NOT NULL DEFAULT 'FORMING',
            -- FORMING | ACTIVE | EXPANDING | EXTRACTING | DORMANT | ARCHIVED
            known_family     TEXT NOT NULL DEFAULT 'UNKNOWN_OPERATOR',
            -- WATCHTOWER_FAMILY | WATCHTOWER_LIKE | UNKNOWN_OPERATOR | HIGH_CONFIDENCE
            confidence       REAL NOT NULL DEFAULT 0.0,
            first_seen       INTEGER NOT NULL,
            last_seen        INTEGER NOT NULL,
            treasury_root    TEXT,                       -- convergence wallet, if found
            creator_count    INTEGER NOT NULL DEFAULT 0,
            collector_count  INTEGER NOT NULL DEFAULT 0,
            treasury_count   INTEGER NOT NULL DEFAULT 0,
            funding_templates TEXT,                      -- json list of base amounts
            evidence_json    TEXT,
            created_at       INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            updated_at       INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );

        CREATE TABLE IF NOT EXISTS wt_ops_v2_wallets (
            operation_uuid   TEXT NOT NULL,
            wallet           TEXT NOT NULL,
            role             TEXT NOT NULL,   -- creator|funder|passthrough|collector|treasury
            first_seen       INTEGER NOT NULL,
            last_seen        INTEGER NOT NULL,
            sol_in           REAL DEFAULT 0,
            sol_out          REAL DEFAULT 0,
            fanin            INTEGER DEFAULT 0,
            fanout           INTEGER DEFAULT 0,
            PRIMARY KEY (operation_uuid, wallet, role)
        );

        CREATE TABLE IF NOT EXISTS wt_ops_v2_edges (
            operation_uuid   TEXT NOT NULL,
            src              TEXT NOT NULL,   -- upstream wallet
            dst              TEXT NOT NULL,   -- downstream wallet
            amount_sol       REAL,
            block_time       INTEGER,
            PRIMARY KEY (operation_uuid, src, dst)
        );
        CREATE INDEX IF NOT EXISTS ix_ov2_wallets_wallet ON wt_ops_v2_wallets(wallet);
        CREATE INDEX IF NOT EXISTS ix_ov2_edges_dst ON wt_ops_v2_edges(dst);
        """
    )
    conn.commit()


# ───────────────────────────── tier 1: local-first ─────────────────────────
def _local_inbound(conn, addr: str) -> list[tuple[int, str, float]]:
    """Local-first: read the freshest inbound SOL transfer to `addr` from FLEX
    transfer tables. Returns [(block_time, from_wallet, amount_sol)] or []."""
    rows = []
    try:
        cur = conn.execute(
            """SELECT block_time, sender_address, amount_sol
               FROM funder_incoming_transfers
               WHERE funder_address = ? AND amount_sol >= ?
               ORDER BY block_time ASC""",
            (addr, MIN_SOL),
        )
        rows = [(bt, frm, amt) for bt, frm, amt in cur.fetchall()]
    except Exception:
        pass
    return rows


# ───────────────────────────── tier 2: RPC-fallback ────────────────────────
def _rpc_addr_txs_page(addr: str, before: Optional[str], cache) -> list:
    """One page (<=100) of Helius enriched transactions for an address, cached."""
    if cache is not None and RPCCache is not None:
        key = RPCCache.make_key_helius_addr_txs(addr, before, 100)
        hit = cache.get(key)
        if hit is not None:
            return hit if isinstance(hit, list) else hit.get("data", [])
    url = (
        f"https://api.helius.xyz/v0/addresses/{addr}/transactions/"
        f"?api-key={HELIUS_API_KEY}&limit=100"
    )
    if before:
        url += f"&before={before}"
    data = None
    for attempt in range(4):                     # jittered backoff on 429/5xx/timeout
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "flex-op-discovery/0.1"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read().decode())
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                import time as _t, random as _r
                _t.sleep(min(8.0, 2 ** attempt) + _r.random())
                continue
            return []
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError):
            if attempt < 3:
                import time as _t, random as _r
                _t.sleep(min(8.0, 2 ** attempt) + _r.random())
                continue
            return []
    if not isinstance(data, list):
        return []
    if cache is not None and RPCCache is not None:
        try:
            cache.set(RPCCache.make_key_helius_addr_txs(addr, before, 100),
                      data, "helius_enhanced_addresses_transactions")
        except Exception:
            pass
    return data


def _rpc_addr_txs(addr: str, cache, max_pages: int = 1) -> list:
    """Helius enriched transactions, optionally paginated.

    max_pages=1 (default) keeps a cheap single-page read for the linear walk.
    Role classification calls this with a higher max_pages so a node's true fan-in
    is measured beyond the 100-tx window (a fast distributor's funding/sweep history
    is often older than its most recent page).
    """
    out, before = [], None
    for _ in range(max(1, max_pages)):
        page = _rpc_addr_txs_page(addr, before, cache)
        if not page:
            break
        out.extend(page)
        if len(page) < 100:
            break
        before = page[-1].get("signature")
        if not before:
            break
    return out


def score_wallet_role(addr: str, cache=None) -> dict:
    """Behavioural role SCORER — the right model for treasury attribution.

    No single upstream-walk rule identifies the treasury (direct-fanout treasuries like
    Cgwr/EtyBB1 are the creator's direct funder; sub-provisioned ones like 8C7p41 sit
    above infra — opposite walk directions). So instead of asking "which wallet IS the
    treasury", we score what role each wallet BEHAVES like, with reasons.

    NON-DESTRUCTIVE: returns scores + evidence only. Never re-roots. A human confirms.

    Returns: {treasury_score, sub_prov_score, collector_score, pass_through_score,
              top_role, reasons[]}  — scores in [0,1].
    """
    flows = _native_flows(_rpc_addr_txs(addr, cache, max_pages=4), addr)
    fanin  = flows.get("fanin", 0)
    fanout = flows.get("fanout", 0)
    sol_in = flows.get("sol_in", 0.0)
    sol_out = flows.get("sol_out", 0.0)
    max_tx = flows.get("max_tx", 0.0)
    # sweepback = a downstream wallet that also sends back (capital recycling) — the
    # decisive treasury signal (funds branches AND receives returns).
    out_set = set(flows.get("out_edges", {}).keys())
    in_set = set(flows.get("in_edges", {}).keys())
    sweepbacks = len(out_set & in_set)              # wallets both funded-by and funding-back

    reasons = []
    treasury = sub_prov = collector = pass_through = 0.0

    # ── TREASURY: broad fan both ways + big capital + sweepback recycling ──────
    if max_tx >= TREASURY_MIN_MAX_TX and max(sol_in, sol_out) >= TREASURY_MIN_SOL:
        treasury += 0.35; reasons.append(f"large capital (max_tx {max_tx:.0f}, vol {max(sol_in,sol_out):.0f} SOL)")
    if fanout >= 4 and fanin >= 4:
        treasury += 0.25; reasons.append(f"broad fan both ways (in {fanin}, out {fanout})")
    if sweepbacks >= 2:
        treasury += 0.30; reasons.append(f"capital recycling — {sweepbacks} sweepback wallets (fund→return)")

    # ── SUB-PROVISIONER: ~one upstream, localised downstream, no recycling ─────
    if fanin <= 2 and fanout >= 3:
        sub_prov += 0.45; reasons.append(f"single upstream, localised fanout (in {fanin}, out {fanout})")
    if sweepbacks == 0 and fanout >= 3:
        sub_prov += 0.25; reasons.append("no sweepback (provisions only, doesn't recycle)")

    # ── COLLECTOR: high fan-in, consolidates, pushes upstream ─────────────────
    if fanin >= 6 and fanout <= 3:
        collector += 0.55; reasons.append(f"high fan-in consolidation (in {fanin}, out {fanout})")

    # ── PASS-THROUGH: ~one in, ~one out (relay) ───────────────────────────────
    if fanin <= 2 and fanout <= 2:
        pass_through += 0.5; reasons.append(f"relay shape (in {fanin}, out {fanout})")

    scores = {"treasury_score": round(min(treasury, 1.0), 2),
              "sub_prov_score": round(min(sub_prov, 1.0), 2),
              "collector_score": round(min(collector, 1.0), 2),
              "pass_through_score": round(min(pass_through, 1.0), 2)}
    top_role = max(scores, key=scores.get).replace("_score", "").upper()
    return {**scores, "top_role": top_role, "reasons": reasons,
            "signals": {"fanin": fanin, "fanout": fanout, "max_tx": round(max_tx, 1),
                        "sol_in": round(sol_in, 1), "sol_out": round(sol_out, 1),
                        "sweepbacks": sweepbacks}}


def _native_flows(txs: list, addr: str):
    """Summarise native-SOL flows for `addr` from a Helius enriched-tx list.

    Also records the REAL on-chain signature for each directed edge, so the
    persistence layer can store authentic (signature, from, to, amount) keys instead
    of synthetic ones. `out_edges`/`in_edges` map counterparty -> best (sig, ts, amt).
    """
    inbound = []                      # (ts, from, amt)
    invol = defaultdict(float)        # from -> total inbound
    out = defaultdict(float)          # to -> total
    out_edges = {}                    # to -> (signature, ts, amount) of largest transfer
    in_edges = {}                     # from -> (signature, ts, amount) of largest transfer
    inset = set()
    last_ts = 0
    for t in txs:
        ts = t.get("timestamp", 0) or 0
        sig = t.get("signature")
        last_ts = max(last_ts, ts)
        for nt in t.get("nativeTransfers", []):
            amt = nt.get("amount", 0) / 1e9
            if amt < MIN_SOL:
                continue
            frm, to = nt.get("fromUserAccount"), nt.get("toUserAccount")
            if to == addr:
                inbound.append((ts, frm, amt))
                inset.add(frm)
                invol[frm] += amt
                if frm not in in_edges or amt > in_edges[frm][2]:
                    in_edges[frm] = (sig, ts, amt)
            if frm == addr:
                out[to] += amt
                if to not in out_edges or amt > out_edges[to][2]:
                    out_edges[to] = (sig, ts, amt)
    inbound.sort()
    sol_in = sum(a for _, _, a in inbound)
    sol_out = sum(out.values())
    # largest single transfer either direction — a treasury makes big sub-prov loads,
    # a sweep hub only ever moves small amounts.
    max_tx = max([a for _, _, a in inbound] + [e[2] for e in out_edges.values()] + [0.0])
    return {
        "inbound": inbound,
        "invol": dict(invol),
        "fanin": len(inset),
        "out": dict(out),
        "fanout": len(out),
        "out_edges": out_edges,
        "in_edges": in_edges,
        "last_ts": last_ts,
        "sol_in": sol_in,
        "sol_out": sol_out,
        "max_tx": max_tx,
    }


def _pick_next(flows: dict, came_from: Optional[str], seen: set) -> Optional[str]:
    """Choose the next hop toward the treasury root, never doubling back.

    The funding/treasury path is a chain of single-use forwarders carrying value in
    one direction. We follow the dominant counterparty (by SOL volume), excluding the
    node we just came from and anything already visited, so we keep moving outward
    along the value chain rather than oscillating between two adjacent wallets.
    """
    block = (seen | {came_from}) - {None}
    cand = {}
    for w, v in flows.get("invol", {}).items():
        if w not in block:
            cand[w] = cand.get(w, 0) + v
    for w, v in flows.get("out", {}).items():
        if w not in block:
            cand[w] = cand.get(w, 0) + v
    if not cand:
        return None
    return max(cand.items(), key=lambda kv: kv[1])[0]


def trace_node(conn, addr: str, cache):
    """Hybrid resolve of a node's flows. Local-first, RPC-fallback."""
    local = _local_inbound(conn, addr)
    if local:  # fresh local data available (activates once ingestion restored)
        invol = defaultdict(float)
        for _, frm, a in local:
            invol[frm] += a
        return {"inbound": local, "invol": dict(invol), "fanin": len(invol),
                "out": {}, "fanout": 0,
                "last_ts": max(bt for bt, _, _ in local), "sol_in": sum(a for _, _, a in local),
                "sol_out": 0.0, "source": "local"}
    txs = _rpc_addr_txs(addr, cache)
    flows = _native_flows(txs, addr)
    flows["source"] = "rpc"
    return flows


DEEP_PAGES = 4   # pages to pull when deep-classifying an ambiguous node


def _classify_role(conn, addr: str, flows: dict, cache):
    """Role from fan-in/out. Re-measures with pagination when the single-page view
    is ambiguous: a node moving big SOL but showing thin fan-in is almost certainly a
    collector/distributor whose sweep history is older than its latest page.

    Returns (role, flows) — flows may be the deeper, more accurate measurement.
    """
    def _is_treasury(f) -> bool:
        # fan signature AND capital signature: a treasury MOVES money (>=50 SOL in a
        # direction) with at least one large transfer (>=20 SOL sub-prov load). This
        # rejects high-frequency sweep/shuffle hubs that pass fan-count but only move
        # ~1 SOL (the 5-of-9 mislabel root cause).
        return (f["fanin"] >= TREASURY_FANIO and f["fanout"] >= TREASURY_FANIO
                and max(f.get("sol_in", 0), f.get("sol_out", 0)) >= TREASURY_MIN_SOL
                and f.get("max_tx", 0) >= TREASURY_MIN_MAX_TX)

    role = "passthrough"
    if flows["fanin"] >= SWEEP_FANIN:
        role = "collector"        # high fan-in, low value = sweep/collector (correct)
    if _is_treasury(flows):
        role = "treasury"

    # Deep re-check guards against TWO temporal traps:
    #  (a) a forwarder moving big SOL whose sweep history is older than its latest page
    #  (b) a TREASURY caught mid-QUIET-PHASE: it's only sending small (e.g. 1.11
    #      templates) in the recent window, so the single-page view shows it as a
    #      collector/passthrough and misses its large sub-prov loads. A treasury that's
    #      quiet now still moved big capital earlier — page deeper to find it before
    #      demoting. (Treasuries also send small amounts; the value floor is "EVER moved
    #      big", measured over a deep window, not "big right now".)
    big_value = max(flows["sol_in"], flows["sol_out"]) >= 100.0
    quiet_treasury_suspect = (role in ("collector", "passthrough")
                              and flows.get("source") == "rpc"
                              and flows["fanin"] >= TREASURY_FANIO
                              and flows["fanout"] >= TREASURY_FANIO)
    ambiguous = (role == "passthrough" and big_value and flows.get("source") == "rpc") \
                or quiet_treasury_suspect
    if ambiguous:
        deep_txs = _rpc_addr_txs(addr, cache, max_pages=DEEP_PAGES)
        deep = _native_flows(deep_txs, addr)
        deep["source"] = "rpc-deep"
        if _is_treasury(deep):
            return "treasury", deep
        if deep["fanin"] >= SWEEP_FANIN:
            return "collector", deep
        # still thin after paging -> it really is a forwarder
        return "passthrough", deep
    return role, flows


def _edge_signature(flows: dict, came_from: Optional[str],
                    prev_flows: Optional[dict] = None, node: Optional[str] = None):
    """Real (signature, ts, amount) of the came_from -> node transfer, if known.

    Tries this node's inbound view first; falls back to the predecessor's OUTBOUND
    view (the same transfer seen from the sender) — which is how we recover the
    signature when a distributor's inbound history is paged out of its window.
    """
    # this node's view of the edge (in or out) keyed on the predecessor
    if came_from and came_from in flows.get("in_edges", {}):
        return flows["in_edges"][came_from]
    if came_from and came_from in flows.get("out_edges", {}):
        return flows["out_edges"][came_from]
    # predecessor's view of the same transfer keyed on THIS node — needed when the
    # current node is a high-volume distributor whose edge is paged out of its window
    if prev_flows and node:
        if node in prev_flows.get("in_edges", {}):
            return prev_flows["in_edges"][node]
        if node in prev_flows.get("out_edges", {}):
            return prev_flows["out_edges"][node]
    return (None, flows.get("last_ts"), None)


# ───────────────────────────── the discovery loop ──────────────────────────
def discover_from_creator(creator: str, conn, cache, verbose=True):
    """The smallest loop: creator -> funder -> passthrough -> collector -> treasury.

    Walks upstream following the dominant inbound SOL source, classifying each node
    by its fan-in/out signature, until it reaches a convergence/treasury node.
    Returns a structured trace dict.
    """
    chain = []
    seen = set()
    template = None

    # The seed creator is a live trader: its biggest edge is trading, not funding.
    # The FUNDING edge is the ATA-template transfer (…2039280 tail). Pick that as
    # the true upstream out of hop 0, regardless of volume.
    cflow = trace_node(conn, creator, cache)
    funder = None
    for ts, frm, amt in cflow["inbound"]:
        lamports = round(amt * 1e9)
        if lamports % 1_000_000 == ATA_RENT_LAMPORTS % 1_000_000:
            template = round((lamports - ATA_RENT_LAMPORTS) / 1e9, 4)
            funder = frm
            break
    if funder is None:                      # no template inbound -> fall back to volume
        funder = cflow.get("upstream")
    if funder is None:
        return {"creator": creator, "template": None, "chain": [], "treasury": None}
    # real signature of the funder -> creator funding transfer
    fund_sig, fund_ts, fund_amt = (None, None, None)
    if funder in cflow.get("in_edges", {}):
        fund_sig, fund_ts, fund_amt = cflow["in_edges"][funder]
    node = funder                            # start the upstream walk from the funder
    came_from = creator
    prev_flows = cflow                        # predecessor's flows (for edge-sig fallback)
    seen.add(creator)

    for hop in range(MAX_HOPS):
        if node in seen:
            break
        seen.add(node)
        flows = trace_node(conn, node, cache)
        nxt = _pick_next(flows, came_from, seen)
        # classify this node (with deep pagination when the single-page view is
        # ambiguous — a high-value node showing thin fan-in is usually a distributor
        # whose sweep/fund history sits beyond the most recent 100 txs).
        role, flows = _classify_role(conn, node, flows, cache)
        # capture the REAL on-chain signature of the edge we arrived on (came_from -> node).
        # Prefer this node's inbound view; fall back to the predecessor's OUTBOUND view
        # (same transfer, seen from the sender) — needed when a distributor's inbound is
        # paged out of its recent window.
        edge_sig, edge_ts, edge_amt = _edge_signature(flows, came_from, prev_flows, node)
        chain.append({
            "wallet": node, "role": role, "fanin": flows["fanin"],
            "fanout": flows["fanout"], "sol_in": round(flows["sol_in"], 2),
            "sol_out": round(flows["sol_out"], 2), "last_ts": flows["last_ts"],
            "source": flows["source"],
            "in_sig": edge_sig, "in_sig_ts": edge_ts, "in_sig_amt": edge_amt,
        })
        if verbose:
            print(f"  hop{hop}: {node[:12]}…  role={role:11s} "
                  f"fanin={flows['fanin']:<3} fanout={flows['fanout']:<3} "
                  f"in={flows['sol_in']:.1f} out={flows['sol_out']:.1f} [{flows['source']}]")
        if role == "treasury":
            break
        if not nxt:
            break
        came_from, node, prev_flows = node, nxt, flows

    treasury = next((c["wallet"] for c in reversed(chain) if c["role"] == "treasury"), None)
    return {"creator": creator, "template": template, "chain": chain, "treasury": treasury,
            "funding_edge": {"funder": funder, "sig": fund_sig, "ts": fund_ts, "amt": fund_amt}}


# ───────────────────────────── persistence (merge, never rebuild) ──────────
def persist_operation(conn, trace: dict) -> Optional[str]:
    """DISCOVER / MERGE / EXPAND into wt_ops_v2, keyed on the treasury root.

    If an operation with this treasury already exists -> MERGE (expand wallets,
    bump last_seen). Else -> DISCOVER (new stable uuid). Never deletes.
    """
    treasury = trace.get("treasury")
    chain = trace.get("chain", [])
    if not chain:
        return None
    now = int(time.time())
    key_root = treasury or chain[-1]["wallet"]   # fall back to deepest node

    row = conn.execute(
        "SELECT operation_uuid FROM wt_ops_v2 WHERE treasury_root = ?", (key_root,)
    ).fetchone()
    if row:
        op_uuid = row[0]            # MERGE into existing operation
    else:
        op_uuid = str(uuid.uuid4()) # DISCOVER new operation
        conn.execute(
            """INSERT INTO wt_ops_v2
               (operation_uuid, status, known_family, confidence, first_seen, last_seen,
                treasury_root, funding_templates, evidence_json)
               VALUES (?, 'FORMING', 'UNKNOWN_OPERATOR', ?, ?, ?, ?, ?, ?)""",
            (op_uuid, _score(trace), now, now, key_root,
             json.dumps([trace["template"]] if trace.get("template") else []),
             json.dumps({"seed_creator": trace["creator"]})),
        )

    # MERGE wallets (idempotent upsert of each node in the chain)
    roles = defaultdict(set)
    for c in chain:
        roles[c["role"]].add(c["wallet"])
        conn.execute(
            """INSERT INTO wt_ops_v2_wallets
               (operation_uuid, wallet, role, first_seen, last_seen, sol_in, sol_out, fanin, fanout)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(operation_uuid, wallet, role) DO UPDATE SET
                 last_seen=excluded.last_seen,
                 sol_in=excluded.sol_in, sol_out=excluded.sol_out,
                 fanin=excluded.fanin, fanout=excluded.fanout""",
            (op_uuid, c["wallet"], c["role"], now, now,
             c["sol_in"], c["sol_out"], c["fanin"], c["fanout"]),
        )
    # the seed creator itself
    conn.execute(
        """INSERT OR IGNORE INTO wt_ops_v2_wallets
           (operation_uuid, wallet, role, first_seen, last_seen)
           VALUES (?,?, 'creator', ?, ?)""",
        (op_uuid, trace["creator"], now, now),
    )
    # MERGE edges
    for a, b in zip(chain, chain[1:]):
        conn.execute(
            """INSERT OR IGNORE INTO wt_ops_v2_edges
               (operation_uuid, src, dst, block_time) VALUES (?,?,?,?)""",
            (op_uuid, b["wallet"], a["wallet"], a["last_ts"]),  # src=upstream, dst=downstream
        )

    # update rollup counts + bump last_seen (EXPAND, never overwrite history)
    conn.execute(
        """UPDATE wt_ops_v2 SET
             last_seen=?,
             creator_count=(SELECT COUNT(*) FROM wt_ops_v2_wallets
                            WHERE operation_uuid=? AND role='creator'),
             collector_count=(SELECT COUNT(DISTINCT wallet) FROM wt_ops_v2_wallets
                            WHERE operation_uuid=? AND role='collector'),
             treasury_count=(SELECT COUNT(DISTINCT wallet) FROM wt_ops_v2_wallets
                            WHERE operation_uuid=? AND role='treasury'),
             confidence=?,
             updated_at=?
           WHERE operation_uuid=?""",
        (now, op_uuid, op_uuid, op_uuid, _score(trace), now, op_uuid),
    )
    conn.commit()
    return op_uuid


def _score(trace: dict) -> float:
    """Cheap PoC confidence: rewards reaching a treasury + a known template tail."""
    s = 0.0
    if trace.get("template") is not None:
        s += 0.4
    if any(c["role"] == "collector" for c in trace.get("chain", [])):
        s += 0.3
    if trace.get("treasury"):
        s += 0.3
    return round(s, 2)


# ───────────────────────────── entry point ─────────────────────────────────
def main():
    seed = sys.argv[1] if len(sys.argv) > 1 else \
        "887CypXkzNVUcGfajdqCwbRpUMPkHhgx4sKg1gmWScmR"   # known June-8 1.11 creator
    # read-only handle on the live DB (local-first tier); isolated writer for ops v2
    live = db_connect(DB_PATH, timeout=10)
    conn = db_connect(OPS_DB_PATH, timeout=30)
    ensure_v2_schema(conn)
    cache = RPCCache(OPS_DB_PATH) if RPCCache is not None else None

    print(f"\n[OP-DISCOVERY-POC] seed creator: {seed}")
    trace = discover_from_creator(seed, live, cache)
    print(f"\n  template tail: {trace['template']}   treasury: "
          f"{(trace['treasury'] or 'NOT REACHED')[:24]}…")
    op_uuid = persist_operation(conn, trace)
    print(f"  persisted operation_uuid: {op_uuid}")
    if op_uuid is None:
        print("  -> trace produced no chain; nothing persisted.")
        conn.close()
        return

    # show the persisted operation
    op = conn.execute("SELECT status, known_family, confidence, treasury_root, "
                      "creator_count, collector_count, treasury_count "
                      "FROM wt_ops_v2 WHERE operation_uuid=?", (op_uuid,)).fetchone()
    print(f"  -> status={op[0]} family={op[1]} conf={op[2]} "
          f"treasury={op[3][:12]}… creators={op[4]} collectors={op[5]} treasuries={op[6]}")
    nwallets = conn.execute("SELECT COUNT(*) FROM wt_ops_v2_wallets WHERE operation_uuid=?",
                            (op_uuid,)).fetchone()[0]
    print(f"  -> {nwallets} wallets stored in operation graph")
    conn.close()


if __name__ == "__main__":
    main()
