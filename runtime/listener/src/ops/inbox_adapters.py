"""
Operations OS — Inbox Adapters.

Each adapter reads the current lifecycle snapshot for one operation and
emits zero or more InboxItems to the store.

Rules identical to lifecycle_adapters.py:
- Adapters are read-only.  They never write to operation DBs.
- Adapters use ONLY what lifecycle snapshots already computed.
  No additional DB round-trips.
- If the store write fails, log but never raise.
- Dedup keys are deterministic so re-running is idempotent.
"""

from __future__ import annotations

import time
from typing import List

from src.ops.assessment_engine import AssessmentEngine, OperatorAssessmentBundle
from src.ops.forecast_engine import ForecastEngine
from src.ops.inbox import (
    InboxItem, InboxStore,
    CRITICAL, HIGH, MEDIUM, LOW, INFO,
    SUBJECT_LIFECYCLE, SUBJECT_BEHAVIOUR, SUBJECT_CONFIDENCE,
    SUBJECT_OPERATION, SUBJECT_RELATIONSHIP, SUBJECT_ASSESSMENT, SUBJECT_FORECAST,
    NEW,
)
from src.ops.lifecycle import LifecycleSnapshot, IDLE, OBSERVING, ARMED, ACTIVE, COMPLETED
from src.ops.lifecycle_adapters import get_all_lifecycles


# ── Helper ────────────────────────────────────────────────────────────────────

def _item(
    *,
    operation_id: str,
    subject_type: str,
    priority: str,
    headline: str,
    summary: str,
    reason: str,
    recommended_action: str,
    dedup_key: str,
    entity_id: str | None = None,
    confidence: float | None = None,
    meta: dict | None = None,
) -> InboxItem:
    return InboxItem(
        operation_id=operation_id,
        entity_id=entity_id,
        subject_type=subject_type,
        priority=priority,
        headline=headline,
        summary=summary,
        reason=reason,
        recommended_action=recommended_action,
        dedup_key=dedup_key,
        confidence=confidence,
        meta=meta or {},
    )


# ── WATCHTOWER adapter ───────────────────────────────────────────────────────

def _watchtower_items(snap: LifecycleSnapshot) -> List[InboxItem]:
    items: list[InboxItem] = []
    counts = snap.counts
    armed  = counts.get(ARMED,     0)
    active = counts.get(ACTIVE,    0)
    obs    = counts.get(OBSERVING, 0)
    comp   = counts.get(COMPLETED, 0)

    if active > 0:
        items.append(_item(
            operation_id="watchtower",
            subject_type=SUBJECT_LIFECYCLE,
            priority=HIGH,
            headline=f"{active} launch event(s) in progress",
            summary=(
                f"WATCHTOWER has detected {active} launch(es) that are currently within the "
                f"post-create window awaiting migration confirmation."
            ),
            reason="Creator entered ACTIVE lifecycle state — launch detected at CREATE time.",
            recommended_action="Monitor migration. Verify on-chain outcome via Entity Intelligence.",
            dedup_key="watchtower:active_launches",
            confidence=snap.confidence,
            meta={"active_count": active, "armed_count": armed},
        ))

    if armed > 0:
        conf = snap.confidence
        priority = HIGH if (conf is not None and conf >= 0.7) else MEDIUM
        items.append(_item(
            operation_id="watchtower",
            subject_type=SUBJECT_LIFECYCLE,
            priority=priority,
            headline=f"{armed} creator(s) armed — launch expected",
            summary=(
                f"{armed} creator wallet(s) have been funded and are waiting "
                f"for the token create instruction. Confidence: "
                f"{int(conf * 100)}%." if conf is not None else
                f"{armed} creator wallet(s) are in ARMED state."
            ),
            reason="Wrap-close funding confirmed. Creator identity resolved.",
            recommended_action="Monitor for CREATE instruction. Prepare to act on launch.",
            dedup_key="watchtower:armed_creators",
            confidence=conf,
            meta={"armed_count": armed, "observing_count": obs},
        ))

    if snap.lifecycle_state == IDLE and snap.last_transition_at:
        age = int(time.time()) - snap.last_transition_at
        if age > 86400 * 2:
            items.append(_item(
                operation_id="watchtower",
                subject_type=SUBJECT_OPERATION,
                priority=LOW,
                headline="WATCHTOWER has been quiet for an extended period",
                summary=(
                    f"No ARMED or ACTIVE events observed in the last "
                    f"{age // 3600}h. The operation is monitoring but no "
                    f"qualifying subprov activity has been detected."
                ),
                reason="Operation has been IDLE beyond the 48h threshold.",
                recommended_action="Verify subprov subscription coverage is healthy.",
                dedup_key="watchtower:extended_idle",
                meta={"idle_hours": age // 3600},
            ))

    return items


# ── Launcher Observatory adapter ─────────────────────────────────────────────

def _launcher_observatory_items(snap: LifecycleSnapshot) -> List[InboxItem]:
    items: list[InboxItem] = []
    counts = snap.counts
    armed  = counts.get(ARMED,     0)
    active = counts.get(ACTIVE,    0)

    if active > 0:
        items.append(_item(
            operation_id="launcher-observatory",
            subject_type=SUBJECT_LIFECYCLE,
            priority=MEDIUM,
            headline=f"{active} launch(es) detected in the last 24h",
            summary=(
                f"Launcher Observatory has detected {active} token launch(es) "
                f"within the observation window. Attribution status varies."
            ),
            reason="Launches detected within the active observation window.",
            recommended_action="Review attribution. Open Launcher Observatory for funder analysis.",
            dedup_key="lo:active_launches",
            meta={"active_count": active},
        ))

    if armed > 0:
        items.append(_item(
            operation_id="launcher-observatory",
            subject_type=SUBJECT_LIFECYCLE,
            priority=MEDIUM,
            headline=f"{armed} attributed operator(s) — next wave predictable",
            summary=(
                f"{armed} operator(s) with confirmed attribution have been "
                f"active in the last 7 days. Historical cadence suggests "
                f"further activity is likely."
            ),
            reason="Known operators with recent activity match predictable launch cadence.",
            recommended_action="Review operator profiles. Monitor for next wave.",
            dedup_key="lo:armed_operators",
            meta={"armed_count": armed},
        ))

    if snap.lifecycle_state == IDLE and snap.last_transition_at:
        age = int(time.time()) - snap.last_transition_at
        if age > 86400 * 3:
            items.append(_item(
                operation_id="launcher-observatory",
                subject_type=SUBJECT_OPERATION,
                priority=INFO,
                headline="Launcher Observatory — no launch activity in 72h",
                summary=(
                    f"No launches detected for {age // 3600}h. The operation "
                    f"is monitoring but no qualifying activity has been observed."
                ),
                reason="Extended quiet period beyond the 72h threshold.",
                recommended_action="No action required. Monitor for resumption.",
                dedup_key="lo:extended_idle",
                meta={"idle_hours": age // 3600},
            ))

    return items


# ── Buy Swarm Observatory adapter ────────────────────────────────────────────

def _buy_swarm_observatory_items(snap: LifecycleSnapshot) -> List[InboxItem]:
    items: list[InboxItem] = []
    counts = snap.counts
    active = counts.get(ACTIVE, 0)
    armed  = counts.get(ARMED,  0)

    if active > 0:
        items.append(_item(
            operation_id="buy-swarm-observatory",
            subject_type=SUBJECT_BEHAVIOUR,
            priority=MEDIUM,
            headline=f"{active} coordinated buy campaign(s) in progress",
            summary=(
                f"{active} token(s) are seeing coordinated buy activity "
                f"from wallets with a known subprov funding source. "
                f"Campaign window is active."
            ),
            reason="Coordinated wallet cluster detected with qualified participant count.",
            recommended_action="Review token(s) for manipulation risk. Inspect swarm operator.",
            dedup_key="bso:active_campaigns",
            meta={"active_count": active},
        ))

    if armed > 0 and active == 0:
        items.append(_item(
            operation_id="buy-swarm-observatory",
            subject_type=SUBJECT_BEHAVIOUR,
            priority=LOW,
            headline=f"{armed} qualified swarm pattern(s) identified",
            summary=(
                f"{armed} token(s) have been associated with a coordinated "
                f"buy pattern meeting the qualification threshold but no "
                f"longer show active campaign behaviour."
            ),
            reason="Qualified swarm patterns detected within the 7-day lookback.",
            recommended_action="Review historical swarm patterns. No immediate action required.",
            dedup_key="bso:armed_swarms",
            meta={"armed_count": armed},
        ))

    return items


# ── Operator Resolution inbox items ──────────────────────────────────────────

def _operator_resolution_items() -> List[InboxItem]:
    """
    Emit inbox items for operator resolution state.
    Reads directly from OperatorReader — no lifecycle snapshot needed.
    """
    items: list[InboxItem] = []
    try:
        from src.core.db import OPS_DB_PATH
        from src.ops.operator_reader import OperatorReader
        op_store = OperatorReader(str(OPS_DB_PATH))
        summary  = op_store.fetch_summary()
    except Exception:
        return items

    review_pending = summary.get("review_pending", 0)
    candidates     = summary.get("candidates",     0)
    provisional    = summary.get("provisional",    0)

    if review_pending > 0:
        items.append(_item(
            operation_id="operator-resolution",
            subject_type=SUBJECT_OPERATION,
            priority=HIGH,
            headline=f"{review_pending} operator(s) awaiting review decision",
            summary=(
                f"{review_pending} operator attribution(s) are in MERGE_REVIEW or "
                f"SPLIT_REVIEW state and require an analyst decision before the "
                f"platform can advance them."
            ),
            reason="Automated resolution proposed a merge or split requiring human confirmation.",
            recommended_action="Open Operator Resolution and review the flagged operators.",
            dedup_key="operator:review_pending",
            meta={"review_pending": review_pending},
        ))

    if provisional > 0:
        items.append(_item(
            operation_id="operator-resolution",
            subject_type=SUBJECT_CONFIDENCE,
            priority=MEDIUM,
            headline=f"{provisional} provisional operator(s) — confirm or reject",
            summary=(
                f"{provisional} operator(s) have two or more independent identity signals "
                f"and have been promoted to PROVISIONAL. Human confirmation strengthens "
                f"the intelligence layer."
            ),
            reason="Multiple identity-class evidence records exist without human review.",
            recommended_action="Review provisional operators and confirm or reject attribution.",
            dedup_key="operator:provisional",
            meta={"provisional": provisional},
        ))

    if candidates > 0 and provisional == 0 and review_pending == 0:
        items.append(_item(
            operation_id="operator-resolution",
            subject_type=SUBJECT_LIFECYCLE,
            priority=LOW,
            headline=f"{candidates} operator candidate(s) discovered",
            summary=(
                f"{candidates} potential operator(s) have been identified with a single "
                f"identity signal. The resolver will promote them as further evidence "
                f"accumulates."
            ),
            reason="New IDENTITY-class evidence triggered operator candidate creation.",
            recommended_action="No immediate action required. Monitor for promotion to PROVISIONAL.",
            dedup_key="operator:candidates",
            meta={"candidates": candidates},
        ))

    return items


# ── Behaviour intelligence inbox items ───────────────────────────────────────

def _behaviour_items() -> List[InboxItem]:
    """
    Emit informational inbox items when operator behaviour profiles mature.
    Never emits predictions — only evidence-based maturity signals.
    """
    items: List[InboxItem] = []
    try:
        from src.core.db import OPS_DB_PATH, DB_PATH
        from src.ops.operator_reader import OperatorReader
        from src.ops.behaviour_engine import BehaviourEngine, MIN_OBSERVATIONS

        store  = OperatorReader(str(OPS_DB_PATH))
        engine = BehaviourEngine(str(OPS_DB_PATH), str(DB_PATH))
        all_ops = store.fetch_all_operators(exclude_rejected=True)

        for op in all_ops:
            op_id = op["operator_id"]
            profile = engine.compute(op_id)

            n_obs  = profile.total_observations
            conf   = profile.overall_confidence

            # Not enough data yet — skip
            if n_obs < MIN_OBSERVATIONS:
                continue

            # First time profile is HIGH confidence
            if conf == "HIGH":
                items.append(_item(
                    operation_id=f"operator-{op_id}",
                    subject_type=SUBJECT_BEHAVIOUR,
                    priority=INFO,
                    headline=f"Operator behaviour profile matured (HIGH confidence)",
                    summary=(
                        f"Operator {op_id[:12]}… now has a HIGH-confidence behavioural profile "
                        f"based on {n_obs} observations across "
                        f"{len(profile.dimensions)} dimensions."
                    ),
                    reason=f"{n_obs} observations collected — confidence threshold met.",
                    recommended_action="Review behaviour profile to understand this operator's normal operating pattern.",
                    dedup_key=f"behaviour:high:{op_id}",
                    meta={"operator_id": op_id, "observations": n_obs},
                ))

            elif conf == "MEDIUM":
                items.append(_item(
                    operation_id=f"operator-{op_id}",
                    subject_type=SUBJECT_BEHAVIOUR,
                    priority=INFO,
                    headline=f"Operator behaviour profile has sufficient evidence",
                    summary=(
                        f"Operator {op_id[:12]}… has {n_obs} observations — "
                        f"enough for reliable behavioural modelling across most dimensions."
                    ),
                    reason=f"{n_obs} observations collected.",
                    recommended_action="Review behaviour profile. Confidence will increase as more campaigns are observed.",
                    dedup_key=f"behaviour:medium:{op_id}",
                    meta={"operator_id": op_id, "observations": n_obs},
                ))

    except Exception as exc:
        print(f"[INBOX] behaviour items error: {exc}")

    return items


# ── Behaviour change inbox items ─────────────────────────────────────────────

_CHANGE_PRIORITY_MAP = {
    "VERY_HIGH": HIGH,
    "HIGH":      MEDIUM,
    "MODERATE":  LOW,
}

_CHANGE_LABEL_MAP = {
    "preferred_treasury_size":   "Funding amount changed",
    "preferred_creator_funding": "Creator funding changed",
    "avg_launch_delay_s":        "Launch delay changed",
    "avg_creators_per_campaign": "Campaign size changed",
    "avg_fanout_count":          "Fan-out count changed",
    "wrap_close_usage":          "Wrap-close usage changed",
    "avg_fanout_to_create_s":    "Fan-out-to-CREATE timing changed",
    "infra_reuse_pct":           "Infrastructure reuse changed",
    "migration_rate":            "Migration rate changed",
}


def _behaviour_change_items() -> List[InboxItem]:
    """
    Emit inbox items when an operator's behaviour deviates from its baseline.

    Never emits predictions. Only reports observed differences backed
    by sufficient evidence on both sides of the comparison window.
    """
    items: List[InboxItem] = []
    try:
        from src.core.db import OPS_DB_PATH, DB_PATH
        from src.ops.operator_reader import OperatorReader
        from src.ops.behaviour_change import BehaviourChangeEngine, CURRENT_WINDOW_DAYS

        store  = OperatorReader(str(OPS_DB_PATH))
        engine = BehaviourChangeEngine(str(OPS_DB_PATH), str(DB_PATH))
        all_ops = store.fetch_all_operators(exclude_rejected=True)

        for op in all_ops:
            op_id = op["operator_id"]
            try:
                report = engine.compare(op_id)
            except Exception:
                continue

            # Skip operators without enough evidence
            if report.baseline_observations == 0 or report.current_observations == 0:
                continue

            if report.overall_drift == "INSUFFICIENT_EVIDENCE":
                continue

            # Emit one item per changed fact with sufficient confidence
            for dc in report.dimension_changes:
                if dc.drift != "CHANGED":
                    continue
                for cmp in dc.changed_facts:
                    if cmp.confidence == "INSUFFICIENT":
                        continue
                    priority = _CHANGE_PRIORITY_MAP.get(cmp.deviation, LOW)
                    short_label = _CHANGE_LABEL_MAP.get(cmp.key, f"{cmp.label} changed")
                    items.append(_item(
                        operation_id=f"operator-{op_id}",
                        subject_type=SUBJECT_BEHAVIOUR,
                        priority=priority,
                        headline=short_label,
                        summary=(
                            f"Operator {op_id[:12]}…: {cmp.reason}"
                        ),
                        reason=cmp.reason,
                        recommended_action=(
                            f"Review {dc.label} on the operator dossier. "
                            f"Deviation: {cmp.deviation}. Confidence: {cmp.confidence}."
                        ),
                        dedup_key=f"beh-change:{op_id}:{cmp.key}",
                        meta={
                            "operator_id": op_id,
                            "fact_key": cmp.key,
                            "deviation": cmp.deviation,
                            "historical": cmp.historical_value,
                            "current": cmp.current_value,
                        },
                    ))

    except Exception as exc:
        print(f"[INBOX] behaviour change items error: {exc}")

    return items


# ── Operator similarity inbox items ──────────────────────────────────────────

def _similarity_items() -> List[InboxItem]:
    """
    Emit inbox items when operators are found to be highly similar.

    Reads from the pre-computed snapshot only — never triggers computation.
    Only emits for HIGH or VERY_HIGH similarity with sufficient confidence.
    """
    items: List[InboxItem] = []
    try:
        from src.ops.similarity_routes import _get_engine
        from src.ops.operator_similarity import OperatorSimilarityResult

        snap = _get_engine().current_snapshot()
        if not snap.available:
            return items

        seen_pairs: set[frozenset] = set()
        HIGH_BANDS = {"HIGH", "VERY_HIGH"}

        for op_id, results in snap.results.items():
            for r in results:
                if r.similarity_band not in HIGH_BANDS:
                    continue
                if r.confidence == "INSUFFICIENT":
                    continue
                pair_key = frozenset({r.operator_a, r.operator_b})
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                priority = HIGH if r.similarity_band == "VERY_HIGH" else MEDIUM
                top_reason = r.reasons[0] if r.reasons else "Multiple behavioural dimensions align."

                items.append(_item(
                    operation_id=f"operator-{r.operator_a}",
                    subject_type=SUBJECT_BEHAVIOUR,
                    priority=priority,
                    headline="Operator behaviour similarity identified",
                    summary=(
                        f"Operators {r.operator_a[:12]}… and {r.operator_b[:12]}… "
                        f"show {r.similarity_band} similarity across "
                        f"{len(r.dimensions_compared)} behavioural dimension(s). "
                        f"{top_reason}"
                    ),
                    reason=top_reason,
                    recommended_action=(
                        f"Review operator dossiers for {r.operator_a[:12]}… and "
                        f"{r.operator_b[:12]}… Similarity band: {r.similarity_band}. "
                        f"Confidence: {r.confidence}. "
                        f"This is analytical context — similarity does not imply shared control."
                    ),
                    dedup_key=f"similarity:{min(r.operator_a, r.operator_b)}:{max(r.operator_a, r.operator_b)}",
                    meta={
                        "operator_a":       r.operator_a,
                        "operator_b":       r.operator_b,
                        "similarity_band":  r.similarity_band,
                    },
                ))
    except Exception as exc:
        print(f"[INBOX] similarity items error: {exc}")

    return items


# ── Intelligence Assessment inbox items ──────────────────────────────────────

_ASSESS_PRIORITY_MAP = {
    # assessment_type → (min_confidence_to_emit, priority)
    "CAMPAIGN_EXPANSION":    ("MEDIUM", HIGH),
    "CAMPAIGN_CONTRACTION":  ("MEDIUM", HIGH),
    "FUNDING_SHIFT":         ("MEDIUM", HIGH),
    "INFRASTRUCTURE_SHIFT":  ("LOW",    MEDIUM),
    "BEHAVIOUR_CHANGE":      ("LOW",    MEDIUM),
    "SIMILARITY_OBSERVED":   ("MEDIUM", MEDIUM),
    "RETURN_TO_BASELINE":    ("MEDIUM", LOW),
    "BASELINE_BEHAVIOUR":    ("HIGH",   INFO),
}


def _assessment_items() -> List[InboxItem]:
    """
    Emit inbox items for meaningful assessments.

    Does NOT emit INSUFFICIENT_EVIDENCE assessments.
    Does NOT emit weak assessments (below configured minimum confidence).
    Reads operators from ops DB then calls AssessmentEngine once per operator.
    """
    items: List[InboxItem] = []
    _CONF_ORDER = {"INSUFFICIENT": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
    try:
        from src.core.db import OPS_DB_PATH, LIVE_DB_PATH
        from src.ops.behaviour_engine import BehaviourEngine
        from src.ops.behaviour_change import BehaviourChangeEngine
        from src.ops.operator_similarity import OperatorSimilarityEngine
        import sqlite3

        with sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True) as conn:
            rows = conn.execute(
                "SELECT operator_id FROM operators ORDER BY updated_at DESC LIMIT 30"
            ).fetchall()
        operator_ids = [r[0] for r in rows]
        if not operator_ids:
            return items

        engine = AssessmentEngine(
            behaviour_engine  = BehaviourEngine(str(OPS_DB_PATH), str(LIVE_DB_PATH)),
            change_engine     = BehaviourChangeEngine(str(OPS_DB_PATH), str(LIVE_DB_PATH)),
            similarity_engine = OperatorSimilarityEngine(str(OPS_DB_PATH)),
        )

        for op_id in operator_ids:
            bundle = engine.assess(op_id)
            if not bundle.available:
                continue
            for assessment in bundle.assessments:
                atype = assessment.assessment_type
                if atype == "INSUFFICIENT_EVIDENCE":
                    continue
                min_conf, priority = _ASSESS_PRIORITY_MAP.get(atype, ("HIGH", LOW))
                if _CONF_ORDER.get(assessment.confidence, 0) < _CONF_ORDER.get(min_conf, 0):
                    continue
                dedup = f"assessment:{op_id}:{atype}"
                items.append(InboxItem(
                    operation_id      = op_id,
                    subject_type      = SUBJECT_ASSESSMENT,
                    priority          = priority,
                    headline          = assessment.headline,
                    summary           = assessment.summary,
                    reason            = (
                        f"{len(assessment.supporting_evidence)} supporting, "
                        f"{len(assessment.contradictory_evidence)} contradictory evidence items."
                    ),
                    recommended_action = "Review the Assessment section of the operator dossier.",
                    dedup_key         = dedup,
                    confidence        = float(_CONF_ORDER.get(assessment.confidence, 0)) / 3.0,
                    meta              = {
                        "assessment_type": atype,
                        "evidence_count":  assessment.evidence_count,
                    },
                ))
    except Exception as exc:
        print(f"[INBOX] assessment adapter error: {exc}")
    return items


# ── Lifecycle Forecast inbox items ───────────────────────────────────────────

def _forecast_items() -> List[InboxItem]:
    """
    Emit inbox items for MEDIUM/HIGH confidence forecasts of meaningful transitions.

    Rules:
      — Only MEDIUM or HIGH confidence forecasts emitted.
      — INSUFFICIENT_EVIDENCE forecasts suppressed.
      — STATE_STABLE forecasts suppressed (not newsworthy).
      — ARMED → ACTIVE forecasts emit at HIGH priority.
      — Dedup key prevents spam for the same operator/transition pair.
    """
    items: List[InboxItem] = []
    _CONF_ORDER = {"INSUFFICIENT": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
    try:
        from src.core.db import OPS_DB_PATH, LIVE_DB_PATH
        from src.ops.behaviour_engine import BehaviourEngine
        from src.ops.behaviour_change import BehaviourChangeEngine
        from src.ops.operator_similarity import OperatorSimilarityEngine
        from src.ops.assessment_engine import AssessmentEngine

        operator_ids = ForecastEngine.list_operator_ids(str(OPS_DB_PATH), limit=30)
        if not operator_ids:
            return items

        beh  = BehaviourEngine(str(OPS_DB_PATH), str(LIVE_DB_PATH))
        chg  = BehaviourChangeEngine(str(OPS_DB_PATH), str(LIVE_DB_PATH))
        sim  = OperatorSimilarityEngine(str(OPS_DB_PATH))
        asm  = AssessmentEngine(beh, chg, sim)
        engine = ForecastEngine(asm, beh, chg)

        for op_id in operator_ids:
            # Use OBSERVING as default state — callers with lifecycle data should pass it explicitly
            forecast = engine.forecast(op_id, "OBSERVING")
            if not forecast:
                continue
            ftype = forecast.forecast_type
            if ftype in ("INSUFFICIENT_EVIDENCE", "STATE_STABLE"):
                continue
            if _CONF_ORDER.get(forecast.confidence, 0) < _CONF_ORDER["MEDIUM"]:
                continue

            is_armed_active = (
                forecast.current_state == "ARMED"
                and forecast.predicted_next_state == "ACTIVE"
            )
            priority  = HIGH if is_armed_active else MEDIUM
            dedup_key = f"forecast:{op_id}:{forecast.current_state}:{forecast.predicted_next_state}"

            items.append(InboxItem(
                operation_id      = op_id,
                subject_type      = SUBJECT_FORECAST,
                priority          = priority,
                headline          = (
                    f"Forecast: {forecast.current_state} → {forecast.predicted_next_state} "
                    f"({forecast.confidence})"
                ),
                summary           = forecast.forecast_reason,
                reason            = (
                    f"Window: {forecast.expected_window}. "
                    f"{forecast.historical_observations} historical observations."
                ),
                recommended_action = "Review the Forecast section of the operator dossier.",
                dedup_key         = dedup_key,
                confidence        = float(_CONF_ORDER.get(forecast.confidence, 0)) / 3.0,
                meta              = {
                    "forecast_type":   ftype,
                    "current_state":   forecast.current_state,
                    "next_state":      forecast.predicted_next_state,
                    "window":          forecast.expected_window,
                },
            ))
    except Exception as exc:
        print(f"[INBOX] forecast adapter error: {exc}")
    return items


# ── Dispatch ──────────────────────────────────────────────────────────────────

_ITEM_BUILDERS = {
    "watchtower":            _watchtower_items,
    "launcher-observatory":  _launcher_observatory_items,
    "buy-swarm-observatory": _buy_swarm_observatory_items,
}


def refresh_inbox(store: InboxStore) -> int:
    """
    Pull current lifecycle snapshots for all operations and emit/update
    inbox items.  Returns the total number of items written.
    """
    snapshots = get_all_lifecycles()
    total = 0
    for snap in snapshots:
        builder = _ITEM_BUILDERS.get(snap.operation_id)
        if not builder:
            continue
        try:
            items = builder(snap)
            for item in items:
                store.upsert(item)
                total += 1
        except Exception as exc:
            print(f"[INBOX] adapter error for {snap.operation_id}: {exc}")

    # Operator resolution items (independent of lifecycle snapshots)
    try:
        for item in _operator_resolution_items():
            store.upsert(item)
            total += 1
    except Exception as exc:
        print(f"[INBOX] operator adapter error: {exc}")

    # Behaviour intelligence items
    try:
        for item in _behaviour_items():
            store.upsert(item)
            total += 1
    except Exception as exc:
        print(f"[INBOX] behaviour adapter error: {exc}")

    # Behaviour change items
    try:
        for item in _behaviour_change_items():
            store.upsert(item)
            total += 1
    except Exception as exc:
        print(f"[INBOX] behaviour change adapter error: {exc}")

    # Operator similarity items (reads snapshot only — no computation triggered)
    try:
        for item in _similarity_items():
            store.upsert(item)
            total += 1
    except Exception as exc:
        print(f"[INBOX] similarity adapter error: {exc}")

    # Intelligence Assessment items
    try:
        for item in _assessment_items():
            store.upsert(item)
            total += 1
    except Exception as exc:
        print(f"[INBOX] assessment adapter error: {exc}")

    # Lifecycle Forecast items (MEDIUM+ confidence only)
    try:
        for item in _forecast_items():
            store.upsert(item)
            total += 1
    except Exception as exc:
        print(f"[INBOX] forecast adapter error: {exc}")

    return total
