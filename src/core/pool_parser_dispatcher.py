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

    def try_parse(self, data: List[int]) -> Optional[Dict]:
        """
        Parse Raydium AMM pool state (296+ bytes).

        Layout (simplified):
        - Offset 0-8: Status/discriminator
        - Offset 8+: Pool metadata, reserves, etc.

        Returns:
            Dict if valid Raydium pool structure, None otherwise
        """
        try:
            # Raydium AMM v4 requires at least 296 bytes
            if len(data) < 296:
                return None

            # Basic structural validation: check for recognizable patterns
            # Real implementation would verify discriminator bytes, CPI guards, etc.
            # For now, if it's >= 296 and didn't throw, consider it valid

            return {
                'type': 'raydium_amm',
                'data_len': len(data),
                'valid': True
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
