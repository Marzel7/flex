"""Non-canonical, provider-free Byzantine creator birth signals."""
from __future__ import annotations

import hashlib
import json
import queue
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

OPERATOR_ID = "d8ee4d7a-fcd6-5a5b-b897-24f6ab56e334"
STRICT_SOURCE = "P3R_063E_BYZC_CURRENT"
SIGNAL_TYPE = "BYZANTINE_CREATOR_CONTINUITY_BIRTH_SIGNAL"


def load_proven_creators(path: str | Path) -> Mapping[str, Mapping[str, str]]:
    """Read the strict admission source once at startup, never per birth."""
    db = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
    try:
        db.execute("PRAGMA query_only=ON")
        rows = db.execute(
            """SELECT q.creator, m.mint, cm.evidence_json
               FROM operator_launch_membership m
               JOIN confirmed_operation_matches cm ON cm.mint=m.mint AND cm.operator_id=m.operator_id
               JOIN wt_walkback_queue q ON q.mint=m.mint
               WHERE m.operator_id=? AND m.source_population_id=?""",
            (OPERATOR_ID, STRICT_SOURCE),
        ).fetchall()
    finally:
        db.close()
    proof: dict[str, Mapping[str, str]] = {}
    for creator, mint, evidence in rows:
        item = json.loads(evidence)
        proof[str(creator)] = {"mint": str(mint), "signature": str(item["signature"]), "creator": str(creator)}
    if len(proof) != 36:
        raise RuntimeError(f"expected 36 strict Byzantine creators, got {len(proof)}")
    return MappingProxyType(proof)


class ByzantineBirthSignalEmitter:
    def __init__(self, creators: Mapping[str, Mapping[str, str]], *, enabled: bool, capacity: int = 256) -> None:
        self.creators = creators
        self.enabled = enabled
        self.events: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=capacity)
        self.seen: set[str] = set()
        self.dropped = 0

    def observe(self, *, mint: str, creator: str, signature: str, receive_utc_ns: int, monotonic_ns: int | None = None) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        t2 = monotonic_ns or time.monotonic_ns()
        proof = self.creators.get(creator)
        if proof is None:
            return None
        signal_id = hashlib.sha256(f"{SIGNAL_TYPE}|{mint}|{creator}|{signature}|{proof['signature']}".encode()).hexdigest()
        if signal_id in self.seen:
            return None
        self.seen.add(signal_id)
        t4 = time.monotonic_ns()
        signal = {"signal_type": SIGNAL_TYPE, "signal_id": signal_id, "mint": mint, "creator": creator,
                  "birth_signature": signature, "pumpportal_receive_time": receive_utc_ns,
                  "signal_monotonic_time": t4, "prior_strict_proof_mint": proof["mint"],
                  "prior_strict_proof_signature": proof["signature"], "prior_strict_proof_creator": proof["creator"],
                  "qualification": "PROVEN_CREATOR_CONTINUITY_BIRTH", "canonical_membership": False,
                  "migration_observed": "UNKNOWN", "source": "PUMPPORTAL_CREATE", "lookup_started_ns": t2}
        try:
            self.events.put_nowait(signal)
        except queue.Full:
            self.dropped += 1
            return None
        return signal
