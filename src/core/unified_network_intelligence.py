"""
Unified Network Intelligence Layer

Joins System 1 (Networks: creator_funders → networks_release / network_membership)
and System 2 (Graph Clusters: transfer_index → farm_clusters / wallet_clusters)
into a single enrichment API.

Public API
----------
wallet_intelligence(conn, wallets)
    -> dict[wallet -> WalletContext]
    For any wallet (creator OR funder) returns all system memberships and a
    unified signal.

enrich_tokens_unified(conn, tokens)
    -> tokens enriched in-place with unified signal fields

network_cross_links(conn, network_names)
    -> dict[network_name -> {farm_clusters, coordinator_wallets, token_count}]
    For /networks page: shows which farm clusters overlap each named network.

coordinator_wallets_list(conn, limit=200)
    -> list of wallet_clusters rows enriched with network/farm context

system_freshness(conn)
    -> dict with freshness status for each pipeline
"""

import sqlite3
import time
from collections import defaultdict
from typing import Optional


# ── helpers ───────────────────────────────────────────────────────────────────

def _fmtw(addr: str) -> str:
    if not addr or len(addr) < 14:
        return addr or ''
    return addr[:8] + '…' + addr[-6:]


def _safe(val, default=None):
    return val if val is not None else default


# ── WalletContext schema ──────────────────────────────────────────────────────

def _empty_wctx() -> dict:
    return {
        # Identity
        'wallet': None,
        'token_count': 0,

        # System 1 — Networks
        'network_memberships': [],      # list of network_name strings
        'c2c_networks': [],             # creator_to_creator_networks
        'is_self_funding': False,
        'self_funding_pct': 0.0,

        # System 2 — Graph Clusters
        'farm_clusters': [],            # list of {cluster_id, role, risk_level, token_count}
        'wallet_cluster': None,         # wallet_clusters row if this wallet is a coordinator
        'is_coordinator': False,
        'coordinator_funders': [],      # wallet_clusters funders of this wallet (if creator)
        'overlap_peers': [],            # funder_overlap peers

        # Unified signal
        'signal': 'NONE',
        'signal_reason': '',
        'signal_sources': [],           # which systems contributed
    }


# ── core batch function ───────────────────────────────────────────────────────

def wallet_intelligence(
    conn: sqlite3.Connection,
    wallets: list[str],
) -> dict[str, dict]:
    """
    Return unified intelligence context for a list of wallet addresses.
    Works for creators, funders, or any wallet — caller does not need to know
    which system they belong to.
    """
    if not wallets:
        return {}

    wallets = list(dict.fromkeys(wallets))  # dedupe
    ph = ','.join('?' * len(wallets))
    result: dict[str, dict] = {}
    for w in wallets:
        ctx = _empty_wctx()
        ctx['wallet'] = w
        result[w] = ctx

    # ── 1. Token count (both creator fields) ─────────────────────────────────
    try:
        rows = conn.execute(f"""
            SELECT creator, SUM(cnt) FROM (
                SELECT earliest_tx_creator AS creator, COUNT(*) AS cnt
                FROM token_analysis WHERE earliest_tx_creator IN ({ph})
                GROUP BY earliest_tx_creator
                UNION ALL
                SELECT pf_ws_creator AS creator, COUNT(*) AS cnt
                FROM token_analysis
                WHERE pf_ws_creator IN ({ph})
                  AND (earliest_tx_creator IS NULL OR earliest_tx_creator != pf_ws_creator)
                GROUP BY pf_ws_creator
            ) GROUP BY creator
        """, wallets + wallets).fetchall()
        for r in rows:
            if r[0] in result:
                result[r[0]]['token_count'] = r[1] or 0
    except Exception:
        pass

    # ── 2. System 1: network_membership ──────────────────────────────────────
    try:
        rows = conn.execute(f"""
            SELECT creator_address, network_name
            FROM network_membership WHERE creator_address IN ({ph})
        """, wallets).fetchall()
        for r in rows:
            if r[0] in result:
                result[r[0]]['network_memberships'].append(r[1])
    except Exception:
        pass

    # ── 3. System 1: creator_to_creator_networks ─────────────────────────────
    try:
        rows = conn.execute(f"""
            SELECT creator_address, network_name
            FROM creator_to_creator_networks WHERE creator_address IN ({ph})
        """, wallets).fetchall()
        seen: dict[str, set] = defaultdict(set)
        for r in rows:
            if r[0] in result:
                seen[r[0]].add(r[1])
        for w, nets in seen.items():
            result[w]['c2c_networks'] = sorted(nets)
    except Exception:
        pass

    # ── 4. System 1: self-funding ─────────────────────────────────────────────
    try:
        rows = conn.execute(f"""
            SELECT creator_address, is_self_funding,
                   self_funding_intermediates, total_funders, self_funding_percentage
            FROM creator_self_funding WHERE creator_address IN ({ph})
        """, wallets).fetchall()
        for r in rows:
            if r[0] not in result:
                continue
            pct = float(r[4] or 0) or (
                (r[2] or 0) / max(r[3] or 1, 1) * 100
            )
            result[r[0]]['self_funding_pct'] = round(pct, 1)
            if r[1]:
                result[r[0]]['is_self_funding'] = True
    except Exception:
        pass

    # ── 5. System 2: farm_cluster_members ────────────────────────────────────
    try:
        rows = conn.execute(f"""
            SELECT m.wallet_address, m.cluster_id, m.wallet_role,
                   m.token_count, fc.risk_level, fc.farm_risk_score
            FROM farm_cluster_members m
            JOIN farm_clusters fc ON fc.cluster_id = m.cluster_id
            WHERE m.wallet_address IN ({ph})
        """, wallets).fetchall()
        for r in rows:
            if r[0] not in result:
                continue
            result[r[0]]['farm_clusters'].append({
                'cluster_id':  r[1],
                'role':        r[2],
                'token_count': r[3] or 0,
                'risk_level':  r[4],
                'risk_score':  r[5],
            })
    except Exception:
        pass

    # ── 6. System 2: wallet_clusters (this wallet IS a coordinator) ───────────
    try:
        rows = conn.execute(f"""
            SELECT funder_wallet, cluster_id, creator_count,
                   confidence_score, avg_transfer_sol, has_burst, days_active
            FROM wallet_clusters WHERE funder_wallet IN ({ph})
        """, wallets).fetchall()
        for r in rows:
            if r[0] not in result:
                continue
            result[r[0]]['is_coordinator'] = True
            result[r[0]]['wallet_cluster'] = {
                'cluster_id':      r[1],
                'creator_count':   r[2],
                'confidence':      round(r[3], 1),
                'avg_sol':         round(r[4] or 0, 3),
                'has_burst':       bool(r[5]),
                'days_active':     round(r[6] or 0, 1),
            }
    except Exception:
        pass

    # ── 7. System 2: wallet_clusters funders OF these wallets (if creator) ────
    try:
        rows = conn.execute(f"""
            SELECT cf.creator_address,
                   wc.funder_wallet, wc.cluster_id, wc.creator_count, wc.confidence_score
            FROM creator_funders cf
            JOIN wallet_clusters wc ON wc.funder_wallet = cf.funder_address
            WHERE cf.creator_address IN ({ph}) AND wc.confidence_score >= 40
            ORDER BY cf.creator_address, wc.confidence_score DESC
        """, wallets).fetchall()
        grp: dict[str, list] = defaultdict(list)
        for r in rows:
            grp[r[0]].append({
                'wallet':        r[1],
                'wallet_short':  _fmtw(r[1]),
                'cluster_id':    r[2],
                'creators_funded': r[3],
                'confidence':    round(r[4], 1),
            })
        for w, funders in grp.items():
            if w in result:
                result[w]['coordinator_funders'] = funders[:5]
    except Exception:
        pass

    # ── 8. System 2: funder_overlap peers ────────────────────────────────────
    try:
        rows = conn.execute(f"""
            SELECT cf1.creator_address,
                   cf2.creator_address AS peer,
                   fo.shared_creators, fo.coordination_level
            FROM creator_funders cf1
            JOIN funder_overlap fo
              ON fo.funder_a = cf1.funder_address OR fo.funder_b = cf1.funder_address
            JOIN creator_funders cf2
              ON cf2.funder_address = CASE
                    WHEN fo.funder_a = cf1.funder_address THEN fo.funder_b
                    ELSE fo.funder_a END
            WHERE cf1.creator_address IN ({ph})
              AND cf2.creator_address != cf1.creator_address
              AND fo.coordination_level IN ('very_strong','high','medium')
            ORDER BY cf1.creator_address, fo.shared_creators DESC
        """, wallets).fetchall()
        peer_map: dict[str, dict] = defaultdict(dict)
        for r in rows:
            if r[1] not in peer_map[r[0]]:
                peer_map[r[0]][r[1]] = {
                    'peer': r[1],
                    'peer_short': _fmtw(r[1]),
                    'shared_funders': r[2],
                    'coordination': r[3],
                }
        for w, peers in peer_map.items():
            if w in result:
                result[w]['overlap_peers'] = sorted(
                    peers.values(), key=lambda x: x['shared_funders'], reverse=True
                )[:8]
    except Exception:
        pass

    # ── 9. Compute unified signal ─────────────────────────────────────────────
    for w, ctx in result.items():
        ctx['signal'], ctx['signal_reason'], ctx['signal_sources'] = _compute_unified_signal(ctx)

    return result


def _compute_unified_signal(ctx: dict) -> tuple[str, str, list[str]]:
    """
    Unified signal combining both systems.
    Considers farm clusters, coordinator funders, network membership,
    self-funding, C2C, and overlap peers.
    """
    sources: list[str] = []

    farm_clusters = ctx.get('farm_clusters') or []
    top_farm = max(farm_clusters, key=lambda f: f.get('risk_score', 0), default=None)
    farm_risk = top_farm['risk_level'] if top_farm else None
    farm_score = top_farm['risk_score'] if top_farm else 0

    in_wc = bool(ctx.get('coordinator_funders'))
    is_coord = ctx.get('is_coordinator', False)
    overlap = ctx.get('overlap_peers') or []
    strong_overlap = [o for o in overlap if o.get('coordination') in ('very_strong', 'high')]
    network_memberships = ctx.get('network_memberships') or []
    c2c = ctx.get('c2c_networks') or []
    is_sf = ctx.get('is_self_funding', False)
    sf_pct = ctx.get('self_funding_pct', 0)

    if farm_risk in ('CRITICAL', 'HIGH'):
        sources.append('farm_cluster')
        if in_wc:
            sources.append('coordinator_funder')
        return 'HIGH', f'{farm_risk} farm cluster' + (' + coordinator funder' if in_wc else ''), sources

    if in_wc and farm_risk == 'MEDIUM':
        sources += ['farm_cluster', 'coordinator_funder']
        return 'HIGH', 'Coordinator funder + medium farm cluster', sources

    if in_wc and len(strong_overlap) >= 3:
        sources += ['coordinator_funder', 'overlap']
        return 'HIGH', f'Coordinator funder, {len(strong_overlap)} strongly overlapping creators', sources

    if farm_risk == 'MEDIUM':
        sources.append('farm_cluster')
        return 'MEDIUM', f'Medium-risk farm cluster', sources

    if in_wc and len(strong_overlap) >= 1:
        sources += ['coordinator_funder', 'overlap']
        return 'MEDIUM', 'Coordinator funder + strong peer overlap', sources

    if farm_risk == 'LOW':
        sources.append('farm_cluster')
        return 'MEDIUM', 'In LOW-risk farm cluster', sources

    if in_wc:
        sources.append('coordinator_funder')
        if network_memberships:
            sources.append('network')
            return 'MEDIUM', f'Coordinator funder + named network ({network_memberships[0]})', sources
        return 'LOW', 'Funded by coordinator wallet', sources

    if network_memberships and c2c:
        sources += ['network', 'c2c']
        return 'MEDIUM', f'Named network + C2C ({network_memberships[0]})', sources

    if len(overlap) >= 2:
        sources.append('overlap')
        return 'LOW', f'{len(overlap)} creators share funders', sources

    if network_memberships:
        sources.append('network')
        return 'LOW', f'Named network member ({network_memberships[0]})', sources

    if c2c:
        sources.append('c2c')
        return 'LOW', f'C2C network ({c2c[0]})', sources

    if is_sf and sf_pct >= 80:
        sources.append('self_funding')
        return 'LOW', f'Self-funding ({sf_pct:.0f}%)', sources

    return 'NONE', '', []


# ── token enrichment ──────────────────────────────────────────────────────────

def enrich_tokens_unified(conn: sqlite3.Connection, tokens: list[dict]) -> list[dict]:
    """
    Enrich a list of token dicts in-place using unified intelligence.
    Uses earliest_tx_creator and pf_ws_creator, prefers earliest_tx_creator.
    Adds: graph_signal, graph_signal_reason, farm_cluster_id, farm_cluster_risk,
          in_wallet_cluster, network_memberships, signal_sources.
    """
    creator_set: set[str] = set()
    for t in tokens:
        for field in ('creator', 'earliest_tx_creator', 'pf_ws_creator'):
            v = t.get(field)
            if v:
                creator_set.add(v)
    if not creator_set:
        return tokens

    ctx_map = wallet_intelligence(conn, list(creator_set))

    for t in tokens:
        creator = (
            t.get('creator') or
            t.get('earliest_tx_creator') or
            t.get('pf_ws_creator') or ''
        )
        ctx = ctx_map.get(creator, _empty_wctx())
        t['graph_signal']         = ctx['signal']
        t['graph_signal_reason']  = ctx['signal_reason']
        t['signal_sources']       = ctx.get('signal_sources', [])

        farm = next(iter(ctx.get('farm_clusters') or []), None)
        t['farm_cluster_id']   = farm['cluster_id']   if farm else None
        t['farm_cluster_risk'] = farm['risk_level']   if farm else None
        t['farm_cluster_size'] = None  # not exposed per-token
        t['in_wallet_cluster'] = bool(ctx.get('coordinator_funders'))
        t['network_memberships'] = ctx.get('network_memberships', [])

    return tokens


# ── networks page cross-links ─────────────────────────────────────────────────

def network_cross_links(conn: sqlite3.Connection, network_names: list[str]) -> dict[str, dict]:
    """
    For each named network, find farm clusters and coordinator wallets that overlap.
    All data fetched in 4 bulk queries — no per-network loops.
    """
    if not network_names:
        return {}

    result: dict[str, dict] = {n: {
        'farm_cluster_ids': [],
        'coordinator_wallets': [],
        'token_count': 0,
        'creator_count': 0,
    } for n in network_names}

    try:
        # 1. All creators across all networks in one query
        ph = ','.join('?' * len(network_names))
        rows = conn.execute(
            f"SELECT network_name, creator_address FROM network_membership WHERE network_name IN ({ph})",
            network_names
        ).fetchall()

        net_creators: dict[str, list[str]] = defaultdict(list)
        creator_to_nets: dict[str, list[str]] = defaultdict(list)
        for r in rows:
            net_creators[r[0]].append(r[1])
            creator_to_nets[r[1]].append(r[0])
            result[r[0]]['creator_count'] += 1

        all_creators = list(creator_to_nets.keys())
        if not all_creators:
            return result

        cph = ','.join('?' * len(all_creators))

        # 2. Token counts — one query, aggregate in Python
        try:
            tc_rows = conn.execute(
                f"SELECT earliest_tx_creator, COUNT(DISTINCT mint) AS cnt "
                f"FROM token_analysis WHERE earliest_tx_creator IN ({cph}) "
                f"GROUP BY earliest_tx_creator",
                all_creators
            ).fetchall()
            for r in tc_rows:
                for net in creator_to_nets.get(r[0], []):
                    result[net]['token_count'] += r[1]
        except Exception:
            pass

        # 3. Farm cluster overlaps — one query
        try:
            fc_rows = conn.execute(f"""
                SELECT DISTINCT fcm.wallet_address, fc.cluster_id, fc.risk_level
                FROM farm_cluster_members fcm
                JOIN farm_clusters fc ON fc.cluster_id = fcm.cluster_id
                WHERE fcm.wallet_address IN ({cph})
            """, all_creators).fetchall()
            # aggregate: net -> set of (cluster_id, risk_level)
            net_clusters: dict[str, dict] = defaultdict(dict)
            for r in fc_rows:
                for net in creator_to_nets.get(r[0], []):
                    net_clusters[net][r[1]] = r[2]
            for net, clusters in net_clusters.items():
                result[net]['farm_cluster_ids'] = [
                    {'cluster_id': cid, 'risk_level': rl} for cid, rl in clusters.items()
                ]
        except Exception:
            pass

        # 4. Coordinator wallets — one query, keep top-5 per network by confidence
        try:
            wc_rows = conn.execute(f"""
                SELECT DISTINCT cf.creator_address, wc.funder_wallet, wc.creator_count, wc.confidence_score
                FROM creator_funders cf
                JOIN wallet_clusters wc ON wc.funder_wallet = cf.funder_address
                WHERE cf.creator_address IN ({cph}) AND wc.confidence_score >= 60
                ORDER BY wc.confidence_score DESC
            """, all_creators).fetchall()
            net_coords: dict[str, list] = defaultdict(list)
            seen: dict[str, set] = defaultdict(set)
            for r in wc_rows:
                for net in creator_to_nets.get(r[0], []):
                    if r[1] not in seen[net] and len(net_coords[net]) < 5:
                        seen[net].add(r[1])
                        net_coords[net].append({
                            'wallet': r[1], 'wallet_short': _fmtw(r[1]),
                            'creator_count': r[2], 'confidence': round(r[3], 1)
                        })
            for net, coords in net_coords.items():
                result[net]['coordinator_wallets'] = coords
        except Exception:
            pass

    except Exception:
        pass

    return result


# ── coordinator wallets list ──────────────────────────────────────────────────

def coordinator_wallets_list(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    """
    Top coordinator wallets from wallet_clusters, enriched with:
    - named network memberships (via creator_funders → network_membership)
    - farm cluster memberships
    - token count (tokens launched by this wallet itself)
    - self-funding status
    """
    try:
        rows = conn.execute(f"""
            SELECT funder_wallet, cluster_id, creator_count, confidence_score,
                   avg_transfer_sol, has_burst, days_active,
                   first_transfer_ts, last_transfer_ts
            FROM wallet_clusters
            ORDER BY confidence_score DESC, creator_count DESC
            LIMIT {int(limit)}
        """).fetchall()
    except Exception:
        return []

    wallets = [r[0] for r in rows]
    if not wallets:
        return []

    # Batch enrichment
    ctx_map = wallet_intelligence(conn, wallets)

    result = []
    for r in rows:
        w = r[0]
        ctx = ctx_map.get(w, _empty_wctx())
        result.append({
            'wallet':          w,
            'wallet_short':    _fmtw(w),
            'cluster_id':      r[1],
            'creator_count':   r[2],
            'confidence':      round(r[3], 1),
            'avg_sol':         round(r[4] or 0, 3),
            'has_burst':       bool(r[5]),
            'days_active':     round(r[6] or 0, 1),
            'first_transfer_ts': r[7],
            'last_transfer_ts':  r[8],
            'token_count':     ctx['token_count'],
            'network_memberships': ctx['network_memberships'],
            'farm_clusters':   ctx['farm_clusters'],
            'is_self_funding': ctx['is_self_funding'],
            'self_funding_pct': ctx['self_funding_pct'],
            'signal':          ctx['signal'],
        })
    return result


# ── system freshness ──────────────────────────────────────────────────────────

def system_freshness(conn: sqlite3.Connection) -> dict:
    """
    Returns freshness status for all pipelines.
    """
    status: dict[str, dict] = {}
    now = time.time()

    # Graph analyzer runs
    try:
        row = conn.execute("""
            SELECT analyzer_name, status, finished_at, duration_seconds
            FROM analyzer_runs
            WHERE status IN ('success','failed')
            ORDER BY finished_at DESC LIMIT 1
        """).fetchone()
        if row:
            age_s = now - float(row[2]) if row[2] else None
            status['graph_analyzers'] = {
                'last_run': row[2],
                'status':   row[1],
                'age_seconds': round(age_s) if age_s else None,
                'fresh': age_s is not None and age_s < 3600,
            }
        else:
            status['graph_analyzers'] = {'status': 'never_run', 'fresh': False}
    except Exception:
        status['graph_analyzers'] = {'status': 'unknown', 'fresh': False}

    # networks_release freshness
    try:
        row = conn.execute(
            "SELECT MAX(last_built_at) FROM networks_release"
        ).fetchone()
        last_built = row[0] if row else None
        status['networks_release'] = {
            'last_built_at': last_built,
            'fresh': last_built is not None,
            'warning': 'last_built_at is NULL for all rows' if not last_built else None,
        }
    except Exception:
        status['networks_release'] = {'status': 'unknown', 'fresh': False}

    # Empty table warnings — use LIMIT 1 existence checks to avoid full scans
    empty_checks = {
        'coordinated_creator_edges': "SELECT EXISTS(SELECT 1 FROM coordinated_creator_edges LIMIT 1)",
        'creator_inbound_transfers': "SELECT EXISTS(SELECT 1 FROM creator_inbound_transfers LIMIT 1)",
    }
    for key, sql in empty_checks.items():
        try:
            exists = conn.execute(sql).fetchone()[0]
            status[key] = {'count': exists, 'warning': 'Empty — pipeline not running' if not exists else None}
        except Exception:
            status[key] = {'count': None, 'warning': 'Table missing or inaccessible'}

    return status
