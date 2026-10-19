"""
Network enrichment helpers.

All functions take an open sqlite3 connection and operate in read-only mode.
They use precomputed tables only — no graph recomputation at request time.

Public API
----------
enrich_creators(conn, creator_wallets)
    -> dict[wallet -> GraphContext]

graph_context_for_token(conn, mint)
    -> GraphContext | None   (resolves creator wallet internally)

farm_cluster_detail(conn, cluster_id)
    -> dict with members, funder list, creator list, stats

wallet_cluster_detail(conn, funder_wallet)
    -> dict with wallet_clusters row + overlap partners

GraphContext fields
-------------------
  farm_cluster_id        int | None
  farm_cluster_risk      str | None   (LOW/MEDIUM/HIGH/CRITICAL)
  farm_cluster_size      int | None   (total_wallets)
  farm_cluster_creators  int | None
  farm_cluster_funders   int | None
  farm_cluster_strength  float | None
  wallet_cluster_id      int | None   (wallet_clusters row for this creator if it is also a funder)
  wallet_cluster_score   float | None
  wallet_cluster_creators int | None
  in_wallet_cluster      bool         (any of this creator's funders is a high-signal coordinator)
  coordinator_funders    list[dict]   (funders present in wallet_clusters, top 5)
  overlap_creators       list[dict]   (creators sharing funders with this creator, top 8)
  signal_level           str          (HIGH / MEDIUM / LOW / NONE)
  signal_reason          str
"""

from __future__ import annotations
import json
import sqlite3
import time
from typing import Any


# ── helpers ───────────────────────────────────────────────────────────────────

def _safe_json(val) -> list:
    if not val:
        return []
    try:
        return json.loads(val) if isinstance(val, str) else (val or [])
    except Exception:
        return []


def _fmtw(addr: str) -> str:
    if not addr:
        return ''
    return addr[:8] + '…' + addr[-6:]


# ── main batch enrichment ─────────────────────────────────────────────────────

def enrich_creators(conn: sqlite3.Connection, creator_wallets: list[str]) -> dict[str, dict]:
    """
    Batch-enrich a list of creator wallet addresses.
    Returns a dict keyed by wallet address → GraphContext dict.
    Safe to call with an empty list.
    """
    if not creator_wallets:
        return {}

    wallets = list(dict.fromkeys(creator_wallets))  # dedupe, preserve order
    ph = ','.join('?' * len(wallets))

    result: dict[str, dict] = {w: _empty_context() for w in wallets}

    # ── 1. farm_cluster membership (via farm_cluster_members) ─────────────────
    try:
        rows = conn.execute(f"""
            SELECT
                m.wallet_address,
                m.cluster_id,
                m.wallet_role,
                fc.funder_count,
                fc.creator_count,
                fc.total_wallets,
                fc.farm_risk_score,
                fc.risk_level,
                fc.cluster_strength
            FROM farm_cluster_members m
            JOIN farm_clusters fc ON fc.cluster_id = m.cluster_id
            WHERE m.wallet_address IN ({ph})
              AND m.wallet_role IN ('creator', 'ambiguous')
        """, wallets).fetchall()
        for r in rows:
            w = r[0]
            if w in result:
                ctx = result[w]
                ctx['farm_cluster_id']       = r[1]
                ctx['farm_cluster_risk']     = r[7]
                ctx['farm_cluster_size']     = r[5]
                ctx['farm_cluster_creators'] = r[4]
                ctx['farm_cluster_funders']  = r[3]
                ctx['farm_cluster_strength'] = r[8]
    except Exception:
        pass

    # ── 2. wallet_clusters: is any funder of these creators a coordinator? ────
    # Join creator_funders → wallet_clusters to find high-signal funders
    try:
        rows = conn.execute(f"""
            SELECT
                cf.creator_address,
                wc.cluster_id     AS wc_id,
                wc.funder_wallet,
                wc.creator_count  AS wc_creators,
                wc.confidence_score
            FROM creator_funders cf
            JOIN wallet_clusters wc ON wc.funder_wallet = cf.funder_address
            WHERE cf.creator_address IN ({ph})
              AND wc.confidence_score >= 40
            ORDER BY cf.creator_address, wc.confidence_score DESC
        """, wallets).fetchall()

        # Group by creator
        from collections import defaultdict
        creator_funders: dict[str, list] = defaultdict(list)
        for r in rows:
            creator_funders[r[0]].append({
                'wallet': r[2],
                'wallet_short': _fmtw(r[2]),
                'cluster_id': r[1],
                'creators_funded': r[3],
                'confidence': round(r[4], 1),
            })

        for w, funders in creator_funders.items():
            if w in result:
                top = funders[:5]
                result[w]['coordinator_funders'] = top
                result[w]['in_wallet_cluster'] = True
                if top:
                    result[w]['wallet_cluster_id'] = top[0]['cluster_id']
                    result[w]['wallet_cluster_score'] = top[0]['confidence']
                    result[w]['wallet_cluster_creators'] = top[0]['creators_funded']
    except Exception:
        pass

    # ── 3. funder_overlap: related creators (share funders) ──────────────────
    # We look for creators whose funders overlap with any of our wallets' funders.
    # Join path: creator_funders → funder_overlap → creator_funders
    try:
        rows = conn.execute(f"""
            SELECT
                cf1.creator_address  AS our_creator,
                cf2.creator_address  AS peer_creator,
                fo.shared_creators,
                fo.overlap_ratio,
                fo.coordination_level
            FROM creator_funders cf1
            JOIN funder_overlap fo
              ON (fo.funder_a = cf1.funder_address OR fo.funder_b = cf1.funder_address)
            JOIN creator_funders cf2
              ON cf2.funder_address = CASE
                    WHEN fo.funder_a = cf1.funder_address THEN fo.funder_b
                    ELSE fo.funder_a
                 END
            WHERE cf1.creator_address IN ({ph})
              AND cf2.creator_address != cf1.creator_address
              AND fo.coordination_level IN ('very_strong', 'high', 'medium')
            ORDER BY cf1.creator_address, fo.shared_creators DESC
        """, wallets).fetchall()

        peer_map: dict[str, dict[str, dict]] = defaultdict(dict)
        for r in rows:
            our, peer = r[0], r[1]
            if peer not in peer_map[our]:
                peer_map[our][peer] = {
                    'creator': peer,
                    'creator_short': _fmtw(peer),
                    'shared_funders': r[2],
                    'overlap_ratio': round(r[3], 2),
                    'coordination': r[4],
                }

        for w, peers in peer_map.items():
            if w in result:
                top = sorted(peers.values(), key=lambda x: x['shared_funders'], reverse=True)[:8]
                result[w]['overlap_creators'] = top
    except Exception:
        pass

    # ── 4. System 1: network_membership (named networks) ─────────────────────
    try:
        rows = conn.execute(f"""
            SELECT creator_address, network_name
            FROM network_membership WHERE creator_address IN ({ph})
        """, wallets).fetchall()
        for r in rows:
            if r[0] in result:
                result[r[0]].setdefault('network_memberships', []).append(r[1])
    except Exception:
        pass

    # ── 5. compute signal_level for each ─────────────────────────────────────
    for w, ctx in result.items():
        ctx['signal_level'], ctx['signal_reason'] = _compute_signal(ctx)

    return result


def _empty_context() -> dict:
    return {
        'farm_cluster_id': None,
        'farm_cluster_risk': None,
        'farm_cluster_size': None,
        'farm_cluster_creators': None,
        'farm_cluster_funders': None,
        'farm_cluster_strength': None,
        'wallet_cluster_id': None,
        'wallet_cluster_score': None,
        'wallet_cluster_creators': None,
        'in_wallet_cluster': False,
        'coordinator_funders': [],
        'overlap_creators': [],
        'network_memberships': [],
        'signal_level': 'NONE',
        'signal_reason': '',
    }


def _compute_signal(ctx: dict) -> tuple[str, str]:
    farm_risk = ctx.get('farm_cluster_risk') or ''
    farm_size = ctx.get('farm_cluster_creators') or 0
    in_wc     = ctx.get('in_wallet_cluster', False)
    overlap   = ctx.get('overlap_creators') or []
    networks  = ctx.get('network_memberships') or []
    strong_overlap = [o for o in overlap if o.get('coordination') in ('very_strong', 'high')]

    if farm_risk in ('CRITICAL', 'HIGH') and farm_size >= 10:
        return 'HIGH', f'Part of {farm_risk} farm cluster ({farm_size} creators)'
    if farm_risk in ('CRITICAL', 'HIGH'):
        return 'HIGH', f'In {farm_risk} farm cluster'
    if in_wc and farm_risk == 'MEDIUM':
        return 'HIGH', 'Coordinator funder + medium farm cluster'
    if in_wc and len(strong_overlap) >= 3:
        return 'HIGH', f'Coordinator funder, {len(strong_overlap)} strongly overlapping creators'
    if farm_risk == 'MEDIUM' or (in_wc and len(strong_overlap) >= 1):
        return 'MEDIUM', 'Medium farm cluster or coordinator funder overlap'
    if ctx.get('farm_cluster_id') is not None:
        return 'MEDIUM', 'In LOW-risk farm cluster'
    if len(overlap) >= 2:
        return 'LOW', f'{len(overlap)} creators share funders'
    if in_wc:
        net_suffix = f' ({networks[0]})' if networks else ''
        return 'LOW', f'Funded by a coordinator wallet{net_suffix}'
    if networks:
        return 'LOW', f'Named network member ({networks[0]})'
    return 'NONE', ''


# ── single-token convenience ──────────────────────────────────────────────────

def graph_context_for_token(conn: sqlite3.Connection, mint: str) -> dict | None:
    """Look up creator for a mint, then return graph context."""
    try:
        row = conn.execute(
            "SELECT earliest_tx_creator FROM token_analysis WHERE mint = ?", (mint,)
        ).fetchone()
        creator = row[0] if row and row[0] else None
        if not creator:
            return None
        result = enrich_creators(conn, [creator])
        ctx = result.get(creator, _empty_context())
        ctx['creator_wallet'] = creator
        return ctx
    except Exception:
        return None


# ── farm cluster detail ───────────────────────────────────────────────────────

def farm_cluster_detail(conn: sqlite3.Connection, cluster_id: int) -> dict | None:
    """Full detail for a farm cluster including member list."""
    try:
        row = conn.execute(
            "SELECT * FROM farm_clusters WHERE cluster_id = ?", (cluster_id,)
        ).fetchone()
        if not row:
            return None

        cols = [d[0] for d in conn.execute(
            "SELECT * FROM farm_clusters LIMIT 0"
        ).description]
        # Re-fetch with Row factory
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM farm_clusters WHERE cluster_id = ?", (cluster_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d['funder_list']  = _safe_json(d.get('funder_list'))
        d['creator_list'] = _safe_json(d.get('creator_list'))
        d['all_wallets']  = _safe_json(d.get('all_wallets'))
        # Strip any BLOB/bytes values that can't be JSON-serialised
        d = {k: (None if isinstance(v, (bytes, bytearray)) else v) for k, v in d.items()}

        # Members
        members = conn.execute("""
            SELECT wallet_address, wallet_role, in_degree, out_degree,
                   total_sent_sol, total_received_sol, role_confidence
            FROM farm_cluster_members
            WHERE cluster_id = ?
            ORDER BY wallet_role, total_sent_sol DESC
        """, (cluster_id,)).fetchall()
        d['members'] = [dict(m) for m in members]

        return d
    except Exception:
        return None


# ── wallet cluster detail ─────────────────────────────────────────────────────

def wallet_cluster_detail(conn: sqlite3.Connection, funder_wallet: str) -> dict | None:
    """wallet_clusters row + top overlap partners for a funder wallet."""
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM wallet_clusters WHERE funder_wallet = ?", (funder_wallet,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d['creator_addresses'] = _safe_json(d.get('creator_addresses'))

        # Overlap partners
        overlaps = conn.execute("""
            SELECT
                CASE WHEN funder_a = ? THEN funder_b ELSE funder_a END AS partner,
                shared_creators, overlap_ratio, coordination_level
            FROM funder_overlap
            WHERE funder_a = ? OR funder_b = ?
            ORDER BY shared_creators DESC
            LIMIT 10
        """, (funder_wallet, funder_wallet, funder_wallet)).fetchall()
        d['overlap_partners'] = [dict(o) for o in overlaps]

        return d
    except Exception:
        return None


# ── batch signal for token list ───────────────────────────────────────────────

def batch_signal_for_tokens(conn: sqlite3.Connection, tokens: list[dict]) -> list[dict]:
    """
    Given a list of token dicts (each with a 'creator' or 'earliest_tx_creator' key),
    add 'graph_signal', 'graph_signal_reason', 'farm_cluster_id', 'farm_cluster_risk'
    to each token dict in-place. Returns the same list.
    """
    wallets = list({
        t.get('creator') or t.get('earliest_tx_creator') or ''
        for t in tokens
    } - {''})

    if not wallets:
        return tokens

    ctx_map = enrich_creators(conn, wallets)

    for t in tokens:
        creator = t.get('creator') or t.get('earliest_tx_creator') or ''
        ctx = ctx_map.get(creator, _empty_context())
        t['graph_signal']        = ctx['signal_level']
        t['graph_signal_reason'] = ctx['signal_reason']
        t['farm_cluster_id']     = ctx['farm_cluster_id']
        t['farm_cluster_risk']   = ctx['farm_cluster_risk']
        t['farm_cluster_size']   = ctx['farm_cluster_creators']
        t['in_wallet_cluster']   = ctx['in_wallet_cluster']

    return tokens


# ── creator-analysis tags ─────────────────────────────────────────────────────

def batch_creator_tags(conn: sqlite3.Connection, wallets: list[str]) -> dict[str, dict]:
    """
    Return creator-analysis tags for a list of wallets using precomputed tables.
    No graph recomputation — read-only joins only.

    Returns dict[wallet -> {
        tags: list[str],           # e.g. ['SELF-FUNDING', 'C2C_NETWORK']
        is_self_funding: bool,
        self_funding_pct: float,
        c2c_networks: list[str],
        is_coordinated: bool,
    }]
    """
    if not wallets:
        return {}

    ph = ','.join('?' * len(wallets))
    result: dict[str, dict] = {w: {
        'tags': [], 'is_self_funding': False, 'self_funding_pct': 0.0,
        'c2c_networks': [], 'is_coordinated': False,
    } for w in wallets}

    # Self-funding
    try:
        rows = conn.execute(f"""
            SELECT creator_address, is_self_funding, self_funding_intermediates,
                   total_funders, self_funding_percentage
            FROM creator_self_funding WHERE creator_address IN ({ph})
        """, wallets).fetchall()
        for r in rows:
            addr, is_sf, intermediates, total, pct_col = r[0], r[1], r[2], r[3], r[4]
            if addr not in result:
                continue
            pct = float(pct_col or 0) or ((intermediates or 0) / max(total or 1, 1) * 100)
            result[addr]['self_funding_pct'] = round(pct, 1)
            if is_sf:
                result[addr]['is_self_funding'] = True
                result[addr]['tags'].append(f'SELF-FUNDING ({pct:.0f}%)')
    except Exception:
        pass

    # C2C networks
    try:
        rows = conn.execute(f"""
            SELECT creator_address, network_name
            FROM creator_to_creator_networks WHERE creator_address IN ({ph})
        """, wallets).fetchall()
        seen: dict[str, set] = {}
        for r in rows:
            addr, net = r[0], r[1]
            if addr in result:
                seen.setdefault(addr, set()).add(net)
        for addr, nets in seen.items():
            result[addr]['c2c_networks'] = sorted(nets)
            result[addr]['tags'].append('C2C_NETWORK')
    except Exception:
        pass

    # Coordinated edges
    try:
        rows = conn.execute(f"""
            SELECT creator_a FROM coordinated_creator_edges WHERE creator_a IN ({ph})
            UNION
            SELECT creator_b FROM coordinated_creator_edges WHERE creator_b IN ({ph})
        """, wallets + wallets).fetchall()
        for r in rows:
            addr = r[0]
            if addr in result:
                result[addr]['is_coordinated'] = True
                if 'COORDINATED' not in result[addr]['tags']:
                    result[addr]['tags'].append('COORDINATED')
    except Exception:
        pass

    # CLEAN fallback
    for ctx in result.values():
        if not ctx['tags']:
            ctx['tags'].append('CLEAN')

    return result
