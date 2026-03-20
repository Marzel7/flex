#!/usr/bin/env python3
"""
Pool Account Validator
Validates that pool accounts (base_account, quote_account) actually exist on-chain
before registering them to the database.
"""

import asyncio
import aiohttp
import logging
from typing import Optional, Tuple, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class AccountInfo:
    """Represents on-chain account information."""
    address: str
    exists: bool
    owner: Optional[str] = None
    lamports: int = 0
    executable: bool = False
    data_size: int = 0
    is_spl_token_account: bool = False
    mint: Optional[str] = None


class PoolAccountValidator:
    """
    Validates pool accounts exist on-chain before registration.
    Uses RPC getAccountInfo to verify account existence.
    """

    def __init__(self, rpc_url: str, timeout: float = 10.0):
        self.rpc_url = rpc_url
        self.timeout = aiohttp.ClientTimeout(total=timeout)

    async def validate_account(self, address: str) -> AccountInfo:
        """
        Check if an account exists on-chain.

        Args:
            address: Public key address to validate

        Returns:
            AccountInfo with existence status and metadata
        """
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getAccountInfo",
                    "params": [address, {"encoding": "base64"}]
                }

                async with session.post(self.rpc_url, json=payload) as resp:
                    if resp.status != 200:
                        logger.warning(f"RPC error for {address}: HTTP {resp.status}")
                        return AccountInfo(address=address, exists=False)

                    data = await resp.json()

                    # Check for RPC error
                    if "error" in data:
                        error = data.get("error", {})
                        logger.debug(f"RPC error for {address}: {error.get('message', 'unknown')}")
                        return AccountInfo(address=address, exists=False)

                    # Check if account exists
                    # Helius returns: {"result": {"value": {...}}} or {"result": {"value": null}}
                    result = data.get("result")
                    if not result:
                        logger.debug(f"Account not found on-chain: {address}")
                        return AccountInfo(address=address, exists=False)

                    # Handle Helius format with "value" key
                    account_data = result.get("value") if isinstance(result, dict) else result
                    if not account_data:
                        logger.debug(f"Account not found on-chain: {address}")
                        return AccountInfo(address=address, exists=False)

                    # Account exists - extract metadata
                    return self._parse_account_info(address, account_data)

        except asyncio.TimeoutError:
            logger.warning(f"Timeout validating account {address}")
            return AccountInfo(address=address, exists=False)
        except Exception as e:
            logger.error(f"Error validating account {address}: {e}")
            return AccountInfo(address=address, exists=False)

    def _parse_account_info(self, address: str, account_data: Dict[str, Any]) -> AccountInfo:
        """Parse RPC account response into AccountInfo."""
        lamports = account_data.get("lamports", 0)
        owner = account_data.get("owner")
        executable = account_data.get("executable", False)

        # Check data size
        data = account_data.get("data", [])
        data_size = 0
        if isinstance(data, list) and len(data) > 0:
            # Data comes as [base64_string, encoding]
            import base64
            try:
                decoded = base64.b64decode(data[0])
                data_size = len(decoded)
            except Exception:
                pass

        # Check if it's an SPL token account (size 165 or 170)
        is_spl_token = data_size in (165, 170)

        return AccountInfo(
            address=address,
            exists=True,
            owner=owner,
            lamports=lamports,
            executable=executable,
            data_size=data_size,
            is_spl_token_account=is_spl_token
        )

    async def validate_pool_pair(self, base_account: str, quote_account: str) -> Tuple[bool, Dict[str, AccountInfo]]:
        """
        Validate both accounts in a pool pair.

        Args:
            base_account: Base token vault address
            quote_account: Quote token vault address

        Returns:
            (valid: bool, account_info: Dict with 'base' and 'quote' keys)
        """
        # Validate both accounts in parallel
        base_info, quote_info = await asyncio.gather(
            self.validate_account(base_account),
            self.validate_account(quote_account),
            return_exceptions=False
        )

        valid = base_info.exists and quote_info.exists

        if not valid:
            logger.warning(
                f"Pool pair validation failed: "
                f"base={base_account} (exists={base_info.exists}), "
                f"quote={quote_account} (exists={quote_info.exists})"
            )

        return valid, {
            "base": base_info,
            "quote": quote_info
        }

    async def validate_pool_accounts_are_spl(self, base_account: str, quote_account: str) -> bool:
        """
        Validate that both pool accounts are SPL token accounts.

        Args:
            base_account: Base token vault address
            quote_account: Quote token vault address

        Returns:
            True if both exist and are SPL token accounts
        """
        valid, accounts = await self.validate_pool_pair(base_account, quote_account)

        if not valid:
            return False

        base_is_spl = accounts["base"].is_spl_token_account
        quote_is_spl = accounts["quote"].is_spl_token_account

        if not (base_is_spl and quote_is_spl):
            logger.warning(
                f"Pool accounts are not SPL token accounts: "
                f"base_is_spl={base_is_spl} (size={accounts['base'].data_size}), "
                f"quote_is_spl={quote_is_spl} (size={accounts['quote'].data_size})"
            )
            return False

        return True


async def validate_pool_accounts(
    rpc_url: str,
    base_account: str,
    quote_account: str,
    require_spl: bool = False
) -> Tuple[bool, Dict[str, Any]]:
    """
    Convenience function to validate pool accounts.

    Args:
        rpc_url: RPC endpoint URL
        base_account: Base token vault address
        quote_account: Quote token vault address
        require_spl: If True, require accounts to be SPL token accounts (size 165/170)

    Returns:
        (valid: bool, details: Dict with validation details)
    """
    validator = PoolAccountValidator(rpc_url)

    valid, accounts = await validator.validate_pool_pair(base_account, quote_account)

    details = {
        "valid": valid,
        "base": {
            "address": accounts["base"].address,
            "exists": accounts["base"].exists,
            "data_size": accounts["base"].data_size,
            "is_spl": accounts["base"].is_spl_token_account,
        },
        "quote": {
            "address": accounts["quote"].address,
            "exists": accounts["quote"].exists,
            "data_size": accounts["quote"].data_size,
            "is_spl": accounts["quote"].is_spl_token_account,
        }
    }

    if require_spl and valid:
        base_spl = accounts["base"].is_spl_token_account
        quote_spl = accounts["quote"].is_spl_token_account
        valid = base_spl and quote_spl
        details["spl_valid"] = valid

    return valid, details
