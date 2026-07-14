"""
Behaviour Change Detection Engine — Sprint X11.

Answers: "Is this operator behaving differently from its own historical baseline?"

Architecture:
  BehaviourChangeEngine.compare(operator_id)
      → loads all evidence via _OperatorData (reused from X10)
      → splits evidence into baseline window vs. current window
      → derives a BehaviourProfile for each window (reuses X10 dimension computers)
      → compares fact-by-fact, producing FactComparison objects
      → groups into DimensionChange objects
      → returns BehaviourChangeReport

Windowing strategy:
  - Baseline window: all observations OLDER than CURRENT_WINDOW_DAYS
  - Current window:  observations within the last CURRENT_WINDOW_DAYS
  - If either window has < MIN_OBSERVATIONS the comparison for that dimension
    returns confidence=INSUFFICIENT and deviation=UNKNOWN

Deviation classification (qualitative only — no raw scores exposed):
  NONE        |Δ| / baseline ≤ 5%
  LOW         |Δ| / baseline ≤ 20%
  MODERATE    |Δ| / baseline ≤ 50%
  HIGH        |Δ| / baseline ≤ 100%
  VERY_HIGH   |Δ| / baseline  > 100%

  Categorical facts (strings) use exact-match: equal → NONE, else HIGH.

The comparison layer is strictly read-only over X10 structures.
It never calls any RPC endpoint.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from src.ops.behaviour_engine import (
    MIN_OBSERVATIONS,
    BehaviourDimension,
    BehaviourFact,
    BehaviourProfile,
    BehaviourTimelineEntry,
    _OperatorData,
    _campaign_behaviour,
    _confidence,
    _dimension_data,
    _funding_behaviour,
    _launch_behaviour,
    _operational_behaviour,
    _outcome_behaviour,
    _stability,
)

# How many days of observations constitute the "current" window.
CURRENT_WINDOW_DAYS = 7

# Deviation thresholds (relative change vs baseline)
_DEV_THRESHOLDS = [
    (0.05,  "NONE"),
    (0.20,  "LOW"),
    (0.50,  "MODERATE"),
    (1.00,  "HIGH"),
]

# Confidence requirement: both sides must meet this to produce a meaningful comparison
_MIN_COMPARISON_CONFIDENCE = {"LOW", "MEDIUM", "HIGH"}


# ── Deviation ─────────────────────────────────────────────────────────────────

def _deviation(baseline_raw: Any, current_raw: Any) -> str:
    """
    Classify the magnitude of change from baseline to current.

    Numeric: relative change = |current - baseline| / |baseline|
    Categorical (str): NONE if equal, HIGH otherwise.
    Returns UNKNOWN when values are missing or incomparable.
    """
    if baseline_raw is None or current_raw is None:
        return "UNKNOWN"

    # Categorical comparison
    if isinstance(baseline_raw, str) and isinstance(current_raw, str):
        return "NONE" if baseline_raw == current_raw else "HIGH"

    try:
        b = float(baseline_raw)
        c = float(current_raw)
    except (TypeError, ValueError):
        return "UNKNOWN"

    if b == 0:
        # Baseline zero: current nonzero is always HIGH change
        return "NONE" if c == 0 else "VERY_HIGH"

    rel = abs(c - b) / abs(b)
    for threshold, label in _DEV_THRESHOLDS:
        if rel <= threshold:
            return label
    return "VERY_HIGH"


def _comparison_confidence(baseline_fact: BehaviourFact, current_fact: BehaviourFact) -> str:
    """
    Return the confidence level for a fact comparison.

    Both sides must have non-INSUFFICIENT confidence. Otherwise INSUFFICIENT.
    The result is the lesser of the two confidence levels.
    """
    order = ["INSUFFICIENT", "LOW", "MEDIUM", "HIGH"]
    if baseline_fact.confidence not in _MIN_COMPARISON_CONFIDENCE:
        return "INSUFFICIENT"
    if current_fact.confidence not in _MIN_COMPARISON_CONFIDENCE:
        return "INSUFFICIENT"
    b_idx = order.index(baseline_fact.confidence)
    c_idx = order.index(current_fact.confidence)
    return order[min(b_idx, c_idx)]


# ── Fact comparison ───────────────────────────────────────────────────────────

@dataclass
class FactComparison:
    key: str
    label: str
    historical_value: Any          # display value from baseline
    historical_raw: Any
    historical_observations: int
    current_value: Any             # display value from current window
    current_raw: Any
    current_observations: int
    deviation: str                 # NONE|LOW|MODERATE|HIGH|VERY_HIGH|UNKNOWN
    confidence: str                # INSUFFICIENT|LOW|MEDIUM|HIGH
    reason: str                    # human-readable explanation
    unit: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def changed(self) -> bool:
        return self.deviation not in ("NONE", "UNKNOWN") and self.confidence != "INSUFFICIENT"


# ── Dimension change ──────────────────────────────────────────────────────────

@dataclass
class DimensionChange:
    key: str
    label: str
    drift: str          # STABLE | CHANGED | INSUFFICIENT_EVIDENCE
    overall_confidence: str
    summary: str
    comparisons: list[FactComparison] = field(default_factory=list)

    @property
    def changed_facts(self) -> list[FactComparison]:
        return [c for c in self.comparisons if c.changed]

    @property
    def unchanged_facts(self) -> list[FactComparison]:
        return [c for c in self.comparisons if not c.changed and c.deviation != "UNKNOWN"]

    def to_dict(self) -> dict:
        return {
            "key":                self.key,
            "label":              self.label,
            "drift":              self.drift,
            "overall_confidence": self.overall_confidence,
            "summary":            self.summary,
            "comparisons":        [c.to_dict() for c in self.comparisons],
            "changed_facts":      [c.to_dict() for c in self.changed_facts],
            "unchanged_facts":    [c.to_dict() for c in self.unchanged_facts],
        }


# ── Behaviour change report ───────────────────────────────────────────────────

@dataclass
class BehaviourChangeReport:
    operator_id: str
    computed_at: int
    baseline_window_days: int          # total history before current window
    current_window_days: int           # CURRENT_WINDOW_DAYS
    baseline_observations: int
    current_observations: int
    overall_drift: str                 # STABLE|CHANGED|MIXED|INSUFFICIENT_EVIDENCE
    drift_summary: dict[str, str]      # {dimension_label: drift}
    dimension_changes: list[DimensionChange] = field(default_factory=list)
    timeline_events: list[BehaviourTimelineEntry] = field(default_factory=list)
    evidence_gap_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "operator_id":           self.operator_id,
            "computed_at":           self.computed_at,
            "baseline_window_days":  self.baseline_window_days,
            "current_window_days":   self.current_window_days,
            "baseline_observations": self.baseline_observations,
            "current_observations":  self.current_observations,
            "overall_drift":         self.overall_drift,
            "drift_summary":         self.drift_summary,
            "dimension_changes":     [d.to_dict() for d in self.dimension_changes],
            "timeline_events":       [e.to_dict() for e in self.timeline_events],
            "evidence_gap_notes":    self.evidence_gap_notes,
            "fingerprint":           self.fingerprint,
        }

    @property
    def fingerprint(self) -> str:
        """Deterministic content hash, excluding computed_at."""
        import hashlib
        import json
        payload = {
            "operator_id":           self.operator_id,
            "baseline_observations": self.baseline_observations,
            "current_observations":  self.current_observations,
            "overall_drift":         self.overall_drift,
            "drift_summary":         self.drift_summary,
            "dimension_changes":     [d.to_dict() for d in self.dimension_changes],
        }
        blob = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ── Data splitter ─────────────────────────────────────────────────────────────

def _split_data(data: _OperatorData, cutoff_ts: int) -> tuple[_OperatorData, _OperatorData]:
    """
    Split a loaded _OperatorData into (baseline_data, current_data).

    baseline: everything with primary timestamp < cutoff_ts
    current:  everything with primary timestamp >= cutoff_ts

    Returns two _OperatorData objects with pre-populated lists (no DB calls).
    """
    if getattr(data, "observations", []):
        return (
            _OperatorData.from_observations([
                observation for observation in data.observations
                if observation.timestamp < cutoff_ts
            ]),
            _OperatorData.from_observations([
                observation for observation in data.observations
                if observation.timestamp >= cutoff_ts
            ]),
        )

    baseline = _empty_data_copy(data)
    current  = _empty_data_copy(data)

    for launch in data.launches:
        ts = launch.get("create_time") or 0
        if ts < cutoff_ts:
            baseline.launches.append(launch)
        else:
            current.launches.append(launch)

    for audit_row in data.audit:
        ts = audit_row.get("create_time") or 0
        if ts < cutoff_ts:
            baseline.audit.append(audit_row)
        else:
            current.audit.append(audit_row)

    for creator in data.creators:
        ts = creator.get("migration_time") or creator.get("op_first_seen") or 0
        if ts < cutoff_ts:
            baseline.creators.append(creator)
        else:
            current.creators.append(creator)

    for fanout in data.fanouts:
        ts = fanout.get("fanout_time") or 0
        if ts < cutoff_ts:
            baseline.fanouts.append(fanout)
        else:
            current.fanouts.append(fanout)

    for wallet in data.wallets:
        ts = wallet.get("first_seen") or 0
        if ts < cutoff_ts:
            baseline.wallets.append(wallet)
        else:
            current.wallets.append(wallet)

    for op in data.ops:
        ts = op.get("first_seen") or 0
        if ts < cutoff_ts:
            baseline.ops.append(op)
        else:
            current.ops.append(op)

    return baseline, current


def _empty_data_copy(source: _OperatorData) -> _OperatorData:
    """Return an _OperatorData shell with the same DB paths but empty lists."""
    obj = object.__new__(_OperatorData)
    obj.treasury_wallets = source.treasury_wallets
    obj._ops_db          = source._ops_db
    obj._live_db         = source._live_db
    obj.launches  = []
    obj.audit     = []
    obj.creators  = []
    obj.fanouts   = []
    obj.wallets   = []
    obj.ops       = []
    obj.observations = []
    return obj


# ── Fact comparator ───────────────────────────────────────────────────────────

def _compare_dimension(
    baseline_dim: BehaviourDimension,
    current_dim: BehaviourDimension,
) -> DimensionChange:
    """
    Compare one baseline dimension against the same current dimension.

    Produces a FactComparison for every fact key found in either side.
    """
    baseline_by_key = {f.key: f for f in baseline_dim.facts}
    current_by_key  = {f.key: f for f in current_dim.facts}
    all_keys = list(dict.fromkeys(
        list(baseline_by_key) + list(current_by_key)
    ))  # stable-ordered union

    comparisons: list[FactComparison] = []

    for key in all_keys:
        bf = baseline_by_key.get(key)
        cf = current_by_key.get(key)

        # Fact exists only in one window
        if bf is None or cf is None:
            present = bf or cf
            comparisons.append(FactComparison(
                key=key,
                label=present.label if present else key,
                historical_value=bf.value if bf else None,
                historical_raw=bf.raw if bf else None,
                historical_observations=bf.observations if bf else 0,
                current_value=cf.value if cf else None,
                current_raw=cf.raw if cf else None,
                current_observations=cf.observations if cf else 0,
                deviation="UNKNOWN",
                confidence="INSUFFICIENT",
                reason="Fact not present in one of the windows.",
                unit=present.unit if present else "",
            ))
            continue

        dev  = _deviation(bf.raw, cf.raw)
        conf = _comparison_confidence(bf, cf)

        reason = _build_reason(bf, cf, dev, conf)

        comparisons.append(FactComparison(
            key=key,
            label=bf.label,
            historical_value=bf.value,
            historical_raw=bf.raw,
            historical_observations=bf.observations,
            current_value=cf.value,
            current_raw=cf.raw,
            current_observations=cf.observations,
            deviation=dev,
            confidence=conf,
            reason=reason,
            unit=bf.unit,
        ))

    # Determine dimension-level drift
    meaningful = [c for c in comparisons if c.confidence != "INSUFFICIENT"]
    changed    = [c for c in meaningful if c.changed]

    if not meaningful:
        drift      = "INSUFFICIENT_EVIDENCE"
        dim_conf   = "INSUFFICIENT"
        summary    = "Insufficient observations in one or both windows for comparison."
    elif changed:
        drift    = "CHANGED"
        dim_conf = min(
            (c.confidence for c in changed),
            key=lambda x: ["INSUFFICIENT","LOW","MEDIUM","HIGH"].index(x)
        )
        summary  = (
            f"{len(changed)} fact(s) changed, {len(meaningful)-len(changed)} stable. "
            f"Changed: {', '.join(c.label for c in changed[:3])}"
            + ("…" if len(changed) > 3 else ".")
        )
    else:
        drift    = "STABLE"
        dim_conf = baseline_dim.overall_confidence
        summary  = f"All {len(meaningful)} measured facts within baseline range."

    return DimensionChange(
        key=baseline_dim.key,
        label=baseline_dim.label,
        drift=drift,
        overall_confidence=dim_conf,
        summary=summary,
        comparisons=comparisons,
    )


def _build_reason(bf: BehaviourFact, cf: BehaviourFact, dev: str, conf: str) -> str:
    """
    Produce a plain-English explanation of a fact comparison.

    Never states intent. Never infers risk. Reports evidence only.
    """
    if conf == "INSUFFICIENT":
        b_short = f"{bf.observations} historical obs ({bf.confidence})"
        c_short = f"{cf.observations} current obs ({cf.confidence})"
        return f"Comparison requires sufficient evidence on both sides. {b_short} vs {c_short}."

    if dev == "NONE":
        return (
            f"Current value ({cf.value}) is within 5% of historical baseline ({bf.value}). "
            f"Based on {bf.observations} historical and {cf.observations} current observations."
        )

    if dev == "UNKNOWN":
        return "One or both values are unavailable for comparison."

    # Compute direction for numeric facts
    direction = ""
    try:
        b_num = float(bf.raw) if bf.raw is not None else None
        c_num = float(cf.raw) if cf.raw is not None else None
        if b_num is not None and c_num is not None and b_num != 0:
            pct = (c_num - b_num) / abs(b_num) * 100
            direction = f" ({'+' if pct >= 0 else ''}{pct:.0f}%)"
    except (TypeError, ValueError):
        pass

    return (
        f"Current value{direction}: {cf.value}. "
        f"Historical baseline: {bf.value}. "
        f"Deviation: {dev}. "
        f"Based on {bf.observations} historical and {cf.observations} current observations."
    )


# ── Change timeline builder ───────────────────────────────────────────────────

def _build_change_timeline(
    dimension_changes: list[DimensionChange],
    data: _OperatorData,
    cutoff_ts: int,
) -> list[BehaviourTimelineEntry]:
    """
    Build a timeline of behavioural change events.

    Emits an entry when a dimension's drift changed from STABLE to CHANGED or back.
    Also includes baseline establishment and window boundary events.
    """
    entries: list[BehaviourTimelineEntry] = []

    # Baseline establishment
    if data.launches:
        first_ts = min(r["create_time"] for r in data.launches if r.get("create_time"))
        entries.append(BehaviourTimelineEntry(
            ts=first_ts,
            label="Behaviour Baseline — first observation",
            detail=f"{len(data.launches)} total launches recorded.",
            category="CAMPAIGN",
        ))

    # Window boundary
    entries.append(BehaviourTimelineEntry(
        ts=cutoff_ts,
        label="Current observation window began",
        detail=f"Last {CURRENT_WINDOW_DAYS} days vs. all prior history.",
        category="CAMPAIGN",
    ))

    # Changed dimensions
    for dc in dimension_changes:
        if dc.drift == "CHANGED":
            # Approximate time = most recent launch in current window
            current_launches = [
                r["create_time"] for r in data.launches
                if r.get("create_time", 0) >= cutoff_ts
            ]
            ts = max(current_launches) if current_launches else cutoff_ts
            facts_desc = ", ".join(c.label for c in dc.changed_facts[:2])
            entries.append(BehaviourTimelineEntry(
                ts=ts,
                label=f"{dc.label} — behaviour changed",
                detail=f"Changed facts: {facts_desc or 'see dimension detail'}.",
                category="LAUNCH" if dc.key == "launch" else "CAMPAIGN",
            ))

    entries.sort(key=lambda e: e.ts)
    return entries


# ── Overall drift reducer ─────────────────────────────────────────────────────

def _overall_drift(dimension_changes: list[DimensionChange]) -> str:
    drifts = {dc.drift for dc in dimension_changes}
    if "CHANGED" in drifts and "STABLE" in drifts:
        return "MIXED"
    if "CHANGED" in drifts:
        return "CHANGED"
    if drifts == {"STABLE"} or drifts == {"STABLE", "INSUFFICIENT_EVIDENCE"}:
        return "STABLE"
    return "INSUFFICIENT_EVIDENCE"


# ── Main engine ───────────────────────────────────────────────────────────────

class BehaviourChangeEngine:
    """
    Compares an operator's current behaviour against its own historical baseline.

    This class is the canonical entry point for X11. It does NOT predict, rank,
    or infer intent. It only reports observed differences.

    X12 (forecasting) should consume BehaviourChangeReport, not raw DBs.
    """

    def __init__(self, ops_db: str, live_db: str) -> None:
        self._ops_db  = ops_db
        self._live_db = live_db

    def _load(self, operator_id: str, treasury_wallets: list[str] | None) -> _OperatorData:
        if treasury_wallets is not None:
            return _OperatorData(treasury_wallets, self._ops_db, self._live_db)
        from src.ops.observation_store import ObservationStore
        return _OperatorData.from_observations(
            ObservationStore(self._ops_db).fetch(operator_id)
        )

    def compare(
        self,
        operator_id: str,
        treasury_wallets: list[str] | None = None,
        current_window_days: int = CURRENT_WINDOW_DAYS,
    ) -> BehaviourChangeReport:
        """
        Produce a BehaviourChangeReport for an operator.

        Splits all evidence into a baseline window (everything older than
        current_window_days) and a current window (last N days), then
        compares dimension by dimension.
        """
        now        = int(time.time())
        cutoff_ts  = now - current_window_days * 86400

        data = self._load(operator_id, treasury_wallets)

        gaps: list[str] = []
        if not getattr(data, "observations", []) and treasury_wallets is None:
            gaps.append("No canonical observations have been materialized for this operator.")

        baseline_data, current_data = _split_data(data, cutoff_ts)

        baseline_obs = len(getattr(baseline_data, "observations", [])) or (
            len(baseline_data.launches) + len(baseline_data.ops)
        )
        current_obs = len(getattr(current_data, "observations", [])) or (
            len(current_data.launches) + len(current_data.ops)
        )

        if baseline_obs == 0:
            gaps.append(
                f"No historical observations before the {current_window_days}-day window. "
                f"Baseline not yet established."
            )
        if current_obs == 0:
            gaps.append(
                f"No observations within the current {current_window_days}-day window. "
                f"Cannot compare against baseline."
            )

        # Derive profiles for each window
        def dimensions(window: _OperatorData) -> list[BehaviourDimension]:
            if getattr(window, "observations", []):
                obs = window.observations
                return [
                    _campaign_behaviour(_dimension_data(obs, "campaign")),
                    _funding_behaviour(_dimension_data(obs, "funding")),
                    _launch_behaviour(_dimension_data(obs, "launch")),
                    _operational_behaviour(_dimension_data(obs, "operational")),
                    _outcome_behaviour(_dimension_data(obs, "outcome")),
                ]
            return [
                _campaign_behaviour(window), _funding_behaviour(window),
                _launch_behaviour(window), _operational_behaviour(window),
                _outcome_behaviour(window),
            ]

        baseline_dims = dimensions(baseline_data)
        current_dims = dimensions(current_data)

        dimension_changes = [
            _compare_dimension(b, c)
            for b, c in zip(baseline_dims, current_dims)
        ]

        overall = _overall_drift(dimension_changes)

        drift_summary = {dc.label: dc.drift for dc in dimension_changes}

        # Baseline span in days
        if baseline_data.launches:
            first_ts = min(
                r["create_time"] for r in baseline_data.launches if r.get("create_time")
            )
            baseline_days = max(1, (cutoff_ts - first_ts) // 86400)
        else:
            baseline_days = 0

        timeline = _build_change_timeline(dimension_changes, data, cutoff_ts)

        return BehaviourChangeReport(
            operator_id=operator_id,
            computed_at=now,
            baseline_window_days=baseline_days,
            current_window_days=current_window_days,
            baseline_observations=baseline_obs,
            current_observations=current_obs,
            overall_drift=overall,
            drift_summary=drift_summary,
            dimension_changes=dimension_changes,
            timeline_events=timeline,
            evidence_gap_notes=gaps,
        )

    def compare_platform(self) -> dict:
        """
        Cross-operator behavioural drift summary.

        Reads operator entities from the store and produces per-operator
        drift labels without full profile computation.

        This is intentionally lightweight: it reads launch counts per treasury
        and classifies data availability, not actual drift.
        """
        try:
            from src.ops.operator_reader import OperatorReader
            from src.ops.behaviour_engine import _ro, _table_exists

            store   = OperatorReader(self._ops_db)
            all_ops = store.fetch_all_operators(exclude_rejected=True)

            now      = int(time.time())
            cutoff   = now - CURRENT_WINDOW_DAYS * 86400

            sufficient = []
            insufficient = []
            stable_ops  = []
            changed_ops = []

            for op in all_ops:
                op_id = op["operator_id"]
                tw = [
                    e["entity_address"]
                    for e in store.fetch_operator(op_id).get("entities", [])
                    if e.get("entity_type") in ("TREASURY", "SUB_PROVISIONER", "UNKNOWN")
                ] if store.fetch_operator(op_id) else []

                if not tw:
                    insufficient.append(op_id)
                    continue

                data = _OperatorData(tw, self._ops_db, self._live_db)
                if len(data.launches) < MIN_OBSERVATIONS * 2:
                    insufficient.append(op_id)
                    continue

                # Quick split
                baseline_l = [r for r in data.launches if r.get("create_time", 0) < cutoff]
                current_l  = [r for r in data.launches if r.get("create_time", 0) >= cutoff]

                if len(baseline_l) < MIN_OBSERVATIONS or len(current_l) < MIN_OBSERVATIONS:
                    insufficient.append(op_id)
                    continue

                sufficient.append(op_id)

                # Quick funding check (most sensitive metric)
                b_funds = [r["subprov_funding_sol"] for r in baseline_l if r.get("subprov_funding_sol")]
                c_funds = [r["subprov_funding_sol"] for r in current_l  if r.get("subprov_funding_sol")]

                if b_funds and c_funds:
                    b_mean = statistics.mean(b_funds)
                    c_mean = statistics.mean(c_funds)
                    dev = _deviation(b_mean, c_mean)
                    if dev in ("HIGH", "VERY_HIGH", "MODERATE"):
                        changed_ops.append(op_id)
                    else:
                        stable_ops.append(op_id)
                else:
                    stable_ops.append(op_id)

            return {
                "ok":                        True,
                "computed_at":               now,
                "operators_with_change":     changed_ops,
                "operators_stable":          stable_ops,
                "operators_insufficient":    insufficient,
                "total_evaluated":           len(all_ops),
                "sufficient_for_comparison": len(sufficient),
            }

        except Exception as exc:
            return {"ok": False, "reason": str(exc)}
