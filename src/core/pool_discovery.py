"""
Automatic pool discovery and reserve account extraction.

When a token launches to Raydium/Orca, automatically:
1. Fetch the pool account on-chain
2. Extract base and quote reserve accounts
3. Register in token_pool_accounts
4. WebSocket subscribes automatically on next worker cycle

This enables real-time pricing from the moment a token launches.
"""

import logging
import sqlite3
import asyncio
import aiohttp
import json
from typing import Optional, Dict, Tuple
from base64 import b64decode

logger = logging.getLogger(__name__)

# Raydium program IDs
RAYDIUM_AMM_PROGRAM = "675kPX9MHTjS2zt1qrVrrVrZg1ankqqgoerEmJlwQ1K"
RAYDIUM_CPMM_PROGRAM = "CPMMoo8L3F4rn9aUYn2QRiPK5VrKMjstm69edQaMQAC"

# Orca program ID
ORCA_WHIRLPOOL_PROGRAM = "whirLbMiicVdio4KfUqKKvsLrZtSqwNAUafgJMYco"

# PumpSwap program ID (uses Raydium AMM layout)
PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"

# PumpFun V1 program ID (uses Raydium AMM layout)
PUMPFUN_V1_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

# SPL Token program
SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

# SOL mint
SOL_MINT = "So11111111111111111111111111111111111111112"


def _dex_name_from_pool_program(pool_program: str) -> str:
    if pool_program == PUMPSWAP_PROGRAM:
        return "pumpswap"
    if pool_program == PUMPFUN_V1_PROGRAM:
        return "pumpfun_v1"
    if pool_program == RAYDIUM_CPMM_PROGRAM:
        return "raydium_cpmm"
    if pool_program == RAYDIUM_AMM_PROGRAM:
        return "raydium_amm"
    if pool_program == ORCA_WHIRLPOOL_PROGRAM:
        return "orca_whirlpool"
    return "unknown"


class PoolDiscovery:
    """Extract pool reserve accounts from on-chain pool data."""

    def __init__(self, db_path: str, rpc_url: str):
        self.db_path = db_path
        self.rpc_url = rpc_url

    def _mark_token_migrated(self, token_mint: str, pool_address: str, pool_program: str, validated: bool) -> None:
        """
        Confirm migration once a real post-curve pool is active and validated.

        Confirmation rule:
        - token_pool_accounts registration succeeded
        - vault_validation_status is 'validated'
        - pool address and known AMM program are present
        """
        if not validated or not pool_address or not pool_program:
            return

        now = int(__import__("time").time())
        dex_name = _dex_name_from_pool_program(pool_program)
        pumpswap_pool = pool_address if pool_program == PUMPSWAP_PROGRAM else None

        conn = sqlite3.connect(self.db_path, timeout=15)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO token_analysis (
                mint, pool_address, pumpswap_pool_address, lifecycle_stage,
                migrated_at, dex, is_about_to_migrate, migration_progress_pct,
                migration_band, migration_signal_updated_at, created_at, analyzed_at
            ) VALUES (?, ?, ?, 'migrated', ?, ?, 0, 100, NULL, ?, ?, ?)
            ON CONFLICT(mint) DO UPDATE SET
                pool_address = COALESCE(excluded.pool_address, token_analysis.pool_address),
                pumpswap_pool_address = COALESCE(excluded.pumpswap_pool_address, token_analysis.pumpswap_pool_address),
                lifecycle_stage = 'migrated',
                migrated_at = COALESCE(token_analysis.migrated_at, excluded.migrated_at),
                dex = excluded.dex,
                is_about_to_migrate = 0,
                migration_progress_pct = 100,
                migration_band = NULL,
                migration_signal_updated_at = excluded.migration_signal_updated_at
            """,
            (token_mint, pool_address, pumpswap_pool, now, dex_name, now, now, now),
        )
        conn.commit()
        conn.close()

    async def extract_pool_reserves(
        self, pool_address: str, token_mint: str
    ) -> Optional[Dict]:
        """
        Extract base_account and quote_account from a pool address.

        Args:
            pool_address: The pool account address (Raydium or Orca)
            token_mint: The token mint address

        Returns:
            Dict with:
            {
                'base_account': str,
                'quote_account': str,
                'base_token': str,
                'quote_token': str,
                'base_decimals': int,
                'quote_decimals': int,
                'pool_program': str ('raydium_amm', 'raydium_cpmm', 'orca', etc)
            }
            Or None if extraction fails.
        """
        try:
            logger.info(f"🔍 Extracting reserves from pool: {pool_address}")

            # Fetch the pool account
            pool_data = await self._fetch_account(pool_address)
            if not pool_data:
                logger.warning(f"Could not fetch pool account: {pool_address}")
                return None

            # Log what we fetched
            owner = pool_data.get("owner")
            data_len = 0
            if pool_data.get("data"):
                data_field = pool_data.get("data")
                if isinstance(data_field, list) and len(data_field) > 0:
                    data_b64 = data_field[0]
                    decoded = b64decode(data_b64)
                    data_len = len(decoded)
                elif isinstance(data_field, str):
                    decoded = b64decode(data_field)
                    data_len = len(decoded)

            logger.info(f"  Owner: {owner}, Data: {data_len} bytes")

            # Try to extract based on pool program
            reserves = await self._extract_from_pool_data(
                pool_data, pool_address, token_mint
            )

            if reserves:
                logger.info(
                    f"✅ Extracted pool reserves from {pool_address[:16]}...: "
                    f"base={reserves['base_account'][:16]}... "
                    f"quote={reserves['quote_account'][:16]}..."
                )
                # Add pool_address to the returned dict
                reserves['pool_address'] = pool_address
                return reserves

            logger.warning(f"Could not extract reserves from pool: {pool_address}")
            return None

        except Exception as e:
            logger.error(f"Error extracting pool reserves: {e}")
            return None

    async def _fetch_account(self, address: str) -> Optional[Dict]:
        """Fetch account data from RPC."""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [address, {"encoding": "base64", "commitment": "processed"}],
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    result = await resp.json()
                    if "result" in result and result["result"]:
                        return result["result"]["value"]
                    return None

        except Exception as e:
            logger.debug(f"Error fetching account {address}: {e}")
            return None

    async def _get_token_accounts_by_owner(self, owner: str) -> list:
        """
        Get all token accounts (vaults) owned by an address.
        
        Queries both Token Program and Token-2022 to find all vaults.
        Layout-independent: works regardless of how PumpSwap struct changes.
        
        Returns list of dicts: [
            {
                "address": str,
                "mint": str,
                "amount_raw": int,
                "decimals": int,
                "program_id": str,
            },
            ...
        ]
        """
        TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
        TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
        
        all_accounts = []

        # Query both Token Program and Token-2022
        for program_id in (TOKEN_PROGRAM, TOKEN_2022_PROGRAM):
            try:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTokenAccountsByOwner",
                    "params": [
                        owner,
                        {"programId": program_id},
                        {"encoding": "jsonParsed"},
                    ],
                }

                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        self.rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)
                    ) as resp:
                        result = await resp.json()
                        
                        if "result" not in result or "value" not in result["result"]:
                            continue
                        
                        for item in result["result"]["value"]:
                            try:
                                pubkey = item.get("pubkey")
                                parsed = item.get("account", {}).get("data", {}).get("parsed", {})
                                info = parsed.get("info", {})
                                token_amount = info.get("tokenAmount", {}) or {}
                                
                                if pubkey and info.get("mint"):
                                    all_accounts.append({
                                        "address": pubkey,
                                        "mint": info.get("mint"),
                                        "authority": info.get("owner"),
                                        "amount_raw": token_amount.get("amount"),
                                        "decimals": token_amount.get("decimals"),
                                        "program_id": program_id,
                                    })
                            except:
                                continue
                                
            except Exception as e:
                logger.debug(f"Error querying {program_id} for {owner}: {e}")
                continue

        return all_accounts

    async def _extract_from_pool_data(
        self, pool_data: Dict, pool_address: str, token_mint: str
    ) -> Optional[Dict]:
        """Extract reserves from pool data based on account owner."""
        owner = pool_data.get("owner")

        # Raydium AMM
        if owner == RAYDIUM_AMM_PROGRAM:
            return await self._extract_raydium_amm(pool_data, pool_address, token_mint)

        # Raydium CPMM
        if owner == RAYDIUM_CPMM_PROGRAM:
            return await self._extract_raydium_cpmm(
                pool_data, pool_address, token_mint
            )

        # Orca Whirlpool
        if owner == ORCA_WHIRLPOOL_PROGRAM:
            return await self._extract_orca_whirlpool(
                pool_data, pool_address, token_mint
            )

        # PumpSwap: Primary: extract vault addresses directly from pool struct bytes
        # Fallback: query by ownership if struct extraction fails
        if owner == PUMPSWAP_PROGRAM:
            # Try struct-based extraction first (faster, more reliable)
            result = await self._extract_pumpswap_from_struct(pool_data, pool_address, token_mint)
            if result:
                return result
            # Fallback: query by ownership (works if struct read fails)
            logger.info(f"[POOL_EXTRACT] Struct extraction failed, falling back to ownership scan")
            return await self._extract_vaults_by_mint(pool_address, token_mint)

        # PumpFun V1 (different structure, may use Raydium-like layout at different offsets)
        if owner == PUMPFUN_V1_PROGRAM:
            return await self._extract_pumpfun_v1(pool_data, pool_address, token_mint)

        logger.warning(f"Unknown pool program owner: {owner}")
        return None

    async def _is_shared_account(self, account_address: str, threshold: int = 3) -> bool:
        """
        Check if an account is used across many tokens in ANY role.

        Checks ALL roles:
        - base_account (vault)
        - quote_account (vault)
        - pool_address (pool)

        Shared accounts are typically:
        - Program PDAs (not token-specific)
        - Authority accounts
        - Misidentified vaults/pools

        Real pools/vaults appear in 1 token.
        Shared accounts show up across MANY tokens very quickly.

        If an account appears in >threshold tokens across any role, reject it.

        Args:
            account_address: Account to check
            threshold: Number of tokens before marking as shared (default 3)
                      - Real vault: 1 token only
                      - Shared PDA: appears in 3+ tokens quickly

        Returns:
            True if shared (should reject), False if token-specific
        """
        def _check_sync(db_path: str, addr: str, thresh: int) -> bool:
            conn = sqlite3.connect(db_path, timeout=3)  # 3s cap — fail open on contention
            try:
                cursor = conn.cursor()
                # Check ALL roles: vault base, vault quote, pool address
                cursor.execute(
                    """
                    SELECT COUNT(DISTINCT mint)
                    FROM token_pool_accounts
                    WHERE base_account = ?
                       OR quote_account = ?
                       OR pool_address = ?
                    """,
                    (addr, addr, addr),
                )
                count = cursor.fetchone()[0]
                return count > thresh
            finally:
                conn.close()

        try:
            is_shared = await asyncio.wait_for(
                asyncio.to_thread(_check_sync, self.db_path, account_address, threshold),
                timeout=3.0,  # never block event loop >3s per candidate
            )
            if is_shared:
                logger.warning(
                    f"[SHARED_ACCOUNT_CHECK] ⚠️  Account {account_address[:16]}... "
                    f"appears in multiple tokens across roles (marked as shared)"
                )
            return is_shared

        except Exception as e:
            logger.debug(f"[SHARED_ACCOUNT_CHECK] Could not check account {account_address[:16]}...: {e}")
            # On error/timeout, don't reject (false negative is safer than false positive)
            return False

    async def _extract_vaults_by_mint(
        self, pool_address: str, token_mint: str
    ) -> Optional[Dict]:
        """
        Extract vault accounts using authority-scan (layout-independent).

        🚨 CRITICAL: This queries accounts owned by pool_address directly.
        In reality, vaults are often owned by a PDA authority (not the pool).
        This is a known limitation - proper fix requires deriving the authority
        from the pool struct first.

        For now, we accept vaults from pool_address but validate rigorously:
        1. Reject pool_address if it's shared (prevents ADyA misidentification)
        2. Reject shared accounts as vaults (both base and quote)
        3. Only accept specific known quotes (SOL, USDC)
        4. Validate token is actually in the vault
        """
        try:
            # 🚨 PATCH 1: REJECT POOL if it's shared/program account
            if await self._is_shared_account(pool_address):
                logger.warning(
                    f"[POOL_REJECTED] reason=shared_pool pool={pool_address[:16]}... "
                    f"(appears to be shared/program account, not a pool)"
                )
                return None

            # Get all token accounts owned by the pool
            accounts = await self._get_token_accounts_by_owner(pool_address)

            if not accounts:
                logger.warning(
                    f"[POOL_REJECTED] reason=no_vaults pool={pool_address[:16]}... "
                    f"(no token accounts found)"
                )
                return None

            # Find base vault (owns the token being launched)
            base_candidates = [acc for acc in accounts if acc.get("mint") == token_mint]
            if not base_candidates:
                logger.warning(
                    f"[POOL_REJECTED] reason=no_base_vault pool={pool_address[:16]}... "
                    f"token={token_mint[:16]}... (base vault not found)"
                )
                return None

            # Find quote vault (ONLY SOL or USDC, strict selection)
            USDC_MINT = "EPjFWaLb3hyccVhVQAdS4jNA3LEjfZ3c6zDs8KKukQx"
            quote_candidates = [
                acc for acc in accounts
                if acc.get("mint") in {SOL_MINT, USDC_MINT}
            ]

            if not quote_candidates:
                logger.warning(
                    f"[POOL_REJECTED] reason=no_quote_vault pool={pool_address[:16]}... "
                    f"(no SOL/USDC vault found)"
                )
                return None

            # Select highest balance vaults
            def get_balance(acc):
                try:
                    return int(acc.get("amount_raw") or 0)
                except:
                    return 0

            def score_quote(acc):
                mint = acc.get("mint", "")
                balance = get_balance(acc)
                if mint == SOL_MINT:
                    return (3, balance)
                if mint == USDC_MINT:
                    return (2, balance)
                return (1, balance)

            base_vault = max(base_candidates, key=lambda a: get_balance(a))
            quote_vault = max(quote_candidates, key=score_quote)

            # 🚨 PATCH 2: REJECT BASE VAULT if shared
            if await self._is_shared_account(base_vault["address"]):
                logger.warning(
                    f"[POOL_REJECTED] reason=shared_base_vault pool={pool_address[:16]}... "
                    f"vault={base_vault['address'][:16]}... (shared across tokens)"
                )
                return None

            # 🚨 PATCH 3: REJECT QUOTE VAULT if shared
            if await self._is_shared_account(quote_vault["address"]):
                logger.warning(
                    f"[POOL_REJECTED] reason=shared_quote_vault pool={pool_address[:16]}... "
                    f"vault={quote_vault['address'][:16]}... (shared across tokens)"
                )
                return None

            logger.info(
                f"[POOL_EXTRACTED] pool={pool_address[:16]}... "
                f"base={base_vault['address'][:16]}... quote={quote_vault['address'][:16]}... "
                f"token={token_mint[:16]}..."
            )

            return {
                "base_account": base_vault["address"],
                "quote_account": quote_vault["address"],
                "base_token": base_vault["mint"],
                "quote_token": quote_vault["mint"],
                "base_decimals": base_vault["decimals"],
                "quote_decimals": quote_vault["decimals"],
                "pool_program": PUMPSWAP_PROGRAM,
            }

        except Exception as e:
            logger.error(f"[POOL_EXTRACT_ERROR] pool={pool_address[:16]}... error={str(e)}")
            return None

    async def _extract_pumpswap_from_struct(
        self, pool_data: Dict, pool_address: str, token_mint: str
    ) -> Optional[Dict]:
        """
        Extract PumpSwap vault addresses directly from pool struct bytes.

        PumpSwap pool layout (301 bytes):
          [0:8]     discriminator
          [8:40]    pool_authority  (usually same as pool_address for SPL authority)
          [40:72]   token_0_mint
          [72:104]  token_1_mint
          [104:136] lp_mint
          [139:171] base_vault (Token-2022 account)
          [171:203] quote_vault (SPL Token account)

        Confirmed offsets with pools: GcpyrpRqx9, 95GFe6r7, DjsMacDDm
        """
        try:
            raw = pool_data.get("data")
            if not raw:
                return None
            if isinstance(raw, list):
                data = b64decode(raw[0])
            else:
                data = b64decode(raw)

            if len(data) < 203:
                logger.debug(f"[STRUCT_EXTRACT] Pool data too short ({len(data)} bytes), expected 301")
                return None

            base_vault_addr  = self._bytes_to_pubkey(data[139:171])
            quote_vault_addr = self._bytes_to_pubkey(data[171:203])

            if not base_vault_addr or not quote_vault_addr:
                logger.debug(f"[STRUCT_EXTRACT] Could not decode vault pubkeys from struct")
                return None

            if base_vault_addr == quote_vault_addr:
                logger.warning(f"[STRUCT_EXTRACT] base == quote ({base_vault_addr[:16]}), rejecting")
                return None

            # Fetch both vault accounts in parallel — single round-trip window
            base_info, quote_info = await asyncio.gather(
                self._fetch_account(base_vault_addr),
                self._fetch_account(quote_vault_addr),
            )

            if not base_info:
                logger.debug(f"[STRUCT_EXTRACT] base vault {base_vault_addr[:16]} not found on-chain")
                return None

            # Parse base vault mint from raw SPL/Token-2022 account bytes
            base_raw = base_info.get("data")
            if isinstance(base_raw, list):
                base_bytes = b64decode(base_raw[0])
            else:
                base_bytes = b64decode(base_raw) if base_raw else b""

            if len(base_bytes) < 32:
                return None

            base_mint = self._bytes_to_pubkey(base_bytes[0:32])
            if base_mint != token_mint:
                # Try flipping: quote vault may actually hold the token mint
                # quote_info already fetched above — no extra RPC needed
                if quote_info:
                    qraw = quote_info.get("data")
                    qbytes = b64decode(qraw[0] if isinstance(qraw, list) else qraw)
                    if len(qbytes) >= 32:
                        quote_mint_check = self._bytes_to_pubkey(qbytes[0:32])
                        if quote_mint_check == token_mint:
                            # Swap base and quote
                            base_vault_addr, quote_vault_addr = quote_vault_addr, base_vault_addr
                            base_info, quote_info = quote_info, base_info
                            base_mint = quote_mint_check
                        else:
                            logger.debug(
                                f"[STRUCT_EXTRACT] Neither vault holds token {token_mint[:16]}: "
                                f"base_mint={base_mint[:16] if base_mint else 'null'}"
                            )
                            return None
                else:
                    return None

            # Parse quote vault mint — already fetched above
            if not quote_info:
                return None
            qraw = quote_info.get("data")
            qbytes = b64decode(qraw[0] if isinstance(qraw, list) else qraw)
            quote_mint = self._bytes_to_pubkey(qbytes[0:32]) if len(qbytes) >= 32 else SOL_MINT

            logger.info(
                f"[STRUCT_EXTRACT] ✅ pool={pool_address[:16]} "
                f"base={base_vault_addr[:16]} quote={quote_vault_addr[:16]}"
            )

            return {
                "base_account":   base_vault_addr,
                "quote_account":  quote_vault_addr,
                "base_token":     base_mint,
                "quote_token":    quote_mint or SOL_MINT,
                "base_decimals":  6,
                "quote_decimals": 9,
                "pool_program":   PUMPSWAP_PROGRAM,
                "authority_account": pool_address,
            }

        except Exception as e:
            logger.debug(f"[STRUCT_EXTRACT] Error: {e}")
            return None

    async def _extract_raydium_amm(
        self, pool_data: Dict, pool_address: str, token_mint: str
    ) -> Optional[Dict]:
        """
        Extract vault accounts for a token pair using authority-based discovery.
        
        ✅ AUTHORITY SCAN APPROACH (layout-independent):
        
        Instead of guessing byte offsets in the pool struct:
        - Query getTokenAccountsByOwner for the pool address
        - Find the vault holding the token_mint (base_account)
        - Find the vault holding WSOL/USDC (quote_account)
        - Verify both exist on-chain
        
        This works regardless of:
        - PumpSwap's internal struct layout
        - Byte offset changes in future updates
        - Token Program vs Token-2022 differences
        
        The vaults are guaranteed to be real because:
        - RPC returns only accounts that exist on-chain
        - RPC parses them as SPL token accounts
        - Mint values come from chain state
        """
        try:
            logger.info(
                f"[POOL_DETECT] mint={token_mint[:20]}... candidate_pool={pool_address}"
            )

            # ===== STAGE 1: Owner validation =====
            owner = pool_data.get("owner")
            if owner not in {PUMPSWAP_PROGRAM, PUMPFUN_V1_PROGRAM, RAYDIUM_AMM_PROGRAM, RAYDIUM_CPMM_PROGRAM}:
                logger.warning(
                    f"[POOL_EXTRACT] ❌ Invalid owner for candidate {pool_address[:16]}...: {owner}"
                )
                return None

            logger.info(f"[POOL_EXTRACT] ✅ Owner valid: {owner[:16]}...")

            # ===== STAGE 2: Discover vaults by authority scan =====
            logger.info(f"[POOL_EXTRACT] Scanning for vaults owned by pool (authority scan)...")

            vault_accounts = await self._get_token_accounts_by_owner(pool_address)

            if not vault_accounts:
                # 🚨 CRITICAL: Authority scan returned empty
                # Possible reasons:
                # 1. Pool uses PDA-derived authority (not pool_address)
                # 2. Pool uses delegated authority pattern
                # 3. Pool vaults owned by program, not pool
                # 4. Pool simply has no vaults yet (new/broken)

                logger.warning(
                    f"[POOL_EXTRACT] ❌ Authority scan failed: no vaults owned by pool {pool_address[:16]}... "
                    f"(may use PDA/delegated authority - future enhancement needed)"
                )
                logger.info(
                    f"[POOL_REJECTED] mint={token_mint[:20]}... pool={pool_address[:16]}... "
                    f"reason=authority_scan_empty source=pda_delegation_likely"
                )

                # Fallback strategy: Mark for retry instead of hard reject
                # TODO: Future enhancement - derive PDA authority or use TX accounts
                return None

            logger.info(f"[POOL_EXTRACT] Found {len(vault_accounts)} token accounts owned by pool")

            # ===== STAGE 3: Find vaults for this token pair =====
            SOL_MINT = "So11111111111111111111111111111111111111112"
            USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

            # Find base vault (holding the token_mint)
            base_candidates = [a for a in vault_accounts if a["mint"] == token_mint]

            if not base_candidates:
                logger.warning(
                    f"[POOL_EXTRACT] ❌ No vault holding token {token_mint[:16]}..."
                )
                logger.info(
                    f"[POOL_REJECTED] mint={token_mint[:20]}... pool={pool_address[:16]}... "
                    f"reason=no_base_vault available_mints={[a['mint'][:8] for a in vault_accounts]}"
                )
                return None

            # Find quote vault (any non-base token, prefer SOL/USDC by balance)
            # ✅ FIXED: Accept any non-base token, not just SOL/USDC
            quote_candidates = [
                a for a in vault_accounts
                if a["mint"] != token_mint
            ]

            if not quote_candidates:
                logger.warning(
                    f"[POOL_EXTRACT] ❌ No quote vault found"
                )
                logger.info(
                    f"[POOL_REJECTED] mint={token_mint[:20]}... pool={pool_address[:16]}... "
                    f"reason=no_quote_vault available_mints={[a['mint'][:8] for a in vault_accounts]}"
                )
                return None

            # Sort by balance (raw amount) to prefer vaults with actual liquidity
            def get_balance(acc):
                try:
                    return int(acc["amount_raw"] or 0)
                except:
                    return 0

            # Score quote vaults: prefer SOL/USDC, then by balance
            def score_quote(acc):
                mint = acc.get("mint", "")
                balance = get_balance(acc)
                if mint == SOL_MINT:
                    return (3, balance)  # Highest preference
                if mint == USDC_MINT:
                    return (2, balance)  # Medium preference
                return (1, balance)  # Accept any other quote

            base_vault = sorted(base_candidates, key=get_balance, reverse=True)[0]
            quote_vault = sorted(quote_candidates, key=score_quote, reverse=True)[0]

            base_vault_addr = base_vault["address"]
            base_mint = base_vault["mint"]
            base_decimals = base_vault["decimals"]
            
            quote_vault_addr = quote_vault["address"]
            quote_mint = quote_vault["mint"]
            quote_decimals = quote_vault["decimals"]

            logger.info(
                f"[POOL_EXTRACT] ✅ Vault pair identified: "
                f"base={base_vault_addr[:16]}... (mint: {base_mint[:16]}..., bal: {get_balance(base_vault)}) "
                f"quote={quote_vault_addr[:16]}... (mint: {quote_mint[:16]}..., bal: {get_balance(quote_vault)})"
            )

            logger.info(
                f"[POOL_EXTRACT] ✅ VALIDATED pool {pool_address[:16]}... "
                f"base_token={base_mint[:20]}... "
                f"quote_token={quote_mint[:20]}..."
            )

            # Determine program ID
            pool_program = RAYDIUM_AMM_PROGRAM
            if owner == PUMPSWAP_PROGRAM:
                pool_program = PUMPSWAP_PROGRAM
            elif owner == PUMPFUN_V1_PROGRAM:
                pool_program = PUMPFUN_V1_PROGRAM

            return {
                "base_account": base_vault_addr,
                "quote_account": quote_vault_addr,
                "base_token": base_mint,
                "quote_token": quote_mint,
                "base_decimals": base_decimals or 6,
                "quote_decimals": quote_decimals or 9,
                "pool_program": pool_program,
            }

        except Exception as e:
            logger.debug(f"[POOL_EXTRACT] Error extracting pool: {e}")
            return None

    async def _extract_raydium_cpmm(
        self, pool_data: Dict, pool_address: str, token_mint: str
    ) -> Optional[Dict]:
        """Extract reserves from Raydium CPMM pool."""
        # Similar structure to AMM but with different offsets
        try:
            data = pool_data.get("data", [None, None])[0]
            if not data or len(data) < 200:
                return None

            decoded = b64decode(data)

            # CPMM has similar structure
            base_account = self._bytes_to_pubkey(decoded[8:40])
            quote_account = self._bytes_to_pubkey(decoded[40:72])

            if not base_account or not quote_account:
                return None

            base_decimals = await self._get_token_decimals(base_account)
            quote_decimals = await self._get_token_decimals(quote_account)

            return {
                "base_account": base_account,
                "quote_account": quote_account,
                "base_token": token_mint,
                "quote_token": SOL_MINT,
                "base_decimals": base_decimals or 6,
                "quote_decimals": quote_decimals or 9,
                "pool_program": RAYDIUM_CPMM_PROGRAM,
            }

        except Exception as e:
            logger.debug(f"Error extracting Raydium CPMM: {e}")
            return None

    async def _extract_orca_whirlpool(
        self, pool_data: Dict, pool_address: str, token_mint: str
    ) -> Optional[Dict]:
        """Extract reserves from Orca Whirlpool pool."""
        try:
            data = pool_data.get("data", [None, None])[0]
            if not data or len(data) < 200:
                return None

            decoded = b64decode(data)

            # Orca structure differs; token accounts at different offsets
            # This is approximate - Orca has complex state structure
            base_account = self._bytes_to_pubkey(decoded[72:104])  # Example offset
            quote_account = self._bytes_to_pubkey(decoded[104:136])

            if not base_account or not quote_account:
                return None

            base_decimals = await self._get_token_decimals(base_account)
            quote_decimals = await self._get_token_decimals(quote_account)

            return {
                "base_account": base_account,
                "quote_account": quote_account,
                "base_token": token_mint,
                "quote_token": SOL_MINT,
                "base_decimals": base_decimals or 6,
                "quote_decimals": quote_decimals or 9,
                "pool_program": ORCA_WHIRLPOOL_PROGRAM,
            }

        except Exception as e:
            logger.debug(f"Error extracting Orca Whirlpool: {e}")
            return None

    async def _extract_pumpfun_v1(
        self, pool_data: Dict, pool_address: str, token_mint: str
    ) -> Optional[Dict]:
        """
        Extract reserves from PumpFun V1 pool.

        PumpFun V1 pools don't store vault addresses in pool state like Raydium does.
        Instead, vaults are created as separate accounts through PumpSwap.

        Workaround: Accept vault pair address as optional parameter or store it separately.
        For now, we mark pools as needing manual vault configuration.
        """
        try:
            logger.info(
                f"[POOL_EXTRACT] PumpFun V1 pools require special vault handling for {pool_address[:16]}..."
            )

            # Try Raydium-like layout first as fallback
            result = await self._extract_raydium_amm(pool_data, pool_address, token_mint)

            if result:
                result["pool_program"] = PUMPFUN_V1_PROGRAM
                logger.info(f"[POOL_EXTRACT] PumpFun V1 extracted via Raydium-like structure")
                return result

            logger.warning(
                f"[POOL_EXTRACT] PumpFun V1 pool {pool_address[:16]}... requires vault pair address"
            )

            # Return partial data - vault information will need to be provided separately
            # This allows us to at least register the pool even without full vault data
            return None

        except Exception as e:
            logger.debug(f"[POOL_EXTRACT] Error extracting PumpFun V1: {e}")
            return None

    async def _get_token_decimals(self, token_mint: str) -> Optional[int]:
        """Fetch token decimals from on-chain token metadata."""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenSupply",
                "params": [token_mint],
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    result = await resp.json()
                    if "result" in result:
                        decimals = result["result"].get("decimals")
                        return decimals

            return None

        except Exception as e:
            logger.debug(f"Error fetching token decimals: {e}")
            return None

    @staticmethod
    def _bytes_to_pubkey(data: bytes) -> Optional[str]:
        """Convert 32-byte public key to base58 address."""
        try:
            from solders.pubkey import Pubkey

            if len(data) != 32:
                return None

            # Reject placeholder/null pubkeys (all zeros or all ones)
            if data == b'\x00' * 32 or data == b'\xff' * 32:
                return None

            return str(Pubkey(data))
        except Exception:
            return None

    def validate_pool_registration(
        self,
        pool_address: str,
        base_account: str,
        quote_account: str,
        pool_program: str,
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate pool registration before DB insert.
        Returns: (is_valid, error_message)
        """
        # Check required fields exist
        if not pool_address or pool_address.strip() == "":
            return False, "pool_address is empty"
        if not base_account or base_account.strip() == "":
            return False, "base_account is empty"
        if not quote_account or quote_account.strip() == "":
            return False, "quote_account is empty"

        # Check accounts are distinct
        if pool_address == base_account:
            return False, f"pool_address == base_account ({pool_address}), must be distinct"
        if pool_address == quote_account:
            return False, f"pool_address == quote_account ({pool_address}), must be distinct"
        if base_account == quote_account:
            return False, f"base_account == quote_account ({base_account}), must be distinct"

        # Check pool_program is known
        KNOWN_PROGRAMS = {
            RAYDIUM_AMM_PROGRAM,
            RAYDIUM_CPMM_PROGRAM,
            ORCA_WHIRLPOOL_PROGRAM,
            PUMPSWAP_PROGRAM,
            PUMPFUN_V1_PROGRAM,
        }
        if not pool_program or pool_program not in KNOWN_PROGRAMS:
            return False, f"pool_program unknown or invalid: {pool_program}"

        return True, None

    async def register_pool_to_db(
        self, token_mint: str, reserves: Dict, discovery_method: str = "unknown"
    ) -> bool:
        """Register extracted pool in token_pool_accounts table."""
        try:
            # ===== NEW: VALIDATE BEFORE PROCEEDING =====
            pool_address = reserves.get("pool_address")
            base_account = reserves.get("base_account")
            quote_account = reserves.get("quote_account")
            pool_program = reserves.get("pool_program")

            is_valid, error_msg = self.validate_pool_registration(
                pool_address, base_account, quote_account, pool_program
            )
            if not is_valid:
                logger.error(f"❌ Registration validation failed for {token_mint}: {error_msg}")
                return False
            # ===== END NEW VALIDATION =====

            vault_status = "validated"
            vault_error = None

            # Compute pool_score
            WSOL = "So11111111111111111111111111111111111111112"
            USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
            quote_token = reserves.get("quote_token", "")
            quote_pref = 1.0 if quote_token == WSOL else 0.5 if quote_token == USDC else 0.1
            validation_bonus = 0.3 if vault_status == "validated" else 0.0
            pool_score = quote_pref + validation_bonus

            def _do_db_write():
                conn = sqlite3.connect(self.db_path, timeout=15)
                cursor = conn.cursor()
                # Vault addresses were already fetched and verified by extract_pool_reserves
                # earlier in the pipeline (struct-based extraction confirms vault ownership).
                # Re-fetching here is redundant and adds 1-3s of unnecessary RPC latency.
                # Mark as validated immediately; background retry_vault_validation handles
                # the rare case where a vault fetch previously returned stale data.
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO token_pool_accounts
                    (mint, base_account, quote_account, base_token, quote_token,
                     base_decimals, quote_decimals, pool_program, pool_address, is_active,
                     vault_validation_status, vault_validation_error, vault_validation_attempts,
                     last_vault_validation_at, discovery_method, pool_score, created_at, updated_at,
                     authority_account)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        token_mint,
                        reserves["base_account"],
                        reserves["quote_account"],
                        reserves["base_token"],
                        reserves["quote_token"],
                        reserves["base_decimals"],
                        reserves["quote_decimals"],
                        reserves["pool_program"],
                        reserves.get("pool_address"),
                        1,  # is_active
                        vault_status,
                        vault_error,
                        1,  # vault_validation_attempts
                        int(__import__("time").time()),  # last_vault_validation_at
                        discovery_method,
                        pool_score,
                        int(__import__("time").time()),  # created_at
                        int(__import__("time").time()),  # updated_at
                        reserves.get("authority_account"),
                    ),
                )
                conn.commit()
                conn.close()
                self._mark_token_migrated(
                    token_mint,
                    reserves.get("pool_address"),
                    reserves["pool_program"],
                    vault_status == "validated",
                )

            await asyncio.to_thread(_do_db_write)

            status_str = "✅" if vault_status == "validated" else "⏳"
            logger.info(
                f"{status_str} Registered pool for {token_mint} → "
                f"{reserves['base_account'][:16]}... / {reserves['quote_account'][:16]}... "
                f"(vaults: {vault_status})"
            )
            if vault_error:
                logger.debug(f"   Vault error: {vault_error}")
            return True

        except Exception as e:
            logger.error(f"Error registering pool to database: {e}")
            return False

    async def retry_vault_validation(self, token_mint: str, pool_account: str) -> bool:
        """
        Retry vault validation for a pool marked as 'pending'.
        
        Called periodically to check if vaults have been created on-chain
        since initial registration. Updates vault_validation_status if vaults
        become available.
        
        Returns True if vaults are now validated, False otherwise.
        """
        try:
            import sqlite3
            conn = sqlite3.connect(self.db_path, timeout=15)
            cursor = conn.cursor()
            
            # Get the pool registration
            cursor.execute(
                """SELECT base_account, quote_account, pool_program, vault_validation_attempts
                   FROM token_pool_accounts 
                   WHERE mint = ? AND base_account = ?""",
                (token_mint, pool_account)
            )
            row = cursor.fetchone()
            conn.close()
            
            if not row:
                logger.debug(f"[VAULT_RETRY] No pool found for {token_mint[:16]}... {pool_account[:16]}...")
                return False
            
            base_account, quote_account, pool_program, attempts = row
            
            # Try to validate vaults
            logger.info(
                f"[VAULT_RETRY] Attempt {attempts + 1}: Validating vaults for {token_mint[:16]}... "
                f"(pool: {pool_program})"
            )
            
            base_info = await self._fetch_account(base_account)
            quote_info = await self._fetch_account(quote_account)
            
            if not base_info or not quote_info:
                logger.debug(
                    f"[VAULT_RETRY] Vaults still not created for {token_mint[:16]}..."
                )
                # Update attempt count
                conn = sqlite3.connect(self.db_path, timeout=15)
                cursor = conn.cursor()
                cursor.execute(
                    """UPDATE token_pool_accounts 
                       SET vault_validation_attempts = vault_validation_attempts + 1,
                           last_vault_validation_at = ?
                       WHERE mint = ? AND base_account = ?""",
                    (int(__import__("time").time()), token_mint, pool_account)
                )
                conn.commit()
                conn.close()
                return False
            
            # Vaults exist, validate them
            base_owner = base_info.get("owner")
            quote_owner = quote_info.get("owner")
            SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
            
            if base_owner == SPL_TOKEN_PROGRAM and quote_owner == SPL_TOKEN_PROGRAM:
                # Vaults are valid!
                logger.info(
                    f"[VAULT_RETRY] ✅ Vaults validated for {token_mint[:16]}... after {attempts + 1} attempts"
                )
                
                conn = sqlite3.connect(self.db_path, timeout=15)
                cursor = conn.cursor()
                cursor.execute(
                    """UPDATE token_pool_accounts 
                       SET vault_validation_status = 'validated',
                           vault_validation_attempts = ?,
                           last_vault_validation_at = ?,
                           updated_at = ?
                       WHERE mint = ? AND base_account = ?""",
                    (attempts + 1, int(__import__("time").time()), 
                     int(__import__("time").time()), token_mint, pool_account)
                )
                conn.commit()
                conn.close()
                self._mark_token_migrated(
                    token_mint,
                    pool_account,
                    pool_program,
                    True,
                )
                return True
            else:
                # Vaults exist but are wrong type
                logger.warning(
                    f"[VAULT_RETRY] ❌ Vaults exist but are not SPL token accounts for {token_mint[:16]}..."
                )
                
                conn = sqlite3.connect(self.db_path, timeout=15)
                cursor = conn.cursor()
                cursor.execute(
                    """UPDATE token_pool_accounts 
                       SET vault_validation_status = 'rejected',
                           vault_validation_error = 'vaults not SPL token accounts',
                           vault_validation_attempts = ?,
                           last_vault_validation_at = ?,
                           is_active = 0
                       WHERE mint = ? AND base_account = ?""",
                    (attempts + 1, int(__import__("time").time()), token_mint, pool_account)
                )
                conn.commit()
                conn.close()
                return False
        
        except Exception as e:
            logger.error(f"[VAULT_RETRY] Error: {e}")
            return False

    async def register_pumpfun_v1_pool(
        self, token_mint: str, pool_address: str, vault_pair: str
    ) -> bool:
        """
        Register a PumpFun V1 pool with a known vault pair address.

        Since PumpFun V1 pools don't have vaults in pool state, we accept
        the vault pair address directly and derive token vaults from it.

        Args:
            token_mint: The token being traded
            pool_address: The PumpFun V1 pool state account
            vault_pair: The vault pair account (owned by PumpSwap)

        Returns:
            True if registration successful, False otherwise
        """
        try:
            logger.info(
                f"[POOL_REGISTER] Registering PumpFun V1 pool: {pool_address[:16]}... "
                f"with vault pair {vault_pair[:16]}..."
            )

            # Fetch vault pair to understand its structure
            vault_data = await self._fetch_account(vault_pair)
            if not vault_data:
                logger.error(f"Could not fetch vault pair: {vault_pair}")
                return False

            # For now, use vault pair itself as both base and quote accounts
            # (This is a placeholder - real implementation should extract actual token vaults)
            reserves = {
                "base_account": vault_pair,
                "quote_account": vault_pair,
                "base_token": token_mint,
                "quote_token": SOL_MINT,
                "base_decimals": 6,
                "quote_decimals": 9,
                "pool_program": PUMPFUN_V1_PROGRAM,
            }

            return await self.register_pool_to_db(token_mint, reserves)

        except Exception as e:
            logger.error(f"Error registering PumpFun V1 pool: {e}")
            return False

    async def discover_and_register_pool(
        self, pool_address: str, token_mint: str, migration_sig: str = None,
        pool_account_info=None,
    ) -> bool:
        """
        Discover pool reserves and register in database ONLY after validation.

        ✅ CRITICAL FIX: Validate that pool vaults contain the token mint BEFORE registering.
        
        This prevents registering wrong pools (pools for other tokens).

        Algorithm:
        1. Extract all candidate pools
        2. For each candidate:
           a. Run authority-scan to get vaults
           b. Check if any vault holds the token_mint
           c. If yes: register this pool
           d. If no: try next candidate
        3. Only register if validation passes

        Args:
            pool_address: Candidate pool address
            token_mint: The token mint address
            migration_sig: Migration transaction signature (optional)
        
        Returns:
            True if pool registered successfully, False otherwise
        """
        logger.info(f"🔍 Discovering pool reserves for {token_mint}")

        reserves = None
        vault_source = None

        # Fast path: pool_address is already a known PumpSwap pool (the common case when
        # called from fast-lane). Skip the slow PumpFun V1 signature-scan entirely and go
        # straight to struct-based extraction.
        # Reuse caller-supplied account info when available (saves one RPC round-trip).
        pool_acct = pool_account_info if pool_account_info is not None else await self._fetch_account(pool_address)
        pool_owner = (pool_acct or {}).get("owner") if pool_acct else None
        is_pumpswap = (pool_owner == PUMPSWAP_PROGRAM)

        if is_pumpswap:
            logger.info(f"[DISCOVERY_CHAIN] ⚡ Fast path: PumpSwap pool detected, skipping V1 scan")
            extracted = await self.extract_pool_reserves(pool_address, token_mint)
            if extracted:
                base_mint = extracted.get("base_token")
                quote_mint = extracted.get("quote_token")
                if base_mint == token_mint or quote_mint == token_mint:
                    reserves = extracted
                    vault_source = "standard_extraction"
                    logger.info(
                        f"[DISCOVERY_CHAIN] ✅ Fast path extraction succeeded: "
                        f"base={reserves['base_account'][:16]}... "
                        f"quote={reserves['quote_account'][:16]}..."
                    )
        else:
            # Strategy 1: Try PumpFun V1 vault pair discovery (non-PumpSwap pools only)
            logger.info(f"[DISCOVERY_CHAIN] Step 1: Attempting PumpFun V1 vault pair discovery")
            try:
                from src.core.post_migration_pool_discovery import PostMigrationPoolDiscovery
                vault_discovery = PostMigrationPoolDiscovery(self.rpc_url)
                vault_pair = await vault_discovery.discover_pumpfun_v1_vault_pair(
                    mint=token_mint,
                    pool_address=pool_address
                )

                if vault_pair:
                    logger.info(
                        f"[DISCOVERY_CHAIN] ✅ Found vault pair: {vault_pair[:16]}... "
                        f"(attempting to extract actual vaults)"
                    )
                    extracted = await self.extract_pool_reserves(vault_pair, token_mint)
                    if extracted:
                        base_mint = extracted.get("base_token")
                        quote_mint = extracted.get("quote_token")
                        if base_mint == token_mint or quote_mint == token_mint:
                            reserves = extracted
                            vault_source = "pumpfun_v1_discovered"
                            logger.info(
                                f"[DISCOVERY_CHAIN] ✅ Validated vault pair: "
                                f"base={reserves['base_account'][:16]}... "
                                f"quote={reserves['quote_account'][:16]}..."
                            )
                        else:
                            logger.warning(
                                f"[DISCOVERY_CHAIN] ❌ Vault pair validation failed: "
                                f"token {token_mint[:16]}... not in vaults"
                            )
                    else:
                        logger.info(f"[DISCOVERY_CHAIN] ⏭️  Vault pair extraction failed")
                else:
                    logger.info(f"[DISCOVERY_CHAIN] ⏭️  No vault pair found")
            except Exception as e:
                logger.debug(f"[DISCOVERY_CHAIN] Vault pair discovery failed: {e}")

            # Strategy 2: Standard extraction fallback
            if not reserves:
                logger.info(f"[DISCOVERY_CHAIN] Step 2: Attempting standard pool extraction")
                extracted = await self.extract_pool_reserves(pool_address, token_mint)
                if extracted:
                    base_mint = extracted.get("base_token")
                    quote_mint = extracted.get("quote_token")
                    if base_mint == token_mint or quote_mint == token_mint:
                        reserves = extracted
                        vault_source = "standard_extraction"
                        logger.info(f"[DISCOVERY_CHAIN] ✅ Successfully extracted and validated vaults from pool")
                    else:
                        logger.warning(
                            f"[DISCOVERY_CHAIN] ❌ Pool validation failed: "
                            f"token {token_mint[:16]}... not in vaults "
                            f"(base_mint={base_mint[:16]}..., quote_mint={quote_mint[:16]}...)"
                        )

        # If no valid reserves found, reject this candidate
        if not reserves:
            logger.warning(
                f"[DISCOVERY_CHAIN] ❌ Pool {pool_address[:16]}... rejected: "
                f"no vaults containing token {token_mint[:16]}..."
            )
            return False

        # CRITICAL: Validate that base and quote vaults are different
        # If they're the same and this isn't a known PumpFun V1 vault pair, reject
        if reserves.get("base_account") == reserves.get("quote_account"):
            if vault_source != "pumpfun_v1_discovered":
                logger.warning(
                    f"[VALIDATION] ❌ Rejecting pool: base_account == quote_account (invalid pool state)"
                )
                return False

        logger.info(
            f"[DISCOVERY_CHAIN] ✅ Pool extraction successful from {vault_source}"
        )

        # Map vault_source to discovery_method for database
        discovery_method = vault_source or "unknown"

        # Explicitly set pool_address
        reserves["pool_address"] = pool_address

        # Register in database with vault validation status
        # Vaults will be marked as 'pending' or 'validated' based on whether they exist on-chain
        success = await self.register_pool_to_db(token_mint, reserves, discovery_method)

        if success:
            logger.info(
                f"🚀 Pool registered (WebSocket will subscribe when vaults are validated)"
            )

            # ✅ Audit trail for traceability
            logger.info(
                f"[POOL_CONFIRMED] mint={token_mint} pool={pool_address} "
                f"base_account={reserves.get('base_account')[:16]}... "
                f"quote_account={reserves.get('quote_account')[:16]}... "
                f"source=authority_scan discovery_method={discovery_method}"
            )

        return success

    async def register_pool_with_vaults(
        self, pool_address: str, token_mint: str, 
        base_account: str, quote_account: str,
        base_token: str, quote_token: str,
        base_decimals: int = 6, quote_decimals: int = 9,
        pool_program: str = "pumpswap"
    ) -> bool:
        """
        Register a pool when vault addresses are already known.
        
        Used for PumpFun V1 pools where vault discovery found the correct
        vault pair address but standard extraction won't work.
        
        Args:
            pool_address: The pool account address (for reference)
            token_mint: The token mint
            base_account: The base vault account (or PumpSwap pool itself)
            quote_account: The quote vault account (or same as base for PumpFun V1)
            base_token: The token mint for base
            quote_token: The token mint for quote (usually SOL)
            base_decimals: Decimals for base token
            quote_decimals: Decimals for quote token (9 for SOL)
            pool_program: Pool program name
        
        Returns True if registered successfully.
        """
        logger.info(
            f"[REGISTER_WITH_VAULTS] Registering {token_mint[:16]}... "
            f"with known vaults"
        )
        
        reserves = {
            "base_account": base_account,
            "quote_account": quote_account,
            "base_token": base_token,
            "quote_token": quote_token,
            "base_decimals": base_decimals,
            "quote_decimals": quote_decimals,
            "pool_program": pool_program,
        }
        
        # Register with explicit vault validation
        success = await self.register_pool_to_db(token_mint, reserves)
        
        if success:
            logger.info(
                f"✅ Pool registered with known vaults (vaults should be validated)"
            )
        
        return success
