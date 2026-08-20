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

    def last_activity_max_block_time(self, wallets: list[str]) -> int | None:
        """STORAGE-LIFECYCLE-P5B-R2.1: reproduces
        DevIntelligenceV2._fetch_last_activity_ts's transfer_index fallback
        (src/core/dev_intelligence_v2.py):

            SELECT MAX(block_time) AS last_ts FROM transfer_index
            WHERE source IN (...) OR destination IN (...)

        across hot_conn and every cold_conns tier. Each connection pushes
        its own MAX(block_time) pushdown (index-friendly, no full-row
        materialization); the per-connection scalar maxima are then
        combined in Python with a single max() -- correctness-safe because
        MAX is associative/order-independent across a partition (unlike
        funder_creator_pairs' DISTINCT-pair merge, there is no
        double-counting risk with a scalar MAX: the same edge appearing in
        both HOT and COLD during the copy-then-verify overlap window
        simply produces the same block_time twice, and max(x, x) == x)."""
        if not wallets:
            return None
        placeholders = ",".join("?" for _ in wallets)
        query = (
            f"SELECT MAX(block_time) FROM transfer_index "
            f"WHERE source IN ({placeholders}) OR destination IN ({placeholders})"
        )
        params = (*wallets, *wallets)
        best: int | None = None
        for conn in [self.hot_conn, *self.cold_conns]:
            row = conn.execute(query, params).fetchone()
            if row is not None and row[0] is not None:
                best = row[0] if best is None else max(best, row[0])
        return best

    def count_by_destination(self, destination: str) -> int:
        """Used for parity checks (e.g. Dv34's historical population
        count) -- must equal the monolithic-source count when HOT+COLD
        together cover the same data as the original single table."""
        return len(self.by_destination(destination, limit=1_000_000))

    # amount_sol is a GENERATED ALWAYS AS (amount_lamports / 1e9) STORED
    # column on the HOT table (and on flex_complete_database.db, the
    # production source of this filter), but the 42 COLD segment files do
    # NOT carry that generated column -- only amount_lamports. Expressing
    # the filter in lamports is mathematically identical (verified: on the
    # HOT candidate DB, `amount_sol BETWEEN 0.5 AND 10` and
    # `amount_lamports BETWEEN 500000000 AND 10000000000` both return
    # 226061 rows) and works unmodified against every tier.
    _FUNDER_CREATOR_LAMPORTS_LO = 500_000_000
    _FUNDER_CREATOR_LAMPORTS_HI = 10_000_000_000

    def funder_creator_pairs(self) -> set[tuple[str, str]]:
        """Reproduces FunderCreatorExtractor.extract_funder_creator_pairs's
        SQL (src/core/funder_overlap_analysis.py):

            SELECT DISTINCT source AS funder_wallet, destination AS creator_wallet
            FROM transfer_index
            WHERE amount_sol BETWEEN 0.5 AND 10 AND is_valid = 1

        across hot_conn and every cold_conns tier. Each connection pushes
        down its own WHERE filter and DISTINCT -- only the (source,
        destination) pairs actually matching the seed-phase amount/validity
        filter ever leave SQL, never full unfiltered transfer_index rows.

        The per-connection DISTINCT pairs are then merged in Python into a
        single set. This is the correctness-critical step: summing
        per-connection distinct-destination COUNTS would double count a
        creator that a funder funded once in COLD (e.g. months ago) and
        again in HOT (e.g. recently) -- the same (source, destination) pair
        appearing in two tiers must contribute exactly one membership to
        the global distinct set, not two. Building one Python set of pairs
        across all tiers before counting distinct destinations per source
        guarantees that.

        Note: the original query has no ORDER BY / LIMIT / secondary sort
        key -- it is an unordered DISTINCT pair extraction consumed
        entirely into a Python dict-of-sets by the caller. This method
        preserves that: it returns an unordered set, matching the
        production consumer's actual usage (FunderCreatorExtractor never
        relies on row order).

        CEX/infra wallet exclusion (`excluded` in the original) and
        pairwise overlap/coordination-level computation are Python-side
        concerns in the production module, not part of the SQL this reader
        proxies -- callers apply that filtering to the returned set
        themselves, exactly as FunderOverlapAnalyzer.compute_funder_overlaps
        does today.
        """
        query = (
            "SELECT DISTINCT source, destination FROM transfer_index "
            "WHERE amount_lamports BETWEEN ? AND ? AND is_valid = 1"
        )
        params = (self._FUNDER_CREATOR_LAMPORTS_LO, self._FUNDER_CREATOR_LAMPORTS_HI)
        pairs: set[tuple[str, str]] = set()
        for conn in [self.hot_conn, *self.cold_conns]:
            for source, destination in conn.execute(query, params):
                pairs.add((source, destination))
        return pairs

    def funder_creator_map(self) -> dict[str, set[str]]:
        """funder_creator_pairs(), grouped by funder -- same shape as
        FunderCreatorExtractor.extract_funder_creator_pairs's return value
        ({funder_wallet: {creator_wallet, ...}}), ready for the same
        Python-side pairwise-overlap computation
        FunderOverlapAnalyzer.compute_funder_overlaps performs."""
        out: dict[str, set[str]] = {}
        for source, destination in self.funder_creator_pairs():
            out.setdefault(source, set()).add(destination)
        return out

    def edge_earliest_block_time(self, source: str, destination: str) -> int | None:
        """STORAGE-LIFECYCLE-P5B-R2.1 Part 4 adapter.

        Reproduces src/analysis/watchtower_detector.py's _edge_ts(conn,
        source, dest):

            SELECT MIN(block_time) FROM transfer_index
            WHERE source = ? AND destination = ? AND is_valid = 1

        across hot_conn and every cold_conns tier. Each connection pushes
        its own MIN(block_time) aggregate down into SQL (index-friendly,
        no full-row materialization); only the small per-connection scalar
        is compared in Python to find the global minimum. This is the
        same per-connection-aggregate-then-merge pattern as
        _transfer_graph_stats_totals_via_hot_cold in src/core/main.py --
        deliberately NOT built by pulling every matching row through
        by_source/by_destination and taking min() in Python, since that
        would materialize full row tuples this scalar query doesn't need.

        Returns None if no matching edge exists in any tier (matching the
        original's `row[0] if row and row[0] else None` behavior).
        """
        query = (
            "SELECT MIN(block_time) FROM transfer_index "
            "WHERE source = ? AND destination = ? AND is_valid = 1"
        )
        candidates: list[int] = []
        for conn in [self.hot_conn, *self.cold_conns]:
            row = conn.execute(query, (source, destination)).fetchone()
            if row and row[0] is not None:
                candidates.append(row[0])
        return min(candidates) if candidates else None

    def funders_of_destination(self, destination: str) -> list[tuple[str, int]]:
        """STORAGE-LIFECYCLE-P5B-R2.1 Part 4 adapter.

        Reproduces the transfer_index half of
        src/analysis/watchtower_detector.py's _funders_of(addr, conn,
        infra):

            SELECT source, amount_lamports FROM transfer_index
            WHERE destination = ? AND is_valid = 1

        across hot_conn and every cold_conns tier -- the exact same
        (source=?, destination=?, is_valid) shape as by_destination(),
        but returning the raw (source, amount_lamports) pairs the caller
        needs (not full 5-tuples), and keeping ALL matching rows per
        source (the caller's own agg dict in watchtower_detector.py keeps
        the max amount per funder; that reduction is intentionally left
        to the caller here, unchanged from the original function's
        contract, since infra-set filtering and max-amount aggregation
        are caller-side policy, not an SQL-pushdown concern).

        Deduplication across HOT/COLD tiers uses the same signature-based
        precedence as _dedupe (a row present in both tiers with identical
        amount_lamports/block_time is counted once, HOT wins), so a
        caller reducing this to per-source max-amount will not double
        count an edge that was copied to COLD but not yet purged from
        HOT.
        """
        hot_rows = list(self.hot_conn.execute(
            "SELECT signature, source, destination, amount_lamports, block_time "
            "FROM transfer_index WHERE destination = ? AND is_valid = 1",
            (destination,),
        ))
        rows = list(hot_rows)
        for cold in self.cold_conns:
            rows.extend(cold.execute(
                "SELECT signature, source, destination, amount_lamports, block_time "
                "FROM transfer_index WHERE destination = ? AND is_valid = 1",
                (destination,),
            ))
        deduped = self._dedupe(rows, hot_count=len(hot_rows))
        return [(r[1], r[3]) for r in deduped]

    def funders_of_destination_set(self, destinations: list[str]) -> set[str]:
        """STORAGE-LIFECYCLE-P5B-R2.1 Part 4 adapter.

        Reproduces the transfer_index half of
        src/core/funder_overlap_analysis.py's
        OrganizationOverlapScorer.get_organization_funder_overlap (line
        ~403):

            SELECT DISTINCT wallet FROM (
                SELECT source as wallet FROM transfer_index
                WHERE destination IN (<creator_wallets of an org>)
                UNION
                SELECT source as wallet FROM transfer_index
                WHERE destination IN (<same set again>)
            )

        The original issues the identical subquery twice (a harmless
        no-op UNION against itself in the production SQL); this adapter
        collapses that to one DISTINCT source pass per connection, which
        is equivalent (UNION of a set with itself is itself) and avoids
        doubling the COLD-tier scan cost for no behavioral difference.

        `destinations` (the org's creator_wallet set) is resolved by the
        CALLER from dev_organization_members -- a HOT-only table with no
        COLD equivalent, so it is out of scope for this adapter, exactly
        as funder_creator_pairs() leaves CEX/infra-exclusion and
        pairwise-overlap computation to its caller. This method only
        proxies the transfer_index IN(...) lookup across HOT+COLD tiers,
        pushed down per-connection as a DISTINCT source scan (no full-row
        materialization), merged into a single Python set (so a funder
        appearing in both HOT and a COLD segment contributes one
        membership, not two -- same double-count-avoidance rationale as
        funder_creator_pairs()).

        Returns an empty set immediately (no queries issued) if
        `destinations` is empty, matching the original's short-circuit
        for `len(org_wallets) < 2`.
        """
        if not destinations:
            return set()
        placeholders = ",".join("?" for _ in destinations)
        query = f"SELECT DISTINCT source FROM transfer_index WHERE destination IN ({placeholders})"
        wallets: set[str] = set()
        for conn in [self.hot_conn, *self.cold_conns]:
            for (source,) in conn.execute(query, tuple(destinations)):
                wallets.add(source)
        return wallets

    def last_activity_max_block_time(self, wallet_list: list[str]) -> int | None:
        """STORAGE-LIFECYCLE-P5B-R2.1 Part 4 adapter.

        Reproduces src/core/dev_intelligence_v2.py's
        OrganizationSignalComputer._fetch_last_activity_ts's transfer_index
        fallback:

            SELECT MAX(block_time) AS last_ts FROM transfer_index
            WHERE source IN (<wallet_list>) OR destination IN (<wallet_list>)

        across hot_conn and every cold_conns tier. Each connection pushes
        its own MAX(block_time) aggregate down into SQL; only the small
        per-connection scalar is compared in Python to find the global
        maximum -- same per-connection-aggregate-then-merge pattern as
        edge_earliest_block_time()/_transfer_graph_stats_totals_via_hot_cold,
        deliberately not materializing matching rows in Python.

        Returns None if wallet_list is empty (matching the original's
        `if not wallet_list: return None` short-circuit) or if no tier has
        a matching row.
        """
        if not wallet_list:
            return None
        placeholders = ",".join("?" for _ in wallet_list)
        query = (
            f"SELECT MAX(block_time) FROM transfer_index "
            f"WHERE source IN ({placeholders}) OR destination IN ({placeholders})"
        )
        params = tuple(wallet_list) + tuple(wallet_list)
        candidates: list[int] = []
        for conn in [self.hot_conn, *self.cold_conns]:
            row = conn.execute(query, params).fetchone()
            if row and row[0] is not None:
                candidates.append(row[0])
        return max(candidates) if candidates else None
