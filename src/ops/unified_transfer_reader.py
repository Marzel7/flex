"""STORAGE-LIFECYCLE-P3: HOT+COLD unified transfer query prototype.

Read-only. Never writes to HOT or COLD. Proves that a caller can query
across the hot main-DB table and any number of closed cold segments
without knowing which tier a row lives in, with deterministic
deduplication.

This is a PROTOTYPE for query-shape compatibility testing (Part 15),
not the production implementation -- the real UnifiedTransferReader
(Part 8) is a documented design contract for a future P4 milestone.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass
class UnifiedTransferReader:
    hot_conn: sqlite3.Connection
    cold_conns: list[sqlite3.Connection]

    def _dedupe(self, rows: list[tuple]) -> list[tuple]:
        seen = set()
        result = []
        for row in rows:
            key = (row[0], row[1], row[2])  # (signature, source, destination)
            if key in seen:
                continue
            seen.add(key)
            result.append(row)
        return result

    def by_signature(self, signature: str) -> list[tuple]:
        rows = list(self.hot_conn.execute(
            "SELECT signature, source, destination, amount_lamports, block_time FROM transfer_index WHERE signature=?",
            (signature,),
        ))
        for cold in self.cold_conns:
            rows.extend(cold.execute(
                "SELECT signature, source, destination, amount_lamports, block_time FROM transfer_index WHERE signature=?",
                (signature,),
            ))
        return self._dedupe(rows)

    def by_source(self, source: str, *, limit: int = 500) -> list[tuple]:
        rows = list(self.hot_conn.execute(
            "SELECT signature, source, destination, amount_lamports, block_time FROM transfer_index "
            "WHERE source=? ORDER BY block_time DESC LIMIT ?",
            (source, limit),
        ))
        for cold in self.cold_conns:
            rows.extend(cold.execute(
                "SELECT signature, source, destination, amount_lamports, block_time FROM transfer_index "
                "WHERE source=? ORDER BY block_time DESC LIMIT ?",
                (source, limit),
            ))
        deduped = self._dedupe(rows)
        deduped.sort(key=lambda r: r[4], reverse=True)
        return deduped[:limit]

    def by_destination(self, destination: str, *, limit: int = 500) -> list[tuple]:
        rows = list(self.hot_conn.execute(
            "SELECT signature, source, destination, amount_lamports, block_time FROM transfer_index "
            "WHERE destination=? ORDER BY block_time DESC LIMIT ?",
            (destination, limit),
        ))
        for cold in self.cold_conns:
            rows.extend(cold.execute(
                "SELECT signature, source, destination, amount_lamports, block_time FROM transfer_index "
                "WHERE destination=? ORDER BY block_time DESC LIMIT ?",
                (destination, limit),
            ))
        deduped = self._dedupe(rows)
        deduped.sort(key=lambda r: r[4], reverse=True)
        return deduped[:limit]

    def by_time_range(self, start_ts: int, end_ts: int, *, limit: int = 5000) -> list[tuple]:
        rows = list(self.hot_conn.execute(
            "SELECT signature, source, destination, amount_lamports, block_time FROM transfer_index "
            "WHERE block_time BETWEEN ? AND ? ORDER BY block_time DESC LIMIT ?",
            (start_ts, end_ts, limit),
        ))
        for cold in self.cold_conns:
            rows.extend(cold.execute(
                "SELECT signature, source, destination, amount_lamports, block_time FROM transfer_index "
                "WHERE block_time BETWEEN ? AND ? ORDER BY block_time DESC LIMIT ?",
                (start_ts, end_ts, limit),
            ))
        deduped = self._dedupe(rows)
        deduped.sort(key=lambda r: r[4], reverse=True)
        return deduped[:limit]

    def count_by_destination(self, destination: str) -> int:
        """Used for parity checks (e.g. Dv34's historical population
        count) -- must equal the monolithic-source count when HOT+COLD
        together cover the same data as the original single table."""
        return len(self.by_destination(destination, limit=1_000_000))
