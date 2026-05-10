#!/usr/bin/env python3
"""
Pump Bot Poller — 5-minute poll of known pump bot wallets.

For each wallet in pump_bot_wallets:
  1. Fetch recent transactions via Helius Enhanced API (type=SWAP/TRANSFER)
  2. Find bonding curve interactions (pump.fun program)
  3. Derive mint from bonding curve PDA
  4. Look up token in token_analysis, check lifecycle stage
  5. Call getTokenLargestAccounts to get bot % of supply
  6. Write signals to pump_bot_signals
"""

import os
import time
import asyncio
import aiohttp
import sqlite3
import logging
from typing import Optional

from src.utils.db_locking import db_connect

_LOG = logging.getLogger("pump_bot_poller")
_LOG.setLevel(logging.INFO)

# Mayhem routes buys through its own program, not directly through pump.fun
PUMPFUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
MAYHEM_PROGRAM  = "MAyhSmzXzV1pTf7LsNkrNwkWKTo4ougAJ1PPg47MD4e"
BOT_PROGRAMS = {PUMPFUN_PROGRAM, MAYHEM_PROGRAM}
POLL_INTERVAL_SECS = 300  # 5 minutes
HELIUS_BATCH_SIZE = 100
BONDING_CURVE_PREFIX = "bonding-curve"

_DB_PATH: Optional[str] = None
_HELIUS_API_KEY: Optional[str] = None


def configure(db_path: str, helius_api_key: Optional[str] = None):
    global _DB_PATH, _HELIUS_API_KEY
    _DB_PATH = db_path
    _HELIUS_API_KEY = helius_api_key or os.environ.get("HELIUS_API_KEY")


async def _fetch_wallet_txs(session: aiohttp.ClientSession, wallet: str, before: Optional[str] = None) -> list:
    if not _HELIUS_API_KEY:
        return []
    url = f"https://api.helius.xyz/v0/addresses/{wallet}/transactions"
    params = {
        "api-key": _HELIUS_API_KEY,
        "limit": HELIUS_BATCH_SIZE,
        "commitment": "confirmed",
    }
    if before:
        params["before"] = before
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            return data if isinstance(data, list) else []
    except Exception as e:
        _LOG.warning(f"[PUMP_BOT_POLLER] fetch_wallet_txs error for {wallet[:8]}: {e}")
        return []


async def _get_token_largest_accounts(session: aiohttp.ClientSession, mint: str) -> list:
    """Returns list of (address, uiAmount, pct) for top holders."""
    rpc_url = os.environ.get("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenLargestAccounts",
        "params": [mint, {"commitment": "confirmed"}],
    }
    try:
        async with session.post(rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            data = await resp.json()
            accounts = data.get("result", {}).get("value", [])
            total = sum(float(a.get("uiAmount") or 0) for a in accounts)
            result = []
            for a in accounts:
                amt = float(a.get("uiAmount") or 0)
                pct = (amt / total * 100) if total > 0 else 0
                result.append({
                    "address": a.get("address"),
                    "amount": amt,
                    "pct": round(pct, 2),
                })
            return result
    except Exception as e:
        _LOG.warning(f"[PUMP_BOT_POLLER] getTokenLargestAccounts error for {mint[:8]}: {e}")
        return []


def _extract_pumpfun_mints_from_txs(txs: list, bot_address: str) -> dict:
    """
    Returns {mint: {bonding_curve, buy_count, sol_spent}}.

    Buy detection:
      - nativeTransfers: bot → X  (X is the bonding curve, sol_spent = amount)
      - tokenTransfers:  bot receives mint M  (M is the token bought on that curve)
    Sell detection:
      - tokenTransfers: bot → X (bot sends tokens)
      - we ignore sells for now, only counting buys
    """
    mint_data: dict = {}
    for tx in txs:
        if not isinstance(tx, dict):
            continue

        native = tx.get("nativeTransfers") or []
        token_transfers = tx.get("tokenTransfers") or []

        # Bot outgoing SOL transfers → bonding curves being bought
        sol_by_curve: dict = {}
        for t in native:
            if t.get("fromUserAccount") == bot_address:
                dest = t.get("toUserAccount")
                if dest:
                    sol_by_curve[dest] = sol_by_dest = sol_by_curve.get(dest, 0) + float(t.get("amount", 0)) / 1e9

        if not sol_by_curve:
            continue  # not a buy tx

        # Bot incoming token transfers → identify which mint was bought
        mint_received: str | None = None
        for tt in token_transfers:
            if tt.get("toUserAccount") == bot_address:
                mint_received = tt.get("mint")
                break

        if not mint_received:
            continue  # can't identify mint

        # Map: one buy tx = one mint bought on one curve
        # Use the curve that received the most SOL if multiple
        bonding_curve = max(sol_by_curve, key=lambda k: sol_by_curve[k])
        sol_spent = sol_by_curve[bonding_curve]

        if mint_received not in mint_data:
            mint_data[mint_received] = {
                "bonding_curve": bonding_curve,
                "buy_count": 0,
                "sol_spent": 0.0,
            }
        mint_data[mint_received]["buy_count"] += 1
        mint_data[mint_received]["sol_spent"] += sol_spent

    return mint_data


def _get_token_lifecycle(db_conn: sqlite3.Connection, mint: str) -> Optional[str]:
    row = db_conn.execute(
        "SELECT lifecycle_stage FROM token_analysis WHERE mint = ?", (mint,)
    ).fetchone()
    return row[0] if row else None


def _upsert_signal(db_conn: sqlite3.Connection, mint: str, bonding_curve: str,
                   bot_address: str, bot_label: str, mint_data: dict,
                   holder_info: list, lifecycle_stage: Optional[str], now: int):
    bot_pct = None
    is_top_holder = 0
    total_buyers = None

    # Find bot's token account in holder_info
    for h in holder_info:
        # holder addresses are token accounts, not wallets — can't directly match
        # so we just take the top holder's pct as a proxy when buy_count is high
        pass

    # If holder_info is available, check if top holder's pct is significant
    if holder_info and mint_data["buy_count"] > 0:
        top_pct = holder_info[0]["pct"] if holder_info else 0
        bot_pct = top_pct  # approximation — the bot likely holds a concentrated position
        is_top_holder = 1 if top_pct > 5 else 0
        total_buyers = len(holder_info)

    buy_count = mint_data["buy_count"]
    sol_spent = mint_data["sol_spent"]

    signal_strength = "low"
    if buy_count >= 10 and (bot_pct or 0) > 10:
        signal_strength = "high"
    elif buy_count >= 5 or (bot_pct or 0) > 5:
        signal_strength = "medium"

    existing = db_conn.execute(
        "SELECT id FROM pump_bot_signals WHERE mint = ? AND bot_address = ?",
        (mint, bot_address)
    ).fetchone()

    if existing:
        db_conn.execute("""
            UPDATE pump_bot_signals SET
                bonding_curve = ?,
                bot_label = ?,
                detected_at = ?,
                lifecycle_stage = ?,
                bot_buy_count = ?,
                bot_sol_spent = ?,
                bot_pct_supply = ?,
                total_buyers = ?,
                is_top_holder = ?,
                signal_strength = ?
            WHERE id = ?
        """, (bonding_curve, bot_label, now, lifecycle_stage, buy_count,
              sol_spent, bot_pct, total_buyers, is_top_holder, signal_strength,
              existing[0]))
    else:
        db_conn.execute("""
            INSERT INTO pump_bot_signals (
                mint, bonding_curve, bot_address, bot_label, detected_at,
                lifecycle_stage, bot_buy_count, bot_sol_spent, bot_pct_supply,
                total_buyers, is_top_holder, signal_strength, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (mint, bonding_curve, bot_address, bot_label, now,
              lifecycle_stage, buy_count, sol_spent, bot_pct,
              total_buyers, is_top_holder, signal_strength, now))


async def _poll_one_wallet(session: aiohttp.ClientSession, wallet_row: dict) -> int:
    address = wallet_row["address"]
    label = wallet_row["label"]
    last_sig = wallet_row.get("last_seen_signature")

    txs = await _fetch_wallet_txs(session, address, before=None)
    if not txs:
        return 0

    # Only process transactions newer than last_seen_signature
    new_txs = []
    for tx in txs:
        sig = tx.get("signature", "")
        if sig == last_sig:
            break
        new_txs.append(tx)

    if not new_txs:
        return 0

    mint_data = _extract_pumpfun_mints_from_txs(new_txs, address)
    if not mint_data:
        # Update last_seen_signature even if no pump interactions
        _bump_last_sig(address, new_txs[0].get("signature"))
        return 0

    now = int(time.time())
    mints = list(mint_data.keys())

    # 1. Short read-only DB open — grab lifecycle stages
    lifecycle_map = {}
    with db_connect(_DB_PATH, timeout=10) as db_conn:
        for mint in mints:
            lifecycle_map[mint] = _get_token_lifecycle(db_conn, mint)

    # 2. All RPC calls — no DB connection open
    holder_map = {}
    for mint in mints:
        holder_map[mint] = await _get_token_largest_accounts(session, mint)

    # 3. Short write-only DB open — just upserts, no I/O
    signals_written = 0
    newest_sig = new_txs[0].get("signature") if new_txs else last_sig
    with db_connect(_DB_PATH, timeout=10) as db_conn:
        for mint, mdata in mint_data.items():
            _upsert_signal(
                db_conn, mint, mdata["bonding_curve"], address, label,
                mdata, holder_map.get(mint, []), lifecycle_map.get(mint), now
            )
            signals_written += 1
        db_conn.execute("""
            UPDATE pump_bot_wallets
            SET last_polled_at = ?, last_seen_signature = ?,
                total_tokens_touched = total_tokens_touched + ?
            WHERE address = ?
        """, (now, newest_sig, signals_written, address))

    return signals_written


def _bump_last_sig(address: str, sig: Optional[str]):
    if not sig or not _DB_PATH:
        return
    try:
        with db_connect(_DB_PATH, timeout=5) as db_conn:
            db_conn.execute(
                "UPDATE pump_bot_wallets SET last_polled_at = ?, last_seen_signature = ? WHERE address = ?",
                (int(time.time()), sig, address)
            )
    except Exception:
        pass


async def _poll_all_wallets():
    if not _DB_PATH:
        return

    with db_connect(_DB_PATH, timeout=10) as db_conn:
        rows = db_conn.execute("""
            SELECT address, label, last_seen_signature
            FROM pump_bot_wallets
            WHERE is_active = 1
        """).fetchall()

    if not rows:
        return

    wallet_rows = [{"address": r[0], "label": r[1], "last_seen_signature": r[2]} for r in rows]

    async with aiohttp.ClientSession() as session:
        for wallet_row in wallet_rows:
            try:
                n = await _poll_one_wallet(session, wallet_row)
                if n > 0:
                    _LOG.info(f"[PUMP_BOT_POLLER] {wallet_row['label']} → {n} new signals")
            except Exception as e:
                _LOG.error(f"[PUMP_BOT_POLLER] Error polling {wallet_row['address'][:8]}: {e}")


def run_poller_loop(db_path: str, helius_api_key: Optional[str] = None):
    """Blocking loop — call from a daemon thread."""
    configure(db_path, helius_api_key)
    print("[PUMP_BOT_POLLER] Started — polling every 5 minutes", flush=True)

    # Seed Mayhem on first run if not already present
    _seed_known_bots()

    while True:
        try:
            import sqlite3 as _sqlite3
            with _sqlite3.connect(db_path, timeout=5) as _c:
                _row = _c.execute("SELECT setting_value FROM listener_settings WHERE setting_key='pump_bot_enabled'").fetchone()
            _enabled = _row[0] != 'false' if _row else True
        except Exception:
            _enabled = True
        if _enabled:
            try:
                asyncio.run(_poll_all_wallets())
            except Exception as e:
                print(f"[PUMP_BOT_POLLER] Poll cycle error: {e}", flush=True)
        else:
            print("[PUMP_BOT_POLLER] Skipping poll — disabled via settings", flush=True)
        time.sleep(POLL_INTERVAL_SECS)


def _seed_known_bots():
    """Seed well-known pump bots into pump_bot_wallets if not already present."""
    KNOWN_BOTS = [
        {
            "address": "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s",
            "label": "Mayhem Trading Agent",
            "source": "arkham",
        },
    ]
    if not _DB_PATH:
        return
    for attempt in range(10):
        try:
            now = int(time.time())
            with db_connect(_DB_PATH, timeout=60) as db_conn:
                db_conn.execute("PRAGMA busy_timeout=60000")
                for bot in KNOWN_BOTS:
                    db_conn.execute("""
                        INSERT OR IGNORE INTO pump_bot_wallets
                            (address, label, source, added_at, is_active)
                        VALUES (?, ?, ?, ?, 1)
                    """, (bot["address"], bot["label"], bot["source"], now))
            print("[PUMP_BOT_POLLER] Known bots seeded", flush=True)
            return
        except Exception as e:
            wait = min(10 * (attempt + 1), 60)
            print(f"[PUMP_BOT_POLLER] Seed attempt {attempt+1} failed: {e} — retrying in {wait}s", flush=True)
            time.sleep(wait)
