"""
Behaviour Intelligence Engine — Sprint X10.

Computes descriptive behavioural profiles per operator from existing
intelligence. Never predicts. Never speculates. Every metric includes
a confidence level and observation count.

Architecture:
  BehaviourEngine.compute(operator_id)
      → reads operator entities from OperatorReader
      → groups launches, creators, fanouts by treasury membership
      → derives per-dimension behaviour facts
      → returns BehaviourProfile

Public API surface:
  GET /api/operators/<id>/behaviour    → BehaviourProfile as JSON
  BehaviourProfile is the canonical object for X11/X12 to consume.

Confidence rules:
  INSUFFICIENT  < MIN_OBSERVATIONS  observations
  LOW           1–4 observations
  MEDIUM        5–11 observations
  HIGH          ≥ 12 observations
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import statistics
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from src.ops.operator_observation import OperatorObservation

DIMENSION_OBSERVATION_TYPES = {
    "campaign": frozenset({"CAMPAIGN", "CREATOR", "INFRASTRUCTURE"}),
    "funding": frozenset({"FUNDING", "PROVISIONING", "COORDINATION"}),
    "launch": frozenset({"LAUNCH", "CREATOR", "MIGRATION"}),
    "operational": frozenset({"TREASURY", "INFRASTRUCTURE", "RELAY", "COORDINATION"}),
    "outcome": frozenset({"LAUNCH", "MIGRATION"}),
}

MIN_OBSERVATIONS = 3

# ── Confidence ────────────────────────────────────────────────────────────────

def _confidence(n: int) -> str:
    if n < MIN_OBSERVATIONS:
        return "INSUFFICIENT"
    if n < 5:
        return "LOW"
    if n < 12:
        return "MEDIUM"
    return "HIGH"


def _stability(values: list[float]) -> str:
    """Coefficient of variation → stability label."""
    if len(values) < 2:
        return "UNKNOWN"
    mean = statistics.mean(values)
    if mean == 0:
        return "UNKNOWN"
    cv = statistics.stdev(values) / mean
    if cv < 0.15:
        return "STABLE"
    if cv < 0.40:
        return "MODERATE"
    return "VARIABLE"


# ── Fact dataclass ────────────────────────────────────────────────────────────

@dataclass
class BehaviourFact:
    key: str
    label: str
    value: Any            # formatted display value (str | int | float | None)
    raw: Any              # raw numeric for downstream consumption
    confidence: str       # INSUFFICIENT | LOW | MEDIUM | HIGH
    observations: int
    unit: str = ""
    stability: str = "UNKNOWN"
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── Dimension dataclass ───────────────────────────────────────────────────────

@dataclass
class BehaviourDimension:
    key: str
    label: str
    facts: list[BehaviourFact] = field(default_factory=list)
    overall_confidence: str = "INSUFFICIENT"
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "overall_confidence": self.overall_confidence,
            "summary": self.summary,
            "facts": [f.to_dict() for f in self.facts],
        }


# ── Timeline entry ────────────────────────────────────────────────────────────

@dataclass
class BehaviourTimelineEntry:
    ts: int
    label: str
    detail: str
    category: str  # CAMPAIGN | FUNDING | LAUNCH | INFRA

    def to_dict(self) -> dict:
        return asdict(self)


# ── Top-level profile ─────────────────────────────────────────────────────────

@dataclass
class BehaviourProfile:
    operator_id: str
    computed_at: int
    total_observations: int
    overall_confidence: str
    dimensions: list[BehaviourDimension] = field(default_factory=list)
    timeline: list[BehaviourTimelineEntry] = field(default_factory=list)
    stability_summary: dict[str, str] = field(default_factory=dict)
    insufficient_dimensions: list[str] = field(default_factory=list)
    evidence_gap_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "operator_id": self.operator_id,
            "computed_at": self.computed_at,
            "total_observations": self.total_observations,
            "overall_confidence": self.overall_confidence,
            "dimensions": [d.to_dict() for d in self.dimensions],
            "timeline": [e.to_dict() for e in self.timeline],
            "stability_summary": self.stability_summary,
            "insufficient_dimensions": self.insufficient_dimensions,
            "evidence_gap_notes": self.evidence_gap_notes,
            "fingerprint": self.fingerprint,
        }

    @property
    def fingerprint(self) -> str:
        """
        Deterministic content hash, excluding computed_at (a timestamp is not
        a content change). Two profiles with identical facts/confidence
        produce the same fingerprint regardless of when each was computed.
        """
        import hashlib
        import json
        payload = {
            "operator_id": self.operator_id,
            "total_observations": self.total_observations,
            "overall_confidence": self.overall_confidence,
            "dimensions": [d.to_dict() for d in self.dimensions],
            "stability_summary": self.stability_summary,
            "insufficient_dimensions": sorted(self.insufficient_dimensions),
        }
        blob = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ── DB connection helpers ─────────────────────────────────────────────────────

def _ro(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


# ── Raw data loader ───────────────────────────────────────────────────────────

class _OperatorData:
    """Loads all raw evidence for an operator from the available DBs."""

    def __init__(self, treasury_wallets: list[str], ops_db: str, live_db: str) -> None:
        self.treasury_wallets = treasury_wallets
        self._ops_db  = ops_db
        self._live_db = live_db

        self.launches:  list[dict] = []
        self.audit:     list[dict] = []
        self.creators:  list[dict] = []
        self.fanouts:   list[dict] = []
        self.wallets:   list[dict] = []
        self.ops:       list[dict] = []
        self.observations: list[OperatorObservation] = []

        if treasury_wallets:
            self._load()

    @classmethod
    def from_observations(cls, observations: list[OperatorObservation]) -> "_OperatorData":
        """Project canonical observations into the existing dimension calculators."""
        data = cls([], "", "")
        data.observations = list(observations)
        data.treasury_wallets = sorted({
            o.entity for o in observations if o.observation_type == "TREASURY" and o.entity
        })
        for observation in observations:
            row = dict(observation.metadata)
            kind = observation.observation_type
            if kind == "LAUNCH":
                data.launches.append(row)
            elif kind == "CAMPAIGN":
                data.ops.append(row)
            elif kind == "CREATOR":
                data.creators.append(row)
            elif kind == "COORDINATION":
                data.fanouts.append(row)
            elif kind in {"FUNDING", "PROVISIONING"}:
                data.launches.append(row)
            elif kind in {"TREASURY", "INFRASTRUCTURE", "RELAY"}:
                if row.get("role") == "SUB_PROVISIONER":
                    row["role"] = "SUB_PROV"
                data.wallets.append(row)
            elif kind == "MIGRATION":
                row.setdefault("migrated", True)
                data.audit.append(row)
        return data

    def _load(self) -> None:
        tw = self.treasury_wallets
        placeholders = ",".join("?" * len(tw))

        try:
            with _ro(self._ops_db) as conn:
                if _table_exists(conn, "wt_watchtower_launches"):
                    self.launches = [
                        dict(r) for r in conn.execute(
                            f"SELECT * FROM wt_watchtower_launches "
                            f"WHERE treasury_wallet IN ({placeholders}) "
                            f"ORDER BY create_time ASC",
                            tw,
                        ).fetchall()
                    ]
                if _table_exists(conn, "wt_launch_audit"):
                    self.audit = [
                        dict(r) for r in conn.execute(
                            f"SELECT * FROM wt_launch_audit "
                            f"WHERE treasury IN ({placeholders})",
                            tw,
                        ).fetchall()
                    ]
                if _table_exists(conn, "wt_ops_v2_creators"):
                    # Join via wt_ops_v2 to link treasury→operation→creators
                    self.creators = [
                        dict(r) for r in conn.execute(
                            f"""
                            SELECT c.*, o.treasury_root, o.first_seen as op_first_seen
                            FROM wt_ops_v2_creators c
                            JOIN wt_ops_v2 o ON c.operation_uuid = o.operation_uuid
                            WHERE o.treasury_root IN ({placeholders})
                            ORDER BY c.migration_time ASC
                            """,
                            tw,
                        ).fetchall()
                    ]
                if _table_exists(conn, "wt_fanout_events"):
                    # subprov wallets linked to these treasuries
                    subprovs = [
                        r[0] for r in conn.execute(
                            f"""
                            SELECT DISTINCT wallet FROM wt_ops_v2_wallets
                            WHERE operation_uuid IN (
                                SELECT operation_uuid FROM wt_ops_v2
                                WHERE treasury_root IN ({placeholders})
                            ) AND role = 'SUB_PROV'
                            """,
                            tw,
                        ).fetchall()
                    ] if _table_exists(conn, "wt_ops_v2_wallets") else []

                    if subprovs:
                        sp_ph = ",".join("?" * len(subprovs))
                        self.fanouts = [
                            dict(r) for r in conn.execute(
                                f"SELECT * FROM wt_fanout_events "
                                f"WHERE subprov_wallet IN ({sp_ph}) "
                                f"ORDER BY fanout_time ASC",
                                subprovs,
                            ).fetchall()
                        ]

                if _table_exists(conn, "wt_ops_v2_wallets"):
                    self.wallets = [
                        dict(r) for r in conn.execute(
                            f"""
                            SELECT w.*, o.treasury_root
                            FROM wt_ops_v2_wallets w
                            JOIN wt_ops_v2 o ON w.operation_uuid = o.operation_uuid
                            WHERE o.treasury_root IN ({placeholders})
                            """,
                            tw,
                        ).fetchall()
                    ]

                if _table_exists(conn, "wt_ops_v2"):
                    self.ops = [
                        dict(r) for r in conn.execute(
                            f"SELECT * FROM wt_ops_v2 WHERE treasury_root IN ({placeholders})",
                            tw,
                        ).fetchall()
                    ]
        except Exception:
            pass


def _dimension_data(observations: list[OperatorObservation], dimension: str) -> _OperatorData:
    declared = DIMENSION_OBSERVATION_TYPES[dimension]
    return _OperatorData.from_observations([
        observation for observation in observations
        if observation.observation_type in declared
    ])


# ── Dimension computers ───────────────────────────────────────────────────────

def _campaign_behaviour(data: _OperatorData) -> BehaviourDimension:
    ops = data.ops
    n   = len(ops)
    dim = BehaviourDimension(key="campaign", label="Campaign Behaviour")

    # Observed operations
    dim.facts.append(BehaviourFact(
        key="observed_campaigns",
        label="Observed Campaigns",
        value=n, raw=n,
        confidence=_confidence(n),
        observations=n,
    ))

    if n < MIN_OBSERVATIONS:
        dim.overall_confidence = "INSUFFICIENT"
        dim.summary = f"Insufficient evidence — {n} campaign{'s' if n!=1 else ''} observed."
        return dim

    # Timespan and cadence
    timestamps = sorted(
        o["first_seen"] for o in ops if o.get("first_seen")
    )
    if len(timestamps) >= 2:
        span_days = (timestamps[-1] - timestamps[0]) / 86400
        if span_days > 0:
            cadence = n / span_days
            dim.facts.append(BehaviourFact(
                key="campaigns_per_day",
                label="Average Campaigns / Day",
                value=round(cadence, 2), raw=cadence,
                confidence=_confidence(n),
                observations=n,
            ))

        # Spacing between campaigns
        gaps = [
            (timestamps[i] - timestamps[i-1]) / 3600
            for i in range(1, len(timestamps))
        ]
        if gaps:
            avg_gap_h = statistics.mean(gaps)
            dim.facts.append(BehaviourFact(
                key="avg_campaign_spacing_h",
                label="Average Campaign Spacing",
                value=f"{avg_gap_h:.1f}h", raw=avg_gap_h,
                confidence=_confidence(len(gaps)),
                observations=len(gaps),
                unit="hours",
                stability=_stability(gaps),
            ))

    # Campaign durations (first_seen → last_seen)
    durations = [
        (o["last_seen"] - o["first_seen"]) / 3600
        for o in ops
        if o.get("first_seen") and o.get("last_seen")
           and o["last_seen"] > o["first_seen"]
    ]
    if durations:
        avg_dur = statistics.mean(durations)
        dim.facts.append(BehaviourFact(
            key="avg_campaign_duration_h",
            label="Average Campaign Duration",
            value=f"{avg_dur:.1f}h", raw=avg_dur,
            confidence=_confidence(len(durations)),
            observations=len(durations),
            unit="hours",
            stability=_stability(durations),
        ))

    # Creators per campaign
    if data.creators:
        from collections import Counter
        creators_per_op = Counter(
            c["operation_uuid"] for c in data.creators if c.get("operation_uuid")
        )
        counts = list(creators_per_op.values())
        if counts:
            avg_c = statistics.mean(counts)
            dim.facts.append(BehaviourFact(
                key="avg_creators_per_campaign",
                label="Average Creators / Campaign",
                value=round(avg_c, 1), raw=avg_c,
                confidence=_confidence(len(counts)),
                observations=len(counts),
                stability=_stability(counts),
            ))

    # Sub-provisioners per campaign
    subprov_by_op: dict[str, set] = {}
    for w in data.wallets:
        if w.get("role") == "SUB_PROV":
            op_id = w.get("operation_uuid", "?")
            subprov_by_op.setdefault(op_id, set()).add(w["wallet"])
    if subprov_by_op:
        sp_counts = [len(v) for v in subprov_by_op.values()]
        avg_sp = statistics.mean(sp_counts)
        dim.facts.append(BehaviourFact(
            key="avg_subprovs_per_campaign",
            label="Average Sub-Provisioners / Campaign",
            value=round(avg_sp, 1), raw=avg_sp,
            confidence=_confidence(len(sp_counts)),
            observations=len(sp_counts),
            stability=_stability(sp_counts),
        ))

    dim.overall_confidence = _confidence(n)
    dim.summary = f"{n} campaigns observed across {len(data.treasury_wallets)} treasury wallet(s)."
    return dim


def _funding_behaviour(data: _OperatorData) -> BehaviourDimension:
    dim = BehaviourDimension(key="funding", label="Funding Behaviour")
    launches = data.launches
    n = len(launches)

    # Treasury sizes (subprov_funding_sol = capital sent to subprov)
    treasury_sizes = [
        r["subprov_funding_sol"] for r in launches
        if r.get("subprov_funding_sol")
    ]
    if treasury_sizes:
        avg_ts = statistics.mean(treasury_sizes)
        dim.facts.append(BehaviourFact(
            key="preferred_treasury_size",
            label="Preferred Treasury Size",
            value=f"{avg_ts:.2f} SOL", raw=avg_ts,
            confidence=_confidence(len(treasury_sizes)),
            observations=len(treasury_sizes),
            unit="SOL",
            stability=_stability(treasury_sizes),
        ))
    else:
        dim.facts.append(BehaviourFact(
            key="preferred_treasury_size",
            label="Preferred Treasury Size",
            value=None, raw=None,
            confidence="INSUFFICIENT",
            observations=0,
            notes="No subprov funding amounts recorded",
        ))

    # Creator funding (wrap-close SOL)
    creator_funding = [
        r["wrap_close_sol"] for r in launches
        if r.get("wrap_close_sol")
    ]
    if not creator_funding and data.creators:
        creator_funding = [
            c["funding_amount_sol"] for c in data.creators
            if c.get("funding_amount_sol")
        ]
    if creator_funding:
        avg_cf = statistics.mean(creator_funding)
        dim.facts.append(BehaviourFact(
            key="preferred_creator_funding",
            label="Preferred Creator Funding",
            value=f"{avg_cf:.3f} SOL", raw=avg_cf,
            confidence=_confidence(len(creator_funding)),
            observations=len(creator_funding),
            unit="SOL",
            stability=_stability(creator_funding),
        ))
    else:
        dim.facts.append(BehaviourFact(
            key="preferred_creator_funding",
            label="Preferred Creator Funding",
            value=None, raw=None,
            confidence="INSUFFICIENT",
            observations=0,
            notes="No creator funding amounts recorded",
        ))

    # Wrap-close usage (100% for WATCHTOWER, but be explicit)
    wc_count = sum(
        1 for r in launches
        if r.get("funding_mechanism") == "WSOL_WRAP_CLOSE"
    )
    wrap_pct = (wc_count / n * 100) if n else 0
    dim.facts.append(BehaviourFact(
        key="wrap_close_usage",
        label="Wrap-Close Funding",
        value=f"{wrap_pct:.0f}%", raw=wrap_pct,
        confidence=_confidence(n),
        observations=n,
        unit="%",
    ))

    # Fanout behaviour
    if data.fanouts:
        fanout_counts = [f["fanout_count"] for f in data.fanouts if f.get("fanout_count")]
        if fanout_counts:
            avg_fc = statistics.mean(fanout_counts)
            dim.facts.append(BehaviourFact(
                key="avg_fanout_count",
                label="Average Fan-Out Count",
                value=round(avg_fc, 1), raw=avg_fc,
                confidence=_confidence(len(fanout_counts)),
                observations=len(fanout_counts),
                stability=_stability(fanout_counts),
            ))

        fanout_sols = [f["avg_sol"] for f in data.fanouts if f.get("avg_sol")]
        if fanout_sols:
            avg_fs = statistics.mean(fanout_sols)
            dim.facts.append(BehaviourFact(
                key="avg_fanout_sol",
                label="Average SOL per Fan-Out Recipient",
                value=f"{avg_fs:.4f} SOL", raw=avg_fs,
                confidence=_confidence(len(fanout_sols)),
                observations=len(fanout_sols),
                unit="SOL",
                stability=_stability(fanout_sols),
            ))

    obs = max(n, len(treasury_sizes) if treasury_sizes else 0)
    dim.overall_confidence = _confidence(obs)
    dim.summary = f"Funding profile based on {n} observed launches."
    return dim


def _launch_behaviour(data: _OperatorData) -> BehaviourDimension:
    dim = BehaviourDimension(key="launch", label="Launch Behaviour")
    launches = data.launches
    n = len(launches)

    dim.facts.append(BehaviourFact(
        key="observed_launches",
        label="Observed Launches",
        value=n, raw=n,
        confidence=_confidence(n),
        observations=n,
    ))

    if n < MIN_OBSERVATIONS:
        dim.overall_confidence = "INSUFFICIENT"
        dim.summary = f"Insufficient evidence — {n} launch{'es' if n!=1 else ''} observed."
        return dim

    # Birth-to-launch delay (signaller-to-create seconds)
    delays = [
        r["birth_to_launch_seconds"] for r in launches
        if r.get("birth_to_launch_seconds") is not None
    ]
    if delays:
        avg_d = statistics.mean(delays)
        dim.facts.append(BehaviourFact(
            key="avg_launch_delay_s",
            label="Average Launch Delay",
            value=f"{avg_d:.0f}s", raw=avg_d,
            confidence=_confidence(len(delays)),
            observations=len(delays),
            unit="seconds",
            stability=_stability(delays),
        ))

    # Fanout-to-create timing
    f2c = [
        r["fanout_to_create_secs"] for r in launches
        if r.get("fanout_to_create_secs") is not None
    ]
    if f2c:
        avg_f2c = statistics.mean(f2c)
        dim.facts.append(BehaviourFact(
            key="avg_fanout_to_create_s",
            label="Fan-Out to CREATE Latency",
            value=f"{avg_f2c:.1f}s", raw=avg_f2c,
            confidence=_confidence(len(f2c)),
            observations=len(f2c),
            unit="seconds",
            stability=_stability(f2c),
        ))

    # Creator-to-migration timing
    migration_times = [
        r["create_to_migration_secs"] for r in launches
        if r.get("create_to_migration_secs") is not None
    ]
    if not migration_times and data.creators:
        # Fall back to creator records with migration timestamps
        migrated = [
            c for c in data.creators
            if c.get("migration_time") and c.get("op_first_seen")
        ]
        migration_times = [
            c["migration_time"] - c["op_first_seen"] for c in migrated
            if c["migration_time"] > c["op_first_seen"]
        ]
    if migration_times:
        avg_mt = statistics.mean(migration_times)
        dim.facts.append(BehaviourFact(
            key="avg_migration_timing_s",
            label="Average Time to Migration",
            value=f"{avg_mt/60:.0f}m", raw=avg_mt,
            confidence=_confidence(len(migration_times)),
            observations=len(migration_times),
            unit="seconds",
            stability=_stability(migration_times),
        ))

    # Launch mode breakdown
    from collections import Counter
    modes = Counter(
        r.get("launch_mode") or "UNKNOWN" for r in launches
    )
    if len(modes) > 1 or (len(modes) == 1 and "UNKNOWN" not in modes):
        primary_mode = modes.most_common(1)[0][0]
        dim.facts.append(BehaviourFact(
            key="primary_launch_mode",
            label="Primary Launch Mode",
            value=primary_mode, raw=primary_mode,
            confidence=_confidence(n),
            observations=n,
        ))

    # Operating hours from create_time
    hours = [
        time.gmtime(r["create_time"]).tm_hour
        for r in launches
        if r.get("create_time")
    ]
    if len(hours) >= MIN_OBSERVATIONS:
        # Find the modal 4-hour window
        from collections import Counter as C
        hour_counts = C(hours)
        peak_hour = hour_counts.most_common(1)[0][0]
        dim.facts.append(BehaviourFact(
            key="peak_launch_hour_utc",
            label="Peak Launch Hour (UTC)",
            value=f"{peak_hour:02d}:00–{(peak_hour+2)%24:02d}:00 UTC",
            raw=peak_hour,
            confidence=_confidence(len(hours)),
            observations=len(hours),
        ))

        # Most active weekday
        from collections import Counter as C2
        weekday_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        wdays = C2(time.gmtime(r["create_time"]).tm_wday for r in launches if r.get("create_time"))
        peak_day_idx = wdays.most_common(1)[0][0]
        dim.facts.append(BehaviourFact(
            key="peak_launch_weekday",
            label="Most Active Day",
            value=weekday_names[peak_day_idx], raw=peak_day_idx,
            confidence=_confidence(len(hours)),
            observations=len(hours),
        ))

    dim.overall_confidence = _confidence(n)
    dim.summary = f"{n} launches measured. Timing and funding mechanics derived from on-chain data."
    return dim


def _operational_behaviour(data: _OperatorData) -> BehaviourDimension:
    dim = BehaviourDimension(key="operational", label="Operational Behaviour")
    wallets = data.wallets

    # Infrastructure reuse — wallets appearing in >1 operation
    from collections import Counter
    wallet_op_count = Counter(w["wallet"] for w in wallets)
    reused = sum(1 for v in wallet_op_count.values() if v > 1)
    total_wallets = len(wallet_op_count)
    sample_count = max(len(data.ops), total_wallets)

    dim.facts.append(BehaviourFact(
        key="total_infra_wallets",
        label="Total Infrastructure Wallets",
        value=total_wallets, raw=total_wallets,
        confidence=_confidence(sample_count),
        observations=sample_count,
    ))

    if total_wallets:
        reuse_pct = reused / total_wallets * 100
        dim.facts.append(BehaviourFact(
            key="infra_reuse_pct",
            label="Infrastructure Reuse",
            value=f"{reuse_pct:.0f}%", raw=reuse_pct,
            confidence=_confidence(sample_count),
            observations=sample_count,
            unit="%",
        ))

    # Role breakdown
    role_counts = Counter(w.get("role", "UNKNOWN") for w in wallets)
    for role, cnt in role_counts.most_common():
        dim.facts.append(BehaviourFact(
            key=f"wallet_role_{role.lower()}",
            label=f"Wallets with Role: {role}",
            value=cnt, raw=cnt,
            confidence=_confidence(cnt),
            observations=cnt,
        ))

    # Preferred operational workflow — WSOL_WRAP_CLOSE is the mechanism
    n_launches = len(data.launches)
    if n_launches:
        dim.facts.append(BehaviourFact(
            key="preferred_workflow",
            label="Preferred Funding Workflow",
            value="WSOL wrap-close provisioning",
            raw="WSOL_WRAP_CLOSE",
            confidence=_confidence(n_launches),
            observations=n_launches,
        ))

    dim.overall_confidence = _confidence(sample_count)
    dim.summary = f"{total_wallets} infrastructure entities observed."
    return dim


def _outcome_behaviour(data: _OperatorData) -> BehaviourDimension:
    """Derived from launch_audit — outcome and actionability patterns."""
    dim = BehaviourDimension(key="outcome", label="Launch Outcome Behaviour")
    audit = data.audit
    n = len(audit)

    dim.facts.append(BehaviourFact(
        key="audited_launches",
        label="Audited Launches",
        value=n, raw=n,
        confidence=_confidence(n),
        observations=n,
    ))

    if n < MIN_OBSERVATIONS:
        dim.overall_confidence = "INSUFFICIENT"
        dim.summary = f"Insufficient audit data — {n} record(s) available."
        return dim

    # Migration rate
    migrated = [r for r in audit if r.get("migrated")]
    mig_pct = len(migrated) / n * 100
    dim.facts.append(BehaviourFact(
        key="migration_rate",
        label="Migration Rate",
        value=f"{mig_pct:.0f}%", raw=mig_pct,
        confidence=_confidence(n),
        observations=n,
        unit="%",
        stability=_stability([1.0 if r.get("migrated") else 0.0 for r in audit]),
    ))

    # Actionable multiples
    multiples = [r["actionable_multiple"] for r in audit if r.get("actionable_multiple")]
    if multiples:
        avg_m = statistics.mean(multiples)
        dim.facts.append(BehaviourFact(
            key="avg_actionable_multiple",
            label="Average Actionable Multiple",
            value=f"{avg_m:.1f}×", raw=avg_m,
            confidence=_confidence(len(multiples)),
            observations=len(multiples),
            stability=_stability(multiples),
        ))
        # Bucket: % with >5× upside
        gt5 = sum(1 for m in multiples if m > 5)
        dim.facts.append(BehaviourFact(
            key="pct_gt5x",
            label="Launches with >5× Upside",
            value=f"{gt5/len(multiples)*100:.0f}%",
            raw=gt5 / len(multiples) * 100,
            confidence=_confidence(len(multiples)),
            observations=len(multiples),
            unit="%",
        ))

    # Peak MC
    peaks = [r["peak_mc"] for r in audit if r.get("peak_mc")]
    if peaks:
        avg_pk = statistics.mean(peaks)
        dim.facts.append(BehaviourFact(
            key="avg_peak_mc",
            label="Average Peak Market Cap",
            value=f"${avg_pk:,.0f}", raw=avg_pk,
            confidence=_confidence(len(peaks)),
            observations=len(peaks),
            unit="USD",
            stability=_stability(peaks),
        ))

    dim.overall_confidence = _confidence(n)
    dim.summary = f"{n} launches audited. {len(migrated)} migrated ({mig_pct:.0f}%)."
    return dim


# ── Timeline builder ──────────────────────────────────────────────────────────

def _build_timeline(data: _OperatorData) -> list[BehaviourTimelineEntry]:
    entries: list[BehaviourTimelineEntry] = []

    for op in data.ops:
        ts = op.get("first_seen")
        if ts:
            entries.append(BehaviourTimelineEntry(
                ts=ts,
                label="Campaign started",
                detail=f"Operation {op['operation_uuid'][:8]}…",
                category="CAMPAIGN",
            ))
        ls = op.get("last_seen")
        if ls and ls != ts:
            entries.append(BehaviourTimelineEntry(
                ts=ls,
                label="Campaign last active",
                detail=f"Operation {op['operation_uuid'][:8]}…",
                category="CAMPAIGN",
            ))

    for launch in data.launches:
        ts = launch.get("create_time")
        if ts:
            entries.append(BehaviourTimelineEntry(
                ts=ts,
                label="Launch detected",
                detail=f"Creator {(launch.get('creator_wallet') or '')[:8]}… via {(launch.get('subprov_wallet') or '')[:8]}…",
                category="LAUNCH",
            ))

    entries.sort(key=lambda e: e.ts)

    # Deduplicate to at most one entry per 60-second window per category
    deduped: list[BehaviourTimelineEntry] = []
    last_by_cat: dict[str, int] = {}
    for e in entries:
        prev = last_by_cat.get(e.category, 0)
        if e.ts - prev >= 60:
            deduped.append(e)
            last_by_cat[e.category] = e.ts

    return deduped[:100]  # cap at 100 entries


def _build_observation_timeline(
    observations: list[OperatorObservation],
) -> list[BehaviourTimelineEntry]:
    entries = [BehaviourTimelineEntry(
        ts=observation.timestamp,
        label=f"{observation.observation_type.replace('_', ' ').title()} observed",
        detail=(observation.entity or observation.source)[:48],
        category=observation.observation_type,
    ) for observation in observations if observation.timestamp]
    entries.sort(key=lambda entry: (entry.ts, entry.category, entry.detail))
    return entries[:100]


# ── Main engine ───────────────────────────────────────────────────────────────

class BehaviourEngine:
    """
    Computes a BehaviourProfile for an operator.

    Production computation consumes only persisted OperatorObservation rows.
    The optional treasury_wallets argument retains the pre-X17 test/integration
    compatibility path while callers migrate to materialization.
    """

    def __init__(self, ops_db: str, live_db: str) -> None:
        self._ops_db  = ops_db
        self._live_db = live_db

    def _treasury_wallets(self, operator_id: str) -> list[str]:
        """Read treasury wallets from the operator's entity list."""
        try:
            from src.ops.operator_reader import OperatorReader
            store = OperatorReader(self._ops_db)
            op = store.fetch_operator(operator_id)
            if not op:
                return []
            return [
                e["entity_address"]
                for e in op.get("entities", [])
                if e.get("entity_type") in ("TREASURY", "SUB_PROVISIONER", "UNKNOWN")
            ]
        except Exception:
            return []

    def compute(self, operator_id: str, treasury_wallets: list[str] | None = None) -> BehaviourProfile:
        """
        Compute a full BehaviourProfile for the operator.

        If `treasury_wallets` is supplied, use it directly (avoids a second
        OperatorReader lookup when the caller already has the operator dict).
        """
        now = int(time.time())
        from src.ops.observation_store import ObservationStore
        observations = ObservationStore(self._ops_db).fetch(operator_id)

        if observations:
            total_obs = len(observations)
            dimensions = [
                _campaign_behaviour(_dimension_data(observations, "campaign")),
                _funding_behaviour(_dimension_data(observations, "funding")),
                _launch_behaviour(_dimension_data(observations, "launch")),
                _operational_behaviour(_dimension_data(observations, "operational")),
                _outcome_behaviour(_dimension_data(observations, "outcome")),
            ]
            timeline = _build_observation_timeline(observations)
        else:
            # Compatibility for callers that explicitly supply the legacy input.
            # Normal operator routes do not take this branch.
            tw = treasury_wallets or []
            data = _OperatorData(tw, self._ops_db, self._live_db)
            total_obs = len(data.launches) + len(data.ops)
            dimensions = [
                _campaign_behaviour(data), _funding_behaviour(data),
                _launch_behaviour(data), _operational_behaviour(data),
                _outcome_behaviour(data),
            ]
            timeline = _build_timeline(data)

        # Stability summary (one label per dimension)
        stability: dict[str, str] = {}
        for dim in dimensions:
            for fact in dim.facts:
                if fact.stability and fact.stability != "UNKNOWN":
                    stability[dim.label] = fact.stability
                    break

        # Which dimensions are insufficient?
        insufficient = [d.label for d in dimensions if d.overall_confidence == "INSUFFICIENT"]

        # Evidence gaps
        gaps: list[str] = []
        if not observations and treasury_wallets is not None:
            if not data.launches:
                gaps.append("No launches recorded for these treasuries.")
            if not data.creators:
                gaps.append("No creator observations available.")
            if not data.fanouts:
                gaps.append("No coordination or fan-out observations available.")
        elif not observations:
            gaps.append("No canonical observations have been materialized for this operator.")
        else:
            observed_types = {observation.observation_type for observation in observations}
            for dimension, declared_types in DIMENSION_OBSERVATION_TYPES.items():
                if not observed_types.intersection(declared_types):
                    gaps.append(
                        f"No observations available for the {dimension} dimension "
                        f"({', '.join(sorted(declared_types))})."
                    )

        # Overall confidence = worst non-insufficient dimension, or INSUFFICIENT
        order = ["HIGH", "MEDIUM", "LOW", "INSUFFICIENT"]
        conf_values = [d.overall_confidence for d in dimensions]
        overall = max(conf_values, key=lambda c: -order.index(c) if c in order else -99)

        return BehaviourProfile(
            operator_id=operator_id,
            computed_at=now,
            total_observations=total_obs,
            overall_confidence=overall,
            dimensions=dimensions,
            timeline=timeline,
            stability_summary=stability,
            insufficient_dimensions=insufficient,
            evidence_gap_notes=gaps,
        )

    def compute_platform_summary(self) -> dict:
        """
        Cross-operator behaviour summary.

        Returns aggregate statistics for the operators index views.
        Does not read individual profiles — reads aggregate DB queries directly.
        """
        try:
            with _ro(self._ops_db) as conn:
                if not _table_exists(conn, "wt_watchtower_launches"):
                    return {"ok": False, "reason": "No launch data"}

                rows = conn.execute(
                    """
                    SELECT treasury_wallet, COUNT(*) as n,
                           AVG(subprov_funding_sol) as avg_fund,
                           AVG(wrap_close_sol) as avg_wrap,
                           MIN(create_time) as first,
                           MAX(create_time) as last
                    FROM wt_watchtower_launches
                    GROUP BY treasury_wallet
                    ORDER BY n DESC
                    """
                ).fetchall()

                return {
                    "ok": True,
                    "treasury_count": len(rows),
                    "total_launches": sum(r["n"] for r in rows),
                    "most_active_treasury": dict(rows[0]) if rows else None,
                    "computed_at": int(time.time()),
                }
        except Exception as exc:
            return {"ok": False, "reason": str(exc)}
