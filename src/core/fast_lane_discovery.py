"""
Fast-lane discovery integration for PumpFunCurveListener.

Provides methods to integrate fast-candidate-retry into the main discovery flow.
"""

import asyncio
import time
import logging
from typing import Dict, List, Optional, Tuple

from src.core.fast_candidate_retry import (
    PendingCandidateShortlist,
    score_candidate,
    get_retry_delay_for_attempt,
)

logger = logging.getLogger(__name__)


class FastLaneDiscovery:
    """
    Mixin class providing fast-lane discovery methods for PumpFunCurveListener.

    This class should be inherited by PumpFunCurveListener to add:
    - Fast-lane retry for transient failures
    - Candidate scoring
    - Narrow shortlist rechecks
    """

    def __init__(self, *args, **kwargs):
        """Initialize fast-lane discovery state."""
        super().__init__(*args, **kwargs)
        self.pending_candidates = PendingCandidateShortlist(max_retries=5)
        self.discovery_start_times: Dict[str, float] = {}
        # Stores all valid candidates from the most recent fast-lane run per mint.
        # Written just before cleanup so the caller can try each one for registration.
        self._last_valid_candidates: Dict[str, List[str]] = {}

    def _log_fl(self, msg: str):
        """Log fast-lane message. Override by subclass for custom logging."""
        logger.info(msg)

    def _filter_failed(self, mint: str, candidates: List[str]) -> List[str]:
        """
        Remove candidates that previously failed registration.
        Base implementation is a no-op; subclass overrides to apply _failed_registration.
        """
        return candidates

    def _save_valid_and_cleanup(self, mint: str, valid: List[str], best: str) -> None:
        """
        Save the full valid candidate list before cleanup so the caller can try
        all candidates for registration, not just the best pick.
        Ordered: best first, then remaining by their shortlist confidence score.
        """
        shortlist = self.pending_candidates.pending.get(mint, {})

        def _score(addr: str) -> float:
            entry = shortlist.get(addr)
            return entry.confidence_score if entry else 0.0

        rest = sorted([a for a in valid if a != best], key=_score, reverse=True)
        self._last_valid_candidates[mint] = [best] + rest
        self.pending_candidates.cleanup_mint(mint)

    def pop_valid_candidates(self, mint: str) -> List[str]:
        """Return (and clear) the saved valid candidate list for a mint."""
        return self._last_valid_candidates.pop(mint, [])

    async def _probe_candidate_visibility(self, candidates: List[str]) -> List[str]:
        """
        Cheap visibility probe: return only candidates that currently have account data.

        Used before full strict validation to avoid wasting cycles on accounts that
        don't exist yet on-chain.

        Args:
            candidates: List of candidate pool addresses

        Returns:
            Subset of candidates that currently have account data
        """
        if not candidates:
            return []

        try:
            # Cheap multi-account fetch with short timeout
            # processed commitment: ~100-300ms visibility vs 5-20s for finalized
            result = await self.call_discovery_rpc(
                "getMultipleAccounts",
                [candidates, {"encoding": "base64", "commitment": "processed"}],
                timeout=5.0,
            )
            values = (result or {}).get("result", {}).get("value", []) if result else []

            # Return only candidates that have account data
            visible = [
                addr for addr, value in zip(candidates, values)
                if value is not None
            ]

            self._log_fl(
                f"[VISIBILITY_PROBE] {len(visible)}/{len(candidates)} candidates visible"
            )
            return visible
        except Exception as e:
            self._log_fl(f"[VISIBILITY_PROBE] Error: {e}, returning empty list")
            return []  # Safer: don't validate if probe fails

    async def fast_lane_resolve_with_retries(
        self,
        mint: str,
        tx_data: Dict,
        max_wait_secs: float = 8.0,  # RPC visibility lag can exceed 5-10s for slower-indexing migrations
    ) -> Optional[str]:
        """
        Fast-lane pool resolution with optimized retry logic.

        Algorithm:
        1. Extract and score candidates from TX
        2. Batch validate with strict mode, capturing rejection reasons
        3. For transient failures (account_not_found), mark for retry
        4. Loop: fast-retry candidates with transient failures up to max_wait_secs
        5. Return first valid candidate, or None if timeout

        Args:
            mint: Token mint address
            tx_data: Transaction data dict
            max_wait_secs: Maximum total time to spend retrying (default 8s)

        Returns:
            Valid pool address or None if discovery fails
        """
        if not tx_data:
            return None

        start_time = time.time()
        self.discovery_start_times[mint] = start_time

        try:
            # Step 1: Extract and score candidates
            candidates = await self._extract_pool_from_tx(tx_data)
            if not candidates:
                self._log_fl(f"[FAST_LANE] No candidates extracted for {mint[:16]}")
                return None

            candidates = [str(c) if not isinstance(c, str) else c for c in candidates]
            candidates = [
                c for c in candidates
                if isinstance(c, str) and len(c) >= 32 and not c.startswith("111")
            ]
            # Strip candidates that already failed registration — never retry them
            candidates = self._filter_failed(mint, candidates)

            if not candidates:
                self._log_fl(f"[FAST_LANE] All candidates filtered out for {mint[:16]}")
                return None

            # Score all candidates
            scored = []
            for addr in candidates:
                score = score_candidate(addr, tx_data, mint)
                self.pending_candidates.add_candidate(mint, addr, score)
                scored.append((addr, score))

            scored.sort(key=lambda x: -x[1])  # Sort by score descending

            self._log_fl(
                f"[FAST_LANE] {len(candidates)} candidates scored for {mint[:16]}: "
                f"top 3 = {', '.join(f'{a[:10]}...(score={s:.0f})' for a, s in scored[:3])}"
            )

            # ⚡ FAST PATH: High-confidence shortcut (skip RPC validation)
            if scored:
                top_candidate, top_score = scored[0]
                if top_score >= 80:
                    elapsed = time.time() - start_time
                    self._log_fl(
                        f"[FAST_LANE] ⚡ High-confidence shortcut → {top_candidate[:16]}... "
                        f"(score={top_score:.0f}) in {elapsed:.2f}s"
                    )
                    self.pending_candidates.record_valid(mint, top_candidate)
                    self._save_valid_and_cleanup(mint, [top_candidate], top_candidate)
                    return top_candidate

            # Step 2: Validate top-4 first; widen to remaining only if top-4 all fail.
            # Avoids one large RPC call for 17 candidates when the winner is typically
            # in the first few highest-scored addresses.
            hot_candidates  = [addr for addr, _ in scored[:2]]
            cold_candidates = [addr for addr, _ in scored[2:]]

            valid_rich, rejections = await self.batch_validate_candidates_with_reasons(
                hot_candidates, strict_mode=True
            )

            if not valid_rich and cold_candidates:
                self._log_fl(
                    f"[FAST_LANE] Top-2 failed, widening to {len(cold_candidates)} remaining candidates"
                )
                valid2, rejections2 = await self.batch_validate_candidates_with_reasons(
                    cold_candidates, strict_mode=True
                )
                valid_rich = valid2
                rejections.update(rejections2)

            if valid_rich:
                # Normalise: valid_rich may be dicts (rich) or plain strings (legacy callers)
                valid_addrs = [
                    v["address"] if isinstance(v, dict) else v for v in valid_rich
                ]
                elapsed = time.time() - start_time
                self._log_fl(
                    f"[FAST_LANE] ✅ Found {len(valid_addrs)} valid candidates immediately "
                    f"for {mint[:16]} in {elapsed:.2f}s"
                )
                for addr in valid_addrs:
                    self.pending_candidates.record_valid(mint, addr)
                best = self.select_best_pool(valid_addrs, tx_data)
                self._save_valid_and_cleanup(mint, valid_addrs, best)
                # Return rich object for the winner if available so caller can skip re-fetch
                best_rich = next(
                    (v for v in valid_rich if (v["address"] if isinstance(v, dict) else v) == best),
                    best,
                )
                return best_rich

            # Step 3: Classify rejections into permanent vs transient
            # Transient: account_not_found (pool not yet indexed/visible)
            # Permanent: wrong_owner, shared_account, shared_check_failed
            transient_candidates = []
            permanent_candidates = []
            
            TRANSIENT_REASONS = {"account_not_found"}
            PERMANENT_REASONS = {"wrong_owner", "shared_account", "shared_check_failed"}

            for addr, reason in rejections.items():
                if reason in TRANSIENT_REASONS:
                    transient_candidates.append((addr, reason))
                    self.pending_candidates.record_rejection(mint, addr, reason)
                elif reason in PERMANENT_REASONS:
                    permanent_candidates.append((addr, reason))
                    self.pending_candidates.record_rejection(mint, addr, reason)
                else:
                    # Unknown reason - treat as transient
                    transient_candidates.append((addr, reason))
                    self.pending_candidates.record_rejection(mint, addr, reason)

            self._log_fl(
                f"[FAST_LANE] Rejection summary: {len(transient_candidates)} transient, "
                f"{len(permanent_candidates)} permanent"
            )

            # 🔥 IMMEDIATE RETRY BOOTSTRAP: Kick off retry without waiting
            if transient_candidates:
                # Pre-populate shortlist with transient candidates for immediate retry
                for addr, reason in transient_candidates:
                    if addr not in self.pending_candidates.pending[mint]:
                        score = score_candidate(addr, tx_data, mint) if tx_data else 0.0
                        self.pending_candidates.add_candidate(mint, addr, score)
                    # Mark as transient so it's eligible for immediate retry
                    self.pending_candidates.pending[mint][addr].is_transient_reject = True
                    self.pending_candidates.pending[mint][addr].rejection_reason = reason

                # Trigger immediate first retry attempt
                self._log_fl(f"[FAST_LANE] ⚡ Immediate retry bootstrap: {len(transient_candidates)} candidates ready")

            # Step 4: Fast-retry loop for transient failures
            if not transient_candidates:
                # All rejections are permanent - no point retrying
                self._log_fl(
                    f"[FAST_LANE] All rejections permanent, no retry candidates for {mint[:16]}"
                )
                return None

            self._log_fl(
                f"[FAST_LANE] No valid candidates initially, entering retry loop for {mint[:16]} "
                f"(max {max_wait_secs:.1f}s, {len(transient_candidates)} transient candidates)"
            )

            attempt = 0
            while time.time() - start_time < max_wait_secs:
                attempt += 1
                elapsed = time.time() - start_time

                # ⚡ VISIBLE SOFT ACCEPT: After 3 attempts, only accept candidates that RPC can see
                # Requires retry_count >= 1 AND on-chain visibility confirmed via probe.
                # Prevents returning account_not_found candidates that crash registration.
                if attempt >= 3:
                    pending = self.pending_candidates.pending.get(mint, {})
                    soft_candidates = [
                        c for c in pending.values()
                        if not c.is_permanent_reject
                        and not c.validation_passed
                        and c.retry_count >= 1
                    ]
                    soft_candidates.sort(key=lambda c: (-c.confidence_score, c.retry_count))

                    if soft_candidates:
                        candidate_addresses = self._filter_failed(
                            mint, [c.address for c in soft_candidates[:3]]
                        )
                        if not candidate_addresses:
                            soft_candidates = []  # all failed — skip soft accept this iteration

                    if soft_candidates:
                        candidate_addresses = candidate_addresses  # already filtered above

                        # VISIBILITY GATE: account must exist on-chain
                        visible = await self._probe_candidate_visibility(candidate_addresses)

                        if visible:
                            # OWNER GATE: visible accounts must also pass strict validation
                            # (existence alone is not enough — Tokenz... accounts are visible but wrong owner)
                            valid_r, _ = await self.batch_validate_candidates_with_reasons(
                                visible, strict_mode=True
                            )
                            if valid_r:
                                valid = [v["address"] if isinstance(v, dict) else v for v in valid_r]
                                best = self.select_best_pool(valid, tx_data)
                                elapsed = time.time() - start_time
                                self._log_fl(
                                    f"[FAST_LANE] ⚡ Visible soft accept → {best[:16]}... "
                                    f"after {attempt} attempts in {elapsed:.2f}s"
                                )
                                for addr in valid:
                                    self.pending_candidates.record_valid(mint, addr)
                                self._save_valid_and_cleanup(mint, valid, best)
                                return best

                # ⚡ SOFT VALIDATION: If top candidate has high confidence and proven stable, accept it
                if self.pending_candidates.pending.get(mint):
                    pending_for_mint = list(self.pending_candidates.pending[mint].values())
                    # Sort by confidence score descending
                    pending_for_mint.sort(key=lambda c: -c.confidence_score)
                    if pending_for_mint:
                        top_candidate = pending_for_mint[0]
                        if (top_candidate.confidence_score >= 70 and
                            top_candidate.retry_count >= 2):
                            elapsed = time.time() - start_time
                            self._log_fl(
                                f"[FAST_LANE] ⚡ Soft validation → {top_candidate.address[:16]}... "
                                f"(score={top_candidate.confidence_score:.0f}, retries={top_candidate.retry_count}) "
                                f"in {elapsed:.2f}s"
                            )
                            self.pending_candidates.record_valid(mint, top_candidate.address)
                            self._save_valid_and_cleanup(mint, [top_candidate.address], top_candidate.address)
                            return top_candidate.address

                # EARLY EXIT: Check if valid candidate was found elsewhere (critical window resolution)
                valid_candidates = self.pending_candidates.get_valid_candidates(mint)
                if valid_candidates:
                    self._log_fl(f"[FAST_LANE] Early exit: valid candidate found for {mint[:16]}")
                    return self.select_best_pool(valid_candidates, tx_data)

                # Get candidates ready to retry
                retry_candidates = self._filter_failed(
                    mint, self.pending_candidates.get_ready_for_retry(mint)
                )
                use_fallback_retry = False

                # 🔥 CRITICAL FIX: break retry starvation
                if not retry_candidates:
                    pending = self.pending_candidates.pending.get(mint, {})

                    if pending:
                        sorted_candidates = sorted(
                            pending.values(),
                            key=lambda c: (-c.confidence_score, c.retry_count)
                        )

                        retry_candidates = self._filter_failed(mint, [
                            c.address
                            for c in sorted_candidates
                            if not c.is_permanent_reject and not c.validation_passed
                        ][:2])

                        if retry_candidates:
                            use_fallback_retry = True
                            self._log_fl(
                                f"[FAST_LANE] 🔥 Forced retry fallback: {len(retry_candidates)} candidates"
                            )

                if not retry_candidates:
                    # No candidates to retry at all
                    self._log_fl(
                        f"[FAST_LANE] No more candidates to retry for {mint[:16]} "
                        f"after {elapsed:.2f}s (attempt {attempt})"
                    )
                    break

                self._log_fl(
                    f"[FAST_LANE] Attempt {attempt}: Validating {len(retry_candidates)} "
                    f"candidates for {mint[:16]} (elapsed {elapsed:.2f}s)"
                )

                # Validate candidates directly (no visibility probe)
                valid_r, rejections_retry = await self.batch_validate_candidates_with_reasons(
                    retry_candidates, strict_mode=True
                )

                if valid_r:
                    valid = [v["address"] if isinstance(v, dict) else v for v in valid_r]
                    elapsed = time.time() - start_time
                    self._log_fl(
                        f"[FAST_LANE] ✅ Found {len(valid)} valid candidates for {mint[:16]} "
                        f"in {elapsed:.2f}s (after {attempt} attempts)"
                    )
                    for addr in valid:
                        self.pending_candidates.record_valid(mint, addr)
                    best = self.select_best_pool(valid, tx_data)
                    self._save_valid_and_cleanup(mint, valid, best)
                    return best

                # Record rejections ONLY if not using fallback retry
                # (fallback retries happen too soon; don't update retry timers)
                if not use_fallback_retry:
                    for addr, reason in rejections_retry.items():
                        self.pending_candidates.record_rejection(mint, addr, reason)

                # Aggressive retry timing: 0.25s for first attempt, 0.5s for rest
                sleep_time = 0.25 if attempt < 2 else 0.5
                await asyncio.sleep(sleep_time)

            # Timeout - try loose validation as last resort
            self._log_fl(
                f"[FAST_LANE] Timeout reached for {mint[:16]} after {max_wait_secs:.1f}s, "
                f"trying loose validation"
            )

            valid_r, _ = await self.batch_validate_candidates_with_reasons(candidates, strict_mode=False)
            if valid_r:
                valid = [v["address"] if isinstance(v, dict) else v for v in valid_r]
                elapsed = time.time() - start_time
                self._log_fl(
                    f"[FAST_LANE] ✅ Found {len(valid)} candidates in loose mode "
                    f"for {mint[:16]} in {elapsed:.2f}s"
                )
                for addr in valid:
                    self.pending_candidates.record_valid(mint, addr)
                best = self.select_best_pool(valid, tx_data)
                self._save_valid_and_cleanup(mint, valid, best)
                return best

            # Complete failure
            elapsed = time.time() - start_time
            stats = self.pending_candidates.get_stats(mint)
            self._log_fl(
                f"[FAST_LANE] ❌ Discovery failed for {mint[:16]} after {elapsed:.2f}s. "
                f"Stats: {stats}"
            )
            self.pending_candidates.cleanup_mint(mint)
            return None

        except Exception as e:
            self._log_fl(f"[FAST_LANE] Exception during fast-lane discovery: {e}")
            self.pending_candidates.cleanup_mint(mint)
            return None

    def get_latency_metrics(self, mint: str) -> Dict:
        """Get latency metrics for a specific mint's discovery."""
        if mint not in self.discovery_start_times:
            return {}

        elapsed = time.time() - self.discovery_start_times[mint]
        stats = self.pending_candidates.get_stats(mint)

        return {
            "mint": mint[:16] + "...",
            "elapsed_secs": round(elapsed, 2),
            **stats,
        }

    def log_discovery_metrics(self, mint: str) -> None:
        """Log discovery metrics for a mint."""
        metrics = self.get_latency_metrics(mint)
        if metrics:
            logger.info(f"[FAST_LANE_METRICS] {metrics}")
