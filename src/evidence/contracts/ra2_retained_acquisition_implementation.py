"""RA2 local contracts for bounded replay qualification.

Fixture-only contracts model hot/cold split and deterministic replay checks.
"""

from __future__ import annotations

import base64
import hashlib
import json
import statistics
from collections import Counter
from dataclasses import dataclass
from typing import Deque
from typing import Any

from src.evidence.contracts.ra1_retained_acquisition_architecture import (
    RetentionBudget,
    RetentionLedger,
    RetentionResourcePolicy,
    RetainedObservationEvent,
    fresh_ledger,
    retention_decision,
    RESOURCE_STATE_CRITICAL,
    RESOURCE_STATE_DEGRADED,
    RESOURCE_STATE_NORMAL,
    OUTCOME_RECORD_FULL_PAYLOAD,
    OUTCOME_RECORD_METADATA_ONLY,
)


RA4_C1_SCHEMA_VERSION = "ra4_c1_hot_plateau_readiness.v1"
RA4_C1_HOT_STORE_CAP_BYTES = 5 * 1024 * 1024 * 1024

SCHEMA_VERSION = "ra2_retained_acquisition_replay_contract.v1"

CLASSIFICATION_CONTENT_STABLE = "CONTENT_STABLE_FOR_SAMPLED_ARTIFACT"
CLASSIFICATION_VARIANT = "VARIES_WITHIN_ARTIFACT_GROUP"
CLASSIFICATION_NOT_OBSERVABLE = "NOT_OBSERVABLE"

REPLAYABLE = "REPLAYABLE"
PARTIAL_REPLAY = "REPLAY_PARTIAL"
NOT_REPLAYABLE = "NOT_REPLAYABLE"

OUTCOME_ARCHIVE_BEFORE_REPLAY_OK = "ARCHIVE_BEFORE_REPLAY_ORDER_OK"
OUTCOME_ARCHIVE_BEFORE_REPLAY_FAILED = "ARCHIVE_BEFORE_REPLAY_ORDER_VIOLATED"

RA2_SCHEMA_VERSION = "ra2_retained_acquisition_replay_preflight.v1"


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _safe_json(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _percentile(values: list[int], pct: int) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = int((pct / 100.0) * (len(ordered) - 1))
    return ordered[idx]


def _response_signature(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "response_status": int(payload.get("response_status") or 0),
        "response_data": payload.get("response_data"),
        "response_text": payload.get("response_text"),
        "response_headers": dict(payload.get("response_headers", {})),
        "artifact_representation": payload.get("artifact_representation"),
    }


def _raw_body_info(payload: dict[str, Any]) -> tuple[str | None, int]:
    raw_body_b64 = payload.get("raw_body_base64")
    if not isinstance(raw_body_b64, str):
        return None, 0
    try:
        raw = base64.b64decode(raw_body_b64.encode())
        return hashlib.sha256(raw).hexdigest(), len(raw)
    except Exception:
        return None, 0


def _classify_values(values: set[Any]) -> str:
    present = {v for v in values if v is not None}
    if not present:
        return CLASSIFICATION_NOT_OBSERVABLE
    return CLASSIFICATION_CONTENT_STABLE if len(present) == 1 else CLASSIFICATION_VARIANT


@dataclass(frozen=True)
class RA2ReplayCheck:
    rowid: int
    observation_id: str
    acquisition_id: str
    correlation_id: str
    launch_mint: str | None
    full_payload_len: int
    hot_row_len: int
    replay_state: str
    replay_digest: str | None
    replay_reason: list[str]
    preserved_acquisition_identity: bool


@dataclass(frozen=True)
class RetentionPlateauDecision:
    rowid: int
    observation_id: str
    correlation_id: str
    launch_mint: str | None
    action: str
    action_reason: str
    full_payload_bytes: int
    hot_payload_bytes: int
    stored_bytes: int
    archive_before_replay_status: str
    kept: bool


def parse_rows(rows: list[tuple[int, str]]) -> list[tuple[int, dict[str, Any]]]:
    parsed: list[tuple[int, dict[str, Any]]] = []
    for rowid, payload_json in rows:
        payload = _safe_json(payload_json)
        if payload is None:
            continue
        payload["rowid"] = rowid
        parsed.append((rowid, payload))
    return parsed


def build_hot_row(payload: dict[str, Any], *, metadata_bytes_per_observation: int = 2048) -> dict[str, Any]:
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    url = payload.get("url", "")
    request_payload = payload.get("request_payload")
    return {
        "schema_version": SCHEMA_VERSION,
        "observation_id": payload.get("observation_id"),
        "acquisition_id": payload.get("acquisition_id"),
        "correlation_id": payload.get("correlation_id") or metadata.get("correlation_id"),
        "launch_mint": payload.get("launch_mint") or metadata.get("launch"),
        "http_method": payload.get("http_method"),
        "url_or_url_digest": hashlib.sha256(str(url).encode()).hexdigest()[:16],
        "request_payload_digest": _digest(request_payload) if request_payload is not None else None,
        "response_status": payload.get("response_status"),
        "artifact_digest": payload.get("artifact_digest"),
        "artifact_size_bytes": payload.get("artifact_size_bytes"),
        "artifact_compressed_bytes": payload.get("artifact_compressed_bytes"),
        "content_type": payload.get("content_type"),
        "payload_size_estimate_bytes": len(_canonical_json(_response_signature(payload))),
        "payload_bytes_hot_allocation_cap": metadata_bytes_per_observation,
    }


def verify_replay_observation(payload: dict[str, Any]) -> tuple[str, str | None, list[str]]:
    reasons: list[str] = []
    if not isinstance(payload.get("response_headers"), dict):
        reasons.append("missing_response_headers")
    for key in ("acquisition_id", "correlation_id", "artifact_digest"):
        if not payload.get(key):
            reasons.append(f"missing_{key}")
    if not payload.get("response_status") and payload.get("response_status") != 0:
        reasons.append("missing_response_status")
    if reasons:
        return NOT_REPLAYABLE, None, reasons
    return REPLAYABLE, _digest(_response_signature(payload)), reasons


def evaluate_ordering(archived: bool, replayed: bool) -> tuple[str, str]:
    return (
        (OUTCOME_ARCHIVE_BEFORE_REPLAY_OK, "archive_before_replay")
        if archived and replayed
        else (OUTCOME_ARCHIVE_BEFORE_REPLAY_FAILED, "archive_after_replay_or_missing")
    )


def _resource_state(free_bytes: int, policy: RetentionResourcePolicy) -> str:
    return (
        RESOURCE_STATE_NORMAL
        if free_bytes >= policy.normal_min_free_bytes
        else RESOURCE_STATE_DEGRADED
        if free_bytes >= policy.critical_min_free_bytes
        else RESOURCE_STATE_CRITICAL
    )


def analyze_rows(
    rows: list[tuple[int, str]],
    *,
    budget: RetentionBudget,
    policy: RetentionResourcePolicy,
    free_bytes: int,
) -> tuple[list[RA2ReplayCheck], dict[str, Any], dict[str, Any]]:
    parsed = parse_rows(rows)
    if not parsed:
        return [], {"state": "empty"}, {
            "sample_size": 0,
            "sample_full_bytes_median": 0,
            "sample_hot_bytes_median": 0,
            "sample_full_bytes_p95": 0,
            "sample_hot_bytes_p95": 0,
            "mean_full_bytes": 0.0,
            "mean_hot_bytes": 0.0,
            "hot_to_full_ratio": 0.0,
        }

    if free_bytes < policy.hard_floor_bytes:
        raise RuntimeError("free_bytes_below_hard_floor")

    # one hot row per acquisition_id
    seen_acquisition_ids: set[str] = set()
    replay_checks: list[RA2ReplayCheck] = []
    by_correlation: Counter[str] = Counter()
    by_mint: Counter[str] = Counter()
    acqui_per_correlation: dict[str, set[str]] = {}
    artifact_per_correlation: dict[str, set[str]] = {}
    artifact_per_mint: dict[str, set[str]] = {}
    artifact_groups: dict[str, list[dict[str, Any]]] = {}

    ledger = fresh_ledger(parsed[0][1].get("metadata", {}).get("timestamp", "1970-01-01T00:00:00Z"))
    retained_count = 0

    for rowid, payload in parsed:
        acquisition_id = str(payload.get("acquisition_id") or "")
        correlation_id = str(payload.get("correlation_id") or payload.get("metadata", {}).get("correlation_id") or "")
        launch_mint = str(payload.get("launch_mint") or payload.get("metadata", {}).get("launch") or "")
        artifact_digest = payload.get("artifact_digest")

        hot_row = build_hot_row(payload)
        replay_state, replay_digest, reasons = verify_replay_observation(payload)

        if not free_bytes:
            resource = RESOURCE_STATE_CRITICAL
        else:
            resource = _resource_state(free_bytes, policy)
        event = RetainedObservationEvent(
            f"{rowid}:{acquisition_id}",
            correlation_id,
            launch_mint,
            int(payload.get("artifact_size_bytes", 0) or 0),
            str(payload.get("metadata", {}).get("timestamp", "1970-01-01T00:00:00Z")),
            replay_state == REPLAYABLE,
        )
        outcome, _reason, next_ledger = retention_decision(
            ledger,
            event,
            policy,
            budget,
            free_bytes,
        )
        if outcome == OUTCOME_RECORD_FULL_PAYLOAD:
            ledger = next_ledger
            retained_count += 1

        # Preserve one output record per acquisition_id while still preserving fan-out.
        preserved = acquisition_id not in seen_acquisition_ids
        if preserved:
            seen_acquisition_ids.add(acquisition_id)

        if artifact_digest:
            artifact_groups.setdefault(str(artifact_digest), []).append(payload)
            artifact_per_correlation.setdefault(correlation_id, set()).add(str(artifact_digest))
            artifact_per_mint.setdefault(launch_mint, set()).add(str(artifact_digest))
        if correlation_id:
            by_correlation[correlation_id] += 1
            acqui_per_correlation.setdefault(correlation_id, set()).add(acquisition_id)
        if launch_mint:
            by_mint[launch_mint] += 1

        replay_checks.append(
            RA2ReplayCheck(
                rowid=rowid,
                observation_id=str(payload.get("observation_id", "")),
                acquisition_id=acquisition_id,
                correlation_id=correlation_id,
                launch_mint=payload.get("launch_mint") or payload.get("metadata", {}).get("launch"),
                full_payload_len=len(_canonical_json(payload)),
                hot_row_len=len(_canonical_json(hot_row)),
                replay_state=replay_state,
                replay_digest=replay_digest,
                replay_reason=reasons,
                preserved_acquisition_identity=preserved,
            )
        )

    full_lengths = [item.full_payload_len for item in replay_checks]
    hot_lengths = [item.hot_row_len for item in replay_checks]

    raw_body_stability = Counter()
    content_stability = {
        CLASSIFICATION_CONTENT_STABLE: 0,
        CLASSIFICATION_VARIANT: 0,
        CLASSIFICATION_NOT_OBSERVABLE: 0,
    }
    field_stability: dict[str, dict[str, int]] = {
        "raw_body_digest": {CLASSIFICATION_CONTENT_STABLE: 0, CLASSIFICATION_VARIANT: 0, CLASSIFICATION_NOT_OBSERVABLE: 0},
        "response_text_digest": {CLASSIFICATION_CONTENT_STABLE: 0, CLASSIFICATION_VARIANT: 0, CLASSIFICATION_NOT_OBSERVABLE: 0},
        "response_data_digest": {CLASSIFICATION_CONTENT_STABLE: 0, CLASSIFICATION_VARIANT: 0, CLASSIFICATION_NOT_OBSERVABLE: 0},
        "artifact_representation_digest": {CLASSIFICATION_CONTENT_STABLE: 0, CLASSIFICATION_VARIANT: 0, CLASSIFICATION_NOT_OBSERVABLE: 0},
        "response_status": {CLASSIFICATION_CONTENT_STABLE: 0, CLASSIFICATION_VARIANT: 0, CLASSIFICATION_NOT_OBSERVABLE: 0},
        "response_headers_digest": {CLASSIFICATION_CONTENT_STABLE: 0, CLASSIFICATION_VARIANT: 0, CLASSIFICATION_NOT_OBSERVABLE: 0},
        "content_type": {CLASSIFICATION_CONTENT_STABLE: 0, CLASSIFICATION_VARIANT: 0, CLASSIFICATION_NOT_OBSERVABLE: 0},
    }
    identity_variance = {
        "acquisition_id": 0,
        "correlation_id": 0,
        "metadata_timestamp": 0,
        "request_payload": 0,
        "url": 0,
        "response_status": 0,
        "other_metadata": 0,
    }
    repeated_artifact_groups: list[dict[str, Any]] = []
    repeated_rows = 0
    repeated_payload_bytes = 0
    repeated_max_rows = 0

    for artifact_digest, members in artifact_groups.items():
        if len(members) <= 1:
            continue
        repeated_rows += len(members)
        repeated_payload_bytes += sum(len(_canonical_json(item)) for item in members)
        repeated_max_rows = max(repeated_max_rows, len(members))

        fields = {
            "raw_body_digest": {_raw_body_info(item)[0] for item in members},
            "response_text_digest": {_digest(item.get("response_text")) for item in members},
            "response_data_digest": {_digest(item.get("response_data")) for item in members},
            "artifact_representation_digest": {_digest(item.get("artifact_representation")) for item in members},
            "response_status": {item.get("response_status") for item in members},
            "response_headers_digest": {_digest(item.get("response_headers", {})) for item in members},
            "content_type": {item.get("content_type") for item in members},
        }
        fields_by_artifact = {k: _classify_values(v) for k, v in fields.items()}
        for field_name, cls in fields_by_artifact.items():
            field_stability[field_name][cls] += 1
        raw_body_stability = _classify_values(fields["raw_body_digest"])
        raw_body_stability
        content_stability[fields_by_artifact.get("raw_body_digest", CLASSIFICATION_NOT_OBSERVABLE)] += 1
        content_stability[fields_by_artifact.get("response_text_digest", CLASSIFICATION_NOT_OBSERVABLE)] += 1

        by_fields = {
            "acquisition_id": {item.get("acquisition_id") for item in members},
            "correlation_id": {item.get("correlation_id") for item in members},
            "metadata_timestamp": {item.get("metadata", {}).get("timestamp") for item in members},
            "request_payload": {_digest(item.get("request_payload")) for item in members},
            "url": {item.get("url") for item in members},
            "response_status": {item.get("response_status") for item in members},
            "other_metadata": {item.get("metadata", {}).get("purpose") for item in members}
            | {item.get("metadata", {}).get("provider") for item in members},
        }
        for field, values in by_fields.items():
            if len({v for v in values if v is not None}) > 1:
                identity_variance[field] += 1

        repeated_artifact_groups.append({
            "artifact_digest": artifact_digest,
            "frequency": len(members),
            "payload_json_rows": len(members),
            "content_stability": raw_body_stability,
            "identity_variance": {k: len({v for v in v if v is not None}) > 1 for k, v in by_fields.items()},
        })

    mean_full = statistics.mean(full_lengths) if full_lengths else 0.0
    mean_hot = statistics.mean(hot_lengths) if hot_lengths else 0.0

    summary = {
        "sample_size": len(replay_checks),
        "sample_full_bytes_median": _percentile(full_lengths, 50),
        "sample_hot_bytes_median": _percentile(hot_lengths, 50),
        "sample_full_bytes_p95": _percentile(full_lengths, 95),
        "sample_hot_bytes_p95": _percentile(hot_lengths, 95),
        "mean_full_bytes": mean_full,
        "mean_hot_bytes": mean_hot,
        "hot_to_full_ratio": (mean_hot / mean_full) if mean_full else 0.0,
        "bounded_duplication_label": "BOUNDED_DUPLICATION_ESTIMATE",
    }

    metrics = {
        "state": "ok",
        "resource_state": _resource_state(free_bytes, policy),
        "resource_rejections": len([item for item in replay_checks if item.replay_state == NOT_REPLAYABLE]),
        "retained_events": retained_count,
        "replayable_events": len([item for item in replay_checks if item.replay_state == REPLAYABLE]),
        "not_replayable_events": len([item for item in replay_checks if item.replay_state == NOT_REPLAYABLE]),
        "unique_acquisition_ids": len(seen_acquisition_ids),
        "stats": {
            "observations_per_correlation": _percentile_series(list(by_correlation.values())),
            "observations_per_mint": _percentile_series(list(by_mint.values())),
            "acquisitions_per_correlation": _percentile_series([len(v) for v in acqui_per_correlation.values()]),
            "artifacts_per_correlation": _percentile_series([len(v) for v in artifact_per_correlation.values()]),
            "artifacts_per_mint": _percentile_series([len(v) for v in artifact_per_mint.values()]),
        },
        "fanout": {
            "observations_per_correlation": by_correlation.most_common(10),
            "observations_per_mint": by_mint.most_common(10),
            "acquisitions_per_correlation": sorted(((k, len(v)) for k, v in acqui_per_correlation.items()), key=lambda kv: kv[1], reverse=True)[:10],
            "artifacts_per_correlation": sorted(((k, len(v)) for k, v in artifact_per_correlation.items()), key=lambda kv: kv[1], reverse=True)[:10],
            "artifacts_per_mint": sorted(((k, len(v)) for k, v in artifact_per_mint.items()), key=lambda kv: kv[1], reverse=True)[:10],
        },
        "repeated_artifact_groups": repeated_artifact_groups,
        "content_stability": content_stability,
        "field_stability": field_stability,
        "identity_variance": identity_variance,
        "observations_in_repeated_artifact_groups": repeated_rows,
        "repeated_artifact_payload_bytes": repeated_payload_bytes,
        "max_artifact_repeat_count": repeated_max_rows,
        "sampled_payload_bytes": sum(full_lengths),
    }

    return replay_checks, summary, metrics


def _cold_archive_verification(payload: dict[str, Any]) -> bool:
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        for key in ("cold_archive_verified", "archive_verified", "cold_archive_state"):
            if key in metadata:
                return bool(metadata.get(key))
    return True


def simulate_hot_store_plateau(
    rows: list[tuple[int, str]],
    *,
    budget: RetentionBudget,
    policy: RetentionResourcePolicy,
    free_bytes: int,
    hot_store_cap_bytes: int = RA4_C1_HOT_STORE_CAP_BYTES,
) -> dict[str, Any]:
    parsed = parse_rows(rows)
    if free_bytes < policy.hard_floor_bytes:
        return {
            "status": "HOLD",
            "verdict": "HOLD_RESOURCE_LIMIT",
            "blockers": ["HOLD_RESOURCE_LIMIT"],
            "decisions": [],
            "plateau_metrics": {
                "hot_store_cap_bytes": hot_store_cap_bytes,
                "free_bytes": free_bytes,
            },
        }
    if not parsed:
        return {
            "status": "PASS",
            "verdict": "READY_HOT_PLATEAU_QUALIFIED",
            "decisions": [],
            "plateau_metrics": {
                "hot_store_cap_bytes": hot_store_cap_bytes,
                "hot_store_bytes": 0,
                "retired_full_rows": 0,
                "retired_unverified_rows": 0,
                "archive_before_replay_failures": 0,
            },
        }

    if hot_store_cap_bytes <= 0:
        raise ValueError("hot_store_cap_bytes must be positive")

    replay_checks, summary, metrics = analyze_rows(rows, budget=budget, policy=policy, free_bytes=free_bytes)
    ledger = fresh_ledger(parsed[0][1].get("metadata", {}).get("timestamp", "1970-01-01T00:00:00Z"))

    hot_window: list[tuple[int, int, bool]] = []
    hot_store_bytes = 0
    retired_full_rows = 0
    retired_unverified_rows = 0
    archive_failures = 0
    decisions: list[RetentionPlateauDecision] = []

    for rowid, payload in parsed:
        observation_id = str(payload.get("observation_id", ""))
        full_payload = _canonical_json(payload)
        full_payload_bytes = len(full_payload)
        hot_payload = _canonical_json(build_hot_row(payload, metadata_bytes_per_observation=budget.metadata_bytes_per_observation))
        hot_payload_bytes = len(hot_payload)

        acquisition_id = str(payload.get("acquisition_id") or "")
        correlation_id = str(payload.get("correlation_id") or payload.get("metadata", {}).get("correlation_id") or "")
        launch_mint = str(payload.get("launch_mint") or payload.get("metadata", {}).get("launch") or "")

        event = RetainedObservationEvent(
            f"{rowid}:{observation_id}",
            correlation_id,
            launch_mint,
            full_payload_bytes,
            str(payload.get("metadata", {}).get("timestamp", "1970-01-01T00:00:00Z")),
            cold_archive_verified=_cold_archive_verification(payload),
        )

        action, reason, next_ledger = retention_decision(ledger, event, policy, budget, free_bytes)
        ledger = next_ledger

        archive_before_replay_status = OUTCOME_ARCHIVE_BEFORE_REPLAY_OK
        kept = True
        stored_bytes = hot_payload_bytes

        if action == OUTCOME_RECORD_FULL_PAYLOAD:
            while hot_store_bytes + full_payload_bytes > hot_store_cap_bytes and hot_window:
                retired_rowid, retired_bytes, archived = hot_window[0]
                hot_window.pop(0)
                if not archived:
                    archive_before_replay_status = OUTCOME_ARCHIVE_BEFORE_REPLAY_FAILED
                    archive_failures += 1
                    retired_unverified_rows += 1
                    break
                hot_store_bytes -= retired_bytes
                retired_full_rows += 1

            if archive_before_replay_status == OUTCOME_ARCHIVE_BEFORE_REPLAY_FAILED:
                action = OUTCOME_RECORD_METADATA_ONLY
                reason = "archive_before_replay_failed"
                archived = _cold_archive_verification(payload)
                if not archived:
                    kept = False
            else:
                kept = True
                archived = _cold_archive_verification(payload)
                hot_store_bytes += full_payload_bytes
                stored_bytes = full_payload_bytes
                hot_window.append((rowid, full_payload_bytes, archived))
        else:
            archived = _cold_archive_verification(payload)

        decisions.append(
            RetentionPlateauDecision(
                rowid=rowid,
                observation_id=observation_id,
                correlation_id=correlation_id,
                launch_mint=payload.get("metadata", {}).get("launch") if isinstance(payload.get("metadata"), dict) else launch_mint,
                action=action,
                action_reason=reason,
                full_payload_bytes=full_payload_bytes,
                hot_payload_bytes=hot_payload_bytes,
                stored_bytes=stored_bytes,
                archive_before_replay_status=archive_before_replay_status,
                kept=kept,
            )
        )

    full_bytes = [item.full_payload_len for item in replay_checks]
    hot_bytes = [item.hot_row_len for item in replay_checks]
    bounded_duplication_ratio = 0.0
    if sum(full_bytes):
        bounded_duplication_ratio = 1.0 - (sum(hot_bytes) / sum(full_bytes))

    return {
        "status": "PASS" if archive_failures == 0 else "HOLD",
        "verdict": "READY_HOT_PLATEAU_QUALIFIED" if archive_failures == 0 else "HOLD_ARCHIVE_PRE_RETIRED",
        "decisions": [d.__dict__ for d in decisions],
        "plateau_metrics": {
            "hot_store_cap_bytes": hot_store_cap_bytes,
            "hot_store_bytes": hot_store_bytes,
            "retired_full_rows": retired_full_rows,
            "retired_unverified_rows": retired_unverified_rows,
            "archive_before_replay_failures": archive_failures,
            "bounded_duplication_ratio": bounded_duplication_ratio,
        },
        "summary": summary,
        "metrics": metrics,
        "replay_checks": [check.__dict__ for check in replay_checks],
    }


def _percentile_series(values: list[int]) -> dict[str, int | None]:
    return {
        "median": _percentile(values, 50),
        "p90": _percentile(values, 90),
        "p95": _percentile(values, 95),
        "p99": _percentile(values, 99),
        "max": max(values) if values else None,
    }


def estimate_growth(sample_summary: dict[str, Any], observed_observations: int, observed_db_bytes: int, *, daily_budget_bytes: int) -> dict[str, Any]:
    if sample_summary["sample_size"] <= 0:
        return {"bounded_growth_estimate_label": "UNRESOLVED", "sample_size": 0}
    observed_obs_per_day = observed_observations / 7.0
    observed_gb_per_day = max(0.0, observed_db_bytes / max(observed_observations, 1) * observed_obs_per_day / (1024 ** 3))
    ratio = max(0.0, sample_summary["hot_to_full_ratio"])
    split_gb_per_day = observed_gb_per_day * ratio
    cap_gb_per_day = daily_budget_bytes / (1024 ** 3)
    projected_ra2_daily_gb = min(split_gb_per_day, cap_gb_per_day)
    return {
        "bounded_growth_estimate_label": "BOUNDED_DUPLICATION_ESTIMATE",
        "label": "bounded_sample_only",
        "observed_full_gb_per_day": observed_gb_per_day,
        "sample_mean_full_bytes": sample_summary["mean_full_bytes"],
        "sample_mean_hot_bytes": sample_summary["mean_hot_bytes"],
        "hot_to_full_ratio": ratio,
        "projected_ra2_daily_gb_from_split": split_gb_per_day,
        "daily_budget_gb": cap_gb_per_day,
        "projected_ra2_daily_gb": projected_ra2_daily_gb,
        "projected_ra2_monthly_gb": projected_ra2_daily_gb * 30.0,
        "estimated_reduction_ratio": max(0.0, 1.0 - (projected_ra2_daily_gb / max(observed_gb_per_day, 1e-9))),
    }
