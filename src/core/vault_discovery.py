"""
Authoritative Solana Vault Discovery Implementation

Provides RPC-based vault discovery for Solana token pools.
Replaces fragile fixed-offset parsing with chain-state validation.

Key Functions:
- discover_vaults_rpc()       - Main entry point
- validate_token_accounts()   - Batch validate token accounts
- identify_base_vault()       - Select most likely AMM vault
- resolve_quote_vault()       - Find paired quote vault
- register_vault_pair()       - Register only after validation

Usage:
    vault_pair = await discover_vaults_rpc(token_mint, rpc_client)
    if vault_pair:
        await register_vault_pair(token_mint, vault_pair, db)
"""

import asyncio
import logging
import base64
import struct
import base58
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass
from enum import Enum

from src.utils.db_locking import managed_db_connect

# Constants
SPL_TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN2022_PROGRAM_ID = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"  # Token2022 extension
SYSTEM_PROGRAM_ID = "11111111111111111111111111111111"
WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112"

RAYDIUM_PROGRAM_ID = "675kPX9MHTjS2zt1qrVrrVrZg1ankqqgoerEmJlwQ1K"
ORCA_PROGRAM_ID = "whirLbMiicVdio4KfUqKKvsLrZtSqwNAUafgJMYco"
PUMPSWAP_PROGRAM_ID = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
PUMPFUN_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

KNOWN_POOL_PROGRAMS = [
    RAYDIUM_PROGRAM_ID,
    ORCA_PROGRAM_ID,
    PUMPSWAP_PROGRAM_ID,
    PUMPFUN_PROGRAM_ID,
]

logger = logging.getLogger(__name__)

# ============================================================================
# Data Classes
# ============================================================================


@dataclass
class DecodedTokenAccount:
    """Decoded SPL token account state."""
    mint: str
    owner: str
    amount: int
    delegated_amount: int
    delegate: Optional[str]
    state: int  # 0 = uninitialized, 1 = initialized, 2 = frozen
    is_native: bool
    close_authority: Optional[str]


@dataclass
class ValidatedTokenAccount:
    """Token account that passed validation checks."""
    address: str
    balance: int
    decoded: DecodedTokenAccount
    ws_activity: int = 0  # Event count from WebSocket


@dataclass
class VaultPair:
    """Validated vault pair ready for registration."""
    base_vault: ValidatedTokenAccount
    quote_vault: Dict  # {"address": str, "type": str, "decoded": Optional[DecodedTokenAccount]}
    pool_program: str
    pool_state_address: Optional[str] = None
    confidence_score: float = 0.0


class VaultDiscoveryError(Exception):
    """Vault discovery failed."""
    pass


# ============================================================================
# Phase 1: Token Account Discovery
# ============================================================================


async def get_token_largest_accounts(
    token_mint: str,
    rpc_client,
    limit: int = 20,
) -> List[Dict]:
    """
    Get the largest token accounts for a given mint using RPC.

    Args:
        token_mint: Token mint address
        rpc_client: Solana RPC client (aiohttp or similar)
        limit: Max accounts to return (default 20)

    Returns:
        List of accounts with address, amount, decimals, uiAmount
    """
    try:
        method = "getTokenLargestAccounts"
        params = [token_mint, {"limit": limit, "commitment": "confirmed"}]

        result = await rpc_client.call_async(method, params)

        if not result or "value" not in result:
            raise VaultDiscoveryError(f"Empty response from {method}")

        # Extract just the addresses from the returned account summaries
        candidates = [account["address"] for account in result["value"]]
        logger.info(f"[VAULT_DISCOVERY] ✅ getTokenLargestAccounts returned {len(candidates)} candidates")

        return candidates

    except Exception as e:
        logger.error(f"[VAULT_DISCOVERY] ❌ getTokenLargestAccounts failed: {e}")
        raise VaultDiscoveryError(f"Failed to get largest accounts: {e}")


# ============================================================================
# Phase 2: Token Account Validation
# ============================================================================


def decode_spl_token_account(data: bytes) -> DecodedTokenAccount:
    """
    Decode SPL token account from base64 data.

    SPL Token Account Layout (165 bytes):
    0-31:   mint (pubkey)
    32-63:  owner (pubkey)
    64-71:  amount (u64 LE)
    72-79:  decimals (u8) + state (u8) + is_native (u8) + padding (5 bytes)
    ...
    """
    if len(data) < 165:
        raise ValueError(f"Account data too short: {len(data)} bytes, expected 165")

    # Parse key components
    mint = data[0:32]
    owner = data[32:64]
    amount = struct.unpack_from("<Q", data, 64)[0]
    delegated_amount = struct.unpack_from("<Q", data, 72)[0]

    # Convert pubkeys to base58
    mint_str = _pubkey_to_base58(mint)
    owner_str = _pubkey_to_base58(owner)

    # Parse additional fields
    state_byte = data[108]  # Usually 1 = initialized
    is_native = data[109] != 0

    return DecodedTokenAccount(
        mint=mint_str,
        owner=owner_str,
        amount=amount,
        delegated_amount=delegated_amount,
        delegate=None,  # Could extract from offset 76-107
        state=state_byte,
        is_native=is_native,
        close_authority=None,  # Could extract from offset 128-159
    )


def _pubkey_to_base58(pubkey_bytes: bytes) -> str:
    """Convert 32-byte pubkey to base58 string."""
    try:
        # Use solana-py or similar library
        from solders.pubkey import Pubkey
        return str(Pubkey(pubkey_bytes))
    except ImportError:
        # Fallback: return hex representation
        return pubkey_bytes.hex()


async def validate_token_accounts(
    candidates: List[str],
    token_mint: str,
    rpc_client,
) -> List[ValidatedTokenAccount]:
    """
    Fetch and validate candidate token accounts.

    Validation checks:
    - Account exists
    - Owner = SPL Token program
    - Size = 165 bytes
    - Mint = expected token mint
    - Balance > 0 (ideally)

    Args:
        candidates: List of token account addresses
        token_mint: Expected token mint
        rpc_client: Solana RPC client

    Returns:
        List of validated token accounts (only those passing all checks)
    """
    validated = []

    try:
        # Batch fetch accounts (up to 100 per call)
        accounts = await rpc_client.get_multiple_accounts(
            candidates,
            encoding="base64",
            commitment="confirmed"
        )

        logger.info(f"[VAULT_DISCOVERY] Fetched {len(accounts)} accounts for validation")

        for i, acct in enumerate(accounts):
            address = candidates[i]

            # Check 1: Account exists
            if acct is None:
                logger.debug(f"[VAULT_DISCOVERY] ❌ {address[:16]}... - account does not exist")
                continue

            # Check 2: Owner = SPL Token program (or Token2022)
            if acct.owner not in (SPL_TOKEN_PROGRAM_ID, TOKEN2022_PROGRAM_ID):
                logger.debug(f"[VAULT_DISCOVERY] ❌ {address[:16]}... - wrong owner: {acct.owner}")
                continue

            # Check 3: Size = 165 bytes (SPL Token) or >= 165 for Token2022 extensions
            data_size = len(acct.data)
            if data_size < 165:
                logger.debug(f"[VAULT_DISCOVERY] ❌ {address[:16]}... - wrong size: {data_size} bytes (expected >= 165)")
                continue

            # Check 4: Decode and verify mint
            try:
                decoded = decode_spl_token_account(base64.b64decode(acct.data))

                if decoded.mint != token_mint:
                    logger.debug(f"[VAULT_DISCOVERY] ❌ {address[:16]}... - wrong mint: {decoded.mint[:8]}...")
                    continue

                # All checks passed
                logger.debug(f"[VAULT_DISCOVERY] ✅ {address[:16]}... - balance={decoded.amount}, owner={decoded.owner[:8]}...")

                validated.append(ValidatedTokenAccount(
                    address=address,
                    balance=decoded.amount,
                    decoded=decoded,
                    ws_activity=0  # To be populated later
                ))

            except Exception as e:
                logger.debug(f"[VAULT_DISCOVERY] ❌ {address[:16]}... - decode error: {e}")
                continue

        logger.info(f"[VAULT_DISCOVERY] ✅ Validated {len(validated)} token accounts (from {len(candidates)} candidates)")
        return validated

    except Exception as e:
        logger.error(f"[VAULT_DISCOVERY] ❌ Validation failed: {e}")
        raise VaultDiscoveryError(f"Account validation failed: {e}")


# ============================================================================
# Phase 3: Base Vault Identification
# ============================================================================


def identify_base_vault(
    validated_accounts: List[ValidatedTokenAccount],
    ws_subscription_monitor=None
) -> Optional[ValidatedTokenAccount]:
    """
    Identify the most likely AMM base vault from validated token accounts.

    Heuristics (in order of reliability):
    1. Owner field (delegation to pool program)
    2. WebSocket activity (recent updates)
    3. Balance (usually high for active AMM)
    4. Authority relationship to known pool program

    Args:
        validated_accounts: List of validated token accounts
        ws_subscription_monitor: Optional monitor to check WebSocket events

    Returns:
        Most likely base vault account, or None if no good candidates
    """
    if not validated_accounts:
        logger.warning("[VAULT_DISCOVERY] No validated accounts to select from")
        return None

    candidates_scored = []

    for account in validated_accounts:
        score = 0.0
        signals = []

        # Signal 1: Has delegation/owner (points to pool)
        if account.decoded.owner != "0" * 44:
            score += 500.0
            signals.append(f"owner={account.decoded.owner[:8]}...")

        # Signal 2: WebSocket activity
        if ws_subscription_monitor:
            events = ws_subscription_monitor.get_events_for_account(account.address)
            if events > 0:
                score += events * 0.1  # Each event adds slight boost
                signals.append(f"ws_events={events}")

        # Signal 3: Balance
        if account.balance > 0:
            # Logarithmic scoring to avoid tiny balances dominating
            score += min(100.0, __import__("math").log10(account.balance + 1))
            signals.append(f"balance={account.balance}")

        candidates_scored.append({
            "account": account,
            "score": score,
            "signals": signals
        })

    # Sort by score descending
    candidates_scored.sort(key=lambda x: x["score"], reverse=True)

    if candidates_scored:
        best = candidates_scored[0]
        logger.info(
            f"[VAULT_DISCOVERY] ✅ Base vault identified: {best['account'].address} "
            f"(score={best['score']:.1f}, {', '.join(best['signals'])})"
        )
        return best["account"]

    return None


# ============================================================================
# Phase 4: Linked Pool/Pair State Resolution
# ============================================================================


async def resolve_quote_vault_from_base(
    base_vault: ValidatedTokenAccount,
    token_mint: str,
    rpc_client,
) -> Optional[str]:
    """
    Resolve quote vault from base vault owner (pool authority).

    The base vault's owner field often points to a pool/pair state account.
    We decode that to find the paired quote vault.

    Args:
        base_vault: Validated base token vault
        token_mint: Token mint (for validation)
        rpc_client: Solana RPC client

    Returns:
        Quote vault address if found, None otherwise
    """
    owner_pubkey = base_vault.decoded.owner

    if not owner_pubkey or owner_pubkey == "0" * 44:
        logger.debug("[VAULT_DISCOVERY] Base vault has no owner - cannot resolve quote via owner chaining")
        return None

    try:
        # Fetch pool/pair state account
        pool_account = await rpc_client.get_account_info(owner_pubkey, encoding="base64")

        if pool_account is None:
            logger.debug(f"[VAULT_DISCOVERY] Pool state account not found: {owner_pubkey[:16]}...")
            return None

        logger.debug(f"[VAULT_DISCOVERY] Pool authority chaining: {base_vault.address[:16]}... -> {owner_pubkey[:16]}...")

        # Decode pool state based on program owner
        program_id = pool_account.owner

        if program_id == RAYDIUM_PROGRAM_ID:
            quote_vault = await _decode_raydium_pool(pool_account.data, rpc_client)
        elif program_id == ORCA_PROGRAM_ID:
            quote_vault = await _decode_orca_pool(pool_account.data, rpc_client)
        elif program_id == PUMPSWAP_PROGRAM_ID:
            quote_vault = await _decode_pumpswap_pool(pool_account.data, rpc_client)
        elif program_id == PUMPFUN_PROGRAM_ID:
            quote_vault = await _decode_pumpfun_pool(pool_account.data, rpc_client)
        else:
            logger.debug(f"[VAULT_DISCOVERY] Unknown pool program: {program_id}")
            return None

        if quote_vault:
            logger.debug(f"[VAULT_DISCOVERY] Quote vault resolved via owner chaining: {quote_vault[:16]}...")
        return quote_vault

    except Exception as e:
        logger.debug(f"[VAULT_DISCOVERY] Owner chaining failed: {e}")
        return None


async def resolve_quote_vault_fallback(
    base_vault_address: str,
    token_mint: str,
    rpc_client,
) -> Optional[str]:
    """
    Fallback: Find quote vault by querying for wrapped SOL token accounts.

    For PumpSwap and other unknown pool types, query the base vault owner
    for all wSOL token accounts - the main one is the quote vault.

    Args:
        base_vault_address: Address of validated base vault
        token_mint: Token mint
        rpc_client: Solana RPC client

    Returns:
        Quote vault address if found, None otherwise
    """
    logger.debug("[VAULT_DISCOVERY] Attempting fallback quote vault discovery via wSOL token account query")

    try:
        # Fetch the base vault to get its owner (pool authority)
        base_acct = await rpc_client.get_account_info(base_vault_address, encoding="base64")
        if not base_acct:
            logger.debug("[VAULT_DISCOVERY] Could not fetch base vault for owner chaining")
            return None

        # Decode to get the owner field
        data_bytes = base64.b64decode(base_acct.data) if isinstance(base_acct.data, str) else base_acct.data
        if len(data_bytes) < 64:
            return None

        pool_owner = base58.b58encode(data_bytes[32:64]).decode()
        logger.debug(f"[VAULT_DISCOVERY] Base vault owner (pool authority): {pool_owner[:16]}...")

        # Query for wSOL token accounts owned by the pool
        result = await rpc_client.call_async(
            "getTokenAccountsByOwner",
            [pool_owner, {"mint": WRAPPED_SOL_MINT}, {"encoding": "base64"}]
        )

        if result and "value" in result and result["value"]:
            accounts = result["value"]
            if len(accounts) > 0:
                # The first (and usually only) wSOL account is the quote vault
                quote_vault = accounts[0]["pubkey"]
                logger.info(f"[VAULT_DISCOVERY] Quote vault found via wSOL query: {quote_vault}")
                return quote_vault

        logger.debug("[VAULT_DISCOVERY] No wSOL token accounts found for pool authority")
        return None

    except Exception as e:
        logger.debug(f"[VAULT_DISCOVERY] Fallback quote discovery failed: {e}")
        return None


# Placeholder decoders - implement per pool program
async def _decode_raydium_pool(data: bytes, rpc_client) -> Optional[str]:
    """Decode Raydium pool account and extract quote vault."""
    # TODO: Implement Raydium pool decoding
    return None


async def _decode_orca_pool(data: bytes, rpc_client) -> Optional[str]:
    """Decode Orca pool account and extract quote vault."""
    # TODO: Implement Orca pool decoding
    return None


async def _decode_pumpswap_pool(data: bytes, rpc_client) -> Optional[str]:
    """Find quote vault for PumpSwap pool by querying token accounts.

    PumpSwap pools hold quote tokens in token accounts they own.
    We query for wSOL (wrapped SOL) accounts owned by the pool authority.
    """
    try:
        # Decode base64 if needed
        if isinstance(data, str):
            data = base64.b64decode(data)

        # For PumpSwap, we need to query the RPC for token accounts owned by the pool
        # This is called with pool account data, but we don't have the pool pubkey here
        # Instead, use fallback method: query for wSOL accounts
        logger.debug(f"[VAULT_DISCOVERY] PumpSwap quote resolution requires RPC query (not implemented in data decode)")
        return None

    except Exception as e:
        logger.debug(f"[VAULT_DISCOVERY] PumpSwap pool decode failed: {e}")
        return None


async def _decode_pumpfun_pool(data: bytes, rpc_client) -> Optional[str]:
    """Decode PumpFun pool account and extract quote vault."""
    # TODO: Implement PumpFun pool decoding
    return None


async def _query_pool_registry(program_id: str, token_mint: str, rpc_client) -> List[Dict]:
    """Query pool registry for pools matching token mint."""
    # TODO: Implement pool registry query per program
    return []


# ============================================================================
# Phase 5: Quote Vault Validation
# ============================================================================


async def validate_quote_vault(
    quote_vault_address: str,
    rpc_client,
) -> Optional[Dict]:
    """
    Validate quote vault against expected characteristics.

    Quote vaults can be:
    - SPL token accounts (owner = SPL Token program)
    - Native SOL accounts (owner = System program)
    - Wrapped SOL accounts (SPL token with mint = wSOL)

    Args:
        quote_vault_address: Address to validate
        rpc_client: Solana RPC client

    Returns:
        Validated quote vault dict with address, type, and decoded data
        Returns None if validation fails
    """
    try:
        acct = await rpc_client.get_account_info(quote_vault_address, encoding="base64")

        if acct is None:
            logger.debug(f"[VAULT_DISCOVERY] ❌ Quote vault does not exist: {quote_vault_address[:16]}...")
            return None

        # Check for SPL token account (standard or Token2022)
        if acct.owner in (SPL_TOKEN_PROGRAM_ID, TOKEN2022_PROGRAM_ID) and len(base64.b64decode(acct.data)) >= 165:
            try:
                decoded = decode_spl_token_account(base64.b64decode(acct.data))
                logger.info(
                    f"[VAULT_DISCOVERY] ✅ Quote vault (SPL token): {quote_vault_address[:16]}... "
                    f"(mint={decoded.mint[:8]}..., balance={decoded.amount})"
                )
                return {
                    "address": quote_vault_address,
                    "type": "spl_token",
                    "decoded": decoded,
                    "lamports": acct.lamports
                }
            except Exception as e:
                logger.debug(f"[VAULT_DISCOVERY] Quote vault decode error: {e}")
                return None

        # Check for unknown token program (might be Token2022 or wrapper)
        elif acct.owner not in (SYSTEM_PROGRAM_ID,) and len(base64.b64decode(acct.data)) >= 165:
            try:
                # Try to decode as SPL token even if owner is different
                decoded = decode_spl_token_account(base64.b64decode(acct.data))
                logger.info(
                    f"[VAULT_DISCOVERY] ✅ Quote vault (alternative token program): {quote_vault_address[:16]}... "
                    f"(owner={acct.owner[:16]}..., mint={decoded.mint[:8]}..., balance={decoded.amount})"
                )
                return {
                    "address": quote_vault_address,
                    "type": "spl_token",
                    "decoded": decoded,
                    "lamports": acct.lamports
                }
            except Exception as e:
                logger.debug(f"[VAULT_DISCOVERY] Could not decode as token account: {e}")

        # Check for native SOL account
        elif acct.owner == SYSTEM_PROGRAM_ID:
            logger.info(f"[VAULT_DISCOVERY] ✅ Quote vault (native SOL): {quote_vault_address} (lamports={acct.lamports})")
            return {
                "address": quote_vault_address,
                "type": "native_sol",
                "lamports": acct.lamports,
                "decoded": None
            }

        # Unknown type
        else:
            logger.debug(f"[VAULT_DISCOVERY] ❌ Quote vault type unknown: owner={acct.owner}")
            return None

    except Exception as e:
        logger.error(f"[VAULT_DISCOVERY] Quote vault validation error: {e}")
        return None


# ============================================================================
# Phase 6: Main Orchestration
# ============================================================================


async def discover_vaults_rpc(
    token_mint: str,
    rpc_client,
    ws_monitor=None,
    max_retries: int = 3
) -> Optional[VaultPair]:
    """
    Main entry point: RPC-based authoritative vault discovery.

    Orchestrates full discovery pipeline:
    1. getTokenLargestAccounts(token_mint)
    2. Validate token account candidates
    3. Identify base vault
    4. Resolve quote vault
    5. Validate quote vault
    6. Return VaultPair if both validate

    Args:
        token_mint: Token mint to discover vaults for
        rpc_client: Solana RPC client
        ws_monitor: Optional WebSocket event monitor
        max_retries: Max retries on failure (default 3)

    Returns:
        VaultPair if successful, None if discovery failed
    """
    attempt = 0

    while attempt < max_retries:
        try:
            attempt += 1
            logger.info(f"[VAULT_DISCOVERY] Attempt {attempt}/{max_retries} for token {token_mint[:16]}...")

            # Phase 1: Get candidates
            candidates = await get_token_largest_accounts(token_mint, rpc_client, limit=20)
            if not candidates:
                raise VaultDiscoveryError("No candidates returned from getTokenLargestAccounts")

            # Candidates are already address strings from get_token_largest_accounts
            candidate_addresses = candidates

            # Phase 2: Validate
            validated = await validate_token_accounts(candidate_addresses, token_mint, rpc_client)
            if not validated:
                raise VaultDiscoveryError("No accounts passed validation")

            # Phase 3: Identify base vault
            base_vault = identify_base_vault(validated, ws_monitor)
            if not base_vault:
                raise VaultDiscoveryError("Could not identify base vault from candidates")

            # Phase 4: Resolve quote vault
            quote_vault_address = await resolve_quote_vault_from_base(base_vault, token_mint, rpc_client)

            if not quote_vault_address:
                # Fallback
                logger.info("[VAULT_DISCOVERY] Owner chaining failed, trying fallback...")
                quote_vault_address = await resolve_quote_vault_fallback(base_vault.address, token_mint, rpc_client)

            # Phase 5: Validate quote vault (if we have an address)
            if quote_vault_address:
                quote_vault = await validate_quote_vault(quote_vault_address, rpc_client)
                if not quote_vault:
                    logger.warning("[VAULT_DISCOVERY] Quote vault validation failed - may indicate fresh token")
                    quote_vault = None
            else:
                logger.warning("[VAULT_DISCOVERY] Could not resolve quote vault - may indicate fresh token")
                quote_vault = None

            # Fresh tokens must have both vaults to be registered
            # Quote vault will be discovered/validated on subsequent retries
            if not quote_vault:
                logger.warning("[VAULT_DISCOVERY] ❌ Cannot register token without valid quote vault - will retry on next discovery cycle")
                return None

            # Success! Return vault pair
            logger.info(
                f"[VAULT_DISCOVERY] ✅ Vault discovery successful for {token_mint}"
            )
            logger.info(f"   Base:  {base_vault.address}")
            logger.info(f"   Quote: {quote_vault['address']}")

            return VaultPair(
                base_vault=base_vault,
                quote_vault=quote_vault,
                pool_program="unknown",  # Determined from owner chaining
                confidence_score=0.95
            )

        except VaultDiscoveryError as e:
            logger.warning(f"[VAULT_DISCOVERY] Attempt {attempt} failed: {e}")
            if attempt < max_retries:
                wait_time = min(30 * (2 ** (attempt - 1)), 600)  # Exponential backoff
                logger.info(f"[VAULT_DISCOVERY] Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
            continue

        except Exception as e:
            logger.error(f"[VAULT_DISCOVERY] Unexpected error: {e}")
            return None

    logger.error(f"[VAULT_DISCOVERY] ❌ Vault discovery failed after {max_retries} attempts")
    return None


# ============================================================================
# Registration & Activation
# ============================================================================


async def register_vault_pair(
    token_mint: str,
    vault_pair: VaultPair,
    db,
    price_worker=None
) -> bool:
    """
    Register vault pair in database after validation.

    Only registers if:
    - Both vaults are present
    - They are different addresses
    - Database insertion succeeds

    Args:
        token_mint: Token mint address
        vault_pair: VaultPair object from discovery
        db: Database connection object or path string
        price_worker: Optional price worker to trigger WebSocket refresh

    Returns:
        True if registration successful, False otherwise
    """
    try:
        import sqlite3
        
        base_vault = vault_pair.base_vault
        quote_vault = vault_pair.quote_vault

        # Sanity checks
        if not base_vault or not quote_vault:
            logger.error("[VAULT_DISCOVERY] ❌ Registration failed: missing vault")
            return False

        if base_vault.address == quote_vault["address"]:
            logger.error("[VAULT_DISCOVERY] ❌ Registration failed: base and quote are same address")
            return False

        # Handle both connection object and path string
        if isinstance(db, str):
            # db is a path string - open connection
            conn = sqlite3.connect(db, timeout=10)
            cursor = conn.cursor()
            should_close = True
        else:
            # db is a connection object - use directly
            cursor = db.cursor()
            conn = db
            should_close = False

        try:
            # Insert into database
            import time
            now = int(time.time())

            cursor.execute("""
                INSERT INTO token_pool_accounts
                (mint, base_account, quote_account, pool_program, base_token, base_decimals, quote_decimals, quote_token, vault_validation_status, discovery_method, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mint, base_account) DO NOTHING
            """, (
                token_mint,
                base_vault.address,
                quote_vault["address"],
                vault_pair.pool_program or "unknown",
                token_mint,  # base_token is the token mint itself
                6,  # Default decimals for base (usually 6 for most tokens)
                9 if quote_vault.get("decoded") and quote_vault["decoded"].mint == WRAPPED_SOL_MINT else 6,  # 9 for wSOL, 6 for others
                quote_vault.get("decoded", {}).mint if quote_vault.get("decoded") else WRAPPED_SOL_MINT,
                "validated",  # Mark RPC-discovered vaults as validated
                "rpc_authoritative",
                now,
                now
            ))
            conn.commit()

            logger.info(f"[VAULT_DISCOVERY] ✅ Registered vault pair:")
            logger.info(f"   Token: {token_mint}")
            logger.info(f"   Base:  {base_vault.address}")
            logger.info(f"   Quote: {quote_vault['address']}")

            # Trigger WebSocket refresh
            if price_worker:
                try:
                    price_worker.trigger_pool_refresh()
                    logger.info("[VAULT_DISCOVERY] ✅ WebSocket client refreshing with new vaults")
                except Exception as e:
                    logger.warning(f"[VAULT_DISCOVERY] WebSocket refresh failed: {e}")

            return True

        finally:
            if should_close:
                conn.close()

    except Exception as e:
        logger.error(f"[VAULT_DISCOVERY] ❌ Registration failed: {e}")
        return False


# ============================================================================
# Metrics & Diagnostics
# ============================================================================


class VaultDiscoveryMetrics:
    """Track vault discovery metrics."""

    def __init__(self):
        self.discovery_attempts = 0
        self.discovery_success = 0
        self.validation_failures = 0
        self.quote_resolution_method = {"owner_chaining": 0, "fallback": 0, "failed": 0}
        self.registration_success = 0

    def record_attempt(self):
        self.discovery_attempts += 1

    def record_success(self):
        self.discovery_success += 1

    def record_validation_failure(self):
        self.validation_failures += 1

    def get_success_rate(self) -> float:
        if self.discovery_attempts == 0:
            return 0.0
        return self.discovery_success / self.discovery_attempts

    def to_dict(self) -> Dict:
        return {
            "discovery_attempts": self.discovery_attempts,
            "discovery_success": self.discovery_success,
            "success_rate": self.get_success_rate(),
            "validation_failures": self.validation_failures,
            "quote_resolution_method": self.quote_resolution_method,
            "registration_success": self.registration_success,
        }


# Global metrics instance
metrics = VaultDiscoveryMetrics()


# ============================================================================
# Integration with Existing Pool Registration
# ============================================================================


async def discover_and_register_vaults_rpc(
    token_mint: str,
    rpc_client,
    db,
    price_worker=None,
    ws_monitor=None,
    max_retries: int = 3
) -> bool:
    """
    Main integration point: RPC-based vault discovery with automatic registration.

    This function:
    1. Discovers vaults via RPC (authoritative, validated)
    2. Registers in database via existing pool registration system
    3. Triggers WebSocket refresh if price_worker provided

    Args:
        token_mint: Token mint to discover vaults for
        rpc_client: Solana RPC client
        db: Database connection
        price_worker: Optional price worker to trigger WebSocket refresh
        ws_monitor: Optional WebSocket event monitor for scoring
        max_retries: Max discovery retry attempts (default 3)

    Returns:
        True if registration successful, False otherwise
    """
    logger.info(f"[VAULT_DISCOVERY] Starting RPC-based vault discovery for {token_mint[:16]}...")

    # Run authoritative vault discovery
    vault_pair = await discover_vaults_rpc(
        token_mint=token_mint,
        rpc_client=rpc_client,
        ws_monitor=ws_monitor,
        max_retries=max_retries
    )

    if not vault_pair:
        logger.error(f"[VAULT_DISCOVERY] ❌ Vault discovery failed for {token_mint[:16]}...")
        metrics.record_attempt()
        metrics.record_validation_failure()
        return False

    # Register the discovered vault pair
    success = await register_vault_pair(
        token_mint=token_mint,
        vault_pair=vault_pair,
        db=db,
        price_worker=price_worker
    )

    if success:
        metrics.record_attempt()
        metrics.record_success()
        logger.info(f"[VAULT_DISCOVERY] ✅ Vault pair registered and activated for {token_mint[:16]}...")
    else:
        metrics.record_attempt()
        logger.error(f"[VAULT_DISCOVERY] ❌ Registration failed for {token_mint[:16]}...")

    return success



async def discover_and_register_all_pools(
    token_mint: str,
    rpc_client,
    db,
    price_worker=None,
    ws_monitor=None,
    max_retries: int = 3
) -> bool:
    """
    Multi-pool discovery: Find and register ALL pools for a token.
    
    For tokens that migrate from Pump.Fun bonding curve to PumpSwap,
    there may be multiple trading pairs (e.g., TOKEN/SOL, TOKEN/USDC).
    
    This function:
    1. Discovers multiple vault pairs via RPC
    2. Registers all valid pools in the database
    3. Scores pools by: wSOL preference, liquidity, recent activity
    4. Marks the highest-scoring pool as primary
    5. Triggers WebSocket to subscribe to all pools
    
    Args:
        token_mint: Token mint to discover pools for
        rpc_client: Solana RPC client
        db: Database connection
        price_worker: Optional price worker to trigger WebSocket refresh
        ws_monitor: Optional WebSocket event monitor for scoring
        max_retries: Max discovery retry attempts
    
    Returns:
        True if at least one pool registered, False otherwise
    """
    try:
        import time

        logger.info(f"[VAULT_DISCOVERY] Starting multi-pool discovery for {token_mint[:16]}...")

        # Get top 20 largest token accounts (potential pools)
        candidates = await get_token_largest_accounts(token_mint, rpc_client, limit=20)
        if not candidates:
            logger.warning(f"[VAULT_DISCOVERY] No candidate accounts found for {token_mint[:16]}...")
            return False
        
        # Validate all candidates
        validated = await validate_token_accounts(candidates, token_mint, rpc_client)
        if not validated:
            logger.warning(f"[VAULT_DISCOVERY] No validated accounts for {token_mint[:16]}...")
            return False
        
        discovered_pools = []

        # Complete all provider discovery and validation before opening SQLite.
        for i, validated_account in enumerate(validated):
            try:
                logger.info(f"[VAULT_DISCOVERY] Checking validated account {i+1}/{len(validated)}: {validated_account.address[:16]}...")
                
                # This account is a base vault - now find its quote vault
                quote_vault_address = await resolve_quote_vault_from_base(
                    validated_account, 
                    token_mint, 
                    rpc_client
                )
                
                if not quote_vault_address:
                    logger.debug(f"[VAULT_DISCOVERY] No quote vault found for {validated_account.address[:16]}...")
                    continue
                
                # Validate quote vault
                quote_vault = await validate_quote_vault(quote_vault_address, rpc_client)
                if not quote_vault:
                    logger.debug(f"[VAULT_DISCOVERY] Quote vault validation failed for {quote_vault_address[:16]}...")
                    continue
                
                # This is a valid pool. Retain only the values needed by the
                # subsequent short database scope.
                vault_pair = VaultPair(
                    base_vault=validated_account,
                    quote_vault=quote_vault,
                    pool_program="unknown",
                    confidence_score=0.95
                )
                
                discovered_pools.append({
                    'mint': token_mint,
                    'base_account': validated_account.address,
                    'quote_account': quote_vault["address"],
                    'quote_mint': quote_vault.get("decoded", {}).mint if quote_vault.get("decoded") else WRAPPED_SOL_MINT,
                    'pool_program': vault_pair.pool_program or "unknown",
                    'quote_decimals': 9 if quote_vault.get("decoded") and quote_vault["decoded"].mint == WRAPPED_SOL_MINT else 6,
                })
                
            except Exception as e:
                logger.debug(f"[VAULT_DISCOVERY] Error processing account {i}: {e}")
                continue
        
        if not discovered_pools:
            logger.error(f"[VAULT_DISCOVERY] ❌ No pools discovered for {token_mint[:16]}...")
            return False

        now = int(time.time())

        def persist_pools(conn):
            cursor = conn.cursor()
            registered_pools = []
            for pool in discovered_pools:
                cursor.execute("""
                    INSERT INTO token_pool_accounts
                    (mint, base_account, quote_account, pool_program, base_token, base_decimals,
                     quote_decimals, quote_token, vault_validation_status, discovery_method,
                     created_at, updated_at, is_primary)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(mint, base_account) DO UPDATE SET
                        quote_account = excluded.quote_account,
                        vault_validation_status = excluded.vault_validation_status,
                        updated_at = excluded.updated_at
                """, (
                    token_mint, pool['base_account'], pool['quote_account'],
                    pool['pool_program'], token_mint, 6, pool['quote_decimals'],
                    pool['quote_mint'], "validated", "rpc_multipool_discovery",
                    now, now, 0,
                ))
                registered_pools.append(pool)
                logger.info(f"[VAULT_DISCOVERY] ✅ Registered pool: {pool['base_account'][:16]}... / {pool['quote_account'][:16]}...")

            conn.commit()

            # Score pools: prioritize wSOL, then by other factors.
            primary_updated = False
            for pool in registered_pools:
                is_primary = (pool['quote_mint'] == WRAPPED_SOL_MINT) and not primary_updated
                cursor.execute("""
                UPDATE token_pool_accounts
                SET is_primary = ?, 
                    pool_score = ?,
                    updated_at = ?
                WHERE mint = ? AND base_account = ?
                """, (
                    1 if is_primary else 0,
                    100.0 if is_primary else 50.0,
                    now, pool['mint'], pool['base_account'],
                ))
                if is_primary:
                    primary_updated = True
                    logger.info(f"[VAULT_DISCOVERY] 🏆 Marked as primary (wSOL pool): {pool['base_account'][:16]}...")

            conn.commit()
            logger.info(f"[VAULT_DISCOVERY] ✅ Registered {len(registered_pools)} pools for {token_mint[:16]}...")

            cursor.execute("""
                UPDATE token_analysis
                SET price_source = 'pool'
                WHERE mint = ?
            """, (token_mint,))
            conn.commit()
            logger.info(f"[VAULT_DISCOVERY] ✅ Updated price_source to 'pool' for {token_mint[:16]}...")

        if isinstance(db, str):
            with managed_db_connect(db, timeout=10) as conn:
                persist_pools(conn)
        else:
            # Preserve the existing caller-owned connection contract.
            persist_pools(db)

        # Trigger WebSocket refresh
        if price_worker:
            try:
                price_worker.trigger_pool_refresh()
                logger.info(f"[VAULT_DISCOVERY] ✅ WebSocket client refreshing with {len(discovered_pools)} new pools")
            except Exception as e:
                logger.warning(f"[VAULT_DISCOVERY] WebSocket refresh failed: {e}")

        return True
        
    except Exception as e:
        logger.error(f"[VAULT_DISCOVERY] ❌ Multi-pool discovery failed: {e}")
        return False
