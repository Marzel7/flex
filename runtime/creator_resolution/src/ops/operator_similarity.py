"""
Operator Similarity Engine — Sprint X12.

Answers: "Which operators behave similarly, and why?"

Database-safety guarantees (non-negotiable):
  - Exactly 3 DB queries total per run, regardless of operator count.
  - DB connection is closed before any pairwise comparison begins.
  - All comparison is done in-memory from pre-loaded feature vectors.
  - Zero writes — similarity results are ephemeral this sprint.
  - Uses read-only SQLite URI (?mode=ro) on the ops DB only.
  - Never touches flex_complete_database.db (live detection DB).
  - Fails open: any error returns SimilaritySnapshot(available=False).
  - Never called from websocket handlers, detectors, or lifecycle adapters.

Architecture:
  OperatorSimilarityEngine.compute_snapshot()
      → single bulk DB read (3 queries, connection closed)
      → build FeatureVector per operator (in memory)
      → prune ineligible operators (insufficient confidence)
      → pairwise comparison (in memory, bounded)
      → return SimilaritySnapshot (top-N per operator)

  The snapshot is stored in-process. API handlers read it.
  A manual refresh or scheduled refresh populates it.
  Page loads never trigger recomputation.

Similarity bands (qualitative only — no raw scores in API/UI):
  VERY_HIGH  ≥ 0.85
  HIGH       ≥ 0.65
  MODERATE   ≥ 0.40
  LOW        ≥ 0.20
  (below 0.20 not retained)

Confidence rules (both operators must meet threshold per fact):
  Only facts with confidence in {LOW, MEDIUM, HIGH} are compared.
  If fewer than MIN_COMPARABLE_FACTS facts are comparable: INSUFFICIENT_EVIDENCE.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import statistics
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from src.ops.behaviour_engine import MIN_OBSERVATIONS, _confidence

log = logging.getLogger("operator_similarity")

# ── Tuning constants ──────────────────────────────────────────────────────────

MAX_OPERATORS         = 50     # skip run if more operators than this (safety valve)
MAX_RESULTS_PER_OP    = 5      # top-N per operator retained
MIN_COMPARABLE_FACTS  = 2      # minimum overlapping facts before comparison is meaningful
MIN_BAND_TO_RETAIN    = 0.20   # prune pairs scoring below this
REFRESH_INTERVAL_S    = 300    # minimum seconds between automatic refreshes
MIN_LAUNCHES_FOR_SIM  = MIN_OBSERVATIONS  # operator must have this many launches

_VALID_CONF = {"LOW", "MEDIUM", "HIGH"}


# ── Feature vector ────────────────────────────────────────────────────────────

@dataclass
class FeatureVector:
    """
    Numerical representation of one operator's behaviour, derived from raw
    evidence already loaded into memory.  No DB access after construction.
    """
    operator_id: str
    # Each entry: (fact_key, label, raw_value, confidence, observations, unit)
    facts: list[tuple[str, str, float, str, int, str]] = field(default_factory=list)
    # Categorical facts: (fact_key, label, value_str)
    cat_facts: list[tuple[str, str, str]] = field(default_factory=list)
    eligible: bool = True   # False if too few launches
    launch_count: int = 0

    def get(self, key: str) -> tuple[float, str, int] | None:
        """Return (raw, confidence, observations) for a numeric fact key, or None."""
        for fk, _, raw, conf, obs, _ in self.facts:
            if fk == key:
                return raw, conf, obs
        return None

    def get_cat(self, key: str) -> str | None:
        for fk, _, val in self.cat_facts:
            if fk == key:
                return val
        return None


# ── Fact comparator ───────────────────────────────────────────────────────────

def _score_numeric(
    a_raw: float, a_conf: str, a_obs: int,
    b_raw: float, b_conf: str, b_obs: int,
) -> tuple[float, str] | None:
    """
    Compare two numeric fact values.

    Returns (score 0–1, confidence) or None if either fact is insufficient.
    Score 1.0 = identical; score approaches 0 as relative difference grows.
    Uses a sigmoid-like decay: score = 1 / (1 + |rel_diff|).
    """
    if a_conf not in _VALID_CONF or b_conf not in _VALID_CONF:
        return None
    if a_raw is None or b_raw is None:
        return None
    denom = (abs(a_raw) + abs(b_raw)) / 2.0
    if denom == 0:
        return 1.0, min(a_conf, b_conf, key=lambda c: ["LOW","MEDIUM","HIGH"].index(c))
    rel = abs(a_raw - b_raw) / denom
    score = 1.0 / (1.0 + rel)
    conf_order = ["LOW", "MEDIUM", "HIGH"]
    conf = conf_order[min(conf_order.index(a_conf), conf_order.index(b_conf))]
    return score, conf


def _score_categorical(a_val: str, b_val: str) -> float:
    """1.0 if equal, 0.0 if different."""
    return 1.0 if a_val == b_val else 0.0


# ── Dimension comparators ─────────────────────────────────────────────────────

_DIMENSION_FACTS: dict[str, list[str]] = {
    "campaign": [
        "avg_creators_per_campaign",
        "avg_campaign_duration_h",
        "avg_campaign_spacing_h",
        "campaigns_per_day",
        "avg_subprovs_per_campaign",
    ],
    "funding": [
        "preferred_treasury_size",
        "preferred_creator_funding",
        "avg_fanout_count",
        "avg_fanout_sol",
        "wrap_close_usage",
    ],
    "launch": [
        "avg_launch_delay_s",
        "avg_fanout_to_create_s",
        "avg_migration_timing_s",
        "peak_launch_hour_utc",
    ],
    "operational": [
        "total_infra_wallets",
        "infra_reuse_pct",
    ],
    "outcome": [
        "migration_rate",
        "avg_actionable_multiple",
        "avg_peak_mc",
    ],
}

_CATEGORICAL_FACTS = {"wrap_close_usage_cat", "preferred_workflow"}

# Outcome dimension is context only — never drives the band on its own
_OUTCOME_WEIGHT = 0.5
_DIMENSION_WEIGHTS = {
    "campaign":    1.0,
    "funding":     1.0,
    "launch":      1.0,
    "operational": 0.8,
    "outcome":     _OUTCOME_WEIGHT,
}


# ── Comparison result ─────────────────────────────────────────────────────────

@dataclass
class FactSimilarity:
    key: str
    label: str
    score: float            # 0–1, internal only
    a_value: Any
    b_value: Any
    confidence: str

    def to_dict(self) -> dict:
        return {
            "key": self.key, "label": self.label,
            "a_value": self.a_value, "b_value": self.b_value,
            "confidence": self.confidence,
        }


@dataclass
class DimensionSimilarity:
    key: str
    label: str
    score: float | None         # None = insufficient
    confidence: str             # INSUFFICIENT | LOW | MEDIUM | HIGH
    facts_compared: list[FactSimilarity] = field(default_factory=list)
    facts_excluded: list[str]            = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "key":             self.key,
            "label":           self.label,
            "score":           round(self.score, 3) if self.score is not None else None,
            "confidence":      self.confidence,
            "facts_compared":  [f.to_dict() for f in self.facts_compared],
            "facts_excluded":  self.facts_excluded,
        }


@dataclass
class OperatorSimilarityResult:
    operator_a: str
    operator_b: str
    similarity_band: str        # VERY_HIGH | HIGH | MODERATE | LOW | INSUFFICIENT_EVIDENCE
    confidence: str
    dimensions_compared: list[DimensionSimilarity] = field(default_factory=list)
    dimensions_excluded: list[str]                  = field(default_factory=list)
    reasons: list[str]                              = field(default_factory=list)    # similarities
    differences: list[str]                          = field(default_factory=list)    # differences
    observations_a: int = 0
    observations_b: int = 0
    computed_at: int    = field(default_factory=lambda: int(time.time()))
    _internal_score: float = field(default=0.0, repr=False)

    def to_dict(self) -> dict:
        return {
            "operator_a":           self.operator_a,
            "operator_b":           self.operator_b,
            "similarity_band":      self.similarity_band,
            "confidence":           self.confidence,
            "dimensions_compared":  [d.to_dict() for d in self.dimensions_compared],
            "dimensions_excluded":  self.dimensions_excluded,
            "reasons":              self.reasons,
            "differences":          self.differences,
            "observations_a":       self.observations_a,
            "observations_b":       self.observations_b,
            "computed_at":          self.computed_at,
        }


# ── Platform snapshot ─────────────────────────────────────────────────────────

@dataclass
class SimilaritySnapshot:
    available: bool
    computed_at: int
    eligible_operators: int
    excluded_operators: int
    comparisons_attempted: int
    comparisons_pruned: int
    db_read_duration_ms: float
    compute_duration_ms: float
    error: str | None
    # operator_id → top-N results (sorted by score desc)
    results: dict[str, list[OperatorSimilarityResult]] = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)

    def for_operator(self, operator_id: str) -> list[OperatorSimilarityResult]:
        return self.results.get(operator_id, [])

    def to_summary_dict(self) -> dict:
        return {
            "available":             self.available,
            "computed_at":           self.computed_at,
            "eligible_operators":    self.eligible_operators,
            "excluded_operators":    self.excluded_operators,
            "comparisons_attempted": self.comparisons_attempted,
            "comparisons_pruned":    self.comparisons_pruned,
            "db_read_duration_ms":   round(self.db_read_duration_ms, 1),
            "compute_duration_ms":   round(self.compute_duration_ms, 1),
            "error":                 self.error,
            "metrics":               self.metrics,
        }


_EMPTY_SNAPSHOT = SimilaritySnapshot(
    available=False, computed_at=0,
    eligible_operators=0, excluded_operators=0,
    comparisons_attempted=0, comparisons_pruned=0,
    db_read_duration_ms=0.0, compute_duration_ms=0.0,
    error="No snapshot computed yet.",
)


# ── Feature builder ───────────────────────────────────────────────────────────

def _build_feature_vector(
    operator_id: str,
    treasuries: list[str],
    all_launches: list[dict],
    all_ops: list[dict],
    all_wallets: list[dict],
) -> FeatureVector:
    """
    Derive a FeatureVector from pre-loaded evidence rows.

    No DB access. Reuses the same aggregation logic as X10 dimension computers
    but operating on pre-filtered in-memory lists (no _OperatorData construction).
    """
    fv = FeatureVector(operator_id=operator_id)

    tw_set = set(treasuries)
    launches = [r for r in all_launches if r.get("treasury_wallet") in tw_set]
    ops      = [r for r in all_ops      if r.get("treasury_root") in tw_set]
    wallets  = [r for r in all_wallets  if r.get("treasury_root") in tw_set]

    fv.launch_count = len(launches)
    fv.eligible     = len(launches) >= MIN_LAUNCHES_FOR_SIM

    if not fv.eligible:
        return fv

    n_launches = len(launches)
    n_ops      = len(ops)

    # ── Campaign ──────────────────────────────────────────────────────────────
    if n_ops >= MIN_OBSERVATIONS:
        timestamps = sorted(o["first_seen"] for o in ops if o.get("first_seen"))
        if len(timestamps) >= 2:
            span_days = max((timestamps[-1] - timestamps[0]) / 86400, 1e-6)
            fv.facts.append((
                "campaigns_per_day", "Campaigns / Day",
                n_ops / span_days, _confidence(n_ops), n_ops, "/day",
            ))
            gaps_h = [(timestamps[i] - timestamps[i-1]) / 3600
                      for i in range(1, len(timestamps))]
            if gaps_h:
                fv.facts.append((
                    "avg_campaign_spacing_h", "Campaign Spacing",
                    statistics.mean(gaps_h), _confidence(len(gaps_h)), len(gaps_h), "h",
                ))

    # ── Funding ───────────────────────────────────────────────────────────────
    treasury_sizes = [r["subprov_funding_sol"] for r in launches if r.get("subprov_funding_sol")]
    if treasury_sizes:
        fv.facts.append((
            "preferred_treasury_size", "Treasury Size",
            statistics.mean(treasury_sizes), _confidence(len(treasury_sizes)),
            len(treasury_sizes), "SOL",
        ))

    wrap_soles = [r["wrap_close_sol"] for r in launches if r.get("wrap_close_sol")]
    if wrap_soles:
        fv.facts.append((
            "preferred_creator_funding", "Creator Funding",
            statistics.mean(wrap_soles), _confidence(len(wrap_soles)),
            len(wrap_soles), "SOL",
        ))

    wc_count = sum(1 for r in launches if r.get("funding_mechanism") == "WSOL_WRAP_CLOSE")
    fv.facts.append((
        "wrap_close_usage", "Wrap-Close Usage",
        wc_count / n_launches * 100, _confidence(n_launches), n_launches, "%",
    ))

    fanout_counts = [r["fanout_count"] for r in launches
                     if r.get("fanout_count") is not None]
    if fanout_counts:
        fv.facts.append((
            "avg_fanout_count", "Fan-Out Count",
            statistics.mean(fanout_counts), _confidence(len(fanout_counts)),
            len(fanout_counts), "",
        ))

    # ── Launch ────────────────────────────────────────────────────────────────
    delays = [r["birth_to_launch_seconds"] for r in launches
              if r.get("birth_to_launch_seconds") is not None]
    if delays:
        fv.facts.append((
            "avg_launch_delay_s", "Launch Delay",
            statistics.mean(delays), _confidence(len(delays)), len(delays), "s",
        ))

    f2c = [r["fanout_to_create_secs"] for r in launches
           if r.get("fanout_to_create_secs") is not None]
    if f2c:
        fv.facts.append((
            "avg_fanout_to_create_s", "Fan-Out-to-CREATE",
            statistics.mean(f2c), _confidence(len(f2c)), len(f2c), "s",
        ))

    hours = [time.gmtime(r["create_time"]).tm_hour
             for r in launches if r.get("create_time")]
    if len(hours) >= MIN_OBSERVATIONS:
        from collections import Counter
        peak = Counter(hours).most_common(1)[0][0]
        fv.facts.append((
            "peak_launch_hour_utc", "Peak Launch Hour",
            float(peak), _confidence(len(hours)), len(hours), "UTC",
        ))

    # ── Operational ───────────────────────────────────────────────────────────
    from collections import Counter as C
    wallet_op_count = C(w["wallet"] for w in wallets)
    total_wallets   = len(wallet_op_count)
    if total_wallets:
        fv.facts.append((
            "total_infra_wallets", "Infrastructure Wallets",
            float(total_wallets), _confidence(n_ops), n_ops, "",
        ))
        reused = sum(1 for v in wallet_op_count.values() if v > 1)
        fv.facts.append((
            "infra_reuse_pct", "Infrastructure Reuse",
            reused / total_wallets * 100, _confidence(n_ops), n_ops, "%",
        ))

    return fv


# ── Pairwise comparison ───────────────────────────────────────────────────────

def _band(score: float) -> str:
    if score >= 0.85: return "VERY_HIGH"
    if score >= 0.65: return "HIGH"
    if score >= 0.40: return "MODERATE"
    if score >= 0.20: return "LOW"
    return "LOW"  # retained but won't be below MIN_BAND_TO_RETAIN threshold


def _compare_pair(
    fv_a: FeatureVector,
    fv_b: FeatureVector,
) -> OperatorSimilarityResult | None:
    """
    Compare two FeatureVectors dimension by dimension.

    Returns None if fewer than MIN_COMPARABLE_FACTS facts can be compared.
    No DB access — pure in-memory computation.
    """
    a_by_key = {fk: (raw, conf, obs, unit)
                for fk, _, raw, conf, obs, unit in fv_a.facts}
    b_by_key = {fk: (raw, conf, obs, unit)
                for fk, _, raw, conf, obs, unit in fv_b.facts}

    dim_sims: list[DimensionSimilarity] = []
    excluded_dims: list[str]            = []
    weighted_scores: list[tuple[float, float]] = []  # (score, weight)
    reasons:     list[str] = []
    differences: list[str] = []

    all_compared = 0

    for dim_key, fact_keys in _DIMENSION_FACTS.items():
        dim_label = dim_key.replace("_", " ").title() + " Behaviour"
        compared:  list[FactSimilarity] = []
        excl_keys: list[str]            = []

        for fk in fact_keys:
            a_entry = a_by_key.get(fk)
            b_entry = b_by_key.get(fk)
            if a_entry is None or b_entry is None:
                if a_entry or b_entry:
                    excl_keys.append(fk)
                continue

            a_raw, a_conf, a_obs, a_unit = a_entry
            b_raw, b_conf, b_obs, b_unit = b_entry

            result = _score_numeric(a_raw, a_conf, a_obs, b_raw, b_conf, b_obs)
            if result is None:
                excl_keys.append(fk)
                continue

            score, conf = result
            label = next((lbl for k, lbl, *_ in fv_a.facts if k == fk), fk)
            compared.append(FactSimilarity(
                key=fk, label=label, score=score,
                a_value=_fmt(a_raw, a_unit), b_value=_fmt(b_raw, a_unit),
                confidence=conf,
            ))

        if len(compared) < 1:
            excluded_dims.append(dim_label)
            continue

        dim_score = statistics.mean(fs.score for fs in compared)
        conf_vals = [fs.confidence for fs in compared]
        conf_order = ["LOW", "MEDIUM", "HIGH"]
        dim_conf = conf_order[min(conf_order.index(c) for c in conf_vals if c in conf_order)]

        weight = _DIMENSION_WEIGHTS.get(dim_key, 1.0)
        weighted_scores.append((dim_score, weight))

        dim_sims.append(DimensionSimilarity(
            key=dim_key, label=dim_label, score=dim_score,
            confidence=dim_conf, facts_compared=compared, facts_excluded=excl_keys,
        ))

        all_compared += len(compared)

        # Narrative reasons / differences
        _annotate(dim_key, compared, reasons, differences)

    if all_compared < MIN_COMPARABLE_FACTS:
        return OperatorSimilarityResult(
            operator_a=fv_a.operator_id, operator_b=fv_b.operator_id,
            similarity_band="INSUFFICIENT_EVIDENCE", confidence="INSUFFICIENT",
            dimensions_excluded=excluded_dims,
            observations_a=fv_a.launch_count, observations_b=fv_b.launch_count,
        )

    if not weighted_scores:
        return None

    total_weight = sum(w for _, w in weighted_scores)
    overall      = sum(s * w for s, w in weighted_scores) / total_weight

    if overall < MIN_BAND_TO_RETAIN:
        return None

    # Overall confidence = minimum dimension confidence (conservative)
    all_confs = [d.confidence for d in dim_sims if d.confidence in conf_order]
    conf_order = ["LOW", "MEDIUM", "HIGH"]
    overall_conf = conf_order[min(conf_order.index(c) for c in all_confs)] if all_confs else "LOW"

    return OperatorSimilarityResult(
        operator_a=fv_a.operator_id, operator_b=fv_b.operator_id,
        similarity_band=_band(overall),
        confidence=overall_conf,
        dimensions_compared=dim_sims,
        dimensions_excluded=excluded_dims,
        reasons=reasons[:5],
        differences=differences[:3],
        observations_a=fv_a.launch_count,
        observations_b=fv_b.launch_count,
        _internal_score=overall,
    )


def _fmt(raw: float, unit: str) -> str:
    if unit == "SOL":
        return f"{raw:.3f} SOL"
    if unit == "%":
        return f"{raw:.0f}%"
    if unit == "s":
        return f"{raw:.0f}s"
    if unit == "h":
        return f"{raw:.1f}h"
    if unit == "UTC":
        h = int(raw)
        return f"{h:02d}:00 UTC"
    return f"{raw:.2f}" if raw != int(raw) else str(int(raw))


def _annotate(
    dim_key: str,
    compared: list[FactSimilarity],
    reasons: list[str],
    differences: list[str],
) -> None:
    """
    Produce plain-English similarity and difference notes.

    Never implies common ownership or intent.
    """
    for fs in compared:
        if fs.score >= 0.85:
            reasons.append(
                f"Both operators show similar {fs.label.lower()}: "
                f"{fs.a_value} vs {fs.b_value}."
            )
        elif fs.score < 0.40:
            differences.append(
                f"{fs.label}: operator A shows {fs.a_value}, "
                f"operator B shows {fs.b_value}."
            )


# ── Main engine ───────────────────────────────────────────────────────────────

class OperatorSimilarityEngine:
    """
    Loads all operator evidence in a single bounded DB pass, closes the
    connection, then computes all pairwise similarities in memory.

    The snapshot is stored in-process. API routes read from it.
    Page loads never trigger recomputation.

    DB-safety guarantee: exactly 3 queries per run. No writes.
    """

    def __init__(self, ops_db: str) -> None:
        self._ops_db = ops_db
        self._snapshot: SimilaritySnapshot = _EMPTY_SNAPSHOT
        self._snapshot_lock = threading.Lock()
        self._last_refresh  = 0.0

    def current_snapshot(self) -> SimilaritySnapshot:
        with self._snapshot_lock:
            return self._snapshot

    def compute_snapshot(self) -> SimilaritySnapshot:
        """
        Full similarity run. Safe to call from any background thread.
        Never call from request handlers, websocket handlers, or detection loops.

        DB pattern:
          1. Open read-only connection to ops DB.
          2. Load operator entities, launches, ops, wallets (3 bulk queries).
          3. Close connection immediately.
          4. Build feature vectors in memory.
          5. Prune ineligible operators.
          6. Compare pairs in memory.
          7. Store snapshot.
        """
        t_start = time.monotonic()
        metrics: dict = {
            "eligible_operators": 0,
            "excluded_operators": 0,
            "comparisons_attempted": 0,
            "comparisons_pruned": 0,
            "lock_errors": 0,
        }

        try:
            # ── PHASE 1: bulk DB read (connection lifetime = this block) ──────
            t_db = time.monotonic()
            operator_entities: list[dict] = []
            all_launches:      list[dict] = []
            all_ops:           list[dict] = []
            all_wallets:       list[dict] = []

            try:
                conn = sqlite3.connect(
                    f"file:{self._ops_db}?mode=ro", uri=True,
                    timeout=5, check_same_thread=False,
                )
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA query_only=ON")

                def _tbl(name: str) -> bool:
                    return bool(conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
                    ).fetchone())

                if _tbl("operator_entities"):
                    operator_entities = [
                        dict(r) for r in conn.execute(
                            "SELECT operator_id, entity_address, entity_type "
                            "FROM operator_entities"
                        ).fetchall()
                    ]

                if _tbl("wt_watchtower_launches"):
                    all_launches = [
                        dict(r) for r in conn.execute(
                            "SELECT treasury_wallet, subprov_wallet, create_time, "
                            "birth_to_launch_seconds, subprov_funding_sol, wrap_close_sol, "
                            "fanout_count, fanout_to_create_secs, funding_mechanism "
                            "FROM wt_watchtower_launches"
                        ).fetchall()
                    ]

                if _tbl("wt_ops_v2"):
                    all_ops = [
                        dict(r) for r in conn.execute(
                            "SELECT operation_uuid, treasury_root, first_seen, last_seen "
                            "FROM wt_ops_v2"
                        ).fetchall()
                    ]

                if _tbl("wt_ops_v2_wallets"):
                    all_wallets = [
                        dict(r) for r in conn.execute(
                            """
                            SELECT w.wallet, w.role, w.first_seen,
                                   o.treasury_root
                            FROM wt_ops_v2_wallets w
                            JOIN wt_ops_v2 o ON w.operation_uuid = o.operation_uuid
                            """
                        ).fetchall()
                    ]

            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower():
                    metrics["lock_errors"] += 1
                return self._fail(f"DB read error: {exc}", t_start, 0.0, metrics)
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

            # Connection is now closed. All remaining work is in-memory.
            db_ms = (time.monotonic() - t_db) * 1000

            # ── PHASE 2: group entities by operator ───────────────────────────
            op_treasuries: dict[str, list[str]] = {}
            for row in operator_entities:
                if row.get("entity_type") in ("TREASURY", "SUB_PROVISIONER", "UNKNOWN"):
                    op_treasuries.setdefault(row["operator_id"], []).append(
                        row["entity_address"]
                    )

            if not op_treasuries:
                # No operators yet — not an error
                snap = SimilaritySnapshot(
                    available=True, computed_at=int(time.time()),
                    eligible_operators=0, excluded_operators=0,
                    comparisons_attempted=0, comparisons_pruned=0,
                    db_read_duration_ms=db_ms,
                    compute_duration_ms=0.0, error=None,
                    results={}, metrics=metrics,
                )
                self._store(snap)
                return snap

            # ── PHASE 3: build feature vectors ───────────────────────────────
            t_compute = time.monotonic()
            vectors: list[FeatureVector] = []

            for op_id, treasuries in list(op_treasuries.items())[:MAX_OPERATORS]:
                fv = _build_feature_vector(
                    op_id, treasuries, all_launches, all_ops, all_wallets
                )
                vectors.append(fv)

            eligible   = [fv for fv in vectors if fv.eligible]
            ineligible = [fv for fv in vectors if not fv.eligible]
            metrics["eligible_operators"]  = len(eligible)
            metrics["excluded_operators"]  = len(ineligible)

            # ── PHASE 4: pairwise comparison ──────────────────────────────────
            per_op: dict[str, list[OperatorSimilarityResult]] = {}

            for i in range(len(eligible)):
                for j in range(i + 1, len(eligible)):
                    fv_a = eligible[i]
                    fv_b = eligible[j]
                    metrics["comparisons_attempted"] += 1

                    result = _compare_pair(fv_a, fv_b)
                    if result is None or result._internal_score < MIN_BAND_TO_RETAIN:
                        metrics["comparisons_pruned"] += 1
                        continue

                    per_op.setdefault(fv_a.operator_id, []).append(result)
                    # Symmetric: also store from B's perspective
                    sym = OperatorSimilarityResult(
                        operator_a=fv_b.operator_id, operator_b=fv_a.operator_id,
                        similarity_band=result.similarity_band,
                        confidence=result.confidence,
                        dimensions_compared=result.dimensions_compared,
                        dimensions_excluded=result.dimensions_excluded,
                        reasons=result.reasons, differences=result.differences,
                        observations_a=result.observations_b,
                        observations_b=result.observations_a,
                        computed_at=result.computed_at,
                        _internal_score=result._internal_score,
                    )
                    per_op.setdefault(fv_b.operator_id, []).append(sym)

            # Keep top-N per operator
            results: dict[str, list[OperatorSimilarityResult]] = {
                op_id: sorted(lst, key=lambda r: r._internal_score, reverse=True)[:MAX_RESULTS_PER_OP]
                for op_id, lst in per_op.items()
            }

            compute_ms = (time.monotonic() - t_compute) * 1000

            snap = SimilaritySnapshot(
                available=True, computed_at=int(time.time()),
                eligible_operators=len(eligible),
                excluded_operators=len(ineligible),
                comparisons_attempted=metrics["comparisons_attempted"],
                comparisons_pruned=metrics["comparisons_pruned"],
                db_read_duration_ms=db_ms,
                compute_duration_ms=compute_ms,
                error=None, results=results, metrics=metrics,
            )
            log.info(
                "[SIMILARITY] computed: %d eligible, %d pairs, %.1fms DB + %.1fms compute",
                len(eligible), metrics["comparisons_attempted"], db_ms, compute_ms,
            )
            self._store(snap)
            return snap

        except Exception as exc:
            log.exception("[SIMILARITY] unexpected error")
            return self._fail(str(exc), t_start, 0.0, metrics)

    def _store(self, snap: SimilaritySnapshot) -> None:
        with self._snapshot_lock:
            self._snapshot = snap
            self._last_refresh = time.monotonic()

    def _fail(
        self, reason: str, t_start: float, db_ms: float, metrics: dict
    ) -> SimilaritySnapshot:
        snap = SimilaritySnapshot(
            available=False, computed_at=int(time.time()),
            eligible_operators=0, excluded_operators=0,
            comparisons_attempted=0, comparisons_pruned=0,
            db_read_duration_ms=db_ms,
            compute_duration_ms=(time.monotonic() - t_start) * 1000,
            error=reason, results={}, metrics=metrics,
        )
        log.warning("[SIMILARITY] failed: %s", reason)
        # Fail open: store so repeated calls get the cached failure rather than re-erroring
        self._store(snap)
        return snap

    def compare_pair(
        self,
        operator_id_a: str,
        operator_id_b: str,
    ) -> OperatorSimilarityResult | None:
        """
        Return the pre-computed result for a specific pair, if available.

        Does NOT trigger a new computation — reads from the snapshot only.
        """
        snap = self.current_snapshot()
        for r in snap.for_operator(operator_id_a):
            if r.operator_b == operator_id_b:
                return r
        return None

    def compute_for_operator(self, operator_id: str) -> SimilaritySnapshot:
        """Refresh only relationships involving one newly-promoted operator.

        This is the bounded X16C activation path.  It does not recompute pairs
        among existing operators and performs no writes.
        """
        started = time.monotonic()
        try:
            conn = sqlite3.connect(f"file:{self._ops_db}?mode=ro", uri=True, timeout=5)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            try:
                entities = [dict(r) for r in conn.execute(
                    "SELECT operator_id,entity_address,entity_type FROM operator_entities"
                ).fetchall()]
                tables = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()}
                launches = [dict(r) for r in conn.execute(
                    "SELECT treasury_wallet,subprov_wallet,create_time,birth_to_launch_seconds,"
                    "subprov_funding_sol,wrap_close_sol,fanout_count,fanout_to_create_secs,funding_mechanism "
                    "FROM wt_watchtower_launches"
                ).fetchall()] if "wt_watchtower_launches" in tables else []
                operations = [dict(r) for r in conn.execute(
                    "SELECT operation_uuid,treasury_root,first_seen,last_seen FROM wt_ops_v2"
                ).fetchall()] if "wt_ops_v2" in tables else []
                wallets = [dict(r) for r in conn.execute(
                    "SELECT w.wallet,w.role,w.first_seen,o.treasury_root FROM wt_ops_v2_wallets w "
                    "JOIN wt_ops_v2 o ON w.operation_uuid=o.operation_uuid"
                ).fetchall()] if {"wt_ops_v2", "wt_ops_v2_wallets"} <= tables else []
            finally:
                conn.close()

            grouped: dict[str, list[str]] = {}
            for row in entities:
                if row["entity_type"] in ("TREASURY", "SUB_PROVISIONER", "UNKNOWN"):
                    grouped.setdefault(row["operator_id"], []).append(row["entity_address"])
            grouped = dict(list(grouped.items())[:MAX_OPERATORS])
            vectors = {op_id: _build_feature_vector(op_id, treasuries, launches, operations, wallets)
                       for op_id, treasuries in grouped.items()}
            target = vectors.get(operator_id)
            results = {key: list(value) for key, value in self.current_snapshot().results.items()}
            # Remove only stale relationships involving the promoted operator.
            results.pop(operator_id, None)
            for key in list(results):
                results[key] = [r for r in results[key] if r.operator_b != operator_id]
            attempted = pruned = 0
            if target and target.eligible:
                for other_id, other in vectors.items():
                    if other_id == operator_id or not other.eligible:
                        continue
                    attempted += 1
                    result = _compare_pair(target, other)
                    if result is None or result._internal_score < MIN_BAND_TO_RETAIN:
                        pruned += 1
                        continue
                    results.setdefault(operator_id, []).append(result)
                    results.setdefault(other_id, []).append(OperatorSimilarityResult(
                        operator_a=other_id, operator_b=operator_id,
                        similarity_band=result.similarity_band, confidence=result.confidence,
                        dimensions_compared=result.dimensions_compared,
                        dimensions_excluded=result.dimensions_excluded,
                        reasons=result.reasons, differences=result.differences,
                        observations_a=result.observations_b, observations_b=result.observations_a,
                        computed_at=result.computed_at, _internal_score=result._internal_score,
                    ))
            for key in results:
                results[key] = sorted(results[key], key=lambda r: r._internal_score,
                                      reverse=True)[:MAX_RESULTS_PER_OP]
            eligible = sum(v.eligible for v in vectors.values())
            snapshot = SimilaritySnapshot(
                available=True, computed_at=int(time.time()), eligible_operators=eligible,
                excluded_operators=len(vectors)-eligible, comparisons_attempted=attempted,
                comparisons_pruned=pruned, db_read_duration_ms=0,
                compute_duration_ms=(time.monotonic()-started)*1000, error=None,
                results=results, metrics={"activation_operator_id": operator_id,
                                          "comparisons_attempted": attempted},
            )
            self._store(snapshot)
            return snapshot
        except Exception as exc:
            return self._fail(f"Targeted activation failed: {exc}", started, 0, {})
