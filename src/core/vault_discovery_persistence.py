"""
Vault Discovery Data Persistence Module

Persists real discovery runtime data (strategy, attempts, elapsed time) to the database
so the Vaults page can show actual discovery metrics instead of defaults.

Key design:
- Called at the moment discovery succeeds
- Persists: strategy, attempt count, elapsed time
- Updates: vault_resolution_state, vault_resolved_at
- Increments: vault_discovery_attempts on retries
- Returns: NULL for missing values (API will not default to "unknown"/"0")
"""

import sqlite3
import time
import logging

logger = logging.getLogger(__name__)


def record_vault_discovery_result(
    db_path: str,
    mint: str,
    base_account: str,
    strategy: str,
    attempts: int,
    elapsed_secs: float,
    pool_address: str = None,
):
    """
    Persist vault discovery metadata when discovery succeeds.

    This is called at the moment a pool is successfully discovered and registered.
    It records the actual strategy, attempt count, and elapsed time so the Vaults
    page can display real metrics instead of defaults.

    Args:
        db_path: Path to the database
        mint: Token mint address
        base_account: Base vault account (the vault actually used)
        strategy: Discovery strategy used (e.g., "tx_parsing", "rpc", "follow_on")
        attempts: Number of attempts taken
        elapsed_secs: Total elapsed time in seconds
        pool_address: Optional pool address (for reference)

    Returns:
        True if successful, False otherwise

    Example:
        >>> record_vault_discovery_result(
        ...     "database/flex.db",
        ...     "3yeggvaSvPynVTjoPQsKBe1siivW2nLuwVCNeUcLpump",
        ...     "9tSG8rU4jn3iTLy9KU9WpEW7RjgRHPASGus3vGwBNCF",
        ...     "tx_parsing",
        ...     4,
        ...     256.8
        ... )
    """
    _tp_vp_t0 = time.monotonic()
    logger.debug(f"[TIMING_PROBE] VAULT_PERSISTENCE_START mint={mint[:16]} strategy={strategy}")
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        cursor = conn.cursor()
        now = int(time.time())

        # First try: Update by mint + pool_address (most stable key)
        if pool_address:
            cursor.execute(
                """
                UPDATE token_pool_accounts
                SET
                    vault_discovery_strategy = ?,
                    vault_discovery_attempts = ?,
                    vault_discovery_time_secs = ?,
                    vault_resolution_state = 'resolved',
                    vault_resolved_at = ?,
                    last_vault_validation_at = ?
                WHERE
                    mint = ?
                    AND pool_address = ?
                """,
                (
                    strategy,           # vault_discovery_strategy
                    attempts,           # vault_discovery_attempts
                    elapsed_secs,       # vault_discovery_time_secs
                    now,                # vault_resolved_at
                    now,                # last_vault_validation_at
                    mint,               # WHERE mint
                    pool_address,       # WHERE pool_address
                ),
            )
            rows_affected = cursor.rowcount

            if rows_affected > 0:
                conn.commit()
                conn.close()
                logger.info(
                    f"[VAULT_DISCOVERY_PERSIST] ✅ Recorded discovery result (by pool): "
                    f"mint={mint[:16]}... pool={pool_address[:16]}... "
                    f"strategy={strategy} attempts={attempts} elapsed={elapsed_secs:.1f}s"
                )
                return True

        # Fallback: Update by mint + base_account
        cursor.execute(
            """
            UPDATE token_pool_accounts
            SET
                vault_discovery_strategy = ?,
                vault_discovery_attempts = ?,
                vault_discovery_time_secs = ?,
                vault_resolution_state = 'resolved',
                vault_resolved_at = ?,
                last_vault_validation_at = ?
            WHERE
                mint = ?
                AND base_account = ?
            """,
            (
                strategy,           # vault_discovery_strategy
                attempts,           # vault_discovery_attempts
                elapsed_secs,       # vault_discovery_time_secs
                now,                # vault_resolved_at
                now,                # last_vault_validation_at
                mint,               # WHERE mint
                base_account,       # WHERE base_account
            ),
        )

        rows_affected = cursor.rowcount
        conn.commit()
        conn.close()

        if rows_affected > 0:
            logger.info(
                f"[VAULT_DISCOVERY_PERSIST] ✅ Recorded discovery result (by base): "
                f"mint={mint[:16]}... base={base_account[:16]}... "
                f"strategy={strategy} attempts={attempts} elapsed={elapsed_secs:.1f}s"
            )
            return True
        else:
            logger.warning(
                f"[VAULT_DISCOVERY_PERSIST] ⚠️  No rows updated: "
                f"mint={mint[:16]}... pool={pool_address[:16] if pool_address else 'null'}... "
                f"base={base_account[:16]}... (row may not exist or already resolved)"
            )
            return False

    except Exception as e:
        logger.error(
            f"[VAULT_DISCOVERY_PERSIST] ❌ Failed to persist discovery data: {e}"
        )
        return False
    finally:
        logger.debug(f"[TIMING_PROBE] VAULT_PERSISTENCE_DONE mint={mint[:16]} elapsed_ms={int((time.monotonic()-_tp_vp_t0)*1000)}")


def increment_vault_discovery_attempts(
    db_path: str, mint: str, base_account: str = None, pool_address: str = None
) -> bool:
    """
    Increment the vault_discovery_attempts counter for a token.

    Called during each retry to track how many attempts have been made.

    Args:
        db_path: Path to the database
        mint: Token mint address
        base_account: Optional base vault account
        pool_address: Optional pool address

    Returns:
        True if successful, False otherwise
    """
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        cursor = conn.cursor()

        # Try by pool_address first (more stable)
        if pool_address:
            cursor.execute(
                """
                UPDATE token_pool_accounts
                SET vault_discovery_attempts = COALESCE(vault_discovery_attempts, 0) + 1
                WHERE mint = ? AND pool_address = ?
                """,
                (mint, pool_address),
            )
            if cursor.rowcount > 0:
                conn.commit()
                conn.close()
                return True

        # Fallback to base_account
        if base_account:
            cursor.execute(
                """
                UPDATE token_pool_accounts
                SET vault_discovery_attempts = COALESCE(vault_discovery_attempts, 0) + 1
                WHERE mint = ? AND base_account = ?
                """,
                (mint, base_account),
            )
            if cursor.rowcount > 0:
                conn.commit()
                conn.close()
                return True

        # Last resort: just by mint (should only match one active row per mint)
        cursor.execute(
            """
            UPDATE token_pool_accounts
            SET vault_discovery_attempts = COALESCE(vault_discovery_attempts, 0) + 1
            WHERE mint = ? AND is_active = 1
            LIMIT 1
            """,
            (mint,),
        )

        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success

    except Exception as e:
        logger.error(f"[VAULT_DISCOVERY_ATTEMPTS] ❌ Failed to increment attempts: {e}")
        return False


def get_vault_discovery_status(
    db_path: str, mint: str, base_account: str = None
) -> dict:
    """
    Get the current vault discovery status for a token.

    Returns the discovery metadata, or None if not found.

    Args:
        db_path: Path to the database
        mint: Token mint address
        base_account: Optional base account filter (if None, gets any account for this mint)

    Returns:
        Dict with discovery metadata or None
    """
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        cursor = conn.cursor()

        if base_account:
            cursor.execute(
                """
                SELECT
                    vault_discovery_strategy,
                    vault_discovery_attempts,
                    vault_discovery_time_secs,
                    vault_resolution_state,
                    vault_resolved_at
                FROM token_pool_accounts
                WHERE mint = ? AND base_account = ?
                LIMIT 1
                """,
                (mint, base_account),
            )
        else:
            cursor.execute(
                """
                SELECT
                    vault_discovery_strategy,
                    vault_discovery_attempts,
                    vault_discovery_time_secs,
                    vault_resolution_state,
                    vault_resolved_at
                FROM token_pool_accounts
                WHERE mint = ?
                LIMIT 1
                """,
                (mint,),
            )

        row = cursor.fetchone()
        conn.close()

        if row:
            return {
                "strategy": row[0],
                "attempts": row[1],
                "elapsed_secs": row[2],
                "resolution_state": row[3],
                "resolved_at": row[4],
            }
        return None

    except Exception as e:
        logger.error(f"[VAULT_DISCOVERY_STATUS] ❌ Failed to get status: {e}")
        return None
