"""
Relationship event logging — tracks newly discovered intelligence signals.

Called after scan-triggered rebuilds to diff DB state and emit events for
any relationships that didn't exist before the rebuild.

All operations are best-effort: errors are logged, never raised to callers.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Migration ─────────────────────────────────────────────────────────────────

_MIGRATION = Path(__file__).resolve().parent.parent.parent / \
    "database" / "migrations" / "add_intelligence_relationship_events.sql"


def apply_migration(db_path: str) -> None:
    # X78.0 -- conn.commit()/close() previously sat outside any try/finally
    # (same bug shape as intelligence_refresh.apply_migration, a DIFFERENT
    # function in a different module that was already fixed -- this one was
    # missed on the first pass because rebuild_after_scan calls this
    # module's OWN apply_migration directly, not the imported
    # intelligence_refresh one, which is separately aliased as irc_migrate).
    # Called at the very start of rebuild_after_scan, itself reachable from
    # creator_funding_worker's _post_extraction_intelligence_refresh via
    # asyncio.to_thread, on the same reused executor pool as every other
    # write in that worker.
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        for stmt in _MIGRATION.read_text().split(";"):
            stmt = stmt.strip()
            if stmt:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError as e:
                    if "already exists" not in str(e).lower():
                        logger.warning(f"[IRE] Migration: {e}")
        conn.commit()
    finally:
        conn.close()


# ── Snapshot helpers ──────────────────────────────────────────────────────────

def _snapshot_funder_upstream(conn: sqlite3.Connection) -> set[tuple]:
    """Returns set of (funder_address, upstream_address) non-excluded pairs."""
    rows = conn.execute(
        "SELECT funder_address, upstream_address FROM funder_upstream_links WHERE is_excluded=0"
    ).fetchall()
    return {(r[0], r[1]) for r in rows}


def _snapshot_network_bridges(conn: sqlite3.Connection) -> set[tuple]:
    """Returns set of (upstream_address, network_a, network_b) non-excluded bridges."""
    rows = conn.execute(
        "SELECT upstream_address, network_a, network_b FROM upstream_network_bridge WHERE is_excluded=0"
    ).fetchall()
    return {(r[0], r[1], r[2]) for r in rows}


def _snapshot_creator_second_hop(conn: sqlite3.Connection) -> set[tuple]:
    """Returns set of (creator_address, upstream_address)."""
    rows = conn.execute(
        "SELECT creator_address, upstream_address FROM creator_second_hop"
    ).fetchall()
    return {(r[0], r[1]) for r in rows}


def _snapshot_network_membership(conn: sqlite3.Connection) -> set[tuple]:
    """Returns set of (creator_address, network_name)."""
    rows = conn.execute(
        "SELECT creator_address, network_name FROM network_membership"
    ).fetchall()
    return {(r[0], r[1]) for r in rows}


def _snapshot_irc(conn: sqlite3.Connection) -> set[tuple]:
    """Returns set of (target_type, target_address) watchlist candidates."""
    rows = conn.execute(
        "SELECT target_type, target_address FROM intelligence_refresh_candidates WHERE status='watchlist'"
    ).fetchall()
    return {(r[0], r[1]) for r in rows}


def take_snapshot(db_path: str) -> dict:
    """Capture pre-rebuild counts from all tracked tables. Returns dict of sets."""
    # X78.0 -- conn.close() was only reached on success; the _snapshot_*
    # helpers are read-only (no write-lease risk), but the connection
    # handle itself still leaked on any exception. conn declared before the
    # try so finally can close it regardless of which helper raised.
    conn = None
    try:
        conn = sqlite3.connect(db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        return {
            "funder_upstream":      _snapshot_funder_upstream(conn),
            "network_bridges":      _snapshot_network_bridges(conn),
            "creator_second_hop":   _snapshot_creator_second_hop(conn),
            "network_membership":   _snapshot_network_membership(conn),
            "irc":                  _snapshot_irc(conn),
        }
    except Exception as e:
        logger.warning(f"[IRE] Snapshot failed: {e}")
        return {}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# ── Event insertion ───────────────────────────────────────────────────────────

def _insert_event(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    source_type: Optional[str],
    source_address: Optional[str],
    target_type: Optional[str],
    target_address: Optional[str],
    relationship_type: str,
    confidence_score: Optional[float] = None,
    risk_level: Optional[str] = None,
    reason_codes: Optional[list] = None,
    scan_source: Optional[str] = None,
    scan_id: Optional[str] = None,
) -> bool:
    """Insert event, ignoring duplicates (unique index on relationship_type+source+target)."""
    try:
        conn.execute("""
            INSERT OR IGNORE INTO intelligence_relationship_events
                (event_type, source_type, source_address, target_type, target_address,
                 relationship_type, confidence_score, risk_level, reason_codes,
                 scan_source, scan_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            event_type, source_type, source_address, target_type, target_address,
            relationship_type,
            confidence_score, risk_level,
            json.dumps(reason_codes) if reason_codes else None,
            scan_source, scan_id,
        ))
        return True
    except Exception as e:
        logger.debug(f"[IRE] insert_event skipped: {e}")
        return False


# ── Diff and log ──────────────────────────────────────────────────────────────

def diff_and_log(
    db_path: str,
    before: dict,
    scan_source: str = "scan_rebuild",
    scan_id: Optional[str] = None,
) -> dict:
    """
    Compare current DB state against pre-rebuild snapshot.
    Insert one event per newly discovered relationship.
    Returns summary counts.
    """
    if not before:
        return {"skipped": True, "reason": "empty snapshot"}

    counts = {
        "funder_upstream_found": 0,
        "upstream_network_bridge_found": 0,
        "creator_second_hop_found": 0,
        "creator_network_found": 0,
        "watchlist_added": 0,
    }

    # X78.0 -- conn.commit()/close() previously sat inside the try, only
    # reached on the success path; any exception from the _insert_event
    # calls below left conn (and its write lease) open for the rest of
    # this thread's life. conn declared before the try so finally can
    # close it regardless of which statement raised.
    conn = None
    try:
        conn = sqlite3.connect(db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")

        # ── funder_upstream_found ─────────────────────────────────────────────
        after_fu = _snapshot_funder_upstream(conn)
        new_fu = after_fu - before.get("funder_upstream", set())
        for funder, upstream in new_fu:
            _insert_event(
                conn,
                event_type="funder_upstream_found",
                source_type="funder", source_address=funder,
                target_type="upstream", target_address=upstream,
                relationship_type="funder_upstream_found",
                scan_source=scan_source, scan_id=scan_id,
            )
            counts["funder_upstream_found"] += 1

        # ── upstream_network_bridge_found ─────────────────────────────────────
        after_nb = _snapshot_network_bridges(conn)
        new_nb = after_nb - before.get("network_bridges", set())
        for upstream, net_a, net_b in new_nb:
            _insert_event(
                conn,
                event_type="upstream_network_bridge_found",
                source_type="upstream", source_address=upstream,
                target_type="network", target_address=f"{net_a}↔{net_b}",
                relationship_type="upstream_network_bridge_found",
                scan_source=scan_source, scan_id=scan_id,
            )
            counts["upstream_network_bridge_found"] += 1

        # ── creator_second_hop_found ──────────────────────────────────────────
        after_csh = _snapshot_creator_second_hop(conn)
        new_csh = after_csh - before.get("creator_second_hop", set())
        # Fetch confidence for new pairs
        if new_csh:
            # Build lookup: (creator, upstream) → confidence, risk, reason_codes
            placeholders = ",".join("(?,?)" for _ in new_csh)
            flat = [v for pair in new_csh for v in pair]
            details = {}
            try:
                rows = conn.execute(f"""
                    SELECT creator_address, upstream_address, confidence_score, risk_level, reason_codes
                    FROM creator_second_hop
                    WHERE (creator_address, upstream_address) IN (VALUES {placeholders})
                """, flat).fetchall()
                for r in rows:
                    details[(r[0], r[1])] = (r[2], r[3], r[4])
            except Exception:
                pass

            for creator, upstream in new_csh:
                conf, risk, rc = details.get((creator, upstream), (None, None, None))
                reasons = None
                if rc:
                    try:
                        reasons = json.loads(rc)
                    except Exception:
                        reasons = [rc]
                _insert_event(
                    conn,
                    event_type="creator_second_hop_found",
                    source_type="creator", source_address=creator,
                    target_type="upstream", target_address=upstream,
                    relationship_type="creator_second_hop_found",
                    confidence_score=conf, risk_level=risk, reason_codes=reasons,
                    scan_source=scan_source, scan_id=scan_id,
                )
                counts["creator_second_hop_found"] += 1

        # ── creator_network_found ─────────────────────────────────────────────
        after_nm = _snapshot_network_membership(conn)
        new_nm = after_nm - before.get("network_membership", set())
        for creator, network in new_nm:
            _insert_event(
                conn,
                event_type="creator_network_found",
                source_type="creator", source_address=creator,
                target_type="network", target_address=network,
                relationship_type="creator_network_found",
                scan_source=scan_source, scan_id=scan_id,
            )
            counts["creator_network_found"] += 1

        # ── watchlist_added ───────────────────────────────────────────────────
        after_irc = _snapshot_irc(conn)
        new_irc = after_irc - before.get("irc", set())
        for ttype, taddr in new_irc:
            _insert_event(
                conn,
                event_type="watchlist_added",
                source_type=ttype, source_address=taddr,
                target_type=None, target_address=None,
                relationship_type="watchlist_added",
                scan_source=scan_source, scan_id=scan_id,
            )
            counts["watchlist_added"] += 1

        conn.commit()

        total = sum(counts.values())
        logger.info(f"[IRE] diff_and_log — {total} new events: {counts}")
        return {"ok": True, "total_events": total, **counts}

    except Exception as e:
        logger.error(f"[IRE] diff_and_log failed: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# ── Post-scan rebuild ─────────────────────────────────────────────────────────

SHL_REBUILD_AFTER_SCAN = os.getenv("SHL_REBUILD_AFTER_SCAN", "true").lower() == "true"


def rebuild_after_scan(db_path: str, scan_id: Optional[str] = None, before: Optional[dict] = None) -> dict:
    """
    Called after a successful Phase 2 Lite scan.
    Snapshots state, runs lightweight rebuilds, diffs and logs new events.
    Safe to call from worker — never raises.
    """
    if not SHL_REBUILD_AFTER_SCAN:
        return {"skipped": True, "reason": "SHL_REBUILD_AFTER_SCAN=false"}

    try:
        apply_migration(db_path)
    except Exception as e:
        logger.warning(f"[IRE] Migration failed, continuing: {e}")

    t0 = time.time()
    results: dict = {}

    # 1. Snapshot before (use pre-scan snapshot if provided)
    if not before:
        before = take_snapshot(db_path)

    # 2. SecondHopExpansionBuilder
    try:
        from src.core.second_hop_builder import SecondHopExpansionBuilder
        r = SecondHopExpansionBuilder(db_path).build()
        results["second_hop"] = r.get("status", "unknown")
    except Exception as e:
        logger.warning(f"[IRE] SecondHopExpansionBuilder failed: {e}")
        results["second_hop"] = "failed"

    # 3. NetworksReleaseBuilder
    try:
        from src.utils.build_networks_release import build_networks_release
        r = build_networks_release(db_path)
        results["networks_release"] = r.get("status", "unknown")
    except Exception as e:
        logger.warning(f"[IRE] NetworksReleaseBuilder failed: {e}")
        results["networks_release"] = "failed"

    # 4. IntelligenceRefreshCandidateBuilder
    irc_started = time.time()
    irc_status = "failed"
    try:
        from src.core.intelligence_refresh import (
            IntelligenceRefreshCandidateBuilder, apply_migration as irc_migrate,
        )
        irc_migrate(db_path)
        r = IntelligenceRefreshCandidateBuilder(db_path).run()
        irc_status = r.get("status", "unknown")
        results["irc"] = irc_status
    except Exception as e:
        logger.warning(f"[IRE] IntelligenceRefreshCandidateBuilder failed: {e}")
        results["irc"] = "failed"

    # Log IRC run to analyzer_runs so "Last Build" KPI updates
    # X78.0 -- _conn.close() previously sat inside the try, only reached on
    # success; an INSERT failure left _conn (and its write lease) open for
    # the rest of this thread's life. rebuild_after_scan is called from
    # creator_funding_worker's _post_extraction_intelligence_refresh (via
    # asyncio.to_thread, same reused executor pool as every other write in
    # that worker).
    _conn = None
    try:
        import sqlite3 as _sqlite3
        _conn = _sqlite3.connect(db_path, timeout=10)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("""
            INSERT INTO analyzer_runs
                (analyzer_name, started_at, finished_at, duration_seconds, status, rows_written, created_at)
            VALUES ('IntelligenceRefreshCandidateBuilder', ?, ?, ?, ?, 0, ?)
        """, (irc_started, time.time(), round(time.time() - irc_started, 2), irc_status, time.time()))
        _conn.commit()
    except Exception as e:
        logger.warning(f"[IRE] Failed to log IRC run to analyzer_runs: {e}")
    finally:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass

    # 5. Diff and log
    event_summary = diff_and_log(db_path, before, scan_source="scan_rebuild", scan_id=scan_id)
    results["events"] = event_summary

    results["duration_seconds"] = round(time.time() - t0, 2)
    logger.info(f"[IRE] rebuild_after_scan complete: {results}")
    return results


# ── Query helpers (used by API) ───────────────────────────────────────────────

_TYPE_FILTERS = {
    "creator":   "('creator_second_hop_found','creator_network_found')",
    "funder":    "('funder_upstream_found')",
    "upstream":  "('funder_upstream_found','upstream_network_bridge_found')",
    "network":   "('upstream_network_bridge_found','creator_network_found')",
    "cluster":   "('wallet_cluster_found','farm_cluster_found')",
    "2h":        "('creator_second_hop_found','upstream_network_bridge_found')",
    "watchlist": "('watchlist_added')",
    "outbound":  "('creator_returned_funds','shared_payout_wallet_detected','creator_linked_to_upstream_hub')",
    "all":       None,
}


def get_recent_events(
    db_path: str,
    limit: int = 100,
    type_filter: str = "all",
    since_hours: int = 24,
    exclude: str = None,
) -> dict:
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row

        cutoff = int(time.time()) - (since_hours * 3600)
        today_cutoff = int(time.time()) - 86400

        type_in = _TYPE_FILTERS.get(type_filter)

        where_type = f"AND relationship_type IN {type_in}" if type_in else ""
        where_exclude = f"AND relationship_type != '{exclude}'" if exclude else ""

        events = conn.execute(f"""
            SELECT id, event_type, source_type, source_address,
                   target_type, target_address, relationship_type,
                   confidence_score, risk_level, reason_codes,
                   scan_source, scan_id, created_at
            FROM intelligence_relationship_events
            WHERE created_at >= ? {where_type} {where_exclude}
            ORDER BY created_at DESC
            LIMIT ?
        """, (cutoff, limit)).fetchall()

        # Build CEX/infra lookup
        infra_rows = conn.execute("SELECT funder_address FROM infra_funders_observed").fetchall()
        infra_set: set[str] = {r[0] for r in infra_rows}
        cex_rows = conn.execute(
            "SELECT DISTINCT funder_address FROM creator_funders WHERE is_cex=1"
        ).fetchall()
        cex_set: set[str] = {r[0] for r in cex_rows}

        # Build funder→networks lookup for enrichment
        fnm_rows = conn.execute(
            "SELECT funder_address, network_name FROM funder_network_map"
        ).fetchall()
        funder_networks: dict[str, list[str]] = {}
        for addr, net in fnm_rows:
            funder_networks.setdefault(addr, []).append(net)

        nm_rows = conn.execute(
            "SELECT creator_address, network_name FROM network_membership"
        ).fetchall()
        creator_networks: dict[str, list[str]] = {}
        for addr, net in nm_rows:
            creator_networks.setdefault(addr, []).append(net)

        def _networks_for_event(e) -> list[str]:
            rtype = e["relationship_type"]
            src = e["source_address"] or ""
            tgt = e["target_address"] or ""
            if rtype == "upstream_network_bridge_found":
                # target_address is "NetA↔NetB"
                return [p.strip() for p in tgt.replace("↔", "|").split("|") if p.strip()]
            if rtype == "creator_network_found":
                return [tgt] if tgt else []
            nets = set()
            nets.update(funder_networks.get(src, []))
            nets.update(funder_networks.get(tgt, []))
            nets.update(creator_networks.get(src, []))
            nets.update(creator_networks.get(tgt, []))
            return sorted(nets)

        def _count(rtype):
            row = conn.execute(
                "SELECT COUNT(*) FROM intelligence_relationship_events WHERE relationship_type=? AND created_at>=?",
                (rtype, today_cutoff)
            ).fetchone()
            return row[0] if row else 0

        summary = {
            "new_funder_links_today":       _count("funder_upstream_found"),
            "new_upstream_links_today":     _count("upstream_network_bridge_found"),
            "new_second_hop_creators_today":_count("creator_second_hop_found"),
            "new_network_bridges_today":    _count("upstream_network_bridge_found"),
            "new_cluster_links_today":      _count("wallet_cluster_found") + _count("farm_cluster_found"),
        }

        conn.close()

        def _cex_infra_label(addr: str) -> str | None:
            if not addr:
                return None
            if addr in infra_set:
                return "INFRA"
            if addr in cex_set:
                return "CEX"
            return None

        enriched = []
        for e in events:
            row = dict(e)
            row["networks_affected"] = _networks_for_event(e)
            row["source_label"] = _cex_infra_label(e["source_address"] or "")
            row["target_label"] = _cex_infra_label(e["target_address"] or "")
            enriched.append(row)

        return {
            "summary": summary,
            "events": enriched,
        }

    except Exception as e:
        logger.error(f"[IRE] get_recent_events failed: {e}", exc_info=True)
        return {
            "summary": {
                "new_funder_links_today": 0,
                "new_upstream_links_today": 0,
                "new_second_hop_creators_today": 0,
                "new_network_bridges_today": 0,
                "new_cluster_links_today": 0,
            },
            "events": [],
            "error": str(e),
        }
