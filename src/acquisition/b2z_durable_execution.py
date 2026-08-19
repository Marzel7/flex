"""B2Z-P1: durable, resumable creator-funding acquisition execution.

This module wraps the SAME evidence rules already qualified in
src/acquisition/b2z_execution_boundary.py (resolve_creator,
proves_inbound_sol_funding, the exact 3-request-per-member provider shape)
behind a durability layer modeled directly on B2N's proven
resume_run()/PreDispatchEventLedger pattern
(scripts/run_b2n_p3c_migration_lineage_run.py).

It does NOT modify b2z_execution_boundary.py or its already-qualified
B2ZRunner -- that class and its 11 passing tests are left untouched. This is
a parallel, safer execution path for when B2Z work resumes.

Contract (unchanged from b2z_execution_boundary.py, re-verified this
milestone):
  STAGE 1 MIGRATION_TX:   getTransaction(migration_signature)
  STAGE 2 CREATOR_HISTORY: getSignaturesForAddress(creator, {limit: 1000})
  STAGE 3 FUNDING_TX:      getTransaction(candidate_funding_signature)

Durability properties (new in this module, absent from B2ZRunner):
  - A durable ATTEMPT_RESERVED event is appended BEFORE every physical
    network dispatch (never after).
  - A durable terminal event (ATTEMPT_SUCCEEDED / ATTEMPT_FAILED_AFTER_DISPATCH
    / ATTEMPT_NOT_DISPATCHED) is appended after every attempt, success or
    failure, and physical-request accounting is derived ONLY from dispatched
    terminal events (mirrors B2N's PreDispatchEventLedger.physical_requests_attempted()).
  - Stage outputs required by later stages (resolved creator + migration_time
    after stage 1; candidate signature after stage 2) are persisted durably
    in a compact per-member stage-output ledger, so a resume never needs to
    re-dispatch an already-completed stage to reconstruct its output.
  - resume_next() processes AT MOST ONE stage per invocation and re-reads/
    reconciles all ledger state from disk on every call, exactly like B2N's
    resume_run().
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from src.acquisition.b2n_qualification import B2NManifest
from src.acquisition.b2w_projection import B2WInputProjection
from src.acquisition.b2z_execution_boundary import (
    PROVIDER,
    proves_inbound_sol_funding,
    resolve_creator,
)

CONTRACT_VERSION = "OIP_v2.2E.2B2Z.P1.v1"
MAX_TOTAL_REQUESTS = 60
MAX_REQUESTS_PER_MEMBER = 3
MAX_REQUESTS_PER_STAGE = 1

STAGE_MIGRATION_TX = "MIGRATION_TX"
STAGE_CREATOR_HISTORY = "CREATOR_HISTORY"
STAGE_FUNDING_TX = "FUNDING_TX"
STAGES_IN_ORDER = (STAGE_MIGRATION_TX, STAGE_CREATOR_HISTORY, STAGE_FUNDING_TX)

ENDPOINT_FAMILY = "helius-mainnet-json-rpc"

# Wide local funder fan-out ordinals identified in B2Z-P0's coverage matrix
# (docs/audits/b2z_p0_local_evidence_coverage_matrix.json) -- review metadata
# only, never used to change acquisition/candidate-selection behavior.
FAN_OUT_REVIEW_ORDINALS = frozenset({8, 11, 15, 19})


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


class B2ZP1Error(RuntimeError):
    pass


class JsonRpcTransport(Protocol):
    physical_request_count: int

    def post_json(self, request: dict[str, Any]) -> dict[str, Any]: ...


def _result(response: dict[str, Any]) -> Any:
    if not isinstance(response, dict) or response.get("error") is not None:
        raise ValueError("B2Z_RPC_ERROR")
    result = response.get("result")
    if not isinstance(result, (dict, list)):
        raise ValueError("B2Z_RESULT_MISSING")
    if isinstance(result, dict) and isinstance(result.get("meta"), dict) and result["meta"].get("err") is not None:
        raise ValueError("B2Z_TRANSACTION_FAILED")
    return result


@dataclass(frozen=True)
class StageIdentity:
    """Immutable identity for one physical request stage. Binds run_id,
    ordinal, mint, and stage so a completed stage can never be confused
    with another stage for the same member, or the same stage for a
    different member."""
    run_id: str
    sample_ordinal: int
    mint: str
    stage: str

    def key(self) -> tuple[str, int, str]:
        return (self.run_id, self.sample_ordinal, self.stage)


class B2ZEventLedger:
    """Append-only JSONL pre-dispatch/terminal event ledger. Structurally
    identical in spirit to B2N's PreDispatchEventLedger.

    LOCAL_PREDICTION_SEEDED (added for B2Z-P2 calibration mode): marks a
    stage as dependency-complete WITHOUT any physical dispatch, used only to
    let a calibration run skip a live CREATOR_HISTORY call for a member whose
    local evidence is being frozen and inspected via a targeted Stage 3 call
    instead. It is deliberately EXCLUDED from dispatched_stage_keys() /
    physical_requests_attempted() -- it must never be countable as a
    consumed physical request. It is included in succeeded_stage_keys() so
    _stage_for_next() treats the stage as complete for dependency-ordering
    purposes only."""

    VALID_EVENTS = {"ATTEMPT_RESERVED", "ATTEMPT_SUCCEEDED", "ATTEMPT_FAILED_AFTER_DISPATCH",
                     "ATTEMPT_NOT_DISPATCHED", "LOCAL_PREDICTION_SEEDED", "EXECUTION_EXCLUDED"}

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def append_event(self, *, event: str, run_id: str, sample_ordinal: int, mint: str, stage: str,
                      physical_request_number: int, provider: str, endpoint_family: str, method: str,
                      dependency_digest: str | None, request_digest: str | None, **extra: Any) -> None:
        if event not in self.VALID_EVENTS:
            raise B2ZP1Error(f"B2Z_P1_INVALID_EVENT_TYPE:{event}")
        entry = {
            "event": event, "run_id": run_id, "sample_ordinal": sample_ordinal, "mint": mint,
            "stage": stage, "physical_request_number": physical_request_number, "provider": provider,
            "endpoint_family": endpoint_family, "method": method, "dependency_digest": dependency_digest,
            "request_digest": request_digest, "event_utc_ns": time.time_ns(), **extra,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(_canonical(entry))

    def reserved_stage_keys(self) -> set[tuple[int, str]]:
        return {(e["sample_ordinal"], e["stage"]) for e in self.events() if e["event"] == "ATTEMPT_RESERVED"}

    def excluded_ordinals(self) -> set[int]:
        return {e["sample_ordinal"] for e in self.events() if e["event"] == "EXECUTION_EXCLUDED"}

    def terminal_events(self) -> list[dict[str, Any]]:
        return [e for e in self.events() if e["event"] in
                ("ATTEMPT_SUCCEEDED", "ATTEMPT_FAILED_AFTER_DISPATCH", "ATTEMPT_NOT_DISPATCHED")]

    def dispatched_stage_keys(self) -> set[tuple[int, str]]:
        """A stage counts as a consumed physical request only if its terminal
        event indicates dispatch was actually attempted (SUCCEEDED or
        FAILED_AFTER_DISPATCH) -- NOT_DISPATCHED means an exception occurred
        before the network call and must not consume budget."""
        return {(e["sample_ordinal"], e["stage"]) for e in self.terminal_events()
                if e["event"] in ("ATTEMPT_SUCCEEDED", "ATTEMPT_FAILED_AFTER_DISPATCH")}

    def physical_requests_attempted(self) -> int:
        return len(self.dispatched_stage_keys())

    def succeeded_stage_keys(self) -> set[tuple[int, str]]:
        return {(e["sample_ordinal"], e["stage"]) for e in self.events()
                if e["event"] in ("ATTEMPT_SUCCEEDED", "LOCAL_PREDICTION_SEEDED")}

    def seed_frozen_stage(self, *, run_id: str, sample_ordinal: int, mint: str, stage: str,
                           frozen_prediction_digest: str) -> None:
        """B2Z-P2 calibration only: mark a stage dependency-complete from a
        FROZEN local prediction, with NO physical dispatch and NO reservation
        (there is nothing to reserve -- no network call occurs). Refuses to
        seed a stage that already has any event, including a prior seed, to
        prevent silently overwriting a frozen value."""
        if (sample_ordinal, stage) in self.succeeded_stage_keys() or (sample_ordinal, stage) in self.reserved_stage_keys():
            raise B2ZP1Error(f"B2Z_P2_STAGE_ALREADY_HAS_EVENTS:{sample_ordinal}:{stage}")
        self.append_event(
            event="LOCAL_PREDICTION_SEEDED", run_id=run_id, sample_ordinal=sample_ordinal, mint=mint,
            stage=stage, physical_request_number=0, provider="NONE_LOCAL_PREDICTION",
            endpoint_family="NONE_LOCAL_PREDICTION", method="NONE_LOCAL_PREDICTION",
            dependency_digest=None, request_digest=frozen_prediction_digest,
        )

    def exclude_member_from_execution(self, *, run_id: str, sample_ordinal: int, mint: str,
                                       failed_stage: str, exclusion_reason: str,
                                       decision_source: str) -> None:
        """B2Z-P2 calibration only: permanently mark a member as excluded from
        further execution selection because a PRIOR terminal failure on
        `failed_stage` was reviewed and accepted as non-retryable by an
        explicit human/policy decision (e.g. OPTION_A), rather than a live
        raw-evidence disagreement.

        This is NOT a retry, NOT a success, and NOT a deletion or rewrite of
        the original failure event -- the original ATTEMPT_RESERVED /
        ATTEMPT_FAILED_AFTER_DISPATCH events remain untouched, permanently,
        as the historical record. This method only appends a NEW event that
        `resume_next()` consults to decide whether to skip the member instead
        of raising B2Z_P1_MEMBER_BLOCKED_BY_FAILED_STAGE.

        Consumes NO physical request (physical_request_number=0, excluded
        from dispatched_stage_keys()/physical_requests_attempted() by
        construction -- EXECUTION_EXCLUDED is not in the set the accounting
        methods filter for).

        Refuses to exclude:
          - a member with no matching terminal failure on failed_stage (can't
            manufacture an exclusion for a stage that never actually failed --
            this is what prevents forging an exclusion for an ordinary
            semantic/provider failure that should still fail closed)
          - a member that already has an EXECUTION_EXCLUDED event (no
            duplicate exclusion)
          - a member whose failed_stage already succeeded (nothing to
            exclude)
        """
        terminal_by_key = {(e["sample_ordinal"], e["stage"]): e["event"] for e in self.terminal_events()}
        actual_terminal = terminal_by_key.get((sample_ordinal, failed_stage))
        if actual_terminal != "ATTEMPT_FAILED_AFTER_DISPATCH":
            raise B2ZP1Error(
                f"B2Z_P2R_NO_MATCHING_FAILURE_TO_EXCLUDE:{sample_ordinal}:{failed_stage}:"
                f"found={actual_terminal!r}"
            )
        if sample_ordinal in self.excluded_ordinals():
            raise B2ZP1Error(f"B2Z_P2R_DUPLICATE_EXCLUSION:{sample_ordinal}")
        self.append_event(
            event="EXECUTION_EXCLUDED", run_id=run_id, sample_ordinal=sample_ordinal, mint=mint,
            stage=failed_stage, physical_request_number=0, provider="NONE_EXECUTION_EXCLUDED",
            endpoint_family="NONE_EXECUTION_EXCLUDED", method="NONE_EXECUTION_EXCLUDED",
            dependency_digest=None, request_digest=None,
            exclusion_reason=exclusion_reason, decision_source=decision_source,
        )


class B2ZStageOutputLedger:
    """Durable, compact per-member stage-output store. Persists ONLY the
    minimum fields a later stage needs to continue without re-dispatching an
    already-completed stage -- never full provider response bodies."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text())

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(_canonical(data))

    def record_stage_output(self, *, sample_ordinal: int, stage: str, output: dict[str, Any]) -> None:
        data = self._load()
        key = str(sample_ordinal)
        member = data.setdefault(key, {})
        if stage in member:
            raise B2ZP1Error(f"B2Z_P1_STAGE_OUTPUT_ALREADY_RECORDED:{sample_ordinal}:{stage}")
        member[stage] = output
        self._save(data)

    def get_stage_output(self, *, sample_ordinal: int, stage: str) -> dict[str, Any] | None:
        return self._load().get(str(sample_ordinal), {}).get(stage)

    def member_outputs(self, sample_ordinal: int) -> dict[str, Any]:
        return self._load().get(str(sample_ordinal), {})


@dataclass(frozen=True)
class B2ZAuthorization:
    """New, independent B2Z-specific authorization -- never reuses B2N's
    exhausted run_id or budget."""
    run_id: str
    manifest_digest: str
    projection_digest: str
    b2n_closure_digest: str
    p0_preflight_digest: str
    provider: str
    endpoint_family: str
    allowed_methods: tuple[str, ...]
    max_total_requests: int
    max_requests_per_member: int
    max_requests_per_stage: int
    retries: int
    pagination_budget: int
    fallback_budget: int
    production_db_read: bool
    production_db_write: bool
    candidate_evidence_only: bool
    existing_operation_mutation_forbidden: bool

    def digest(self) -> str:
        return _sha256_json(asdict(self))


def derive_b2z_run_id(*, manifest_digest: str, projection_digest: str, b2n_closure_digest: str) -> str:
    """Deterministic, distinct from B2N's run_id derivation and from any
    prior B2Z experimental run_id (e.g. the random uuid used in the earlier,
    never-executed oip_v2_2e_2b2bq preflight)."""
    seed = f"B2Z-P1|{manifest_digest}|{projection_digest}|{b2n_closure_digest}|DURABLE_RESUMABLE"
    return "b2z-p1-" + hashlib.sha256(seed.encode()).hexdigest()[:24]


def build_authorization(*, manifest: B2NManifest, projection: B2WInputProjection,
                         b2n_closure_digest: str, p0_preflight_digest: str) -> B2ZAuthorization:
    manifest.validate()
    manifest_digest = manifest.digest()
    projection_digest = _sha256_json([asdict(m) for m in projection.members])
    run_id = derive_b2z_run_id(
        manifest_digest=manifest_digest, projection_digest=projection_digest,
        b2n_closure_digest=b2n_closure_digest,
    )
    return B2ZAuthorization(
        run_id=run_id, manifest_digest=manifest_digest, projection_digest=projection_digest,
        b2n_closure_digest=b2n_closure_digest, p0_preflight_digest=p0_preflight_digest,
        provider=PROVIDER, endpoint_family=ENDPOINT_FAMILY,
        allowed_methods=("getTransaction", "getSignaturesForAddress"),
        max_total_requests=MAX_TOTAL_REQUESTS, max_requests_per_member=MAX_REQUESTS_PER_MEMBER,
        max_requests_per_stage=MAX_REQUESTS_PER_STAGE, retries=0, pagination_budget=0, fallback_budget=0,
        production_db_read=False, production_db_write=False,
        candidate_evidence_only=True, existing_operation_mutation_forbidden=True,
    )


class DurableB2ZClient:
    """Wraps a transport so a physical request can never be attempted
    without a durable pre-dispatch reservation, mirroring B2N's
    DurableAccountingClient exactly."""

    def __init__(self, *, transport: JsonRpcTransport, event_ledger: B2ZEventLedger,
                 authorization: B2ZAuthorization) -> None:
        self._transport = transport
        self._event_ledger = event_ledger
        self._auth = authorization

    def dispatch(self, *, sample_ordinal: int, mint: str, stage: str, method: str,
                 params: list[Any], dependency_digest: str | None) -> Any:
        if method not in self._auth.allowed_methods:
            raise B2ZP1Error(f"B2Z_P1_METHOD_NOT_AUTHORIZED:{method}")
        key = (sample_ordinal, stage)
        if key in self._event_ledger.reserved_stage_keys():
            raise B2ZP1Error(f"B2Z_P1_DUPLICATE_STAGE_ATTEMPT:{sample_ordinal}:{stage}")
        if self._event_ledger.physical_requests_attempted() >= self._auth.max_total_requests:
            raise B2ZP1Error("B2Z_P1_GLOBAL_BUDGET_EXHAUSTED")

        request_number = self._event_ledger.physical_requests_attempted() + 1
        request_digest = _sha256_json({"method": method, "params": params})

        # Durable reservation BEFORE any network dispatch -- must complete
        # before the transport call is attempted.
        self._event_ledger.append_event(
            event="ATTEMPT_RESERVED", run_id=self._auth.run_id, sample_ordinal=sample_ordinal, mint=mint,
            stage=stage, physical_request_number=request_number, provider=self._auth.provider,
            endpoint_family=self._auth.endpoint_family, method=method,
            dependency_digest=dependency_digest, request_digest=request_digest,
        )

        transport_before = self._transport.physical_request_count
        try:
            response = self._transport.post_json({
                "jsonrpc": "2.0", "id": request_number, "method": method, "params": params,
            })
            result = _result(response)
        except Exception as exc:
            transport_after = self._transport.physical_request_count
            dispatched = transport_after != transport_before
            self._event_ledger.append_event(
                event="ATTEMPT_FAILED_AFTER_DISPATCH" if dispatched else "ATTEMPT_NOT_DISPATCHED",
                run_id=self._auth.run_id, sample_ordinal=sample_ordinal, mint=mint, stage=stage,
                physical_request_number=request_number, provider=self._auth.provider,
                endpoint_family=self._auth.endpoint_family, method=method,
                dependency_digest=dependency_digest, request_digest=request_digest,
                error_class=type(exc).__name__,
            )
            raise

        transport_after = self._transport.physical_request_count
        dispatched = transport_after != transport_before
        if not dispatched:
            # Should not happen for a real transport (post_json always
            # increments before returning), but fail closed rather than
            # silently mis-account a "free" success.
            self._event_ledger.append_event(
                event="ATTEMPT_NOT_DISPATCHED", run_id=self._auth.run_id, sample_ordinal=sample_ordinal,
                mint=mint, stage=stage, physical_request_number=request_number, provider=self._auth.provider,
                endpoint_family=self._auth.endpoint_family, method=method,
                dependency_digest=dependency_digest, request_digest=request_digest,
                error_class="B2Z_P1_TRANSPORT_COUNTER_NOT_INCREMENTED",
            )
            raise B2ZP1Error("B2Z_P1_TRANSPORT_COUNTER_NOT_INCREMENTED")

        self._event_ledger.append_event(
            event="ATTEMPT_SUCCEEDED", run_id=self._auth.run_id, sample_ordinal=sample_ordinal, mint=mint,
            stage=stage, physical_request_number=request_number, provider=self._auth.provider,
            endpoint_family=self._auth.endpoint_family, method=method,
            dependency_digest=dependency_digest, request_digest=request_digest,
        )
        return result


def _stage_for_next(event_ledger: B2ZEventLedger, sample_ordinal: int) -> str | None:
    """Determine the next incomplete stage for a member, respecting strict
    dependency order. Returns None if all 3 stages are already succeeded, OR
    if an earlier stage failed (member cannot proceed -- see resume_next())."""
    succeeded = event_ledger.succeeded_stage_keys()
    for stage in STAGES_IN_ORDER:
        if (sample_ordinal, stage) not in succeeded:
            return stage
    return None


def _member_has_failed_stage(event_ledger: B2ZEventLedger, sample_ordinal: int) -> str | None:
    """Returns the stage name of the first non-succeeded terminal (failed or
    not-dispatched) event for this member found in dependency order, if the
    member's progression is blocked by a stage that was attempted but did
    not succeed. Returns None if the member has no blocking failure."""
    succeeded = event_ledger.succeeded_stage_keys()
    terminal_by_key = {(e["sample_ordinal"], e["stage"]): e["event"] for e in event_ledger.terminal_events()}
    for stage in STAGES_IN_ORDER:
        key = (sample_ordinal, stage)
        if key in succeeded:
            continue
        if key in terminal_by_key:
            return stage
        return None  # not yet attempted at all -- not a failure, just not reached
    return None


def seed_frozen_creator_history_from_local_prediction(
    *, run_id: str, sample_ordinal: int, mint: str, event_ledger: B2ZEventLedger,
    stage_output_ledger: B2ZStageOutputLedger, frozen_creator: str, frozen_migration_time: int,
    frozen_funding_signature: str, frozen_prediction_digest: str,
) -> None:
    """B2Z-P2 calibration only: seed a CREATOR_HISTORY stage as
    dependency-complete from an ALREADY-FROZEN local prediction, so
    resume_next()'s STAGE_FUNDING_TX branch can proceed directly to a
    TARGETED, deterministic Stage 3 dispatch against the frozen signature --
    without ever making a live getSignaturesForAddress call for this member.

    Must be called BEFORE any resume_next() invocation for this member.
    Requires migration_time to already be known (normally this comes from a
    live Stage 1 dispatch, per the plan -- Stage 1 remains a REAL, live,
    dispatched call for every member, including the 10 calibration-skip
    members; only Stage 2 is skipped).
    """
    # Guard against the event ledger FIRST -- this is the authoritative record
    # of whether this stage has already been touched (dispatched OR seeded).
    # Checking it before writing to the stage-output ledger avoids a
    # partially-applied seed if this function is called twice for the same
    # (ordinal, stage).
    if (sample_ordinal, STAGE_CREATOR_HISTORY) in event_ledger.succeeded_stage_keys() or \
       (sample_ordinal, STAGE_CREATOR_HISTORY) in event_ledger.reserved_stage_keys():
        raise B2ZP1Error(f"B2Z_P2_STAGE_ALREADY_HAS_EVENTS:{sample_ordinal}:{STAGE_CREATOR_HISTORY}")
    output = {
        "creator": frozen_creator, "migration_time": frozen_migration_time,
        "candidate_funding_signature": frozen_funding_signature,
        "candidate_block_time": None,  # not independently re-derived; the frozen signature is inspected raw in Stage 3
        "selection_rule": "FROZEN_LOCAL_PREDICTION_NOT_LIVE_DISCOVERY",
        "candidate_pool_size_after_filter": None,
    }
    stage_output_ledger.record_stage_output(sample_ordinal=sample_ordinal, stage=STAGE_CREATOR_HISTORY, output=output)
    event_ledger.seed_frozen_stage(
        run_id=run_id, sample_ordinal=sample_ordinal, mint=mint, stage=STAGE_CREATOR_HISTORY,
        frozen_prediction_digest=frozen_prediction_digest,
    )


def resume_next(*, manifest: B2NManifest, projection: B2WInputProjection, authorization: B2ZAuthorization,
                 transport: JsonRpcTransport, event_ledger: B2ZEventLedger,
                 stage_output_ledger: B2ZStageOutputLedger) -> dict[str, Any]:
    """Process AT MOST ONE stage for the deterministic next incomplete
    member/stage, re-reading and reconciling all ledger state from disk on
    every call -- exactly like B2N's resume_run()."""
    manifest.validate()
    if manifest.digest() != authorization.manifest_digest:
        raise B2ZP1Error("B2Z_P1_MANIFEST_DIGEST_MISMATCH")

    projection_digest = _sha256_json([asdict(m) for m in projection.members])
    if projection_digest != authorization.projection_digest:
        raise B2ZP1Error("B2Z_P1_PROJECTION_DIGEST_MISMATCH")

    projected = {m.mint: m for m in projection.members}
    client = DurableB2ZClient(transport=transport, event_ledger=event_ledger, authorization=authorization)

    excluded = event_ledger.excluded_ordinals()

    for member in manifest.members:
        ordinal = member.sample_ordinal
        mint = member.mint

        blocking_stage = _member_has_failed_stage(event_ledger, ordinal)
        if blocking_stage is not None:
            if ordinal in excluded:
                # This member's failed stage was reviewed and explicitly
                # excluded from execution (B2Z-P2R) -- the failure itself is
                # NOT retried, ignored, or converted to success; it remains
                # permanently non-dispatchable. Resume simply moves on to the
                # next member instead of treating this as a run-blocking
                # anomaly.
                continue
            # This member is permanently blocked by a failed/not-dispatched
            # stage that has NOT been explicitly excluded -- do NOT retry it,
            # and do NOT skip to a later member's earlier stage out of order.
            # Per the "stop on first anomaly" discipline (matching B2N/B2Z's
            # existing stop-on-first-non-success posture), surface this
            # rather than silently continuing past it.
            raise B2ZP1Error(f"B2Z_P1_MEMBER_BLOCKED_BY_FAILED_STAGE:{ordinal}:{blocking_stage}")

        stage = _stage_for_next(event_ledger, ordinal)
        if stage is None:
            continue  # this member is fully complete, move to the next

        pmint = projected[mint]

        if stage == STAGE_MIGRATION_TX:
            result = client.dispatch(
                sample_ordinal=ordinal, mint=mint, stage=stage, method="getTransaction",
                params=[pmint.migration_signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
                dependency_digest=None,
            )
            creator, migration_time = resolve_creator(result, mint)
            output = {
                "creator": creator, "migration_signature": pmint.migration_signature,
                "migration_time": migration_time, "migration_slot": result.get("slot"),
            }
            stage_output_ledger.record_stage_output(sample_ordinal=ordinal, stage=stage, output=output)
            return {"status": "STAGE_COMPLETE", "sample_ordinal": ordinal, "stage": stage, "output": output}

        if stage == STAGE_CREATOR_HISTORY:
            stage1 = stage_output_ledger.get_stage_output(sample_ordinal=ordinal, stage=STAGE_MIGRATION_TX)
            if stage1 is None:
                raise B2ZP1Error(f"B2Z_P1_MISSING_STAGE_1_OUTPUT:{ordinal}")
            creator = stage1["creator"]
            migration_time = stage1["migration_time"]
            dep_digest = _sha256_json(stage1)
            result = client.dispatch(
                sample_ordinal=ordinal, mint=mint, stage=stage, method="getSignaturesForAddress",
                params=[creator, {"limit": 1000}], dependency_digest=dep_digest,
            )
            rows = result if isinstance(result, list) else result.get("value")
            candidates = [row for row in rows if isinstance(row, dict)
                          and isinstance(row.get("signature"), str)
                          and isinstance(row.get("blockTime"), int)
                          and row["blockTime"] < migration_time] if isinstance(rows, list) else []
            if not candidates:
                output = {"no_candidate": True, "creator": creator, "migration_time": migration_time}
                stage_output_ledger.record_stage_output(sample_ordinal=ordinal, stage=stage, output=output)
                return {"status": "NO_CANDIDATE", "sample_ordinal": ordinal, "stage": stage, "output": output}
            selected = candidates[0]
            output = {
                "creator": creator, "migration_time": migration_time,
                "candidate_funding_signature": selected["signature"],
                "candidate_block_time": selected["blockTime"],
                "selection_rule": "most_recent_qualifying_pre_migration_signature",
                "candidate_pool_size_after_filter": len(candidates),
            }
            stage_output_ledger.record_stage_output(sample_ordinal=ordinal, stage=stage, output=output)
            return {"status": "STAGE_COMPLETE", "sample_ordinal": ordinal, "stage": stage, "output": output}

        if stage == STAGE_FUNDING_TX:
            stage2 = stage_output_ledger.get_stage_output(sample_ordinal=ordinal, stage=STAGE_CREATOR_HISTORY)
            if stage2 is None:
                raise B2ZP1Error(f"B2Z_P1_MISSING_STAGE_2_OUTPUT:{ordinal}")
            if stage2.get("no_candidate"):
                # No provider request is made for stage 3 when there is no
                # candidate -- this member's evidence acquisition ends here
                # with a qualified no-evidence outcome, consuming exactly the
                # 2 requests actually attempted (not 3).
                return {"status": "NO_EVIDENCE_NO_CANDIDATE", "sample_ordinal": ordinal, "stage": stage}
            creator = stage2["creator"]
            migration_time = stage2["migration_time"]
            signature = stage2["candidate_funding_signature"]
            dep_digest = _sha256_json(stage2)
            result = client.dispatch(
                sample_ordinal=ordinal, mint=mint, stage=stage, method="getTransaction",
                params=[signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
                dependency_digest=dep_digest,
            )
            funding_time = result.get("blockTime")
            if not isinstance(funding_time, int) or funding_time >= migration_time:
                raise B2ZP1Error(f"B2Z_P1_FUNDING_TIME_INVALID:{ordinal}")
            if not proves_inbound_sol_funding(result, creator):
                raise B2ZP1Error(f"B2Z_P1_NO_FUNDING_EDGE:{ordinal}")
            review_flag = "FUNDING_SOURCE_REQUIRES_DOWNSTREAM_REVIEW" if ordinal in FAN_OUT_REVIEW_ORDINALS else None
            output = {
                "creator": creator, "migration_signature": stage_output_ledger.get_stage_output(
                    sample_ordinal=ordinal, stage=STAGE_MIGRATION_TX)["migration_signature"],
                "migration_time": migration_time, "funding_signature": signature,
                "funding_time": funding_time, "funding_slot": result.get("slot"),
                "review_flag": review_flag, "evidence_observed": True, "provenance_complete": True,
                "candidate_only": True,
            }
            stage_output_ledger.record_stage_output(sample_ordinal=ordinal, stage=stage, output=output)
            return {"status": "MEMBER_COMPLETE", "sample_ordinal": ordinal, "stage": stage, "output": output}

    return {"status": "ALL_MEMBERS_COMPLETE"}
