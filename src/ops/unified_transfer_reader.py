"""STORAGE-LIFECYCLE-P3/P4: HOT+COLD unified transfer query reader.

Read-only. Never writes to HOT or COLD. Proves that a caller can query
across the hot main-DB table and any number of closed cold segments
without knowing which tier a row lives in, with deterministic
deduplication AND conflict detection.

P4 precedence rule (Part 12): during a migration/overlap window the same
(signature, source, destination) row may exist in BOTH hot_conn and a
cold segment. If the two copies AGREE on amount_lamports and block_time,
HOT is treated as authoritative (it is presumed fresher/canonical) and
the row is silently deduplicated -- this is the expected, benign
overlap case (a row was copied to COLD but not yet deleted from HOT).
If the two copies DISAGREE on amount_lamports or block_time for the
SAME (signature, source, destination) key, this is NOT silently
resolved: it is surfaced as a HOT_COLD_EVIDENCE_CONFLICT with both
provenance records preserved, and the conflicting row is EXCLUDED from
the normal result list (callers must handle conflicts explicitly via
get_conflicts(), not have them silently included as if resolved).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass
class EvidenceConflict:
    key: tuple  # (signature, source, destination)
    hot_row: tuple
    cold_row: tuple
    conflict_type: str  # "HOT_COLD_EVIDENCE_CONFLICT"


@dataclass
class UnifiedTransferReader:
    hot_conn: sqlite3.Connection
    cold_conns: list[sqlite3.Connection]
    _last_conflicts: list[EvidenceConflict] = field(default_factory=list, init=False, repr=False)

    def get_conflicts(self) -> list[EvidenceConflict]:
        """Conflicts detected during the MOST RECENT query call. Callers
        that care about conflict surfacing should call this immediately
        after the query method they invoked."""
        return list(self._last_conflicts)

    def _dedupe(self, rows: list[tuple], *, hot_count: int) -> list[tuple]:
        """rows[:hot_count] are from hot_conn (source of truth on
        agreement), the remainder are from cold_conns, in call order.
        amount_lamports is row[3], block_time is row[4] in every query
        method below -- both must match for a duplicate to be treated as
        benign; any mismatch on either field is a HOT_COLD_EVIDENCE_CONFLICT."""
        self._last_conflicts = []
        by_key: dict[tuple, tuple] = {}
        result: list[tuple] = []
        for i, row in enumerate(rows):
            key = (row[0], row[1], row[2])
            is_hot = i < hot_count
            if key not in by_key:
                by_key[key] = row
                result.append(row)
                continue
            existing = by_key[key]
            if existing[3] == row[3] and existing[4] == row[4]:
                continue  # benign duplicate, HOT (already in result if seen first) wins
            # genuine conflict: differing amount or block_time for the same identity
            hot_row = existing if (i >= hot_count) else row  # whichever of the pair came from HOT
            cold_row = row if (i >= hot_count) else existing
            # if neither is actually from hot_conn (two cold segments disagree), still surface it
            self._last_conflicts.append(EvidenceConflict(
                key=key, hot_row=hot_row, cold_row=cold_row, conflict_type="HOT_COLD_EVIDENCE_CONFLICT",
            ))
            if existing in result:
                result.remove(existing)
        return result

    def by_signature(self, signature: str) -> list[tuple]:
        hot_rows = list(self.hot_conn.execute(
            "SELECT signature, source, destination, amount_lamports, block_time FROM transfer_index WHERE signature=?",
            (signature,),
        ))
        rows = list(hot_rows)
        for cold in self.cold_conns:
            rows.extend(cold.execute(
                "SELECT signature, source, destination, amount_lamports, block_time FROM transfer_index WHERE signature=?",
                (signature,),
            ))
        return self._dedupe(rows, hot_count=len(hot_rows))

    def by_source(self, source: str, *, limit: int = 500) -> list[tuple]:
        hot_rows = list(self.hot_conn.execute(
            "SELECT signature, source, destination, amount_lamports, block_time FROM transfer_index "
            "WHERE source=? ORDER BY block_time DESC LIMIT ?",
            (source, limit),
        ))
        rows = list(hot_rows)
        for cold in self.cold_conns:
            rows.extend(cold.execute(
                "SELECT signature, source, destination, amount_lamports, block_time FROM transfer_index "
                "WHERE source=? ORDER BY block_time DESC LIMIT ?",
                (source, limit),
            ))
        deduped = self._dedupe(rows, hot_count=len(hot_rows))
        deduped.sort(key=lambda r: r[4], reverse=True)
        return deduped[:limit]

    def by_destination(self, destination: str, *, limit: int = 500) -> list[tuple]:
        hot_rows = list(self.hot_conn.execute(
            "SELECT signature, source, destination, amount_lamports, block_time FROM transfer_index "
            "WHERE destination=? ORDER BY block_time DESC LIMIT ?",
            (destination, limit),
        ))
        rows = list(hot_rows)
        for cold in self.cold_conns:
            rows.extend(cold.execute(
                "SELECT signature, source, destination, amount_lamports, block_time FROM transfer_index "
                "WHERE destination=? ORDER BY block_time DESC LIMIT ?",
                (destination, limit),
            ))
        deduped = self._dedupe(rows, hot_count=len(hot_rows))
        deduped.sort(key=lambda r: r[4], reverse=True)
        return deduped[:limit]

    def by_time_range(self, start_ts: int, end_ts: int, *, limit: int = 5000) -> list[tuple]:
        hot_rows = list(self.hot_conn.execute(
            "SELECT signature, source, destination, amount_lamports, block_time FROM transfer_index "
            "WHERE block_time BETWEEN ? AND ? ORDER BY block_time DESC LIMIT ?",
            (start_ts, end_ts, limit),
        ))
        rows = list(hot_rows)
        for cold in self.cold_conns:
            rows.extend(cold.execute(
                "SELECT signature, source, destination, amount_lamports, block_time FROM transfer_index "
                "WHERE block_time BETWEEN ? AND ? ORDER BY block_time DESC LIMIT ?",
                (start_ts, end_ts, limit),
            ))
        deduped = self._dedupe(rows, hot_count=len(hot_rows))
        deduped.sort(key=lambda r: r[4], reverse=True)
        return deduped[:limit]

    def earliest_edge(self, address: str) -> tuple | None:
        """Globally-earliest transfer involving address as source OR
        destination, across hot_conn and every cold_conns tier. Each
        connection is asked for only its own earliest match (LIMIT 1
        pushdown), then the per-connection candidates are compared in
        Python -- no full-result-set materialization across tiers."""
        candidates: list[tuple] = []
        for conn in [self.hot_conn, *self.cold_conns]:
            row = conn.execute(
                "SELECT signature, source, destination, amount_lamports, block_time FROM transfer_index "
                "WHERE source=? OR destination=? ORDER BY block_time ASC LIMIT 1",
                (address, address),
            ).fetchone()
            if row is not None:
                candidates.append(tuple(row))
        if not candidates:
            return None
        return min(candidates, key=lambda r: r[4])

    def count_by_destination(self, destination: str) -> int:
        """Used for parity checks (e.g. Dv34's historical population
        count) -- must equal the monolithic-source count when HOT+COLD
        together cover the same data as the original single table."""
        return len(self.by_destination(destination, limit=1_000_000))
