"""Shadow-only verification of Helius enhanced-history page continuity.

The production endpoint uses an exclusive ``before`` cursor.  To obtain a
chain-observable overlap, the next request must use the *penultimate* signature
of the prior page: the former oldest signature must then reappear as the first
record of the continuation.  This module only verifies that claim; it performs
no RPC and cannot change creator-history coverage or acquisition decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence


@dataclass(frozen=True)
class OverlapRequest:
    before_signature: str
    expected_overlap_signature: str
    expected_overlap_slot: Optional[int]


@dataclass(frozen=True)
class ContinuityVerdict:
    contiguous: bool
    gap: bool
    reason: str
    overlap_signature: Optional[str]
    overlap_slot: Optional[int]
    duplicate_signatures: int


def _signature(row: dict[str, Any]) -> Optional[str]:
    value = row.get("signature")
    return str(value) if value else None


def _slot(row: dict[str, Any]) -> Optional[int]:
    value = row.get("slot")
    return value if isinstance(value, int) else None


def continuation_request(page: Sequence[dict[str, Any]]) -> Optional[OverlapRequest]:
    """Return the exclusive-before request that should reproduce page[-1].

    A one-row page has no independent penultimate signature and is therefore
    deliberately not eligible for overlap continuation.
    """
    rows = [row for row in page if isinstance(row, dict) and _signature(row)]
    if len(rows) < 2:
        return None
    return OverlapRequest(
        before_signature=_signature(rows[-2]) or "",
        expected_overlap_signature=_signature(rows[-1]) or "",
        expected_overlap_slot=_slot(rows[-1]),
    )


def verify_overlap(
    request: OverlapRequest,
    continuation_page: Sequence[dict[str, Any]],
) -> ContinuityVerdict:
    """Verify a deterministic, ordered overlap without trusting provider order.

    The expected prior boundary must be the first valid result.  A later match
    could be a reordered page or duplicate and is never treated as continuity.
    """
    rows = [row for row in continuation_page if isinstance(row, dict)]
    signatures = [_signature(row) for row in rows if _signature(row)]
    duplicate_count = len(signatures) - len(set(signatures))
    if not rows:
        return ContinuityVerdict(False, False, "provider_exhaustion_after_prior_boundary", None, None, 0)
    first_sig = _signature(rows[0])
    if first_sig != request.expected_overlap_signature:
        return ContinuityVerdict(False, True, "expected_overlap_not_first_result", first_sig, _slot(rows[0]), duplicate_count)
    if signatures.count(request.expected_overlap_signature) != 1:
        return ContinuityVerdict(False, True, "overlap_signature_duplicate", first_sig, _slot(rows[0]), duplicate_count)
    observed_slot = _slot(rows[0])
    if request.expected_overlap_slot is not None and observed_slot is not None and observed_slot != request.expected_overlap_slot:
        return ContinuityVerdict(False, True, "overlap_slot_mismatch", first_sig, observed_slot, duplicate_count)
    return ContinuityVerdict(True, False, "ordered_signature_overlap", first_sig, observed_slot, duplicate_count)

