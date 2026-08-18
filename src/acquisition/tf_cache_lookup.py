"""B2Z-2H design support: read-only, default-off lookup adapter against the
existing transaction_first_lineage.db raw-transaction cache
(tf_transaction_cache), so a future selective-RPC executor can check the
cache BEFORE dispatching a live request -- mirroring three_sw2's proven
cache-first acquisition pattern.

This module performs NO writes, NO schema changes, and is not imported or
invoked by any production code path. It exists purely as a tested building
block for a future B2Z-2H implementation milestone.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CachedTransaction:
    signature: str
    block_time: int | None
    transaction_json: dict | None
    fetched_at: int
    source: str
    rpc_verified: bool
    parse_status: str


class TransactionFirstLineageCacheLookup:
    """Read-only lookup against tf_transaction_cache. Opens the database in
    SQLite URI read-only mode -- structurally cannot write, regardless of
    caller behavior."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self.db_path.resolve()}?mode=ro", uri=True)

    def lookup(self, signature: str) -> CachedTransaction | None:
        """Return the cached, rpc-verified transaction for this signature if
        present, else None. Never raises for a cache miss."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT signature, block_time, transaction_json, fetched_at, source, rpc_verified, parse_status "
                "FROM tf_transaction_cache WHERE signature = ?",
                (signature,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        sig, block_time, tx_json, fetched_at, source, rpc_verified, parse_status = row
        parsed = json.loads(tx_json) if tx_json else None
        return CachedTransaction(
            signature=sig, block_time=block_time, transaction_json=parsed,
            fetched_at=fetched_at, source=source, rpc_verified=bool(rpc_verified),
            parse_status=parse_status,
        )

    def lookup_many(self, signatures: list[str]) -> dict[str, CachedTransaction]:
        """Bounded batch lookup -- caller must pass a finite, pre-determined
        list (e.g. the candidate signatures for one B2Z-2H batch), never used
        to iterate the whole cache."""
        if not signatures:
            return {}
        conn = self._connect()
        try:
            placeholders = ",".join("?" for _ in signatures)
            rows = conn.execute(
                f"SELECT signature, block_time, transaction_json, fetched_at, source, rpc_verified, parse_status "
                f"FROM tf_transaction_cache WHERE signature IN ({placeholders})",
                signatures,
            ).fetchall()
        finally:
            conn.close()
        result = {}
        for sig, block_time, tx_json, fetched_at, source, rpc_verified, parse_status in rows:
            parsed = json.loads(tx_json) if tx_json else None
            result[sig] = CachedTransaction(
                signature=sig, block_time=block_time, transaction_json=parsed,
                fetched_at=fetched_at, source=source, rpc_verified=bool(rpc_verified),
                parse_status=parse_status,
            )
        return result

    def cache_hit_rate(self, signatures: list[str]) -> dict[str, float | int]:
        """Bounded diagnostic: what fraction of a candidate signature list is
        already cache-verified. Never used to scan the whole cache."""
        if not signatures:
            return {"total": 0, "hits": 0, "verified_hits": 0, "hit_rate": 0.0, "verified_hit_rate": 0.0}
        found = self.lookup_many(signatures)
        verified = sum(1 for c in found.values() if c.rpc_verified)
        total = len(signatures)
        return {
            "total": total, "hits": len(found), "verified_hits": verified,
            "hit_rate": round(len(found) / total, 4),
            "verified_hit_rate": round(verified / total, 4),
        }
