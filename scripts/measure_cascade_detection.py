#!/usr/bin/env python3
"""
Cascade real-time detection measurement — READ-ONLY.

Answers: of the launches that actually happened (wt_farm_launches = ground truth),
how many did the real-time WS cascade DETECT (had a cascade event), and via which tier?

Baseline before subscription-promotion (2026-06-18): 2.8% (9/322).
This helper re-measures so Phase-1 lift is visible. No classifier/subscription changes —
just measurement.

Usage:  python3 scripts/measure_cascade_detection.py [--window 24h|7d|all]
        (prints both 24h and 7d by default)
"""
import argparse
import json
import os
import sqlite3

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LIVE_DB = os.environ.get("DB_PATH", os.path.join(_REPO, "database", "flex_complete_database.db"))
OPS_DB = os.environ.get("OPS_V2_DB_PATH", os.path.join(_REPO, "database", "wt_ops_v2.db"))
BASELINE_PCT = 2.8  # 9/322, pre-promotion

WINDOWS = {"24h": 86400, "7d": 604800, "all": None}


def _ro(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def _promotable_subprovs(ops) -> set:
    """The Phase-1 promoted set (must match ws_cascade._promotable_subprovs): treasury_known
    AND a wrap-close producer. Used to attribute a detection to the discovered_promotion tier."""
    try:
        return {r[0] for r in ops.execute(
            "SELECT s.subprov FROM wt_discovered_subprovs s "
            "WHERE s.treasury_known=1 "
            "  AND s.subprov IN (SELECT subprov_wallet FROM wt_wrap_close_candidates)").fetchall()}
    except Exception:
        return set()


def measure(window_label):
    secs = WINDOWS[window_label]
    cut = f"AND created_at > strftime('%s','now')-{secs}" if secs else ""
    cut_mig = f"AND migrated_at > strftime('%s','now')-{secs}" if secs else ""

    live = _ro(LIVE_DB)
    ops = _ro(OPS_DB)
    try:
        promoted = _promotable_subprovs(ops)

        # ground truth: real launches in window
        farm_total = ops.execute(
            f"SELECT COUNT(DISTINCT mint) FROM wt_farm_launches WHERE creator IS NOT NULL {cut_mig}").fetchone()[0]
        launch_creators = {r[0] for r in ops.execute(
            f"SELECT DISTINCT creator FROM wt_farm_launches WHERE creator IS NOT NULL {cut_mig}").fetchall()}

        # creators that had ANY cascade event (detected at all)
        evented = {r[0] for r in live.execute(
            f"SELECT DISTINCT COALESCE(wallet_address,related_wallet) w FROM watchtower_events "
            f"WHERE 1=1 {cut}").fetchall()}
        detected = launch_creators & evented
        pct = (100.0 * len(detected) / farm_total) if farm_total else 0.0

        # by source: for each detected creator, find its detecting subprov (from CANDIDATE_DETECTED
        # payload) and attribute to a tier. promoted set is the durable signal; session = had an
        # active-session subprov; treasury = the rest (treasury-WS-triggered session).
        active_session_subprovs = {r[0] for r in ops.execute(
            "SELECT DISTINCT subprov_wallet FROM wt_active_subprov_sessions").fetchall()}
        by = {"discovered_promotion": 0, "active_subprov_session": 0, "treasury_session": 0, "unknown": 0}
        for cr in detected:
            rows = live.execute(
                f"SELECT payload_json FROM watchtower_events "
                f"WHERE event_type='CANDIDATE_DETECTED' AND (wallet_address=? OR related_wallet=?) {cut} "
                f"LIMIT 1", (cr, cr)).fetchall()
            sub = None
            for r in rows:
                try:
                    sub = (json.loads(r[0] or "{}")).get("subprov")
                except Exception:
                    sub = None
            if sub and sub in promoted:
                by["discovered_promotion"] += 1
            elif sub and sub in active_session_subprovs:
                by["active_subprov_session"] += 1
            elif sub:
                by["treasury_session"] += 1
            else:
                by["unknown"] += 1

        # promoted-tier activity
        promoted_active = len(promoted)
        promoted_with_events = 0
        wrap_close_seen = create_seen = launches_fired = 0
        if promoted:
            ph = ",".join("?" * len(promoted))
            pl = list(promoted)
            promoted_with_events = live.execute(
                f"SELECT COUNT(DISTINCT COALESCE(wallet_address,related_wallet)) FROM watchtower_events "
                f"WHERE (wallet_address IN ({ph}) OR related_wallet IN ({ph})) {cut}",
                pl + pl).fetchone()[0]
            wrap_close_seen = live.execute(
                f"SELECT COUNT(*) FROM watchtower_events WHERE event_type='WRAP_CLOSE_FANOUT_DETECTED' "
                f"AND (wallet_address IN ({ph}) OR related_wallet IN ({ph})) {cut}", pl + pl).fetchone()[0]
        create_seen = live.execute(
            f"SELECT COUNT(*) FROM watchtower_events WHERE event_type='CANDIDATE_DETECTED' {cut}").fetchone()[0]
        launches_fired = live.execute(
            f"SELECT COUNT(*) FROM watchtower_events WHERE event_type='WATCHTOWER_LAUNCH_DETECTED' {cut}").fetchone()[0]

        return {
            "window": window_label,
            "farm_launches_total": farm_total,
            "creators_with_any_cascade_event": len(detected),
            "real_time_detection_pct": round(pct, 2),
            "by_source": by,
            "promoted_subprovs_active": promoted_active,
            "promoted_subprovs_with_events": promoted_with_events,
            "wrap_close_seen": wrap_close_seen,
            "create_seen": create_seen,
            "launches_fired": launches_fired,
        }
    finally:
        live.close()
        ops.close()


def _print(m):
    print(f"\n══ WINDOW: {m['window']} ═══════════════════════════════════════")
    print(f"  farm_launches_total              {m['farm_launches_total']}")
    print(f"  creators_with_any_cascade_event  {m['creators_with_any_cascade_event']}")
    pct = m["real_time_detection_pct"]
    delta = pct - BASELINE_PCT
    arrow = "▲" if delta > 0 else ("▼" if delta < 0 else "=")
    print(f"  real_time_detection_pct          {pct}%   (baseline {BASELINE_PCT}%  {arrow}{abs(round(delta,2))})")
    print(f"  creators_detected_by:")
    for k, v in m["by_source"].items():
        print(f"      {k:<26} {v}")
    print(f"  promoted_subprovs_active         {m['promoted_subprovs_active']}")
    print(f"  promoted_subprovs_with_events    {m['promoted_subprovs_with_events']}")
    print(f"  wrap_close_seen (promoted)       {m['wrap_close_seen']}")
    print(f"  create_seen (all)                {m['create_seen']}")
    print(f"  launches_fired (ledger)          {m['launches_fired']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", choices=list(WINDOWS), help="single window; default prints 24h + 7d")
    args = ap.parse_args()
    wins = [args.window] if args.window else ["24h", "7d"]
    for w in wins:
        _print(measure(w))
    print()


if __name__ == "__main__":
    main()
