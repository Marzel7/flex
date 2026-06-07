"""
watchtower_reservoir.py — track relay-funded DORMANT wallets and measure whether
they convert to launchers.

Hypothesis under test (NOT yet proven): profit-relays seed wallets that later
become creators, i.e. PROFIT_RELAY → dormant wallet → future launch. As of the
baseline snapshot: 71 relay-funded wallets, 0 launched — so conversion rate is
UNKNOWN. This table makes the hypothesis falsifiable: re-checking conversion over
days/weeks yields a real conversion % (or proves relay-funding is not a reservoir).

Deliberately NOT called "pre-launch creators" — the evidence supports "relay
funded", not "future creator", until conversion is demonstrated.

Status lifecycle: DORMANT → LAUNCHED (created/migrated a token) | EXPIRED (aged
out with no launch).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time

# Profit-relay funders that seed the reservoir.
PROFIT_RELAYS = {
    "4LpEjcq3PwkE9Hwt1xLdYHCxyNYB13wEahUPCRkzZa9Q": "PROFIT-RELAY-1",
    "7UyCwmSUcG7utdSPikn5caL9QwbEnAs1aDcbdWvGs37A": "PROFIT-RELAY-2",
    "N3TKf3wMBNu8XmZsTSnk2xWQ2LjiGvUJh1ae9Lc3dW7": "PROFIT-RELAY-3",
    "5GZvPqYggF9HS59xBazaTVogMGyCmdMV3sE4oWzJv5Y7": "PROFIT-RELAY-4",
}
EXPIRE_DAYS = 30  # dormant > this with no launch → EXPIRED (relay-funding wasn't a reservoir)


SCHEMA = """
CREATE TABLE IF NOT EXISTS wt_creator_reservoir (
    wallet_address      TEXT PRIMARY KEY,
    relay_funder        TEXT,
    relay_label         TEXT,
    funding_amount      REAL,
    funded_at           TEXT,            -- first relay->wallet edge (creator_funders.first_detected_at)
    first_seen          INTEGER,         -- when we added it to the reservoir
    status              TEXT NOT NULL DEFAULT 'DORMANT',  -- DORMANT | LAUNCHED | EXPIRED
    launch_detected_at  INTEGER,
    launch_token        TEXT,
    launch_creator      TEXT,
    days_since_funded   REAL,
    updated_at          INTEGER,
    priority_tier       INTEGER   -- conversion trip-wire tier: 1=2.10203928, 2=fingerprint, 3=round
);
CREATE INDEX IF NOT EXISTS idx_reservoir_status ON wt_creator_reservoir(status);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # defensive: add priority_tier to pre-existing tables
    try:
        conn.execute("ALTER TABLE wt_creator_reservoir ADD COLUMN priority_tier INTEGER")
    except Exception:
        pass
    conn.commit()


def _assign_tiers(conn: sqlite3.Connection) -> None:
    """
    Conversion trip-wire priority tiers (highest signal first):
      Tier 1 — exactly 2.10203928 SOL (OPERATION_ALPHA's canonical fingerprint)
      Tier 2 — any '…203928' fingerprint amount (ALPHA family)
      Tier 3 — round amounts (1.1 SOL bulk-staging cohort)
    """
    conn.execute("""
        UPDATE wt_creator_reservoir SET priority_tier =
          CASE
            WHEN CAST(funding_amount AS TEXT) = '2.10203928' THEN 1
            WHEN CAST(funding_amount AS TEXT) LIKE '%203928%' THEN 2
            ELSE 3
          END
        WHERE status='DORMANT'
    """)
    conn.commit()


def _funded_at_epoch(text: str | None) -> int | None:
    if not text:
        return None
    import datetime
    try:
        return int(datetime.datetime.strptime(str(text), "%Y-%m-%d %H:%M:%S")
                   .replace(tzinfo=datetime.timezone.utc).timestamp())
    except ValueError:
        return None


def populate(conn: sqlite3.Connection) -> dict:
    """
    Add relay-funded wallets that have NOT migrated to the reservoir as DORMANT.
    Idempotent — existing rows are left as-is (status managed by refresh()).
    """
    ensure_schema(conn)
    now = int(time.time())
    relays = list(PROFIT_RELAYS)
    ph = ",".join("?" * len(relays))
    added = 0
    seen_existing = {r[0] for r in conn.execute(
        "SELECT wallet_address FROM wt_creator_reservoir").fetchall()}
    for r in conn.execute(
        f"SELECT creator_address, funder_address, amount_sol, first_detected_at "
        f"FROM creator_funders WHERE funder_address IN ({ph}) "
        f"ORDER BY first_detected_at", relays,
    ).fetchall():
        wallet = r[0]
        if wallet in seen_existing:
            continue
        # only dormant (never migrated a token)
        if conn.execute(
            "SELECT 1 FROM token_analysis "
            "WHERE COALESCE(earliest_tx_creator, pf_ws_creator)=? AND migrated_at IS NOT NULL LIMIT 1",
            (wallet,)).fetchone():
            continue
        seen_existing.add(wallet)
        funded_ts = _funded_at_epoch(r[3])
        conn.execute(
            "INSERT OR IGNORE INTO wt_creator_reservoir "
            "(wallet_address, relay_funder, relay_label, funding_amount, funded_at, "
            " first_seen, status, days_since_funded, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'DORMANT', ?, ?)",
            (wallet, r[1], PROFIT_RELAYS.get(r[1], r[1]), r[2], r[3], now,
             round((now - funded_ts) / 86400, 2) if funded_ts else None, now),
        )
        added += 1
    conn.commit()
    return {"added": added, "total": len(seen_existing)}


def refresh(conn: sqlite3.Connection) -> dict:
    """
    Update reservoir statuses:
      DORMANT → LAUNCHED if the wallet has since created/migrated a token
      DORMANT → EXPIRED  if funded > EXPIRE_DAYS ago and still no launch
    Refreshes days_since_funded. Returns conversion stats. Safe to run each cycle.
    """
    ensure_schema(conn)
    now = int(time.time())
    converted = 0
    expired = 0
    for row in conn.execute(
        "SELECT wallet_address, funded_at, status FROM wt_creator_reservoir "
        "WHERE status = 'DORMANT'").fetchall():
        wallet, funded_at, _ = row
        funded_ts = _funded_at_epoch(funded_at)
        days = round((now - funded_ts) / 86400, 2) if funded_ts else None
        # Did it launch? (created/migrated any token)
        tok = conn.execute(
            "SELECT mint, migrated_at, COALESCE(earliest_tx_creator, pf_ws_creator) c "
            "FROM token_analysis "
            "WHERE COALESCE(earliest_tx_creator, pf_ws_creator)=? "
            "ORDER BY COALESCE(migrated_at, first_observed_at) LIMIT 1",
            (wallet,)).fetchone()
        if tok and tok[0]:
            conn.execute(
                "UPDATE wt_creator_reservoir SET status='LAUNCHED', launch_detected_at=?, "
                "launch_token=?, launch_creator=?, days_since_funded=?, updated_at=? "
                "WHERE wallet_address=?",
                (now, tok[0], tok[2], days, now, wallet))
            converted += 1
        elif days is not None and days > EXPIRE_DAYS:
            conn.execute(
                "UPDATE wt_creator_reservoir SET status='EXPIRED', days_since_funded=?, updated_at=? "
                "WHERE wallet_address=?", (days, now, wallet))
            expired += 1
        else:
            conn.execute(
                "UPDATE wt_creator_reservoir SET days_since_funded=?, updated_at=? "
                "WHERE wallet_address=?", (days, now, wallet))
    conn.commit()
    _assign_tiers(conn)   # keep conversion trip-wire tiers current

    counts = {r[0]: r[1] for r in conn.execute(
        "SELECT status, COUNT(*) FROM wt_creator_reservoir GROUP BY status").fetchall()}
    total = sum(counts.values())
    launched = counts.get("LAUNCHED", 0)
    return {
        "by_status": counts,
        "total": total,
        "launched": launched,
        "conversion_pct": round(launched / total * 100, 1) if total else 0.0,
        "newly_converted": converted,
        "newly_expired": expired,
    }


def stats(conn: sqlite3.Connection) -> dict:
    """Read-only summary for dashboards/reports."""
    ensure_schema(conn)
    counts = {r[0]: r[1] for r in conn.execute(
        "SELECT status, COUNT(*) FROM wt_creator_reservoir GROUP BY status").fetchall()}
    total = sum(counts.values())
    launched = counts.get("LAUNCHED", 0)
    lags = [r[0] for r in conn.execute(
        "SELECT (launch_detected_at/86400.0) - (first_seen/86400.0) "
        "FROM wt_creator_reservoir WHERE status='LAUNCHED' AND launch_detected_at IS NOT NULL"
    ).fetchall() if r[0] is not None]
    import statistics
    return {
        "by_status": counts,
        "total": total,
        "conversion_pct": round(launched / total * 100, 1) if total else 0.0,
        "median_funding_to_launch_days": round(statistics.median(lags), 2) if lags else None,
    }


def tripwire(conn: sqlite3.Connection) -> dict:
    """
    CONVERSION TRIP-WIRE — the earliest observable signal of a reservoir wave start.

    All dormant reservoir wallets currently have ZERO outbound activity. The first
    outbound transfer from any of them (which precedes token CREATE) is the trip-wire.
    Returns wallets that have moved, ordered by priority tier:
      Tier 1 — the 2.10203928 wallet (ALPHA's canonical fingerprint)
      Tier 2 — fingerprint cohort (…203928)
      Tier 3 — round 1.1 SOL bulk cohort
    Each fired wallet is a CONFIRMED WATCHTOWER lead (funded by the registered ALPHA
    hub), so a hit can auto-attribute its downstream creator/token.
    """
    ensure_schema(conn)
    fired = []
    # tier is a pure function of funding_amount — compute it in the query so the
    # READ-ONLY trip-wire never depends on a prior write having populated the column.
    tier_expr = ("CASE WHEN CAST(funding_amount AS TEXT)='2.10203928' THEN 1 "
                 "WHEN CAST(funding_amount AS TEXT) LIKE '%203928%' THEN 2 ELSE 3 END")
    rows = conn.execute(
        f"SELECT wallet_address, relay_label, funding_amount, {tier_expr} AS tier, "
        f"days_since_funded FROM wt_creator_reservoir WHERE status='DORMANT' "
        f"ORDER BY tier, funding_amount DESC").fetchall()
    has_outbound = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='creator_outgoing_transfers'"
    ).fetchone()
    for w, rl, amt, tier, age in rows:
        moved = 0
        if has_outbound:
            moved = conn.execute(
                "SELECT COUNT(*) FROM creator_outgoing_transfers WHERE creator_address=?",
                (w,)).fetchone()[0]
        if moved > 0:
            fired.append({"wallet": w, "relay": rl, "amount": amt,
                          "tier": tier, "outbound_txs": moved, "days_since_funded": age})
    return {
        "fired": fired,
        "fired_count": len(fired),
        "armed": len(rows),                 # dormant wallets still being watched
        "tier_breakdown": {t: c for t, c in conn.execute(
            f"SELECT {tier_expr} AS tier, COUNT(*) FROM wt_creator_reservoir "
            f"WHERE status='DORMANT' GROUP BY tier").fetchall()},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="database/flex_complete_database.db")
    ap.add_argument("--populate", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db, timeout=30)
    try:
        if args.populate:
            print("populate:", populate(conn))
        if args.refresh:
            print("refresh:", refresh(conn))
        print("stats:", stats(conn))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
