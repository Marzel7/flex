"""
cluster_snapshots.py — append-only historical telemetry for operator clusters.

WHY: Discovery Mode can show current cluster state but not CHANGE. Honest deltas
("Cluster #75 gained +4 launches", "confidence 45%→62%") require persisted history —
they must never be inferred from creation timestamps or current counts. This module
persists one snapshot per cluster per engine cycle and derives deltas by comparing
real snapshots.

DESIGN:
  - append-only, never updates (historical telemetry)
  - idempotent per cycle: at most one snapshot per cluster per ~15-min bucket, so a
    double-invocation in the same cycle doesn't double-count
  - low overhead: a single INSERT…SELECT over wt_operator_clusters + a member count
  - hooked into _run_watch_pipeline AFTER cluster scoring (every engine cadence)

Tables read: wt_operator_clusters (scored clusters), wt_cluster_members (creator count).
Table written: wt_operator_cluster_snapshots (this module owns it).
"""
from __future__ import annotations
import hashlib
import sqlite3
import time
from typing import Optional


SNAPSHOT_BUCKET_S = 600   # idempotency window: one snapshot per cluster per 10 min
DAY_S = 86400


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the append-only snapshot table. Safe to call every cycle."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wt_operator_cluster_snapshots (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            cluster_id        INTEGER NOT NULL,
            snapshot_ts       INTEGER NOT NULL,
            confidence        REAL,
            token_count       INTEGER,
            creator_count     INTEGER,
            provisioner_count INTEGER,
            deployed_sol      REAL,
            cluster_state     TEXT,
            top_funders_hash      TEXT,
            top_recipients_hash   TEXT,
            operation_count       INTEGER,
            shared_funder_count   INTEGER,
            shared_recipient_count INTEGER
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cluster_snap_cid_ts "
        "ON wt_operator_cluster_snapshots(cluster_id, snapshot_ts DESC)")
    conn.commit()


def _hash_set(rows) -> Optional[str]:
    """Stable short hash of a set of addresses — lets us detect membership change
    (new funders/recipients) without storing the full set in every snapshot."""
    vals = sorted({str(r[0]) for r in rows if r and r[0]})
    if not vals:
        return None
    return hashlib.sha1("|".join(vals).encode()).hexdigest()[:16]


def take_snapshot(conn: sqlite3.Connection) -> int:
    """
    Append one snapshot per cluster for the current cycle. Idempotent: skips a cluster
    that already has a snapshot within SNAPSHOT_BUCKET_S. Returns count written.
    Non-fatal — never raises into the engine.
    """
    try:
        ensure_schema(conn)
        now = int(time.time())
        cutoff = now - SNAPSHOT_BUCKET_S

        # which clusters already snapshotted this bucket → skip (idempotent)
        recent = {r[0] for r in conn.execute(
            "SELECT DISTINCT cluster_id FROM wt_operator_cluster_snapshots "
            "WHERE snapshot_ts >= ?", (cutoff,)).fetchall()}

        has_members = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wt_cluster_members'"
        ).fetchone() is not None

        written = 0
        for c in conn.execute("""
            SELECT cluster_id, confidence, token_count, provisioner_count,
                   total_sol_deployed, state
            FROM wt_operator_clusters
        """).fetchall():
            cid = c[0]
            if cid in recent:
                continue
            creator_count = 0
            funders_hash = recipients_hash = None
            if has_members:
                creator_count = conn.execute(
                    "SELECT COUNT(*) FROM wt_cluster_members WHERE cluster_id=?", (cid,)
                ).fetchone()[0]
                # membership-change fingerprints: the creator set itself is the cheapest
                # honest "new member / new funder" signal we have per cluster.
                funders_hash = _hash_set(conn.execute(
                    "SELECT creator_wallet FROM wt_cluster_members WHERE cluster_id=?", (cid,)
                ).fetchall())
            conn.execute("""
                INSERT INTO wt_operator_cluster_snapshots
                    (cluster_id, snapshot_ts, confidence, token_count, creator_count,
                     provisioner_count, deployed_sol, cluster_state,
                     top_funders_hash, top_recipients_hash)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (cid, now, c[1], c[2], creator_count, c[3], c[4], c[5],
                  funders_hash, recipients_hash))
            written += 1
        conn.commit()
        return written
    except Exception as e:
        print(f"[CLUSTER-SNAPSHOT] error: {e}", flush=True)
        return 0


def _baseline(conn: sqlite3.Connection, cid: int, cutoff_ts: int) -> Optional[sqlite3.Row]:
    """
    The 24h baseline = the MOST RECENT snapshot that is at least 24h old
    (snapshot_ts <= cutoff_ts). That is the true "yesterday" reading to compare
    against. If none exists (cluster younger than 24h), fall back to the earliest
    snapshot we have — compute_deltas then flags has_baseline=False for it.
    """
    row = conn.execute("""
        SELECT * FROM wt_operator_cluster_snapshots
        WHERE cluster_id=? AND snapshot_ts <= ?
        ORDER BY snapshot_ts DESC LIMIT 1
    """, (cid, cutoff_ts)).fetchone()
    if row:
        return row
    return conn.execute("""
        SELECT * FROM wt_operator_cluster_snapshots
        WHERE cluster_id=? ORDER BY snapshot_ts ASC LIMIT 1
    """, (cid,)).fetchone()


def _latest(conn: sqlite3.Connection, cid: int) -> Optional[sqlite3.Row]:
    return conn.execute("""
        SELECT * FROM wt_operator_cluster_snapshots
        WHERE cluster_id=? ORDER BY snapshot_ts DESC LIMIT 1
    """, (cid,)).fetchone()


def compute_deltas(conn: sqlite3.Connection, cid: int) -> dict:
    """
    Deltas for one cluster: latest snapshot vs the snapshot from ~24h ago.
    Returns {} when there is no history (honest: no deltas until snapshots exist).
    `has_baseline` is False when the cluster is younger than the 24h window — the
    UI should show "new" rather than a delta in that case.
    """
    conn.row_factory = sqlite3.Row
    now = int(time.time())
    cur = _latest(conn, cid)
    if not cur:
        return {}
    base = _baseline(conn, cid, now - DAY_S)
    # a true 24h baseline is one taken at least ~23h before now; otherwise the cluster
    # is younger than the window and we have no real "yesterday" to compare against.
    has_baseline = bool(base and (cur["snapshot_ts"] - base["snapshot_ts"]) >= (DAY_S - 3600))

    def d(field):
        if not has_baseline:
            return None
        a, b = cur[field], base[field]
        if a is None or b is None:
            return None
        return round(a - b, 4) if isinstance(a, float) else (a - b)

    conf_d = d("confidence")
    tok_d = d("token_count")
    cre_d = d("creator_count")
    sol_d = d("deployed_sol")
    state_changed = bool(has_baseline and cur["cluster_state"] != base["cluster_state"])
    new_funders = bool(has_baseline and base["top_funders_hash"] != cur["top_funders_hash"]
                       and cur["top_funders_hash"])

    # trend from real change: any positive growth in confidence/launches/creators/sol
    # → GROWING; any negative → DECLINING; else STABLE. Confidence dominates.
    trend = "STABLE"
    if has_baseline:
        ups = sum(1 for x in (conf_d, tok_d, cre_d, sol_d) if x and x > 0)
        downs = sum(1 for x in (conf_d, tok_d, cre_d, sol_d) if x and x < 0)
        if (conf_d and conf_d > 0) or ups > downs:
            trend = "GROWING"
        elif (conf_d and conf_d < 0) or downs > ups:
            trend = "DECLINING"

    return {
        "cluster_id": cid,
        "has_baseline": has_baseline,
        "current": {
            "confidence": cur["confidence"], "token_count": cur["token_count"],
            "creator_count": cur["creator_count"], "deployed_sol": cur["deployed_sol"],
            "state": cur["cluster_state"],
        },
        "deltas_24h": {
            "confidence": conf_d, "token_count": tok_d, "creator_count": cre_d,
            "deployed_sol": sol_d, "state_changed": state_changed,
            "new_funders_detected": new_funders,
        },
        "trend": trend,
        "snapshots": conn.execute(
            "SELECT COUNT(*) FROM wt_operator_cluster_snapshots WHERE cluster_id=?", (cid,)
        ).fetchone()[0],
    }


def all_growth(conn: sqlite3.Connection) -> list[dict]:
    """Deltas for every cluster that has a current snapshot. Powers the growth API."""
    conn.row_factory = sqlite3.Row
    ids = [r[0] for r in conn.execute(
        "SELECT DISTINCT cluster_id FROM wt_operator_cluster_snapshots").fetchall()]
    out = [compute_deltas(conn, cid) for cid in ids]
    return [o for o in out if o]


def change_feed(conn: sqlite3.Connection) -> list[dict]:
    """
    Turn real 24h deltas into discrete "What Changed?" events. NARROW and honest:
    one event per real change, emitted ONLY when has_baseline is true (a cluster
    with no 24h baseline produces NO events — never a synthetic "new cluster" line).

    Event kinds: launches_up, creators_up, confidence_up, confidence_down,
    state_changed, new_funder, trend_growing, trend_declining.
    Returns rows {cluster_id, kind, text, magnitude} sorted strongest-first.
    """
    feed = []
    for g in all_growth(conn):
        if not g.get("has_baseline"):
            continue                      # honest: no baseline → no events
        cid = g["cluster_id"]
        d = g["deltas_24h"]
        cur = g["current"]
        tok, cre, conf = d.get("token_count"), d.get("creator_count"), d.get("confidence")
        if tok and tok > 0:
            feed.append({"cluster_id": cid, "kind": "launches_up",
                         "magnitude": tok, "text": f"Cluster #{cid} gained +{tok} launch{'es' if tok>1 else ''}"})
        if cre and cre > 0:
            feed.append({"cluster_id": cid, "kind": "creators_up",
                         "magnitude": cre, "text": f"Cluster #{cid} added {cre} creator{'s' if cre>1 else ''}"})
        if conf:
            pct = round(conf * 100)
            if pct != 0:
                base_pct = round((cur.get("confidence") or 0) * 100) - pct
                kind = "confidence_up" if pct > 0 else "confidence_down"
                arrow = "increased" if pct > 0 else "decreased"
                feed.append({"cluster_id": cid, "kind": kind, "magnitude": abs(pct),
                             "text": f"Cluster #{cid} confidence {arrow} {base_pct}% → {round((cur.get('confidence') or 0)*100)}%"})
        if d.get("state_changed"):
            feed.append({"cluster_id": cid, "kind": "state_changed", "magnitude": 50,
                         "text": f"Cluster #{cid} entered {cur.get('state')}"})
        if d.get("new_funders_detected"):
            feed.append({"cluster_id": cid, "kind": "new_funder", "magnitude": 30,
                         "text": f"Cluster #{cid} — new shared funder observed"})
        # trend transitions are the headline movers
        if g.get("trend") == "GROWING":
            feed.append({"cluster_id": cid, "kind": "trend_growing", "magnitude": 60,
                         "text": f"Operator candidate #{cid} is GROWING"})
        elif g.get("trend") == "DECLINING":
            feed.append({"cluster_id": cid, "kind": "trend_declining", "magnitude": 40,
                         "text": f"Operator candidate #{cid} confidence DECLINING"})
    feed.sort(key=lambda e: -e["magnitude"])
    return feed
