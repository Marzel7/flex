"""
Helius webhook registration for creator SOL movement monitoring.

Uses a single shared webhook (CREATOR_MOVEMENT_WEBHOOK_ID) that accumulates
addresses via GET → append → PUT, rather than one webhook per creator.

Required env vars:
  HELIUS_API_KEY               — Helius API key
  CREATOR_MOVEMENT_WEBHOOK_ID  — existing Helius webhook ID to add addresses to

Optional env vars:
  CREATOR_MOVEMENT_WEBHOOK_URL — only needed when creating a new webhook from scratch
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

_HELIUS_BASE = "https://api-mainnet.helius-rpc.com/v0/webhooks"
_TX_TYPES = ["TRANSFER"]

# Concurrency guard: only one address-list mutation at a time to avoid races
# on the shared webhook's accountAddresses list.
_webhook_lock = asyncio.Lock()


def _api_key() -> str:
    return os.getenv("HELIUS_API_KEY", "").strip()


def _webhook_id() -> str:
    return os.getenv("CREATOR_MOVEMENT_WEBHOOK_ID", "").strip()


def _webhook_url() -> str:
    return os.getenv("CREATOR_MOVEMENT_WEBHOOK_URL", "").strip()


async def _get_webhook(session: aiohttp.ClientSession, wid: str) -> dict:
    url = f"{_HELIUS_BASE}/{wid}?api-key={_api_key()}"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
        body = await r.json()
        if r.status != 200:
            raise RuntimeError(f"GET webhook {wid} failed: {r.status} {body}")
        return body


async def _put_webhook(
    session: aiohttp.ClientSession,
    wid: str,
    existing: dict,
    addresses: list[str],
) -> dict:
    payload = {
        "webhookURL": existing.get("webhookURL") or _webhook_url(),
        "webhookType": existing.get("webhookType") or "enhanced",
        "transactionTypes": existing.get("transactionTypes") or _TX_TYPES,
        "accountAddresses": addresses,
    }
    if existing.get("authHeader"):
        payload["authHeader"] = existing["authHeader"]
    url = f"{_HELIUS_BASE}/{wid}?api-key={_api_key()}"
    async with session.put(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as r:
        body = await r.json()
        if r.status != 200:
            raise RuntimeError(f"PUT webhook {wid} failed: {r.status} {body}")
        return body


async def register_creator_address(creator_address: str) -> Optional[str]:
    """
    Add creator_address to the shared Helius webhook's accountAddresses list.

    Returns the webhook ID on success, None if env vars are missing.
    Raises on HTTP errors (caller should handle and mark watch_status=error).
    """
    api_key = _api_key()
    wid = _webhook_id()

    if not api_key:
        logger.warning("[WATCH] HELIUS_API_KEY not set — skipping registration")
        return None
    if not wid:
        logger.warning("[WATCH] CREATOR_MOVEMENT_WEBHOOK_ID not set — skipping registration")
        return None

    async with _webhook_lock:
        async with aiohttp.ClientSession() as session:
            existing = await _get_webhook(session, wid)
            current_addresses: list[str] = existing.get("accountAddresses") or []

            if creator_address in current_addresses:
                logger.debug("[WATCH] creator=%s already in webhook — no-op", creator_address[:16])
                return wid

            updated = current_addresses + [creator_address]
            await _put_webhook(session, wid, existing, updated)

    logger.info(
        "[WATCH] Added creator=%s to webhook=%s (total=%d)",
        creator_address[:16],
        wid,
        len(updated),
    )

    # Persist enrollment to local DB for lifecycle tracking
    try:
        import sqlite3 as _sq
        import time as _t
        db_path = os.getenv("RPC_METRICS_DB", "database/flex_complete_database.db")
        _conn = _sq.connect(db_path, timeout=5)
        _conn.execute("""
            INSERT OR IGNORE INTO wt_webhook_enrollments
                (wallet_address, webhook_id, role, state, enrolled_at, is_active)
            VALUES (?, ?, 'candidate', 'ENROLLED', ?, 1)
        """, (creator_address, wid, int(_t.time())))
        _conn.execute("""
            UPDATE wt_webhook_enrollments
            SET state='ENROLLED', is_active=1, confirmed_at=?
            WHERE wallet_address=? AND webhook_id=?
              AND state NOT IN ('ENROLLED', 'CONFIRMED')
        """, (int(_t.time()), creator_address, wid))
        _conn.commit()
        _conn.close()
    except Exception as _e:
        logger.debug("[WATCH] DB enrollment write failed (non-fatal): %s", _e)

    return wid
