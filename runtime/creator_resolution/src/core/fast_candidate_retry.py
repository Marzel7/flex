"""
Fast-lane candidate retry system for transient discovery failures.

Optimizes pool/vault resolution latency by:
1. Classifying rejection reasons as permanent vs transient
2. Fast-retrying only transient failures
3. Caching permanent rejects to avoid repeated RPC calls
4. Scoring candidates by confidence
5. Using narrow shortlist rechecks instead of full rediscovery

Target: Reduce resolution time from 58-79s to 3-10s for most tokens.
"""

import time
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from collections import defaultdict

logger = logging.getLogger(__name__)


# Classification of rejection reasons
PERMANENT_REJECTS = {
    "shared_account",
    "wrong_owner",
    "invalid_program",
    "known_bad_actor",
    "base_equals_quote",
    "invalid_shape",
    "executable_account",
    "token_mint_itself",
    "registration_failed",  # Registration attempt failed or timed out — never retry
}

TRANSIENT_REJECTS = {
    "account_not_found",
    "rpc_empty_response",
    "rpc_timeout",
    "indexing_lag",
}


@dataclass
class CandidateStatus:
    """Track a single candidate's validation status and retry history."""
    address: str
    rejection_reason: Optional[str] = None
    is_permanent_reject: bool = False
    is_transient_reject: bool = False
    confidence_score: float = 0.0
    retry_count: int = 0
    first_seen_at: float = field(default_factory=time.time)
    last_checked_at: Optional[float] = None
    next_retry_at: Optional[float] = None
    validation_passed: bool = False

    def mark_permanent_reject(self, reason: str) -> None:
        """Record a permanent rejection reason."""
        self.rejection_reason = reason
        self.is_permanent_reject = True
        self.is_transient_reject = False

    def mark_transient_reject(self, reason: str, retry_delay: float) -> None:
        """Record a transient rejection with scheduled retry."""
        self.rejection_reason = reason
        self.is_transient_reject = True
        self.is_permanent_reject = False
        self.retry_count += 1
        self.last_checked_at = time.time()
        self.next_retry_at = time.time() + retry_delay

    def mark_valid(self) -> None:
        """Candidate passed validation."""
        self.validation_passed = True
        self.rejection_reason = None
        self.is_permanent_reject = False
        self.is_transient_reject = False

    def should_retry_now(self) -> bool:
        """Check if enough time has passed to retry."""
        if not self.is_transient_reject or self.validation_passed:
            return False
        if self.next_retry_at is None:
            return False
        return time.time() >= self.next_retry_at

    def short_repr(self) -> str:
        """Short representation for logging."""
        status = (
            "VALID" if self.validation_passed
            else "PERM_REJECT" if self.is_permanent_reject
            else "TRANSIENT" if self.is_transient_reject
            else "PENDING"
        )
        return f"{self.address[:16]}...({status})"


class PendingCandidateShortlist:
    """Manages per-mint shortlist of pending candidates for fast retry."""

    def __init__(self, max_retries: int = 5):
        """
        Initialize shortlist manager.

        Args:
            max_retries: Maximum retries per candidate (0.5s, 1s, 2s, 3s, and extra)
        """
        self.max_retries = max_retries
        # Dict[mint] -> Dict[address] -> CandidateStatus
        self.pending: Dict[str, Dict[str, CandidateStatus]] = defaultdict(dict)

    def add_candidate(
        self,
        mint: str,
        address: str,
        confidence_score: float = 0.0,
    ) -> None:
        """Add a candidate to the shortlist."""
        if address not in self.pending[mint]:
            self.pending[mint][address] = CandidateStatus(
                address=address,
                confidence_score=confidence_score,
            )

    def record_rejection(
        self, mint: str, address: str, reason: str
    ) -> None:
        """Record a rejection for a candidate."""
        if address not in self.pending[mint]:
            self.add_candidate(mint, address, confidence_score=0.0)

        candidate = self.pending[mint][address]

        if reason in PERMANENT_REJECTS:
            candidate.mark_permanent_reject(reason)
        elif reason in TRANSIENT_REJECTS:
            # Distributed across real RPC visibility lag window (pools can take 5-10s to index).
            # Early retries are fast; later retries cover slower-indexing migrations.
            retry_delays = [0.1, 0.3, 0.7, 1.5, 2.5, 4.0]
            delay = (
                retry_delays[min(candidate.retry_count, len(retry_delays) - 1)]
            )
            candidate.mark_transient_reject(reason, delay)

    def record_valid(self, mint: str, address: str) -> None:
        """Record a successful validation."""
        if address in self.pending[mint]:
            self.pending[mint][address].mark_valid()

    def get_ready_for_retry(self, mint: str) -> List[str]:
        """Get list of candidates ready for retry (transient failures only)."""
        if mint not in self.pending:
            return []

        # Sort by confidence score (higher = better)
        ready = [
            addr
            for addr, status in self.pending[mint].items()
            if status.should_retry_now()
            and status.retry_count <= self.max_retries
        ]

        # Sort by confidence (descending) and retry_count (ascending)
        ready.sort(
            key=lambda addr: (
                -self.pending[mint][addr].confidence_score,
                self.pending[mint][addr].retry_count,
            )
        )

        # Return top 2 candidates (reduced from 3 for tighter focus)
        return ready[:2]

    def get_permanent_rejects(self, mint: str) -> List[str]:
        """Get addresses that were permanently rejected."""
        if mint not in self.pending:
            return []
        return [
            addr
            for addr, status in self.pending[mint].items()
            if status.is_permanent_reject
        ]

    def get_valid_candidates(self, mint: str) -> List[str]:
        """Get all candidates that passed validation."""
        if mint not in self.pending:
            return []
        return [
            addr
            for addr, status in self.pending[mint].items()
            if status.validation_passed
        ]

    def cleanup_mint(self, mint: str) -> None:
        """Clean up shortlist for a mint after discovery completes."""
        if mint in self.pending:
            del self.pending[mint]

    def get_stats(self, mint: str) -> Dict:
        """Get statistics for a mint's shortlist."""
        if mint not in self.pending:
            return {}

        candidates = self.pending[mint].values()
        return {
            "total": len(candidates),
            "valid": sum(1 for c in candidates if c.validation_passed),
            "permanent_reject": sum(1 for c in candidates if c.is_permanent_reject),
            "transient_reject": sum(1 for c in candidates if c.is_transient_reject),
            "pending": sum(
                1
                for c in candidates
                if not c.validation_passed
                and not c.is_permanent_reject
                and not c.is_transient_reject
            ),
            "avg_confidence": (
                sum(c.confidence_score for c in candidates) / len(candidates)
                if candidates
                else 0.0
            ),
        }


def score_candidate(
    address: str,
    tx_data: Dict,
    token_mint: str,
) -> float:
    """
    Score a candidate based on proximity and context clues.

    Returns a score from 0-100 (higher = more likely to be the real pool).

    Scoring factors (pre-validation structural signals only):
    - Near token mint in account keys (+30)
    - Near SOL mint in account keys (+20)
    - In same instruction as token mint (+20)

    Note: no owner bonus — ownership is not known until after RPC validation.

    Penalties:
    - System program account (-100)
    - Token mint itself (-100)
    - Token programs / utilities (-100)
    - Executable account (-40)
    """
    score = 10.0  # Higher baseline to reward real signal

    if not tx_data:
        return score

    account_keys = tx_data.get("transaction", {}).get("message", {}).get("accountKeys", [])
    instructions = tx_data.get("transaction", {}).get("message", {}).get("instructions", [])

    SOL_MINT = "So11111111111111111111111111111111111111112"

    # STRONG PENALTY: address is token mint itself
    if address == token_mint:
        return -100.0

    # STRONG PENALTY: address is system program
    SYSTEM_PROGRAM = "11111111111111111111111111111111"
    if address == SYSTEM_PROGRAM:
        return -100.0

    # STRONG PENALTY: address is token program (SPL Token, Token-2022)
    TOKEN_PROGRAM_ADDRESSES = {
        "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",  # SPL Token
        "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",  # Token-2022
        "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",  # Associated Token Program
    }
    if address in TOKEN_PROGRAM_ADDRESSES:
        return -100.0

    # STRONG PENALTY: address is obvious utility account (rent, compute budget, etc)
    OBVIOUS_UTILITIES = {
        "GS4CU59F31iL7aR2Q8zVS8DRrcRnXX1yjQ66TqNVQnaR",  # Compute Budget Program
        "Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1",  # Sysvar Rent
    }
    if address in OBVIOUS_UTILITIES:
        return -100.0

    # Check if address is executable (from accountKeys metadata)
    if isinstance(account_keys, list):
        for key_obj in account_keys:
            if isinstance(key_obj, dict):
                if key_obj.get("pubkey") == address and key_obj.get("executable"):
                    score -= 40

    # Bonus: proximity to token mint in account keys
    if address in account_keys and token_mint in account_keys:
        addr_idx = account_keys.index(address)
        mint_idx = account_keys.index(token_mint)
        distance = abs(addr_idx - mint_idx)
        if distance <= 5:
            score += 30
        elif distance <= 10:
            score += 15

    # Bonus: proximity to SOL mint in account keys
    if address in account_keys and SOL_MINT in account_keys:
        addr_idx = account_keys.index(address)
        sol_idx = account_keys.index(SOL_MINT)
        distance = abs(addr_idx - sol_idx)
        if distance <= 5:
            score += 20
        elif distance <= 10:
            score += 10

    # Bonus: appears in same instruction as token mint
    if instructions:
        for instr in instructions:
            if not isinstance(instr, dict):
                continue
            instr_accounts = instr.get("accounts", [])
            if isinstance(instr_accounts, list):
                if address in instr_accounts and token_mint in instr_accounts:
                    score += 20
                    break

    return max(0, min(100, score))  # Clamp to 0-100


def rejection_reason_is_permanent(reason: str) -> bool:
    """Check if a rejection reason indicates permanent rejection."""
    return reason in PERMANENT_REJECTS


def rejection_reason_is_transient(reason: str) -> bool:
    """Check if a rejection reason indicates transient rejection."""
    return reason in TRANSIENT_REJECTS


def get_retry_delay_for_attempt(attempt_number: int) -> float:
    """Get retry delay (seconds) for a given retry attempt number."""
    delays = [0.75, 1.5, 3.0, 6.0]
    idx = min(attempt_number, len(delays) - 1)
    return delays[idx]
