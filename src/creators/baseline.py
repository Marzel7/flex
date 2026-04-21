"""
Creator baseline scanner.

Provides the three callables injected into CreatorActivityWorker:

  run_baseline_scan(creator_address, resume_cursor)
      Pages getSignaturesForAddress on the creator wallet backwards in time.
      For each sig, calls getTransaction and checks for pump.fun CREATE pattern.
      Records found mints via repo.link_creator_to_mint().
      Returns {"complete": bool, "cursor": dict|None,
               "newest_sig": str|None, "oldest_sig": str|None}

  get_signatures(address, before, limit)
      Thin wrapper around getSignaturesForAddress via background RPC.
      Returns list[{"signature": str, "slot": int, "blockTime": int, "err": ...}]

  process_signature(creator_address, sig_info)
      Fetches and inspects one tx. If it's a pump.fun create, records the mint.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# Pump.fun bonding curve program — presence in tx confirms it's a PF create
PUMPFUN_BONDING_CURVE_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
TOKEN_PROGRAM  = "TokenkegQfeZyiNwAJsyFbPtrKbVs73Cw6Xj2Yg5MNg"
TOKEN_2022     = "TokenzQdBbjFD8aff5ZZUwWWwG6Go5rm5KWQEypdCU8"
SYSTEM_PROGRAM = "11111111111111111111111111111111"

# Per-page limit for getSignaturesForAddress — 1000 is the RPC max
_PAGE_LIMIT = 200
# Max pages per baseline run before saving cursor and yielding
_MAX_PAGES_PER_RUN = 10


class CreatorBaselineScanner:
    """
    Stateless helper — all state lives in the DB via repo.
    One instance shared across all baseline jobs; the callables are closures
    over the instance.

    background_rpc_fn: async (method: str, params: list) -> Optional[dict]
        Should be listener.call_background_rpc
    repo: CreatorRepository
        Used to record mints found during scan.
    """

    def __init__(
        self,
        *,
        background_rpc_fn: Callable[[str, list], Awaitable[Optional[dict]]],
        repo,
    ) -> None:
        self._rpc  = background_rpc_fn
        self._repo = repo

    # ------------------------------------------------------------------
    # Public callables — injected into CreatorActivityWorker
    # ------------------------------------------------------------------

    async def run_baseline_scan(
        self,
        creator_address: str,
        resume_cursor: Optional[dict],
    ) -> dict:
        """
        Scan the creator's wallet history for pump.fun CREATE transactions.
        Pages backwards up to _MAX_PAGES_PER_RUN pages then yields.
        Resumable via cursor = {"before": last_sig_seen}.
        """
        before      = (resume_cursor or {}).get("before")
        newest_sig  = None
        oldest_sig  = None
        pages       = 0

        while pages < _MAX_PAGES_PER_RUN:
            sigs = await self.get_signatures(creator_address, before=before, limit=_PAGE_LIMIT)
            if not sigs:
                # Reached end of history
                return {
                    "complete":   True,
                    "cursor":     None,
                    "newest_sig": newest_sig,
                    "oldest_sig": oldest_sig,
                }

            for sig_info in sigs:
                sig = sig_info.get("signature")
                if not sig:
                    continue
                if newest_sig is None:
                    newest_sig = sig
                oldest_sig = sig
                try:
                    await self.process_signature(creator_address, sig_info)
                except Exception as e:
                    logger.warning(
                        "[BASELINE] process_signature failed creator=%s sig=%s: %s",
                        creator_address[:16], sig[:16], e,
                    )

            before = oldest_sig
            pages += 1

            if len(sigs) < _PAGE_LIMIT:
                # Last page — history exhausted
                return {
                    "complete":   True,
                    "cursor":     None,
                    "newest_sig": newest_sig,
                    "oldest_sig": oldest_sig,
                }

        # Hit page limit — save cursor for next attempt
        logger.info(
            "[BASELINE] Page limit reached creator=%s pages=%d — saving cursor",
            creator_address[:16], pages,
        )
        return {
            "complete":   False,
            "cursor":     {"before": before} if before else None,
            "newest_sig": newest_sig,
            "oldest_sig": oldest_sig,
        }

    async def get_signatures(
        self,
        address: str,
        before: Optional[str] = None,
        limit: int = 50,
    ) -> list:
        """
        Call getSignaturesForAddress via background RPC.
        Returns list of sig objects (signature, slot, blockTime, err).
        Filters out failed transactions.
        """
        params: dict = {"limit": min(limit, 1000)}
        if before:
            params["before"] = before

        try:
            result = await self._rpc(
                "getSignaturesForAddress",
                [address, params],
            )
        except Exception as e:
            logger.warning("[BASELINE] getSignaturesForAddress failed addr=%s: %s", address[:16], e)
            return []

        if result is None:
            return []

        sigs = result.get("result", [])
        if not isinstance(sigs, list):
            return []

        # Filter out failed transactions — err=None means success
        return [s for s in sigs if isinstance(s, dict) and s.get("err") is None]

    async def process_signature(
        self,
        creator_address: str,
        sig_info: dict,
    ) -> None:
        """
        Fetch the transaction and check if it's a pump.fun CREATE.
        If so, record the mint in creator_tokens.
        """
        sig = sig_info.get("signature")
        if not sig:
            return

        try:
            result = await self._rpc(
                "getTransaction",
                [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
            )
        except Exception as e:
            logger.debug("[BASELINE] getTransaction failed sig=%s: %s", sig[:16], e)
            return

        if not result:
            return
        tx = result.get("result")
        if not tx:
            return

        mint = _extract_pumpfun_create_mint(tx)
        if not mint:
            return

        is_new = await self._repo.link_creator_to_mint(creator_address, mint)
        if is_new:
            logger.debug(
                "[BASELINE] Found create creator=%s mint=%s",
                creator_address[:16], mint[:16],
            )


# ---------------------------------------------------------------------------
# Transaction inspection — detect pump.fun CREATE and extract the mint
# ---------------------------------------------------------------------------

def _extract_pumpfun_create_mint(tx: dict) -> Optional[str]:
    """
    Returns the created mint address if this tx is a pump.fun token creation,
    otherwise None.

    Detection: pump.fun bonding curve program present in program IDs
    AND a new token mint account is initialised in the tx.
    """
    message = (tx.get("transaction") or {}).get("message") or {}
    meta    = tx.get("meta") or {}

    account_keys = message.get("accountKeys") or []
    # jsonParsed format wraps keys as {"pubkey": ..., "signer": ..., "writable": ...}
    pubkeys = []
    for k in account_keys:
        if isinstance(k, str):
            pubkeys.append(k)
        elif isinstance(k, dict):
            pk = k.get("pubkey")
            if pk:
                pubkeys.append(pk)

    # Must involve the pump.fun bonding curve program
    if PUMPFUN_BONDING_CURVE_PROGRAM not in pubkeys:
        return None

    # Find the mint: a post-token-balance entry with no pre-balance
    # (newly created accounts appear in postTokenBalances but not preTokenBalances)
    pre_mints  = {b.get("mint") for b in (meta.get("preTokenBalances")  or [])}
    post_mints = {b.get("mint") for b in (meta.get("postTokenBalances") or [])}
    new_mints  = [m for m in post_mints - pre_mints if m and m not in (None, "")]

    if len(new_mints) == 1:
        return new_mints[0]

    # Fallback: look for initializeMint instruction on a token program
    instructions = message.get("instructions") or []
    inner_blocks = meta.get("innerInstructions") or []
    all_instrs   = list(instructions)
    for block in inner_blocks:
        all_instrs.extend(block.get("instructions") or [])

    for instr in all_instrs:
        parsed = instr.get("parsed")
        if not isinstance(parsed, dict):
            continue
        if parsed.get("type") in ("initializeMint", "initializeMint2"):
            info = parsed.get("info") or {}
            mint = info.get("mint")
            if mint:
                return mint

    return None


# ---------------------------------------------------------------------------
# MovementScanner — used by incremental_reconcile jobs
# ---------------------------------------------------------------------------

class MovementScanner:
    """
    Provides process_signature for the reconcile worker path.
    Fetches the full transaction, parses SOL movement via the same
    parse_creator_sol_movement function used by the webhook path,
    and calls handle_creator_stream_event with source='reconcile'.

    This ensures webhook and reconcile produce identical rows.
    """

    def __init__(
        self,
        *,
        background_rpc_fn: Callable[[str, list], Awaitable[Optional[dict]]],
        repo,
    ) -> None:
        self._rpc  = background_rpc_fn
        self._repo = repo

    async def process_signature(
        self,
        creator_address: str,
        sig_info: dict,
    ) -> None:
        from src.creators.movement import parse_creator_sol_movement
        from src.creators.service import handle_creator_stream_event

        sig = sig_info.get("signature")
        if not sig:
            return

        try:
            result = await self._rpc(
                "getTransaction",
                [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
            )
        except Exception as e:
            logger.debug("[RECONCILE] getTransaction failed sig=%s: %s", sig[:16], e)
            return

        if not result:
            return
        tx = result.get("result")
        if not tx:
            return

        event = parse_creator_sol_movement(tx, creator_address, source="reconcile")
        if event is None:
            return

        await handle_creator_stream_event(
            {
                "creator_address": event.creator_address,
                "signature":       event.signature,
                "slot":            event.slot,
                "block_time":      event.block_time,
                "net_lamports":    event.net_lamports,
                "abs_lamports":    event.abs_lamports,
                "direction":       event.direction,
                "counterparty":    event.counterparty,
                "source":          event.source,
            },
            repo=self._repo,
        )
