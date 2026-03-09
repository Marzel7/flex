#!/usr/bin/env python3
"""
Helius Webhook Address Sync (Webhook M5)

Goal
- Replace polling-based creator scanning with an address-filtered Helius Webhook fleet.
- Keep webhook accountAddresses in sync with your *current* creator universe (e.g., top 1000 by priority).

What it does
1) Pulls creators from your SQLite DB (same priority logic you already use).
2) Shards creators into N webhooks (max 100,000 addresses per webhook via API).
3) Updates each webhook (PUT) to set accountAddresses to its shard.
4) Writes a local mapping table so you can see which creators are assigned to which webhook.
"""

from __future__ import annotations

import os
import json
import time
import sqlite3
import argparse
from typing import List, Dict, Optional

import requests

DB_PATH = os.getenv("DB_PATH", "flex_complete_database.db")

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "").strip()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()
WEBHOOK_TYPE = os.getenv("WEBHOOK_TYPE", "raw").strip()
CREATE_MISSING = os.getenv("CREATE_MISSING", "0").strip() == "1"

CREATOR_LIMIT = int(os.getenv("CREATOR_LIMIT", "100"))
SHARD_SIZE = int(os.getenv("SHARD_SIZE", "100000"))

_TX_TYPES_RAW = os.getenv("TX_TYPES", '["TRANSFER"]').strip()
try:
    TX_TYPES = json.loads(_TX_TYPES_RAW)
    if not isinstance(TX_TYPES, list):
        raise ValueError("TX_TYPES must be a JSON array string")
    if not TX_TYPES:
        TX_TYPES = ["TRANSFER"]
except Exception:
    TX_TYPES = ["TRANSFER"]

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn

def ensure_tables() -> None:
    conn = _connect()
    cur = conn.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS helius_webhook_shards (
      shard_index INTEGER PRIMARY KEY,
      webhook_id TEXT NOT NULL,
      shard_size INTEGER NOT NULL,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS helius_webhook_assignments (
      creator_address TEXT PRIMARY KEY,
      shard_index INTEGER NOT NULL,
      webhook_id TEXT NOT NULL,
      assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    conn.commit()
    conn.close()

def get_top_creators_by_activity(limit: int = 100) -> List[str]:
    """Get top creators by recent webhook transfer activity."""
    conn = _connect()
    cur = conn.cursor()
    try:
        # Get top creators by recent webhook activity (sol_transfers)
        cur.execute("""
          SELECT DISTINCT source
          FROM sol_transfers
          ORDER BY block_time DESC
          LIMIT ?
        """, (limit,))
        rows = cur.fetchall()
        conn.close()
        return [r[0] for r in rows if r and r[0]]
    except sqlite3.OperationalError:
        # Fallback to creator_scan_priority if sol_transfers doesn't exist yet
        conn.close()
        return get_creators(limit)

def get_creators(limit: int = 1000) -> List[str]:
    conn = _connect()
    cur = conn.cursor()
    try:
        cur.execute("""
          SELECT creator
          FROM creator_scan_priority
          ORDER BY tier ASC, last_launch_at DESC, last_scanned_at ASC
          LIMIT ?
        """, (limit,))
        rows = cur.fetchall()
        conn.close()
        return [r["creator"] for r in rows if r and r["creator"]]
    except sqlite3.OperationalError:
        cur.execute("""
          SELECT DISTINCT earliest_tx_creator
          FROM token_analysis
          WHERE earliest_tx_creator IS NOT NULL
          ORDER BY analyzed_at DESC
          LIMIT ?
        """, (limit,))
        rows = cur.fetchall()
        conn.close()
        return [r[0] for r in rows if r and r[0]]

def shard_addresses(addrs: List[str], shard_size: int) -> List[List[str]]:
    shard_size = max(int(shard_size or 100000), 1)
    return [addrs[i:i + shard_size] for i in range(0, len(addrs), shard_size)]

def helius_base() -> str:
    return "https://api-mainnet.helius-rpc.com/v0/webhooks"

def _require_env() -> None:
    if not HELIUS_API_KEY:
        raise SystemExit("HELIUS_API_KEY is required")
    if not WEBHOOK_URL:
        raise SystemExit("WEBHOOK_URL is required (public HTTPS endpoint)")

def api_get_webhook(webhook_id: str) -> dict:
    url = f"{helius_base()}/{webhook_id}?api-key={HELIUS_API_KEY}"
    r = requests.get(url, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"GET webhook {webhook_id} failed: {r.status_code} {r.text[:200]}")
    return r.json()

def api_update_webhook(webhook_id: str, account_addresses: List[str]) -> dict:
    existing = api_get_webhook(webhook_id)
    payload = {
        "webhookURL": existing.get("webhookURL") or WEBHOOK_URL,
        "webhookType": existing.get("webhookType") or WEBHOOK_TYPE,
        "transactionTypes": existing.get("transactionTypes") if existing.get("transactionTypes") is not None else TX_TYPES,
        "accountAddresses": account_addresses,
    }
    if existing.get("authHeader"):
        payload["authHeader"] = existing["authHeader"]

    url = f"{helius_base()}/{webhook_id}?api-key={HELIUS_API_KEY}"
    r = requests.put(url, json=payload, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"PUT webhook {webhook_id} failed: {r.status_code} {r.text[:200]}")
    return r.json()

def api_create_webhook(account_addresses: List[str]) -> dict:
    payload = {
        "webhookURL": WEBHOOK_URL,
        "webhookType": WEBHOOK_TYPE,
        "transactionTypes": TX_TYPES,
        "accountAddresses": account_addresses,
    }
    url = f"{helius_base()}?api-key={HELIUS_API_KEY}"
    r = requests.post(url, json=payload, timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"POST create webhook failed: {r.status_code} {r.text[:200]}")
    return r.json()

def load_webhook_ids_from_env() -> List[str]:
    raw = os.getenv("WEBHOOK_IDS", "").strip()
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]

def persist_shard_mapping(shards: List[List[str]], webhook_ids: List[str]) -> None:
    conn = _connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM helius_webhook_shards")
    for i, wid in enumerate(webhook_ids):
        cur.execute(
            "INSERT INTO helius_webhook_shards(shard_index, webhook_id, shard_size, updated_at) VALUES(?,?,?,datetime('now'))",
            (i, wid, len(shards[i]) if i < len(shards) else 0),
        )

    cur.execute("DELETE FROM helius_webhook_assignments")
    for i, shard in enumerate(shards):
        wid = webhook_ids[i]
        cur.executemany(
            "INSERT INTO helius_webhook_assignments(creator_address, shard_index, webhook_id, assigned_at) VALUES(?,?,?,datetime('now'))",
            [(addr, i, wid) for addr in shard],
        )
    conn.commit()
    conn.close()

def sync_once() -> None:
    _require_env()
    ensure_tables()

    # Get top creators by recent webhook activity
    creators = [c for c in get_top_creators_by_activity(limit=CREATOR_LIMIT) if c]
    print(f"[WEBHOOK_SYNC] top creators by activity={len(creators)} limit={CREATOR_LIMIT}", flush=True)

    shards = shard_addresses(creators, SHARD_SIZE)
    required_webhooks = max(1, len(shards))
    print(f"[WEBHOOK_SYNC] shards={len(shards)} shard_size={SHARD_SIZE} required_webhooks={required_webhooks}", flush=True)

    webhook_ids = load_webhook_ids_from_env()

    if len(webhook_ids) < required_webhooks:
        if not CREATE_MISSING:
            raise SystemExit(
                f"Need {required_webhooks} webhook IDs but WEBHOOK_IDS has {len(webhook_ids)}. "
                f"Set WEBHOOK_IDS or export CREATE_MISSING=1 to auto-create."
            )
        while len(webhook_ids) < required_webhooks:
            shard_index = len(webhook_ids)
            created = api_create_webhook(shards[shard_index])
            wid = created.get("webhookID") or created.get("webhookId") or created.get("id")
            if not wid:
                raise RuntimeError(f"Create webhook response missing ID: {created}")
            webhook_ids.append(wid)
            print(f"[WEBHOOK_SYNC] created webhook shard={shard_index} id={wid}", flush=True)

    webhook_ids = webhook_ids[:required_webhooks]

    for i, wid in enumerate(webhook_ids):
        shard_addrs = shards[i]
        print(f"[WEBHOOK_SYNC] updating shard={i} webhook_id={wid} addresses={len(shard_addrs)}", flush=True)
        updated = api_update_webhook(wid, shard_addrs)
        print(f"[WEBHOOK_SYNC] ✓ updated webhook_id={wid} accountAddresses={len(updated.get('accountAddresses') or [])}", flush=True)
        time.sleep(0.2)

    persist_shard_mapping(shards, webhook_ids)
    print("[WEBHOOK_SYNC] ✓ mapping persisted to DB tables helius_webhook_shards + helius_webhook_assignments", flush=True)
    print("[WEBHOOK_SYNC] Tip: set WEBHOOK_IDS to:", ",".join(webhook_ids), flush=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--watch", type=int, default=0, help="Sync every N seconds")
    args = ap.parse_args()

    if args.once or args.watch == 0:
        sync_once()
        return

    interval = max(60, int(args.watch))
    print(f"[WEBHOOK_SYNC] watching: interval={interval}s", flush=True)
    while True:
        t0 = time.time()
        try:
            sync_once()
        except Exception as e:
            print(f"[WEBHOOK_SYNC] ✗ sync error: {e}", flush=True)

        dt = time.time() - t0
        sleep_for = max(5, interval - dt)
        print(f"[WEBHOOK_SYNC] next sync in {sleep_for:.0f}s", flush=True)
        time.sleep(sleep_for)

if __name__ == "__main__":
    main()
