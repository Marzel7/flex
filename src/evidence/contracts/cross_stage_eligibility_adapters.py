"""EB1.0C exact frozen-document adapters into EB1.0A."""

from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Mapping, Tuple

from .cross_stage_eligibility import AUTHORITY_LANES, StageEligibility, project_cross_stage_eligibility


ADAPTER_VERSION = "eb1.0c.v1"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{7,64}$")


class CrossStageEligibilityAdapterError(ValueError):
    """Named fail-closed EB1.0C adapter error."""


def _fail(code: str) -> None:
    raise CrossStageEligibilityAdapterError(f"EB1_0C_{code}")


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _fail(f"INVALID_{name.upper()}")
    return value


def _exact(value: Mapping[str, object], fields: set[str], name: str) -> None:
    if set(value) != fields:
        _fail(f"{name.upper()}_SCHEMA_DRIFT")


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail(f"INVALID_{name.upper()}")
    return value


def _revision(value: object) -> str:
    if not isinstance(value, str) or not _REVISION.fullmatch(value):
        _fail("INVALID_ENGINEERING_REVISION")
    return value


def _bundle_digest(hashes: Mapping[str, object]) -> str:
    value = hashes.get("bundle_digest")
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        _fail("INVALID_BUNDLE_DIGEST")
    return value


def _record(stage: str, schema: str, digest: str, scope: str, revision: str, total: int, observed: int, missing: int, conflicts: int, completeness: str, provenance_material: object) -> dict[str, object]:
    return {
        "upstream_stage": stage,
        "bundle_schema_version": schema,
        "bundle_digest": digest,
        "cohort_or_window_identity": scope,
        "engineering_revision": revision,
        "authority_lane": AUTHORITY_LANES[stage],
        "applicable": True,
        "total_count": total,
        "observed_count": observed,
        "missing_count": missing,
        "conflicting_count": conflicts,
        "completeness_state": completeness,
        "provenance_digest": _digest({"adapter_version": ADAPTER_VERSION, "stage": stage, "material": provenance_material}),
    }


def adapt_eb0_1j(run: Mapping[str, object], aggregate: Mapping[str, object], hashes: Mapping[str, object], *, engineering_revision: str) -> dict[str, object]:
    _exact(run, {"bundle_schema_version","census_schema_version","run_id","high_water_migrated_at","mint_limit","input_fingerprint","result_digest","source_schema_fingerprints"}, "eb0_1_run")
    required = {"selected_mint_count","eligible_mint_count","excluded_by_cohort_bound_count","corpus_count","mints_without_canonical_evidence_count","observation_count","excluded_observation_count","ignored_explicit_record_count","event_counts","quality_counts","completeness_counts","missing_event_kind_counts","conflicting_observation_count","missing_valuation_count"}
    _exact(aggregate, required, "eb0_1_aggregate")
    if run.get("bundle_schema_version") != "eb0.1j.v1": _fail("EB0_1_VERSION_MISMATCH")
    total = _integer(aggregate["selected_mint_count"], "selected_mint_count")
    observed = _integer(aggregate["corpus_count"], "corpus_count")
    missing = _integer(aggregate["mints_without_canonical_evidence_count"], "missing_count")
    conflicts = _integer(aggregate["conflicting_observation_count"], "conflicts")
    completeness = "COMPLETE" if missing == 0 else ("NOT_OBSERVED" if observed == 0 else "PARTIAL")
    scope = f"high-water:{run['high_water_migrated_at']}:limit:{run['mint_limit']}:input:{run['input_fingerprint']}"
    return _record("EB0.1", "eb0.1j.v1", _bundle_digest(hashes), scope, _revision(engineering_revision), total, observed, missing, conflicts, completeness, {"run": run, "aggregate": aggregate})


def adapt_eb0_2h(run: Mapping[str, object], accounting: Mapping[str, object], hashes: Mapping[str, object]) -> dict[str, object]:
    _exact(run, {"bundle_schema_version","extraction_schema_version","run_id","engineering_revision","input_fingerprint","extraction_result_digest","policies"}, "eb0_2_run")
    _exact(accounting, {"selected_mints","qualified_mints","excluded_mints","policy_count","fact_count","eligible_denominator_count","unknown_count","conflicting_fact_count"}, "eb0_2_accounting")
    if run.get("bundle_schema_version") != "eb0.2h.v1": _fail("EB0_2_VERSION_MISMATCH")
    selected = accounting["selected_mints"]; qualified = accounting["qualified_mints"]; excluded = accounting["excluded_mints"]
    if not isinstance(selected, list) or not isinstance(qualified, list) or not isinstance(excluded, Mapping): _fail("INVALID_EB0_2_ACCOUNTING")
    total, observed, missing = len(selected), len(qualified), len(excluded)
    conflicts = _integer(accounting["conflicting_fact_count"], "conflicts")
    completeness = "COMPLETE" if missing == 0 else ("NOT_OBSERVED" if observed == 0 else "PARTIAL")
    return _record("EB0.2", "eb0.2h.v1", _bundle_digest(hashes), f"run:{run['run_id']}:input:{run['input_fingerprint']}", _revision(run["engineering_revision"]), total, observed, missing, conflicts, completeness, {"run": run, "accounting": accounting})


def adapt_eb0_3g(run: Mapping[str, object], manifest: Mapping[str, object], hashes: Mapping[str, object]) -> dict[str, object]:
    _exact(run, {"bundle_schema_version","run_id","engineering_revision","request_metadata","raw_envelope_digest","manifest_digest"}, "eb0_3_run")
    if run.get("bundle_schema_version") != "eb0.3g.v1": _fail("EB0_3_VERSION_MISMATCH")
    observations = manifest.get("observations"); quality = manifest.get("quality_counts"); completeness_counts = manifest.get("completeness_counts")
    if not isinstance(observations, list) or not isinstance(quality, Mapping) or not isinstance(completeness_counts, Mapping): _fail("INVALID_EB0_3_MANIFEST")
    conflicts = _integer(quality.get("CONFLICTING", 0), "conflicts")
    missing = sum(_integer(value, "completeness_count") for key, value in completeness_counts.items() if key != "COMPLETE")
    total, observed = len(observations), len(observations)
    completeness = "COMPLETE" if missing == 0 else ("NOT_OBSERVED" if observed == 0 else "PARTIAL")
    metadata = _mapping(run["request_metadata"], "request_metadata")
    scope = f"run:{run['run_id']}:mint:{metadata.get('mint')}:window:{metadata.get('from_timestamp_ms')}:{metadata.get('to_timestamp_ms')}"
    return _record("EB0.3", "eb0.3g.v1", _bundle_digest(hashes), scope, _revision(run["engineering_revision"]), total, observed, 0, conflicts, completeness, {"run": run, "manifest_digest": manifest.get("manifest_digest")})


def adapt_eb0_4h(run: Mapping[str, object], accounting: Mapping[str, object], hashes: Mapping[str, object]) -> dict[str, object]:
    _exact(run, {"bundle_schema_version","extraction_schema_version","run_id","engineering_revision","input_fingerprint","extraction_result_digest"}, "eb0_4_run")
    _exact(accounting, {"selected_operation_ids","qualified_operation_ids","excluded_operations","candidate_group_count","fact_count","nomination_count","conflict_count"}, "eb0_4_accounting")
    if run.get("bundle_schema_version") != "eb0.4h.v1": _fail("EB0_4_VERSION_MISMATCH")
    selected = accounting["selected_operation_ids"]; qualified = accounting["qualified_operation_ids"]; excluded = accounting["excluded_operations"]
    if not isinstance(selected, list) or not isinstance(qualified, list) or not isinstance(excluded, Mapping): _fail("INVALID_EB0_4_ACCOUNTING")
    total, observed, missing = len(selected), len(qualified), len(excluded)
    conflicts = _integer(accounting["conflict_count"], "conflicts")
    completeness = "COMPLETE" if missing == 0 else ("NOT_OBSERVED" if observed == 0 else "PARTIAL")
    return _record("EB0.4", "eb0.4h.v1", _bundle_digest(hashes), f"run:{run['run_id']}:input:{run['input_fingerprint']}", _revision(run["engineering_revision"]), total, observed, missing, conflicts, completeness, {"run": run, "accounting": accounting})


def adapt_verified_bundle_summaries(*, eb0_1: Tuple[Mapping[str, object], Mapping[str, object], Mapping[str, object], str], eb0_2: Tuple[Mapping[str, object], Mapping[str, object], Mapping[str, object]], eb0_3: Tuple[Mapping[str, object], Mapping[str, object], Mapping[str, object]], eb0_4: Tuple[Mapping[str, object], Mapping[str, object], Mapping[str, object]]) -> Tuple[StageEligibility, ...]:
    records = (adapt_eb0_1j(*eb0_1[:3], engineering_revision=eb0_1[3]), adapt_eb0_2h(*eb0_2), adapt_eb0_3g(*eb0_3), adapt_eb0_4h(*eb0_4))
    return project_cross_stage_eligibility(records).stages
