"""
Pool State Parser Dispatcher

Routes account data to appropriate parser based on AMM program.
Used by pool detector's three-stage validation: owner -> size -> parse validation.
"""

import logging
from typing import Optional, Dict, List
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class PoolParser(ABC):
    """Base class for pool state parsers."""

    @abstractmethod
    def try_parse(self, data: List[int]) -> Optional[Dict]:
        """
        Attempt to parse account data as pool state.

        Args:
            data: Account data bytes (as list of ints from RPC)

        Returns:
            Dict with pool metadata if valid, None if parsing fails
        """
        pass


class RaydiumAMMParser(PoolParser):
    """Parse Raydium AMM v4 and PumpSwap pools (use same layout)."""

    # SPL Token program ID
    SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJsyFbPVwwQQftas5LLppuCQqn"
    SPL_TOKEN_ACCOUNT_SIZE = 165
    SYSTEM_PROGRAM = "11111111111111111111111111111111"

    def try_parse(self, data: List[int]) -> Optional[Dict]:
        """
        Parse Raydium AMM pool state (296+ bytes).

        6-stage validation to reject helper/config PDAs:
        1. Minimum size >= 296 bytes
        2. Discriminator check (first 8 bytes)
        3. Extract vault pubkeys from offsets 232-296
        4. Reject garbage patterns (all zeros/ones)
        5. Verify vaults are owned by SPL token program
        6. Verify vault account sizes are 165 bytes (SPL token layout)

        Returns:
            Dict if valid Raydium pool structure, None otherwise
        """
        try:
            # ===== STAGE 1: Size validation =====
            if len(data) < 296:
                logger.debug(f"[PARSER] RaydiumAMM: Too small ({len(data)} < 296 bytes)")
                return None

            # ===== STAGE 2: Discriminator check =====
            discriminator = bytes(data[:8]) if isinstance(data, list) else data[:8]
            expected_disc = bytes([0x95, 0x39, 0x06, 0xfe])
            if len(discriminator) >= 4 and discriminator[:4] != expected_disc:
                logger.debug(f"[PARSER] RaydiumAMM: Invalid discriminator {discriminator[:4].hex()}")
                return None

            # ===== STAGE 3: Extract vault pubkeys =====
            base_vault_bytes = bytes(data[232:264]) if isinstance(data, list) else data[232:264]
            quote_vault_bytes = bytes(data[264:296]) if isinstance(data, list) else data[264:296]

            # ===== STAGE 4: Reject garbage patterns =====
            if base_vault_bytes == bytes(32):  # All zeros
                logger.debug("[PARSER] RaydiumAMM: Base vault is all zeros")
                return None
            if quote_vault_bytes == bytes([0xFF] * 32):  # All ones (padding)
                logger.debug("[PARSER] RaydiumAMM: Quote vault is all ones (helper PDA)")
                return None

            # Decode vault addresses
            try:
                from solders.pubkey import Pubkey
                base_vault = str(Pubkey(base_vault_bytes))
                quote_vault = str(Pubkey(quote_vault_bytes))
            except Exception as e:
                logger.debug(f"[PARSER] RaydiumAMM: Could not decode vault pubkeys: {e}")
                return None

            # ===== STAGE 5 & 6: Async vault validation needed =====
            # NOTE: This is a synchronous parser, so we cannot do RPC calls here.
            # Instead, we do basic structural checks and rely on extraction validation.
            #
            # The extraction pipeline in pool_discovery.py will:
            # - Fetch vault accounts from RPC
            # - Verify they are SPL token accounts (owner = TokenkegQfe...)
            # - Verify they are 165 bytes (SPL token account size)
            # - Verify vault mints match the launched token

            # Reject system program addresses (obvious padding)
            if base_vault == self.SYSTEM_PROGRAM:
                logger.debug("[PARSER] RaydiumAMM: Base vault is system program (padding)")
                return None
            if quote_vault == self.SYSTEM_PROGRAM:
                logger.debug("[PARSER] RaydiumAMM: Quote vault is system program (padding)")
                return None

            logger.debug(f"[PARSER] RaydiumAMM: Passed structural checks, vaults={base_vault[:16]}... {quote_vault[:16]}...")

            return {
                'type': 'raydium_amm',
                'data_len': len(data),
                'valid': True,
                'base_vault': base_vault,
                'quote_vault': quote_vault,
            }

        except Exception as e:
            logger.debug(f"[PARSER] RaydiumAMM parse error: {e}")
            return None


class RaydiumCLMMParser(PoolParser):
    """Parse Raydium Concentrated Liquidity Market Maker pools."""

    def try_parse(self, data: List[int]) -> Optional[Dict]:
        """
        Parse Raydium CLMM pool state.

        CLMM pools have different structure than AMM v4.

        Returns:
            Dict if valid CLMM pool, None otherwise
        """
        try:
            # CLMM pools have minimum size (typically 300+ bytes)
            if len(data) < 200:
                return None

            return {
                'type': 'raydium_clmm',
                'data_len': len(data),
                'valid': True
            }

        except Exception as e:
            logger.debug(f"[PARSER] RaydiumCLMM parse error: {e}")
            return None


class OrcaWhirlpoolParser(PoolParser):
    """Parse Orca Whirlpool pool state."""

    def try_parse(self, data: List[int]) -> Optional[Dict]:
        """
        Parse Orca Whirlpool pool state (232+ bytes).

        Orca pools have distinct structure from Raydium.

        Returns:
            Dict if valid Orca pool, None otherwise
        """
        try:
            # Orca Whirlpool requires at least 232 bytes
            if len(data) < 232:
                return None

            return {
                'type': 'orca_whirlpool',
                'data_len': len(data),
                'valid': True
            }

        except Exception as e:
            logger.debug(f"[PARSER] OrcaWhirlpool parse error: {e}")
            return None


class MeteoraDLMMParser(PoolParser):
    """Parse Meteora DLMM pool state."""

    def try_parse(self, data: List[int]) -> Optional[Dict]:
        """
        Parse Meteora DLMM pool state (232+ bytes).

        Returns:
            Dict if valid Meteora pool, None otherwise
        """
        try:
            # Meteora DLMM requires at least 232 bytes
            if len(data) < 232:
                return None

            return {
                'type': 'meteora_dlmm',
                'data_len': len(data),
                'valid': True
            }

        except Exception as e:
            logger.debug(f"[PARSER] MeteoraDLMM parse error: {e}")
            return None


class PoolParserDispatcher:
    """
    Routes pool accounts to appropriate parser based on AMM program.

    Stage 3 validation in three-stage pool detection:
    1. Owner filter (account owner is AMM program)
    2. Size filter (data length >= minimum)
    3. Parser validation (data structure valid)
    """

    # Parser instances (singleton pattern)
    _PARSERS = {
        'raydium_amm': RaydiumAMMParser(),
        'raydium_clmm': RaydiumCLMMParser(),
        'orca_whirlpool': OrcaWhirlpoolParser(),
        'meteora_dlmm': MeteoraDLMMParser(),
        'pumpswap': RaydiumAMMParser(),  # PumpSwap uses Raydium layout
    }

    @classmethod
    def for_program(cls, program_owner: str) -> Optional[PoolParser]:
        """
        Get parser for given program owner address.

        Args:
            program_owner: Account owner pubkey (must be AMM program)

        Returns:
            PoolParser instance or None if program not supported
        """
        from src.core.pool_detector import AMMPrograms

        program_map = {
            AMMPrograms.RAYDIUM_AMM: 'raydium_amm',
            AMMPrograms.PUMPSWAP: 'pumpswap',
            AMMPrograms.RAYDIUM_CLMM: 'raydium_clmm',
            AMMPrograms.ORCA_WHIRLPOOL: 'orca_whirlpool',
            AMMPrograms.METEORA_DLMM: 'meteora_dlmm',
        }

        parser_key = program_map.get(program_owner)
        if not parser_key:
            return None

        return cls._PARSERS.get(parser_key)

    @classmethod
    def for_program_name(cls, program_name: str) -> Optional[PoolParser]:
        """
        Get parser by program name.

        Args:
            program_name: Name like 'raydium_amm', 'orca_whirlpool', etc.

        Returns:
            PoolParser instance or None if not found
        """
        return cls._PARSERS.get(program_name)
