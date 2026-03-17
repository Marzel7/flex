"""
Pool liquidity validation.

Prevents fake/low-liquidity pools from generating prices.

Rules:
- Minimum liquidity: $500 USD
- Minimum quote reserve: 0.1 SOL
- Pool age: > 2 blocks
- Known AMM program
- Quote token: wSOL or stablecoin
"""

import logging
from typing import Tuple

logger = logging.getLogger(__name__)


class PoolLiquidityValidator:
    """Validate pool before registration."""

    # Known AMM programs on Solana
    KNOWN_AMM_PROGRAMS = {
        'RVKd61ztZW9GUwhRbbLoYkwG4Eo3JjMbSdVVR5mB5wG',  # Raydium V4
        'EchgqkavEot9Jrg87VrwcwuRgMwQPbMMVmMQWLKJEZs',  # Raydium CPMM
        'TokenkegQfeZyiNwAJsyFbPVwwQQfzLnh1nqPMM3o9',   # Token Program
        '11111111111111111111111111111111',              # System Program
    }

    MIN_LIQUIDITY_USD = 500
    MIN_QUOTE_RESERVE_SOL = 0.1
    MIN_POOL_AGE_BLOCKS = 2
    WRAPPED_SOL_MINT = 'So11111111111111111111111111111111111111112'
    STABLECOINS = {
        'EPjFWaLb3odccccLmLisTwMzkZ3M5kUDs5Q8WWfVrH4',  # USDC
        '4zMMC9srt5Ri5X14GAguppwZvxkXgUi5DjSnqUInrjA',  # USDCet (ETH bridge)
        'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB', # USDT
    }

    def validate_pool(
        self,
        mint: str,
        base_account: str,
        quote_account: str,
        pool_program: str,
        quote_token: str,
        base_reserve: int,
        quote_reserve: int,
        base_decimals: int,
        quote_decimals: int,
        current_slot: int,
        first_seen_slot: int,
        sol_price_usd: float = 100.0
    ) -> Tuple[bool, str]:
        """
        Validate pool before registration.

        Returns:
            (is_valid, reason)
        """

        # 1. Check pool age
        age_blocks = current_slot - first_seen_slot
        if age_blocks < self.MIN_POOL_AGE_BLOCKS:
            return False, f"Pool too young ({age_blocks} blocks, min {self.MIN_POOL_AGE_BLOCKS})"

        # 2. Check known AMM program (warn if unknown, but allow)
        if pool_program not in self.KNOWN_AMM_PROGRAMS:
            logger.warning(f"Unknown AMM program for {mint[:8]}: {pool_program}")

        # 3. Check quote token (wSOL or stablecoin)
        if quote_token != self.WRAPPED_SOL_MINT and quote_token not in self.STABLECOINS:
            return False, f"Unsupported quote token: {quote_token[:8]}"

        # 4. Check minimum quote reserve
        quote_reserve_sol = quote_reserve / (10 ** quote_decimals)
        if quote_reserve_sol < self.MIN_QUOTE_RESERVE_SOL:
            return False, f"Quote reserve too low ({quote_reserve_sol:.4f} SOL, min {self.MIN_QUOTE_RESERVE_SOL})"

        # 5. Check minimum liquidity (USD)
        liquidity_usd = quote_reserve_sol * sol_price_usd
        if liquidity_usd < self.MIN_LIQUIDITY_USD:
            return False, f"Liquidity too low (${liquidity_usd:.2f}, min ${self.MIN_LIQUIDITY_USD})"

        # 6. Sanity check reserves (no zeros)
        if base_reserve <= 0 or quote_reserve <= 0:
            return False, "Invalid reserves (zero)"

        # 7. Sanity check ratio (not extreme)
        ratio = base_reserve / quote_reserve if quote_reserve > 0 else 0
        if ratio < 1e-10 or ratio > 1e10:
            return False, f"Extreme reserve ratio: {ratio:.2e}"

        logger.info(f"Pool validation passed for {mint[:8]}: ${liquidity_usd:.0f} liquidity")
        return True, "Valid"
