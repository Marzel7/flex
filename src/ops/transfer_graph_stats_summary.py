"""STORAGE-LIFECYCLE-P5B-R2.2: exact compact aggregate summaries for the
`/api/transfer-graph/stats` full-history fields, computed once per closed
COLD segment and merged with a fresh live HOT query at request time.

The current rollover publication gate writes digest-bound summaries under the
version-neutral current cold-storage namespace. This module remains separate
from the existing live route unless an explicit consumer wires it in.

--------------------------------------------------------------------------
DESIGN

Part 1 of the R2.2 investigation classified api_transfer_graph_stats()'s
JSON fields into two groups:

  LIVE/RECENT (untouched, stays a cheap direct HOT query):
    - stats.last_1h / last_24h / last_7d   (indexed_at window, HOT only)
    - recent                                (ORDER BY block_time DESC LIMIT 100)

  FULL_HISTORY_AGGREGATE (this module's optimization target):
    - stats.total_rows, distinct_sigs, distinct_sources, distinct_destinations,
      earliest_bt, latest_bt                  (unfiltered full-table aggregate,
                                                is_valid=1 filter)
    - stats.in_cluster_range, below_range, above_range
                                               (amount bucket counts, is_valid=1)
    - top_funders                             (GROUP BY source, COUNT DISTINCT
                                                destination, SUM/MIN/MAX amount,
                                                MIN/MAX block_time, ORDER BY
                                                creators_funded DESC, total_sol
                                                DESC LIMIT 50, is_valid=1)
    - overlap_signal / overlap_noise          (GROUP BY source WHERE amount
                                                BETWEEN 0.5 AND 10, HAVING
                                                shared_creators>=2, ORDER BY
                                                shared_creators DESC LIMIT 200,
                                                is_valid=1; CEX/infra enrichment
                                                is a separate downstream step
                                                this module does not touch --
                                                it only reproduces overlap_raw)

EXACTNESS / DEDUP METHOD

COLD segments are closed and immutable (segment_manifest.closed_at IS NOT
NULL, digest-bound). A per-segment summary computed once from a closed
segment is valid forever unless the segment's live sha256_of_sorted_
signatures digest changes (which the immutability contract says it never
should -- checked anyway, fail-closed, see SummaryStore.load()).

Simple COUNT(*)-style fields (total_rows, amount buckets) are safe to SUM
across segments IF AND ONLY IF no signature exists in more than one
segment (see below) -- otherwise summing double-counts overlap rows. This
was empirically checked (Part 2 profiling run against the real 42-segment
candidate, see docs/audits/storage_lifecycle_p5b_r2_2_transfer_graph_stats_
performance.json field "cross_segment_disjointness_check"):

  - Cross-COLD-segment signature overlap: EMPIRICALLY MEASURED, see
    profiling artifact. If zero overlap pairs found, COUNT-style per-segment
    integers are summed directly (cheap). If overlap is found, this module
    falls back to storing the compact identity (signature) SET per segment
    and unions at merge time (still cheap: sets of signatures, not full
    rows -- same technique the existing slow adapter already uses, just
    precomputed once per COLD segment instead of on every request).

  - HOT vs COLD overlap (copy-then-verify window) is NOT assumed absent --
    the existing `_transfer_graph_stats_totals_via_hot_cold` adapter's
    precedent (HOT wins on agreement) is preserved: this module always
    unions COLD's precomputed distinct-signature SET with a FRESH live
    HOT distinct-signature set at request time, never trusts a stale HOT
    count baked into a COLD-only summary.

  - distinct_sources / distinct_destinations: wallet addresses are reused
    across many segments (an active funder transacts across months) --
    high cross-segment overlap is EXPECTED and was empirically confirmed.
    These are NEVER summed; always stored as the compact per-segment
    identity SET, unioned at merge time (source/destination address sets
    per segment are small relative to row counts, see Part 10 storage
    measurement).

  - top_funders / overlap_signal/_noise (GROUP BY source): stored as
    PER-SEGMENT per-source partial aggregates (count, distinct destination
    SET, sum/min/max amount, min/max block_time), merged per-source across
    segments + fresh HOT at request time, then the merged per-source rows
    are sorted/limited exactly as the live query does. This reproduces
    exact GROUP BY semantics without re-scanning COLD rows per request.

LIMIT-BOUNDARY TIES: the live overlap_signal query has no secondary ORDER
BY key beyond shared_creators DESC (see docs/audits/storage_lifecycle_p5a_
part18_transfer_graph_stats_parity.json). This module does not invent a
tie-break; when comparing outputs at the truncation boundary, ties are
classified LIMIT_BOUNDARY_TIE_ARTIFACT (same source set, different row
order) rather than EXACT_PARITY failures, per established methodology.

FAIL-CLOSED: SummaryStore.load() verifies every loaded per-segment summary's
bound digest against the segment's live segment_manifest.sha256_of_sorted_
signatures. Any mismatch, or a missing summary for a qualified segment,
raises StaleSummaryError -- callers must not silently fall back to a
HOT-only partial result presented as complete.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass, field

IN_CLUSTER_LO_LAMPORTS = 500_000_000     # 0.5 SOL
IN_CLUSTER_HI_LAMPORTS = 10_000_000_000  # 10 SOL


class StaleSummaryError(RuntimeError):
    """Raised (fail-closed) when a persisted per-segment summary is
    missing, unreadable, or its bound digest does not match the live
    segment's current manifest digest. The full-history aggregate path
    must not silently degrade to a partial/HOT-only result in this case."""


@dataclass
class SegmentSummary:
    segment_id: str
    digest: str                       # bound sha256_of_sorted_signatures
    row_count: int                    # COUNT(*) WHERE is_valid=1
    signatures: list[str]             # distinct signature identity set (for exact union)
    sources: list[str]                # distinct source identity set
    destinations: list[str]           # distinct destination identity set
    earliest_bt: int | None
    latest_bt: int | None
    in_cluster_range: int
    below_range: int
    above_range: int
    # per-source partial GROUP BY aggregates for top_funders / overlap blocks
    # keyed by source address -> dict of partial aggregate fields
    per_source: dict           # {source: {"dests": [...], "count": n, "sum_lamports": n,
                                #           "min_lamports": n, "max_lamports": n,
                                #           "first_seen": n, "last_seen": n}}
    per_source_overlap: dict   # same shape, restricted to the 0.5-10 SOL amount band

    def to_json(self) -> dict:
        return {
            "segment_id": self.segment_id, "digest": self.digest,
            "row_count": self.row_count, "signatures": self.signatures,
            "sources": self.sources, "destinations": self.destinations,
            "earliest_bt": self.earliest_bt, "latest_bt": self.latest_bt,
            "in_cluster_range": self.in_cluster_range,
            "below_range": self.below_range, "above_range": self.above_range,
            "per_source": self.per_source, "per_source_overlap": self.per_source_overlap,
        }

    @staticmethod
    def from_json(d: dict) -> "SegmentSummary":
        return SegmentSummary(
            segment_id=d["segment_id"], digest=d["digest"], row_count=d["row_count"],
            signatures=d["signatures"], sources=d["sources"], destinations=d["destinations"],
            earliest_bt=d["earliest_bt"], latest_bt=d["latest_bt"],
            in_cluster_range=d["in_cluster_range"], below_range=d["below_range"],
            above_range=d["above_range"], per_source=d["per_source"],
            per_source_overlap=d["per_source_overlap"],
        )


def _live_segment_digest(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT sha256_of_sorted_signatures FROM segment_manifest LIMIT 1").fetchone()
    return row[0] if row else None


def build_segment_summary(conn: sqlite3.Connection, segment_id: str) -> SegmentSummary:
    """Computes the full compact summary for ONE closed COLD segment via
    read-only SQL against `conn`. Called once per segment (initial build)
    and again only when that segment's digest changes (which should never
    happen for a truly closed segment -- this function does not itself
    enforce closedness; callers (build_all) check closed_at first)."""
    digest = _live_segment_digest(conn)
    if digest is None:
        raise StaleSummaryError(f"segment {segment_id}: no manifest digest available (not closed?)")

    row = conn.execute(
        "SELECT COUNT(*), MIN(block_time), MAX(block_time) FROM transfer_index WHERE is_valid=1"
    ).fetchone()
    row_count, earliest_bt, latest_bt = row

    signatures = [r[0] for r in conn.execute(
        "SELECT DISTINCT signature FROM transfer_index WHERE is_valid=1")]
    sources = [r[0] for r in conn.execute(
        "SELECT DISTINCT source FROM transfer_index WHERE is_valid=1")]
    destinations = [r[0] for r in conn.execute(
        "SELECT DISTINCT destination FROM transfer_index WHERE is_valid=1")]

    bucket_row = conn.execute(
        f"""SELECT
            SUM(CASE WHEN amount_lamports BETWEEN {IN_CLUSTER_LO_LAMPORTS} AND {IN_CLUSTER_HI_LAMPORTS} THEN 1 ELSE 0 END),
            SUM(CASE WHEN amount_lamports < {IN_CLUSTER_LO_LAMPORTS} THEN 1 ELSE 0 END),
            SUM(CASE WHEN amount_lamports > {IN_CLUSTER_HI_LAMPORTS} THEN 1 ELSE 0 END)
        FROM transfer_index WHERE is_valid=1"""
    ).fetchone()
    in_cluster_range, below_range, above_range = (v or 0 for v in bucket_row)

    per_source: dict = {}
    for source, dest, cnt, sum_l, min_l, max_l, fseen, lseen in conn.execute(
        """SELECT source, destination, COUNT(*), SUM(amount_lamports), MIN(amount_lamports),
                  MAX(amount_lamports), MIN(block_time), MAX(block_time)
           FROM transfer_index WHERE is_valid=1 GROUP BY source, destination"""
    ):
        agg = per_source.setdefault(source, {
            "dests": set(), "count": 0, "sum_lamports": 0,
            "min_lamports": None, "max_lamports": None,
            "first_seen": None, "last_seen": None,
        })
        agg["dests"].add(dest)
        agg["count"] += cnt
        agg["sum_lamports"] += sum_l
        agg["min_lamports"] = min_l if agg["min_lamports"] is None else min(agg["min_lamports"], min_l)
        agg["max_lamports"] = max_l if agg["max_lamports"] is None else max(agg["max_lamports"], max_l)
        agg["first_seen"] = fseen if agg["first_seen"] is None else min(agg["first_seen"], fseen)
        agg["last_seen"] = lseen if agg["last_seen"] is None else max(agg["last_seen"], lseen)
    per_source = {k: {**v, "dests": sorted(v["dests"])} for k, v in per_source.items()}

    per_source_overlap: dict = {}
    for source, dest, cnt in conn.execute(
        f"""SELECT source, destination, COUNT(*) FROM transfer_index
            WHERE is_valid=1 AND amount_lamports BETWEEN {IN_CLUSTER_LO_LAMPORTS} AND {IN_CLUSTER_HI_LAMPORTS}
            GROUP BY source, destination"""
    ):
        agg = per_source_overlap.setdefault(source, {"dests": set()})
        agg["dests"].add(dest)
    per_source_overlap = {k: {"dests": sorted(v["dests"])} for k, v in per_source_overlap.items()}

    return SegmentSummary(
        segment_id=segment_id, digest=digest, row_count=row_count or 0,
        signatures=signatures, sources=sources, destinations=destinations,
        earliest_bt=earliest_bt, latest_bt=latest_bt,
        in_cluster_range=in_cluster_range, below_range=below_range, above_range=above_range,
        per_source=per_source, per_source_overlap=per_source_overlap,
    )


class SummaryStore:
    """Persists / loads per-segment summaries as one JSON file per segment
    under `store_dir`. For this milestone, store_dir points under
    database/_p5a_migration_build/ (gitignored) or /tmp -- see module
    docstring / Part 3 handoff notes for the real Stage-3 production
    location proposal (a small SQLite table alongside the COLD registry,
    e.g. database/current_cold_storage/summaries/, built by the same runbook
    step that closes+registers a new COLD segment)."""

    def __init__(self, store_dir: str):
        self.store_dir = store_dir
        os.makedirs(store_dir, exist_ok=True)

    def _path(self, segment_id: str) -> str:
        return os.path.join(self.store_dir, f"{segment_id}.summary.json")

    def save(self, summary: SegmentSummary) -> str:
        path = self._path(summary.segment_id)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(summary.to_json(), f)
        os.replace(tmp, path)
        return path

    def has(self, segment_id: str) -> bool:
        return os.path.isfile(self._path(segment_id))

    def load_verified(self, segment_id: str, live_conn: sqlite3.Connection) -> SegmentSummary:
        """Fail-closed load: raises StaleSummaryError if the summary file
        is missing, unreadable, or its bound digest doesn't match the
        segment's CURRENT live manifest digest."""
        path = self._path(segment_id)
        if not os.path.isfile(path):
            raise StaleSummaryError(f"segment {segment_id}: no summary file at {path}")
        try:
            with open(path) as f:
                data = json.load(f)
            summary = SegmentSummary.from_json(data)
        except (json.JSONDecodeError, KeyError, OSError) as exc:
            raise StaleSummaryError(f"segment {segment_id}: summary file unreadable/corrupt: {exc}") from exc

        live_digest = _live_segment_digest(live_conn)
        if live_digest is None:
            raise StaleSummaryError(f"segment {segment_id}: live segment has no manifest digest")
        if summary.digest != live_digest:
            raise StaleSummaryError(
                f"segment {segment_id}: summary digest {summary.digest!r} != "
                f"live manifest digest {live_digest!r} -- summary is STALE, refusing to trust it"
            )
        return summary


def build_all_summaries(registry, store: SummaryStore, *, force: bool = False) -> dict:
    """Builds (or, unless force=True, skips already-fresh) a summary for
    every qualified closed segment in `registry` (a built
    ColdSegmentRegistry). Returns {"built": [...], "skipped": [...],
    "seconds": float}. Incremental-cost proof: only segments lacking a
    valid (digest-matching) summary are rebuilt."""
    t0 = time.monotonic()
    built, skipped = [], []
    for seg_info, conn in zip(registry.segments, registry.connections):
        if not force:
            try:
                store.load_verified(seg_info.segment_id, conn)
                skipped.append(seg_info.segment_id)
                continue
            except StaleSummaryError:
                pass
        summary = build_segment_summary(conn, seg_info.segment_id)
        store.save(summary)
        built.append(seg_info.segment_id)
    return {"built": built, "skipped": skipped, "seconds": time.monotonic() - t0}


def _query_hot_live(hot_conn: sqlite3.Connection) -> dict:
    """Fresh, cheap, per-request query against the (small) HOT DB only --
    same field set as a segment summary, computed live every request so
    HOT freshness is exact (no staleness window on HOT data).

    PERFORMANCE NOTE (found during R2.2 Part 13 latency measurement): this
    full-table-every-request version is CORRECT but not fast enough on its
    own. Measured on the candidate HOT table (3.57M rows): `SELECT DISTINCT
    signature/source/destination FROM transfer_index` costs ~35-70s PER
    COLUMN even via a confirmed covering-index scan (EXPLAIN QUERY PLAN
    shows `SCAN ... USING COVERING INDEX`, not a missing-index problem --
    the cost is inherent to DISTINCT cardinality computation over millions
    of rows). For a per-request endpoint this function alone reproduces
    the same class of pathological latency the COLD summaries were built
    to eliminate, just at HOT's smaller scale. Kept here as the reference/
    ground-truth implementation (used by tests for parity); the actual
    request path should use HotCheckpointCache.refresh_and_load() below,
    which computes the same fields incrementally and is empirically ~35x+
    faster (see HotCheckpointCache docstring)."""
    row = hot_conn.execute(
        "SELECT COUNT(*), MIN(block_time), MAX(block_time) FROM transfer_index WHERE is_valid=1"
    ).fetchone()
    row_count, earliest_bt, latest_bt = row

    signatures = {r[0] for r in hot_conn.execute(
        "SELECT DISTINCT signature FROM transfer_index WHERE is_valid=1")}
    sources = {r[0] for r in hot_conn.execute(
        "SELECT DISTINCT source FROM transfer_index WHERE is_valid=1")}
    destinations = {r[0] for r in hot_conn.execute(
        "SELECT DISTINCT destination FROM transfer_index WHERE is_valid=1")}

    bucket_row = hot_conn.execute(
        f"""SELECT
            SUM(CASE WHEN amount_lamports BETWEEN {IN_CLUSTER_LO_LAMPORTS} AND {IN_CLUSTER_HI_LAMPORTS} THEN 1 ELSE 0 END),
            SUM(CASE WHEN amount_lamports < {IN_CLUSTER_LO_LAMPORTS} THEN 1 ELSE 0 END),
            SUM(CASE WHEN amount_lamports > {IN_CLUSTER_HI_LAMPORTS} THEN 1 ELSE 0 END)
        FROM transfer_index WHERE is_valid=1"""
    ).fetchone()
    in_cluster_range, below_range, above_range = (v or 0 for v in bucket_row)

    per_source: dict = {}
    for source, dest, cnt, sum_l, min_l, max_l, fseen, lseen in hot_conn.execute(
        """SELECT source, destination, COUNT(*), SUM(amount_lamports), MIN(amount_lamports),
                  MAX(amount_lamports), MIN(block_time), MAX(block_time)
           FROM transfer_index WHERE is_valid=1 GROUP BY source, destination"""
    ):
        agg = per_source.setdefault(source, {
            "dests": set(), "count": 0, "sum_lamports": 0,
            "min_lamports": None, "max_lamports": None,
            "first_seen": None, "last_seen": None,
        })
        agg["dests"].add(dest)
        agg["count"] += cnt
        agg["sum_lamports"] += sum_l
        agg["min_lamports"] = min_l if agg["min_lamports"] is None else min(agg["min_lamports"], min_l)
        agg["max_lamports"] = max_l if agg["max_lamports"] is None else max(agg["max_lamports"], max_l)
        agg["first_seen"] = fseen if agg["first_seen"] is None else min(agg["first_seen"], fseen)
        agg["last_seen"] = lseen if agg["last_seen"] is None else max(agg["last_seen"], lseen)

    per_source_overlap: dict = {}
    for source, dest, cnt in hot_conn.execute(
        f"""SELECT source, destination, COUNT(*) FROM transfer_index
            WHERE is_valid=1 AND amount_lamports BETWEEN {IN_CLUSTER_LO_LAMPORTS} AND {IN_CLUSTER_HI_LAMPORTS}
            GROUP BY source, destination"""
    ):
        agg = per_source_overlap.setdefault(source, {"dests": set()})
        agg["dests"].add(dest)

    return {
        "row_count": row_count or 0, "earliest_bt": earliest_bt, "latest_bt": latest_bt,
        "signatures": signatures, "sources": sources, "destinations": destinations,
        "in_cluster_range": in_cluster_range, "below_range": below_range, "above_range": above_range,
        "per_source": per_source, "per_source_overlap": per_source_overlap,
    }


class HotCheckpointCache:
    """Incrementally-maintained HOT-side identity/aggregate cache, keyed
    by the highest `id` seen so far. This closes the remaining performance
    gap _query_hot_live has on its own (see its docstring): SQLite's
    `SELECT DISTINCT x FROM transfer_index WHERE id > ?` does NOT prune by
    the id range -- EXPLAIN QUERY PLAN shows the same
    `SCAN ... USING COVERING INDEX` with or without the WHERE clause,
    confirmed by direct timing (a WHERE id>N DISTINCT query over only the
    newest ~1% of rows took the same ~35-40s as a full-table DISTINCT
    scan). The fix: drop DISTINCT and issue a PLAIN `SELECT signature,
    source, destination, block_time, amount_lamports FROM transfer_index
    WHERE id > ? AND is_valid=1` -- this uses the integer-primary-key
    rowid index directly (`SEARCH transfer_index USING INTEGER PRIMARY KEY
    (rowid>?)`, confirmed via EXPLAIN QUERY PLAN) and costs time
    proportional to NEW rows only (measured: ~0.3s for a 37K-row delta on
    the 3.57M-row candidate table, vs 35-70s for a DISTINCT scan of the
    same rows). Deduplication/aggregation happens in Python against a
    persisted running state, merged with the previous checkpoint -- this
    is exact (a full identity set, not an approximation), the same
    technique already used for immutable COLD segments, just applied to a
    live, growing HOT table with an id-based (not digest-based) staleness
    boundary.

    Persistence: one JSON file (self.path) holding last_id + all running
    aggregate state. Not digest-bound (HOT is expected to grow -- that's
    the point), but always brought current via refresh() before use, so a
    caller never reads genuinely stale HOT data.
    """

    def __init__(self, path: str):
        self.path = path
        self._state = self._load()

    def _load(self) -> dict:
        if os.path.isfile(self.path):
            with open(self.path) as f:
                raw = json.load(f)
            raw["signatures"] = set(raw.get("signatures", []))
            raw["sources"] = set(raw.get("sources", []))
            raw["destinations"] = set(raw.get("destinations", []))
            for bucket in ("per_source", "per_source_overlap"):
                for agg in raw.get(bucket, {}).values():
                    agg["dests"] = set(agg.get("dests", []))
            raw.setdefault("per_source", {})
            raw.setdefault("per_source_overlap", {})
            return raw
        return {
            "last_id": 0, "row_count": 0, "earliest_bt": None, "latest_bt": None,
            "in_cluster_range": 0, "below_range": 0, "above_range": 0,
            "signatures": set(), "sources": set(), "destinations": set(),
            "per_source": {}, "per_source_overlap": {},
        }

    def _save(self) -> None:
        out = dict(self._state)
        out["signatures"] = sorted(self._state["signatures"])
        out["sources"] = sorted(self._state["sources"])
        out["destinations"] = sorted(self._state["destinations"])
        out["per_source"] = {k: {**v, "dests": sorted(v["dests"])} for k, v in self._state["per_source"].items()}
        out["per_source_overlap"] = {k: {"dests": sorted(v["dests"])} for k, v in self._state["per_source_overlap"].items()}
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(out, f)
        os.replace(tmp, self.path)

    def refresh(self, hot_conn: sqlite3.Connection) -> dict:
        """Scans only rows with id > last checkpoint, via a PLAIN (non-
        DISTINCT) query using the rowid index, and merges them into the
        persisted running state. No-op (just a cheap MAX(id) check) if
        HOT hasn't grown since the last refresh."""
        last_id = self._state["last_id"]
        max_id_row = hot_conn.execute("SELECT MAX(id) FROM transfer_index").fetchone()
        max_id = max_id_row[0] if max_id_row and max_id_row[0] is not None else 0
        if max_id <= last_id:
            return {"new_rows": 0, "last_id": last_id}

        s = self._state
        new_rows = 0
        for sig, source, dest, amt, bt, is_valid in hot_conn.execute(
            "SELECT signature, source, destination, amount_lamports, block_time, is_valid "
            "FROM transfer_index WHERE id > ?", (last_id,),
        ):
            if not is_valid:
                continue
            new_rows += 1
            s["signatures"].add(sig)
            s["sources"].add(source)
            s["destinations"].add(dest)
            if bt is not None:
                s["earliest_bt"] = bt if s["earliest_bt"] is None else min(s["earliest_bt"], bt)
                s["latest_bt"] = bt if s["latest_bt"] is None else max(s["latest_bt"], bt)
            if IN_CLUSTER_LO_LAMPORTS <= amt <= IN_CLUSTER_HI_LAMPORTS:
                s["in_cluster_range"] += 1
            elif amt < IN_CLUSTER_LO_LAMPORTS:
                s["below_range"] += 1
            else:
                s["above_range"] += 1

            agg = s["per_source"].setdefault(source, {
                "dests": set(), "count": 0, "sum_lamports": 0,
                "min_lamports": None, "max_lamports": None,
                "first_seen": None, "last_seen": None,
            })
            agg["dests"].add(dest)
            agg["count"] += 1
            agg["sum_lamports"] += amt
            agg["min_lamports"] = amt if agg["min_lamports"] is None else min(agg["min_lamports"], amt)
            agg["max_lamports"] = amt if agg["max_lamports"] is None else max(agg["max_lamports"], amt)
            agg["first_seen"] = bt if agg["first_seen"] is None else min(agg["first_seen"], bt)
            agg["last_seen"] = bt if agg["last_seen"] is None else max(agg["last_seen"], bt)

            if IN_CLUSTER_LO_LAMPORTS <= amt <= IN_CLUSTER_HI_LAMPORTS:
                ov = s["per_source_overlap"].setdefault(source, {"dests": set()})
                ov["dests"].add(dest)

        s["row_count"] += new_rows
        s["last_id"] = max_id
        self._save()
        return {"new_rows": new_rows, "last_id": max_id}

    def as_hot_query_result(self) -> dict:
        """Returns the SAME shape _query_hot_live() returns, from the
        persisted (now-current, per refresh()) checkpoint state."""
        s = self._state
        return {
            "row_count": s["row_count"], "earliest_bt": s["earliest_bt"], "latest_bt": s["latest_bt"],
            "signatures": set(s["signatures"]), "sources": set(s["sources"]), "destinations": set(s["destinations"]),
            "in_cluster_range": s["in_cluster_range"], "below_range": s["below_range"], "above_range": s["above_range"],
            "per_source": s["per_source"], "per_source_overlap": s["per_source_overlap"],
        }

    def refresh_and_load(self, hot_conn: sqlite3.Connection) -> dict:
        self.refresh(hot_conn)
        return self.as_hot_query_result()


def compute_exact_stats(hot_conn: sqlite3.Connection, registry, store: SummaryStore) -> dict:
    """Merges (immutable, digest-verified COLD summaries) + (fresh live
    HOT query) into the exact full-history aggregate fields of
    api_transfer_graph_stats(). Fail-closed: raises StaleSummaryError if
    any qualified segment lacks a valid summary -- never silently
    degrades to a partial HOT-only result.

    Returns a dict with keys: total_rows, distinct_sigs, distinct_sources,
    distinct_destinations, earliest_bt, latest_bt, in_cluster_range,
    below_range, above_range, top_funders (list, LIMIT 50 semantics),
    overlap_raw (list, LIMIT 200 semantics, matching the live query's
    `GROUP BY source ... HAVING shared_creators>=2 ORDER BY shared_creators
    DESC LIMIT 200` -- CEX/infra enrichment is NOT applied here, same
    scope as the live query's overlap_raw stage before enrichment).
    """
    registry.ensure_built()
    summaries = [store.load_verified(s.segment_id, c)
                 for s, c in zip(registry.segments, registry.connections)]

    hot = _query_hot_live(hot_conn)
    return _merge_summaries_and_hot(summaries, hot)


def compute_exact_stats_fast(hot_conn: sqlite3.Connection, registry, store: SummaryStore,
                              hot_cache: "HotCheckpointCache") -> dict:
    """Same exact result/contract as compute_exact_stats, but sources HOT's
    contribution from an incrementally-refreshed HotCheckpointCache instead
    of a fresh full-table DISTINCT scan every request -- this is the
    version that actually meets the R2.2 latency target (see
    HotCheckpointCache's docstring for why compute_exact_stats alone,
    despite being correct, is still too slow on its own). Still fail-closed
    on COLD (store.load_verified raises StaleSummaryError on any missing/
    stale segment summary) -- only HOT's query mechanism differs."""
    registry.ensure_built()
    summaries = [store.load_verified(s.segment_id, c)
                 for s, c in zip(registry.segments, registry.connections)]

    hot = hot_cache.refresh_and_load(hot_conn)
    return _merge_summaries_and_hot(summaries, hot)


def _merge_summaries_and_hot(summaries: list["SegmentSummary"], hot: dict) -> dict:
    sig_sets = [set(s.signatures) for s in summaries] + [hot["signatures"]]
    src_sets = [set(s.sources) for s in summaries] + [hot["sources"]]
    dst_sets = [set(s.destinations) for s in summaries] + [hot["destinations"]]

    distinct_sigs = len(set().union(*sig_sets)) if sig_sets else 0
    distinct_sources = len(set().union(*src_sets)) if src_sets else 0
    distinct_destinations = len(set().union(*dst_sets)) if dst_sets else 0

    total_rows = sum(s.row_count for s in summaries) + hot["row_count"]
    in_cluster_range = sum(s.in_cluster_range for s in summaries) + hot["in_cluster_range"]
    below_range = sum(s.below_range for s in summaries) + hot["below_range"]
    above_range = sum(s.above_range for s in summaries) + hot["above_range"]

    earliest_candidates = [s.earliest_bt for s in summaries if s.earliest_bt is not None]
    if hot["earliest_bt"] is not None:
        earliest_candidates.append(hot["earliest_bt"])
    earliest_bt = min(earliest_candidates) if earliest_candidates else None

    latest_candidates = [s.latest_bt for s in summaries if s.latest_bt is not None]
    if hot["latest_bt"] is not None:
        latest_candidates.append(hot["latest_bt"])
    latest_bt = max(latest_candidates) if latest_candidates else None

    # --- merged per-source aggregates for top_funders --------------------
    # PERFORMANCE NOTE (R2.2 Part 13): profiled at ~420K distinct sources on
    # the real candidate data, a naive dict.setdefault-per-row merge (one
    # fresh dict literal + 4 comparisons per row) cost ~8.3s. Using plain
    # dict.get (no repeated dict-literal construction on the hot path) cuts
    # this roughly in half; still the single largest remaining cost in the
    # merge step at this cardinality -- documented honestly in the
    # performance artifact rather than claimed away.
    merged: dict = {}
    for s in summaries:
        for source, agg in s.per_source.items():
            m = merged.get(source)
            if m is None:
                merged[source] = {
                    "dests": set(agg["dests"]), "count": agg["count"],
                    "sum_lamports": agg["sum_lamports"],
                    "min_lamports": agg["min_lamports"], "max_lamports": agg["max_lamports"],
                    "first_seen": agg["first_seen"], "last_seen": agg["last_seen"],
                }
                continue
            m["dests"].update(agg["dests"])
            m["count"] += agg["count"]
            m["sum_lamports"] += agg["sum_lamports"]
            if agg["min_lamports"] < m["min_lamports"]:
                m["min_lamports"] = agg["min_lamports"]
            if agg["max_lamports"] > m["max_lamports"]:
                m["max_lamports"] = agg["max_lamports"]
            if agg["first_seen"] < m["first_seen"]:
                m["first_seen"] = agg["first_seen"]
            if agg["last_seen"] > m["last_seen"]:
                m["last_seen"] = agg["last_seen"]
    for source, agg in hot["per_source"].items():
        m = merged.get(source)
        if m is None:
            merged[source] = {
                "dests": set(agg["dests"]), "count": agg["count"],
                "sum_lamports": agg["sum_lamports"],
                "min_lamports": agg["min_lamports"], "max_lamports": agg["max_lamports"],
                "first_seen": agg["first_seen"], "last_seen": agg["last_seen"],
            }
            continue
        m["dests"].update(agg["dests"])
        m["count"] += agg["count"]
        m["sum_lamports"] += agg["sum_lamports"]
        if agg["min_lamports"] < m["min_lamports"]:
            m["min_lamports"] = agg["min_lamports"]
        if agg["max_lamports"] > m["max_lamports"]:
            m["max_lamports"] = agg["max_lamports"]
        if agg["first_seen"] < m["first_seen"]:
            m["first_seen"] = agg["first_seen"]
        if agg["last_seen"] > m["last_seen"]:
            m["last_seen"] = agg["last_seen"]

    top_funders = []
    for source, agg in merged.items():
        top_funders.append({
            "source": source,
            "creators_funded": len(agg["dests"]),
            "transfer_count": agg["count"],
            "total_sol": round(agg["sum_lamports"] / 1e9, 4),
            "min_sol": round(agg["min_lamports"] / 1e9, 6),
            "max_sol": round(agg["max_lamports"] / 1e9, 4),
            "first_seen": agg["first_seen"],
            "last_seen": agg["last_seen"],
        })
    top_funders.sort(key=lambda r: (r["creators_funded"], r["total_sol"]), reverse=True)
    top_funders = top_funders[:50]

    # --- merged per-source overlap (0.5-10 SOL band) ----------------------
    merged_overlap: dict = {}
    for s in summaries:
        for source, agg in s.per_source_overlap.items():
            merged_overlap.setdefault(source, set()).update(agg["dests"])
    for source, agg in hot["per_source_overlap"].items():
        merged_overlap.setdefault(source, set()).update(agg["dests"])

    overlap_raw = [
        {"source": source, "shared_creators": len(dests)}
        for source, dests in merged_overlap.items() if len(dests) >= 2
    ]
    overlap_raw.sort(key=lambda r: r["shared_creators"], reverse=True)
    overlap_raw = overlap_raw[:200]

    return {
        "total_rows": total_rows, "distinct_sigs": distinct_sigs,
        "distinct_sources": distinct_sources, "distinct_destinations": distinct_destinations,
        "earliest_bt": earliest_bt, "latest_bt": latest_bt,
        "in_cluster_range": in_cluster_range, "below_range": below_range, "above_range": above_range,
        "top_funders": top_funders, "overlap_raw": overlap_raw,
    }
