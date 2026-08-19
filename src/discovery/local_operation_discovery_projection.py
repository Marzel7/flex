"""Local, provider-free operation-family discovery projection.

Reads flex_complete_database.db (mode=ro) only. Writes exclusively to a
NEW, isolated output database
(database/local_operation_discovery_corpus.db) -- never mutates any
production source table. No provider/RPC calls anywhere in this module.

Pipeline:
  1. Build the eligible direct-funding edge population (HIGH-confidence
     per the B2Z-P3B-calibrated policy: gap<=3600s, amount>=0.01 SOL, no
     documented extraction failure).
  2. Cluster by shared direct funder -> candidate families.
  3. Attach 2-hop upstream evidence where available (reusing the same
     query shape SecondHopExpansionBuilder uses, read-only).
  4. Classify each family by evidence quality (STRONG/PARTIAL/AMBIGUOUS/
     SERVICE_DISTRIBUTION/NOISE) using deterministic rules, no ML score.

CREATE_CREATOR and MIGRATION_SIGNER are kept as explicitly separate
feature roles throughout (migration-signer evidence is only available
for the 19-member B2Z-P2 cohort; this module documents that scope limit
rather than fabricating broader coverage).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SOURCE_DB = "database/flex_complete_database.db"
OUTPUT_DB = "database/local_operation_discovery_corpus.db"
MIN_UPSTREAM_SOL_LAMPORTS = 10_000_000  # matches src/core/second_hop_builder.py MIN_UPSTREAM_SOL
HIGH_CONFIDENCE_GAP_SECONDS = 3600  # B2Z-P1.6/P2/P3B-calibrated threshold

DISCOVERY_SCHEMA = """
CREATE TABLE IF NOT EXISTS discovery_run (
    run_id TEXT PRIMARY KEY,
    generated_utc TEXT NOT NULL,
    source_db_path TEXT NOT NULL,
    population_denominator INTEGER NOT NULL,
    high_confidence_edge_count INTEGER NOT NULL,
    manifest_digest TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS direct_funding_edges (
    run_id TEXT NOT NULL,
    mint TEXT NOT NULL,
    create_creator TEXT NOT NULL,
    direct_funder TEXT NOT NULL,
    funding_signature TEXT NOT NULL,
    amount_lamports INTEGER NOT NULL,
    funding_block_time INTEGER NOT NULL,
    migrated_at INTEGER NOT NULL,
    gap_seconds INTEGER NOT NULL,
    confidence TEXT NOT NULL,
    has_extraction_failure INTEGER NOT NULL,
    PRIMARY KEY (run_id, mint)
);

CREATE INDEX IF NOT EXISTS idx_dfe_funder ON direct_funding_edges(run_id, direct_funder);
CREATE INDEX IF NOT EXISTS idx_dfe_creator ON direct_funding_edges(run_id, create_creator);

CREATE TABLE IF NOT EXISTS upstream_edges (
    run_id TEXT NOT NULL,
    direct_funder TEXT NOT NULL,
    upstream_source TEXT NOT NULL,
    upstream_signature TEXT NOT NULL,
    upstream_amount_lamports INTEGER NOT NULL,
    upstream_block_time INTEGER NOT NULL,
    self_loop INTEGER NOT NULL,
    dust INTEGER NOT NULL,
    PRIMARY KEY (run_id, direct_funder, upstream_signature)
);

CREATE INDEX IF NOT EXISTS idx_ue_source ON upstream_edges(run_id, upstream_source);

CREATE TABLE IF NOT EXISTS candidate_families (
    run_id TEXT NOT NULL,
    family_id TEXT NOT NULL,
    family_kind TEXT NOT NULL,
    root_evidence TEXT NOT NULL,
    member_count INTEGER NOT NULL,
    creator_count INTEGER NOT NULL,
    classification TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    PRIMARY KEY (run_id, family_id)
);

CREATE INDEX IF NOT EXISTS idx_cf_classification ON candidate_families(run_id, classification);

CREATE TABLE IF NOT EXISTS composite_families (
    run_id TEXT NOT NULL,
    composite_id TEXT NOT NULL,
    direct_funder_root TEXT NOT NULL,
    upstream_root TEXT NOT NULL,
    shared_mint_count INTEGER NOT NULL,
    PRIMARY KEY (run_id, composite_id)
);

CREATE TABLE IF NOT EXISTS candidate_family_members (
    run_id TEXT NOT NULL,
    family_id TEXT NOT NULL,
    mint TEXT NOT NULL,
    create_creator TEXT NOT NULL,
    PRIMARY KEY (run_id, family_id, mint)
);
"""


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _sha256_json(obj: Any) -> str:
    return hashlib.sha256(_canonical(obj).encode()).hexdigest()


def _connect_source_ro(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)


def _connect_output(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(DISCOVERY_SCHEMA)
    return conn


@dataclass(frozen=True)
class DiscoveryStats:
    run_id: str
    population_denominator: int
    high_confidence_edge_count: int
    family_count: int
    strong_count: int
    partial_count: int
    ambiguous_count: int
    service_distribution_count: int
    noise_count: int
    composite_count: int


def build_high_confidence_direct_funding_edges(source: sqlite3.Connection, out: sqlite3.Connection, run_id: str) -> int:
    """Bounded, indexed, streaming query -- no fetchall of the full result
    set, cursor iteration only. Only HIGH-confidence edges (per the
    calibrated policy) are materialized; LOW-confidence relationships are
    deliberately excluded from this pass (Workstream B requirement)."""
    cursor = source.execute("""
        SELECT ta.mint, ta.pf_ws_creator, ti.source, ti.signature, ti.amount_lamports,
               ti.block_time, pmv.migrated_at,
               (pmv.migrated_at - ti.block_time) AS gap_seconds,
               EXISTS (
                   SELECT 1 FROM creator_funding_queue cfq
                   WHERE cfq.creator_address = ta.pf_ws_creator AND cfq.mint = ta.mint AND cfq.status = 'failed'
               ) AS has_extraction_failure
        FROM token_analysis ta
        JOIN pumpfun_migration_verification pmv ON ta.mint = pmv.mint
        JOIN transfer_index ti ON ti.destination = ta.pf_ws_creator
        WHERE ta.pf_ws_creator IS NOT NULL
          AND ti.source != ta.pf_ws_creator
          AND ti.block_time < pmv.migrated_at
          AND ti.amount_lamports >= ?
          AND (pmv.migrated_at - ti.block_time) <= ?
        ORDER BY ta.mint, ti.block_time DESC
    """, (MIN_UPSTREAM_SOL_LAMPORTS, HIGH_CONFIDENCE_GAP_SECONDS))

    seen_mints: set[str] = set()
    count = 0
    batch: list[tuple] = []
    for row in cursor:
        mint, creator, funder, sig, amount, block_time, migrated_at, gap, has_fail = row
        if mint in seen_mints:
            continue  # keep only the most-recent qualifying candidate per mint (deterministic tie-break via ORDER BY)
        seen_mints.add(mint)
        if has_fail:
            continue  # documented extraction failure -> not HIGH confidence, excluded from this pass
        batch.append((run_id, mint, creator, funder, sig, amount, block_time, migrated_at, gap, "HIGH", 0))
        count += 1
        if len(batch) >= 5000:
            out.executemany(
                "INSERT OR REPLACE INTO direct_funding_edges VALUES (?,?,?,?,?,?,?,?,?,?,?)", batch)
            out.commit()
            batch = []
    if batch:
        out.executemany("INSERT OR REPLACE INTO direct_funding_edges VALUES (?,?,?,?,?,?,?,?,?,?,?)", batch)
        out.commit()
    return count


def build_upstream_edges_for_funders(source: sqlite3.Connection, out: sqlite3.Connection, run_id: str) -> int:
    """2-hop: for each distinct direct_funder in this run, find its own
    upstream funding candidates (who funded the funder). Bounded to the
    exact funder set already materialized -- not an unbounded scan."""
    funders = [r[0] for r in out.execute(
        "SELECT DISTINCT direct_funder FROM direct_funding_edges WHERE run_id=?", (run_id,))]
    count = 0
    batch: list[tuple] = []
    for funder in funders:
        cursor = source.execute("""
            SELECT ti.source, ti.signature, ti.amount_lamports, ti.block_time
            FROM transfer_index ti
            WHERE ti.destination = ?
            ORDER BY ti.block_time DESC
            LIMIT 20
        """, (funder,))
        for upstream_source, sig, amount, block_time in cursor:
            self_loop = 1 if upstream_source == funder else 0
            dust = 1 if amount < MIN_UPSTREAM_SOL_LAMPORTS else 0
            batch.append((run_id, funder, upstream_source, sig, amount, block_time, self_loop, dust))
            count += 1
            if len(batch) >= 5000:
                out.executemany("INSERT OR REPLACE INTO upstream_edges VALUES (?,?,?,?,?,?,?,?)", batch)
                out.commit()
                batch = []
    if batch:
        out.executemany("INSERT OR REPLACE INTO upstream_edges VALUES (?,?,?,?,?,?,?,?)", batch)
        out.commit()
    return count


def _classify_family(member_count: int, creator_count: int, exact_amount_repeats: int,
                      max_single_creator_fanout: int, self_loop_upstream: bool,
                      mega_hub: bool) -> str:
    """Deterministic, interpretable rule-based classification -- no
    invented ML score, per Workstream H instruction."""
    if member_count < 2:
        return "NOISE_OR_INSUFFICIENT"
    if creator_count == 1:
        # single creator, multiple mints funded by the same wallet -- this
        # is a serial-deployer/self-funding pattern, not a multi-creator
        # operational family signal
        return "NOISE_OR_INSUFFICIENT"
    if mega_hub and creator_count > 50:
        return "SERVICE_DISTRIBUTION_CLUSTER"
    if self_loop_upstream:
        return "AMBIGUOUS_FUNDING_CLUSTER"
    if member_count >= 5 and creator_count >= 5 and exact_amount_repeats >= 2:
        return "STRONG_CANDIDATE_FAMILY"
    if member_count >= 3 and creator_count >= 3:
        return "PARTIAL_CANDIDATE_FAMILY"
    return "AMBIGUOUS_FUNDING_CLUSTER"


def build_direct_funder_families(out: sqlite3.Connection, run_id: str) -> dict[str, int]:
    """Cluster the materialized HIGH-confidence edges by shared direct
    funder. Runs entirely against the already-bounded output DB (never
    re-touches the source), so it is safe to use richer SQL here."""
    rows = out.execute("""
        SELECT direct_funder, COUNT(*) AS member_count, COUNT(DISTINCT create_creator) AS creator_count
        FROM direct_funding_edges WHERE run_id=?
        GROUP BY direct_funder
        HAVING member_count >= 2
        ORDER BY member_count DESC
    """, (run_id,)).fetchall()

    counts = {"STRONG_CANDIDATE_FAMILY": 0, "PARTIAL_CANDIDATE_FAMILY": 0,
              "AMBIGUOUS_FUNDING_CLUSTER": 0, "SERVICE_DISTRIBUTION_CLUSTER": 0, "NOISE_OR_INSUFFICIENT": 0}

    for funder, member_count, creator_count in rows:
        amounts = [r[0] for r in out.execute(
            "SELECT amount_lamports FROM direct_funding_edges WHERE run_id=? AND direct_funder=?",
            (run_id, funder))]
        exact_amount_repeats = max(amounts.count(a) for a in set(amounts)) if amounts else 0

        upstream_rows = out.execute(
            "SELECT self_loop FROM upstream_edges WHERE run_id=? AND direct_funder=?", (run_id, funder)).fetchall()
        self_loop_upstream = any(r[0] for r in upstream_rows)

        mega_hub = creator_count > 50

        classification = _classify_family(member_count, creator_count, exact_amount_repeats,
                                           creator_count, self_loop_upstream, mega_hub)
        counts[classification] += 1

        family_id = "DFF_" + hashlib.sha256(funder.encode()).hexdigest()[:16]
        members = out.execute(
            "SELECT mint, create_creator FROM direct_funding_edges WHERE run_id=? AND direct_funder=?",
            (run_id, funder)).fetchall()
        evidence = {
            "kind": "DIRECT_FUNDER_FAMILY",
            "direct_funder": funder,
            "member_count": member_count,
            "creator_count": creator_count,
            "exact_amount_repeats": exact_amount_repeats,
            "self_loop_upstream_detected": self_loop_upstream,
            "mega_hub": mega_hub,
        }
        out.execute("INSERT OR REPLACE INTO candidate_families VALUES (?,?,?,?,?,?,?,?)",
                    (run_id, family_id, "DIRECT_FUNDER", funder, member_count, creator_count,
                     classification, _canonical(evidence)))
        member_batch = [(run_id, family_id, mint, creator) for mint, creator in members]
        out.executemany("INSERT OR REPLACE INTO candidate_family_members VALUES (?,?,?,?)", member_batch)
    out.commit()
    return counts


def build_upstream_source_families(out: sqlite3.Connection, run_id: str) -> dict[str, int]:
    """Cluster by shared upstream source (2-hop) -- excludes self-loop and
    dust-only relationships per Workstream D/K."""
    rows = out.execute("""
        SELECT upstream_source, COUNT(DISTINCT direct_funder) AS funder_count
        FROM upstream_edges
        WHERE run_id=? AND self_loop=0 AND dust=0
        GROUP BY upstream_source
        HAVING funder_count >= 2
        ORDER BY funder_count DESC
    """, (run_id,)).fetchall()

    counts = {"STRONG_CANDIDATE_FAMILY": 0, "PARTIAL_CANDIDATE_FAMILY": 0,
              "AMBIGUOUS_FUNDING_CLUSTER": 0, "SERVICE_DISTRIBUTION_CLUSTER": 0, "NOISE_OR_INSUFFICIENT": 0}

    for upstream_source, funder_count in rows:
        funders = [r[0] for r in out.execute(
            "SELECT DISTINCT direct_funder FROM upstream_edges WHERE run_id=? AND upstream_source=? AND self_loop=0 AND dust=0",
            (run_id, upstream_source))]
        creator_set: set[str] = set()
        mint_creator_pairs: list[tuple[str, str]] = []
        for funder in funders:
            for mint, creator in out.execute(
                "SELECT mint, create_creator FROM direct_funding_edges WHERE run_id=? AND direct_funder=?",
                (run_id, funder)):
                creator_set.add(creator)
                mint_creator_pairs.append((mint, creator))
        creator_count = len(creator_set)
        member_count = len(mint_creator_pairs)
        if member_count == 0:
            continue

        mega_hub = funder_count > 50
        classification = _classify_family(member_count, creator_count, 0, funder_count, False, mega_hub)
        counts[classification] += 1

        family_id = "USF_" + hashlib.sha256(upstream_source.encode()).hexdigest()[:16]
        evidence = {
            "kind": "UPSTREAM_SOURCE_FAMILY",
            "upstream_source": upstream_source,
            "distinct_direct_funders": funder_count,
            "member_count": member_count,
            "creator_count": creator_count,
            "mega_hub": mega_hub,
        }
        out.execute("INSERT OR REPLACE INTO candidate_families VALUES (?,?,?,?,?,?,?,?)",
                    (run_id, family_id, "UPSTREAM_SOURCE", upstream_source, member_count, creator_count,
                     classification, _canonical(evidence)))
        member_batch = [(run_id, family_id, mint, creator) for mint, creator in mint_creator_pairs]
        out.executemany("INSERT OR REPLACE INTO candidate_family_members VALUES (?,?,?,?)", member_batch)
    out.commit()
    return counts


def build_composite_families(out: sqlite3.Connection, run_id: str, *, min_shared_mints: int = 2) -> int:
    """Workstream L: find mints that are members of BOTH a strong/partial
    DIRECT_FUNDER family AND a strong/partial UPSTREAM_SOURCE family --
    a composite structure where two independent signals agree, ranked
    above single-signal clusters without an opaque score (the ranking
    IS the shared_mint_count, an interpretable, deterministic quantity)."""
    from collections import Counter
    rows = out.execute("""
        SELECT m1.mint, f1.root_evidence, f2.root_evidence
        FROM candidate_family_members m1
        JOIN candidate_families f1 ON m1.family_id = f1.family_id AND f1.family_kind='DIRECT_FUNDER'
        JOIN candidate_family_members m2 ON m1.mint = m2.mint
        JOIN candidate_families f2 ON m2.family_id = f2.family_id AND f2.family_kind='UPSTREAM_SOURCE'
        WHERE f1.classification IN ('STRONG_CANDIDATE_FAMILY','PARTIAL_CANDIDATE_FAMILY')
          AND f2.classification IN ('STRONG_CANDIDATE_FAMILY','PARTIAL_CANDIDATE_FAMILY')
    """, ()).fetchall()
    pairs = Counter((r[1], r[2]) for r in rows)
    batch = []
    for (df_root, us_root), count in pairs.items():
        if count < min_shared_mints:
            continue
        composite_id = "COMPOSITE_" + hashlib.sha256(f"{df_root}|{us_root}".encode()).hexdigest()[:16]
        batch.append((run_id, composite_id, df_root, us_root, count))
    out.executemany("INSERT OR REPLACE INTO composite_families VALUES (?,?,?,?,?)", batch)
    out.commit()
    return len(batch)


def run_discovery(*, source_db_path: str = SOURCE_DB, output_db_path: str = OUTPUT_DB) -> DiscoveryStats:
    run_id = "local_discovery_" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    source = _connect_source_ro(source_db_path)
    out = _connect_output(output_db_path)
    try:
        denom = source.execute("""
            SELECT COUNT(*) FROM token_analysis ta
            JOIN pumpfun_migration_verification pmv ON ta.mint = pmv.mint
            WHERE ta.pf_ws_creator IS NOT NULL
        """).fetchone()[0]

        edge_count = build_high_confidence_direct_funding_edges(source, out, run_id)
        build_upstream_edges_for_funders(source, out, run_id)
        dff_counts = build_direct_funder_families(out, run_id)
        usf_counts = build_upstream_source_families(out, run_id)
        composite_count = build_composite_families(out, run_id)

        manifest = {"run_id": run_id, "population_denominator": denom, "high_confidence_edge_count": edge_count}
        out.execute("INSERT OR REPLACE INTO discovery_run VALUES (?,?,?,?,?,?)",
                    (run_id, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), source_db_path,
                     denom, edge_count, _sha256_json(manifest)))
        out.commit()

        strong = dff_counts["STRONG_CANDIDATE_FAMILY"] + usf_counts["STRONG_CANDIDATE_FAMILY"]
        partial = dff_counts["PARTIAL_CANDIDATE_FAMILY"] + usf_counts["PARTIAL_CANDIDATE_FAMILY"]
        ambiguous = dff_counts["AMBIGUOUS_FUNDING_CLUSTER"] + usf_counts["AMBIGUOUS_FUNDING_CLUSTER"]
        service = dff_counts["SERVICE_DISTRIBUTION_CLUSTER"] + usf_counts["SERVICE_DISTRIBUTION_CLUSTER"]
        noise = dff_counts["NOISE_OR_INSUFFICIENT"] + usf_counts["NOISE_OR_INSUFFICIENT"]
        total_families = strong + partial + ambiguous + service + noise

        return DiscoveryStats(
            run_id=run_id, population_denominator=denom, high_confidence_edge_count=edge_count,
            family_count=total_families, strong_count=strong, partial_count=partial,
            ambiguous_count=ambiguous, service_distribution_count=service, noise_count=noise,
            composite_count=composite_count,
        )
    finally:
        source.close()
        out.close()


if __name__ == "__main__":
    stats = run_discovery()
    print(json.dumps(stats.__dict__, indent=2))
