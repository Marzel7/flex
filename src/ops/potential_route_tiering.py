"""Deterministic helpers for Potential Operations route activity snapshots.

These helpers intentionally describe route evidence without attributing it to
any retired operation.
"""
from __future__ import annotations

import hashlib
import json
from typing import Iterable


FINGERPRINT_CONTRACT_VERSION = "POTENTIAL_ROUTE_FINGERPRINTS.v1"


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def base_fingerprint(edges: Iterable[tuple[int, str, int | None]]) -> dict:
    """Return an address-free selected-edge signature."""
    values = {
        (int(depth), str(mechanism), int(amount) if amount not in (None, 0) else None)
        for depth, mechanism, amount in edges
    }
    normalized = [
        {"hop_depth": depth, "mechanism": mechanism, "amount_lamports": amount}
        for depth, mechanism, amount in sorted(
            values, key=lambda item: (item[0], item[1], item[2] is None, item[2] or 0)
        )
    ]
    return {
        "contract": FINGERPRINT_CONTRACT_VERSION,
        "kind": "BASE_SELECTED_EDGE",
        "edges": normalized,
    }


def stable_candidate_id(fingerprint: dict) -> str:
    """Keep existing persisted candidate identifiers stable.

    The prefix is a legacy opaque storage key; changing it would silently fork
    existing Potential Operations history.
    """
    return "p3r-v2-" + _digest(fingerprint)[:20]
