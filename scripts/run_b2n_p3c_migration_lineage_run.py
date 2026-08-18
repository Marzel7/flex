#!/usr/bin/env python3
"""B2N-P3C isolated migration-lineage bounded-run runner.

Executes ONLY after explicit --live (network) invocation; default mode is
--dry-run, which constructs and validates everything except the network
call itself.

Credential handling: the Helius endpoint (including any embedded API key)
is read ONLY from the environment variable B2N_P3C_HELIUS_ENDPOINT at
process start. It is NEVER read from .env, config/.env, or
supervisord.conf, NEVER printed to stdout/stderr, and NEVER written into
any artifact this script produces. If the variable is unset, --live mode
refuses to start.

Authorization: this script hard-binds to the exact P3B authorization
(run_id, manifest digest, provider, endpoint_family, method, budget). Any
mismatch fails closed before any network access.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.acquisition.b2n_qualification import (
    AppendOnlyLedger,
    B2NAttemptLedger,
    B2NExecutor,
    B2NManifest,
    B2NMember,
    B2NQualificationRunAuthorization,
    B2N_MAX_PHYSICAL_REQUESTS,
    B2N_MAX_REQUESTS_PER_MEMBER,
    B2N_VALID_OUTCOMES,
    CONTRACT_VERSION,
    OUTCOME_CACHE_HIT,
)
from src.acquisition.b2w_projection import B2WInputProjection, MigrationGetTransactionAdapter

ROOT = Path(__file__).resolve().parents[1]
FROZEN_MANIFEST_PATH = ROOT / "docs/evidence_platform/oip_v2_2e_2b2u_b2r_frozen_manifest.json"
REVIEWED_BINDING_PATH = ROOT / "docs/audits/b2n_cohort_eligibility_reviewed_provenance_binding.json"
SUCCESSOR_PREFLIGHT_PATH = ROOT / "docs/audits/b2n_p3e_bounded_migration_lineage_run_authorization_preflight.json"

# P3E: the original run_id (b2n-p3b-c39499f523e42083ce045d70) was used for two
# real live attempts whose exact physical-request consumption is UNKNOWN (see
# docs/audits/b2n_p3e_historical_live_attempt_reconciliation.json). Both
# attempts failed before any durable ledger entry was written -- attempt 1
# with a redacted HTTPError, attempt 2 with B2N_CLIENT_REQUEST_COUNTER_MISMATCH,
# a deterministic code defect (see the reconciliation artifact) that would have
# fired identically on every future attempt. Since the true consumed-request
# count against the OLD run_id cannot be proven, that authorization/run_id is
# NOT reused. A fresh run_id is bound below, deterministically derived from the
# old (compromised) run_id plus the manifest/provenance digests, so the
# lineage is traceable without silently pretending nothing happened.
PRIOR_RUN_ID_DO_NOT_REUSE = "b2n-p3b-c39499f523e42083ce045d70"
EXPECTED_RUN_ID = "b2n-p3e-98695fbdf44146c93ff08c8c"
EXPECTED_AUTHORIZATION_DIGEST = "b4994fdda5eb9ae1dd7fe27927efd9cef7504b179dfcbb12fa4bbd9b9bbffaae"
EXPECTED_PROVIDER = "helius"
EXPECTED_ENDPOINT_FAMILY = "helius-mainnet-json-rpc"
EXPECTED_METHOD = "getTransaction"

CREDENTIAL_ENV_VAR = "B2N_P3C_HELIUS_ENDPOINT"

# Pinned inside the repository, on the main project filesystem -- never under
# /private/tmp or any sibling scratch directory -- so a partial/failed ledger
# from a live run always survives independently of unrelated tmp-storage
# exhaustion. The ledger is the authoritative attempt-accounting record for
# this run ID; it must never be silently reset.
DEFAULT_LEDGER_PATH = ROOT / "docs/audits" / f"b2n_p3c_live_ledger_{EXPECTED_RUN_ID}.jsonl"
DEFAULT_RESULT_PATH = ROOT / "docs/audits" / "b2n_p3c_live_result.json"

# P3E: a SEPARATE, append-only, multi-event-per-ordinal pre-dispatch reservation
# ledger. Unlike B2NAttemptLedger (which stores exactly one terminal entry per
# ordinal and is only ever written AFTER a successful counter reconciliation),
# this ledger records an ATTEMPT_RESERVED event durably BEFORE network dispatch,
# and a terminal event (ATTEMPT_SUCCEEDED / ATTEMPT_FAILED) immediately after --
# so a physical request can never be attempted without durable evidence, even if
# B2NExecutor.run() itself later raises before reaching its own ledger.append().
DEFAULT_EVENT_LEDGER_PATH = ROOT / "docs/audits" / f"b2n_p3e_event_ledger_{EXPECTED_RUN_ID}.jsonl"


class B2NP3CError(RuntimeError):
    pass


def _load_frozen_manifest() -> B2NManifest:
    payload = json.loads(FROZEN_MANIFEST_PATH.read_text())
    members = tuple(B2NMember(**m) for m in payload["members"])
    return B2NManifest(
        members=members,
        source_milestone=payload["source_milestone"],
        source_receive_utc_ns_exclusive=payload["source_receive_utc_ns_exclusive"],
    )


def _load_reviewed_binding() -> dict:
    return json.loads(REVIEWED_BINDING_PATH.read_text())


def _verify_authorization_digest() -> dict:
    import hashlib

    raw = SUCCESSOR_PREFLIGHT_PATH.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED_AUTHORIZATION_DIGEST:
        raise B2NP3CError(f"B2N_P3C_AUTHORIZATION_DIGEST_MISMATCH: expected {EXPECTED_AUTHORIZATION_DIGEST}, got {digest}")
    payload = json.loads(raw)
    if payload.get("run_id") != EXPECTED_RUN_ID:
        raise B2NP3CError("B2N_P3C_RUN_ID_MISMATCH")
    binding = payload.get("provider_endpoint_method_binding", {})
    if binding.get("provider") != EXPECTED_PROVIDER:
        raise B2NP3CError("B2N_P3C_PROVIDER_MISMATCH")
    if binding.get("endpoint_family") != EXPECTED_ENDPOINT_FAMILY:
        raise B2NP3CError("B2N_P3C_ENDPOINT_FAMILY_MISMATCH")
    if binding.get("method") != EXPECTED_METHOD:
        raise B2NP3CError("B2N_P3C_METHOD_MISMATCH")
    budget = payload.get("request_budget", {})
    if budget.get("max_total_requests") != B2N_MAX_PHYSICAL_REQUESTS:
        raise B2NP3CError("B2N_P3C_BUDGET_MISMATCH")
    if budget.get("max_requests_per_member") != B2N_MAX_REQUESTS_PER_MEMBER:
        raise B2NP3CError("B2N_P3C_PER_MEMBER_BUDGET_MISMATCH")
    if budget.get("retry_budget") != 0 or budget.get("pagination_budget") != 0 or budget.get("fallback_budget") != 0:
        raise B2NP3CError("B2N_P3C_FORBIDDEN_POLICY_PRESENT")
    return payload


class RedactingJsonRpcTransport:
    """Wraps the endpoint so it is never exposed via repr/str/logging."""

    def __init__(self, endpoint: str, *, timeout_seconds: float = 30.0) -> None:
        if not endpoint.startswith("https://mainnet.helius-rpc.com/"):
            raise B2NP3CError("B2N_P3C_ENDPOINT_PREFIX_MISMATCH")
        if timeout_seconds <= 0:
            raise B2NP3CError("B2N_P3C_TIMEOUT_REQUIRED")
        self._endpoint = endpoint
        self._timeout = timeout_seconds
        self.physical_request_count = 0

    def __repr__(self) -> str:
        return "RedactingJsonRpcTransport(endpoint=<redacted>)"

    def post_json(self, request: dict) -> dict:
        import urllib.error
        import urllib.request

        body = json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        outbound = urllib.request.Request(
            self._endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        self.physical_request_count += 1
        try:
            with urllib.request.urlopen(outbound, timeout=self._timeout) as response:
                return json.loads(response.read())
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
            # Redact: urllib exceptions can embed the request URL (and thus the
            # credential) in their default message/repr. Never let that surface.
            raise B2NP3CError(f"B2N_P3C_TRANSPORT_ERROR:{type(exc).__name__}") from None


class PreDispatchEventLedger:
    """Append-only, multi-event-per-ordinal immutable event log.

    Unlike AppendOnlyLedger/B2NAttemptLedger (exactly one terminal entry per
    ordinal, written only after B2NExecutor has already reconciled counters),
    this ledger permits an ATTEMPT_RESERVED event and a later terminal event
    for the SAME ordinal, because durability here must be established BEFORE
    the outcome of the network call -- or even whether it dispatched at all --
    is known. Events are never updated or deleted, only appended (P3E Part 4:
    "implement a B2N-specific immutable event ledger rather than using UPDATE").
    """

    VALID_EVENTS = {"ATTEMPT_RESERVED", "ATTEMPT_SUCCEEDED", "ATTEMPT_FAILED", "ATTEMPT_NOT_DISPATCHED"}

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def events(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def require_empty(self) -> None:
        if self.events():
            raise RuntimeError("B2N_P3E_EVENT_LEDGER_MUST_BE_EMPTY_FOR_NEW_RUN")

    def append_event(self, *, event: str, run_id: str, sample_ordinal: int, mint: str,
                      provider: str, method: str, request_number: int, **extra) -> None:
        if event not in self.VALID_EVENTS:
            raise B2NP3CError(f"B2N_P3E_INVALID_EVENT_TYPE:{event}")
        entry = {
            "event": event,
            "run_id": run_id,
            "sample_ordinal": sample_ordinal,
            "mint": mint,
            "provider": provider,
            "method": method,
            "request_number": request_number,
            "event_utc_ns": time.time_ns(),
            **extra,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")

    def reserved_ordinals(self) -> set[int]:
        return {e["sample_ordinal"] for e in self.events() if e["event"] == "ATTEMPT_RESERVED"}

    def physical_requests_attempted(self) -> int:
        """Canonical accounting per P3E Part 5: a request counts once network
        dispatch is attempted (ATTEMPT_RESERVED followed by a non-'not
        dispatched' terminal event), regardless of success/failure/timeout/
        malformed response. ATTEMPT_NOT_DISPATCHED (an exception raised BEFORE
        the transport call, e.g. an unknown mint) does NOT count."""
        dispatched_ordinals = {
            e["sample_ordinal"] for e in self.events()
            if e["event"] in ("ATTEMPT_SUCCEEDED", "ATTEMPT_FAILED")
        }
        return len(dispatched_ordinals)


class DurableAccountingClient:
    """Wraps MigrationGetTransactionAdapter so that a physical request can
    never be attempted without a durable pre-dispatch reservation, and so
    B2NExecutor's own counter reconciliation (before/after provider_request_count
    on THIS wrapper, and OneRequestResponse.provider_request_count) is always
    internally consistent -- fixing the root cause of
    B2N_CLIENT_REQUEST_COUNTER_MISMATCH: MigrationGetTransactionAdapter never
    exposed provider_request_count at all, so B2NExecutor's getattr(...) calls
    silently fell back to defaults that never matched
    OneRequestResponse.provider_request_count's own default of 1."""

    def __init__(self, *, adapter, transport, event_ledger: PreDispatchEventLedger,
                 run_id: str, provider: str, method: str) -> None:
        self._adapter = adapter
        self._transport = transport
        self._event_ledger = event_ledger
        self._run_id = run_id
        self._provider = provider
        self._method = method
        self.provider_request_count = 0
        self._sample_ordinal_by_mint: dict[str, int] | None = None

    def _ordinal_for_mint(self, mint: str) -> int:
        if self._sample_ordinal_by_mint is None:
            self._sample_ordinal_by_mint = {item.mint: item.sample_ordinal for item in self._adapter.by_mint.values()}
        ordinal = self._sample_ordinal_by_mint.get(mint)
        if ordinal is None:
            raise B2NP3CError(f"B2N_P3E_MINT_NOT_IN_PROJECTION:{mint}")
        return ordinal

    def acquire_once(self, *, mint: str):
        ordinal = self._ordinal_for_mint(mint)
        if ordinal in self._event_ledger.reserved_ordinals():
            raise RuntimeError("B2N_P3E_DUPLICATE_ORDINAL_ATTEMPT")

        request_number = self._event_ledger.physical_requests_attempted() + 1

        # Durable reservation BEFORE any network dispatch is attempted -- this
        # write must complete before self._adapter.acquire_once() is called.
        self._event_ledger.append_event(
            event="ATTEMPT_RESERVED", run_id=self._run_id, sample_ordinal=ordinal,
            mint=mint, provider=self._provider, method=self._method, request_number=request_number,
        )

        transport_before = self._transport.physical_request_count
        try:
            response = self._adapter.acquire_once(mint=mint)
        except Exception as exc:
            transport_after = self._transport.physical_request_count
            dispatched = transport_after != transport_before
            self._event_ledger.append_event(
                event="ATTEMPT_FAILED" if dispatched else "ATTEMPT_NOT_DISPATCHED",
                run_id=self._run_id, sample_ordinal=ordinal, mint=mint, provider=self._provider,
                method=self._method, request_number=request_number,
                error_class=type(exc).__name__,
            )
            if dispatched:
                self.provider_request_count += 1
            raise

        transport_after = self._transport.physical_request_count
        dispatched = transport_after != transport_before
        self._event_ledger.append_event(
            event="ATTEMPT_SUCCEEDED" if dispatched else "ATTEMPT_NOT_DISPATCHED",
            run_id=self._run_id, sample_ordinal=ordinal, mint=mint, provider=self._provider,
            method=self._method, request_number=request_number,
            request_outcome=response.outcome,
        )
        if dispatched:
            self.provider_request_count += 1

        # Force OneRequestResponse.provider_request_count to reflect what THIS
        # wrapper actually observed on the transport, rather than trusting the
        # adapter's own (always-default) value -- this is what makes
        # B2NExecutor's before/after-vs-response.provider_request_count
        # reconciliation internally consistent instead of deterministically
        # mismatched.
        from dataclasses import replace
        return replace(response, provider_request_count=1 if dispatched else 0)


def _verify_ledger_readiness(ledger_path: Path) -> B2NAttemptLedger:
    """Ledger safety preflight (Part 6): resolve, verify writable, verify no
    foreign/prior attempts, verify expected initial (empty) state, and prove a
    durable append/rollback round-trip is actually possible on this
    filesystem -- all BEFORE any network call is permitted."""
    ledger_path = ledger_path.resolve()
    parent = ledger_path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise B2NP3CError(f"B2N_P3C_LEDGER_PARENT_NOT_CREATABLE:{type(exc).__name__}") from None
    if not os.access(parent, os.W_OK):
        raise B2NP3CError("B2N_P3C_LEDGER_PARENT_NOT_WRITABLE")

    ledger = B2NAttemptLedger(ledger_path)
    ledger.require_empty()  # fails closed on ANY prior/foreign attempt for this run ID

    # Prove a durable write is actually possible on this filesystem right now,
    # without consuming any part of the authorized attempt budget: write a
    # throwaway probe file beside the ledger, then remove it.
    probe_path = parent / f".b2n_p3c_writability_probe_{os.getpid()}.tmp"
    try:
        probe_path.write_text("b2n-p3c-writability-probe\n")
        probe_path.unlink()
    except OSError as exc:
        raise B2NP3CError(f"B2N_P3C_LEDGER_DURABLE_WRITE_PROBE_FAILED:{type(exc).__name__}") from None

    return ledger


def build_projection(manifest: B2NManifest, reviewed: dict) -> B2WInputProjection:
    reviewed_by_ordinal = {m["ordinal"]: m for m in reviewed["members"]}
    members = []
    for member in manifest.members:
        row = reviewed_by_ordinal.get(member.sample_ordinal)
        if row is None:
            raise B2NP3CError(f"B2N_P3C_REVIEWED_MEMBER_MISSING:{member.sample_ordinal}")
        if row["mint"] != member.mint or row["census_event_id"] != member.census_event_id:
            raise B2NP3CError(f"B2N_P3C_REVIEWED_MEMBER_MISMATCH:{member.sample_ordinal}")
        if row["human_approval_decision_record"]["decision"] != "APPROVED":
            raise B2NP3CError(f"B2N_P3C_MEMBER_NOT_APPROVED:{member.sample_ordinal}")
        members.append({
            "sample_ordinal": member.sample_ordinal,
            "mint": member.mint,
            "census_event_id": member.census_event_id,
            "migration_signature": row["reviewed_migration_origin"],
        })
    from src.acquisition.b2w_projection import B2WRequestInput
    return B2WInputProjection(tuple(B2WRequestInput(**m) for m in members))


def dry_run(*, ledger_path: Path | None = None, event_ledger_path: Path | None = None) -> dict:
    ledger_path = (ledger_path or DEFAULT_LEDGER_PATH).resolve()
    event_ledger_path = (event_ledger_path or DEFAULT_EVENT_LEDGER_PATH).resolve()

    manifest = _load_frozen_manifest()
    manifest.validate()
    auth_payload = _verify_authorization_digest()
    reviewed = _load_reviewed_binding()
    if reviewed["closure_state"] != {"COMPLETE": 20, "PARTIAL": 0, "MISSING": 0, "CONFLICTING": 0}:
        raise B2NP3CError("B2N_P3C_PROVENANCE_CLOSURE_NOT_QUALIFIED")

    projection = build_projection(manifest, reviewed)
    if len(projection.members) != 20:
        raise B2NP3CError("B2N_P3C_PROJECTION_COUNT_MISMATCH")

    authorization = B2NQualificationRunAuthorization(
        provider=EXPECTED_PROVIDER,
        endpoint_family=EXPECTED_ENDPOINT_FAMILY,
        run_id=EXPECTED_RUN_ID,
        manifest_digest=manifest.digest(),
        ledger_path=str(ledger_path),
        max_physical_requests=B2N_MAX_PHYSICAL_REQUESTS,
        require_not_production=True,
    )
    authorization.validate(manifest=manifest)  # dry validation only, no client, no network

    _verify_ledger_readiness(ledger_path)  # parent writable, empty, durable-write probe -- still zero network calls
    PreDispatchEventLedger(event_ledger_path).require_empty()

    constructed_requests = []
    for member in projection.members:
        payload = {
            "jsonrpc": "2.0",
            "id": member.sample_ordinal,
            "method": EXPECTED_METHOD,
            "params": [member.migration_signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
        }
        constructed_requests.append({
            "sample_ordinal": member.sample_ordinal,
            "mint": member.mint,
            "method": payload["method"],
            "params_shape_valid": payload["params"][1] == {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
        })

    return {
        "mode": "DRY_RUN",
        "authorization_digest_verified": True,
        "run_id_verified": EXPECTED_RUN_ID,
        "manifest_digest": manifest.digest(),
        "provenance_closure_verified": reviewed["closure_state"],
        "constructed_request_count": len(constructed_requests),
        "constructed_requests_max_20": len(constructed_requests) <= 20,
        "one_request_per_member": len(constructed_requests) == len({r["sample_ordinal"] for r in constructed_requests}),
        "endpoint_family": EXPECTED_ENDPOINT_FAMILY,
        "provider": EXPECTED_PROVIDER,
        "method": EXPECTED_METHOD,
        "ledger_path": str(ledger_path),
        "event_ledger_path": str(event_ledger_path),
        "result_path": str(DEFAULT_RESULT_PATH),
        "ledger_verified_empty": True,
        "event_ledger_verified_empty": True,
        "network_calls_made": 0,
        "requests": constructed_requests,
    }


def live_run(*, ledger_path: Path | None = None, event_ledger_path: Path | None = None) -> dict:
    """Executes the live bounded run.

    P3E durability: a physical request can never be attempted without a
    durable ATTEMPT_RESERVED event written to the event ledger BEFORE
    DurableAccountingClient delegates to the real adapter. This is
    independent of, and stronger than, B2NAttemptLedger's own per-member
    append (which only happens after B2NExecutor has already reconciled
    counters) -- so a failure at ANY point, including inside B2NExecutor's
    own counter-reconciliation checks, still leaves durable evidence of
    every attempted dispatch."""
    endpoint = os.environ.get(CREDENTIAL_ENV_VAR)
    if not endpoint:
        raise B2NP3CError(f"B2N_P3C_CREDENTIAL_ENV_VAR_MISSING:{CREDENTIAL_ENV_VAR}")

    ledger_path = (ledger_path or DEFAULT_LEDGER_PATH).resolve()
    event_ledger_path = (event_ledger_path or DEFAULT_EVENT_LEDGER_PATH).resolve()

    manifest = _load_frozen_manifest()
    manifest.validate()
    _verify_authorization_digest()
    reviewed = _load_reviewed_binding()
    if reviewed["closure_state"] != {"COMPLETE": 20, "PARTIAL": 0, "MISSING": 0, "CONFLICTING": 0}:
        raise B2NP3CError("B2N_P3C_PROVENANCE_CLOSURE_NOT_QUALIFIED")

    projection = build_projection(manifest, reviewed)

    authorization = B2NQualificationRunAuthorization(
        provider=EXPECTED_PROVIDER,
        endpoint_family=EXPECTED_ENDPOINT_FAMILY,
        run_id=EXPECTED_RUN_ID,
        manifest_digest=manifest.digest(),
        ledger_path=str(ledger_path),
        max_physical_requests=B2N_MAX_PHYSICAL_REQUESTS,
        require_not_production=True,
    )

    # Ledger readiness MUST be established before the transport/adapter/executor
    # are even constructed, let alone before any network call (Part 6: "Do not
    # consume the first provider request before ledger readiness is established").
    ledger = _verify_ledger_readiness(ledger_path)

    event_ledger = PreDispatchEventLedger(event_ledger_path)
    event_ledger.require_empty()

    transport = RedactingJsonRpcTransport(endpoint)
    adapter = MigrationGetTransactionAdapter(transport, projection)
    durable_client = DurableAccountingClient(
        adapter=adapter, transport=transport, event_ledger=event_ledger,
        run_id=EXPECTED_RUN_ID, provider=EXPECTED_PROVIDER, method=EXPECTED_METHOD,
    )

    executor = B2NExecutor(
        manifest=manifest, ledger=ledger, client=durable_client,
        provider=EXPECTED_PROVIDER, run_id=EXPECTED_RUN_ID, authorization=authorization,
    )
    results = executor.run()

    return {
        "mode": "LIVE",
        "run_id": EXPECTED_RUN_ID,
        "entries": len(results),
        "physical_requests_used": durable_client.provider_request_count,
        "physical_requests_attempted_per_event_ledger": event_ledger.physical_requests_attempted(),
        "ledger_path": str(ledger_path),
        "event_ledger_path": str(event_ledger_path),
        "results": results,
    }


def _completed_ordinals_from_ledgers(ledger: "AppendOnlyLedger", event_ledger: PreDispatchEventLedger) -> set[int]:
    """P3G: derive the durably-completed ordinal set from BOTH ledgers and
    fail closed on any inconsistency between them (e.g. a reservation with no
    terminal event -- an ambiguous, possibly-in-flight attempt that must never
    be silently treated as either complete or safe to redispatch)."""
    attempt_ordinals = {e["sample_ordinal"] for e in ledger.entries()}

    reserved = event_ledger.reserved_ordinals()
    terminal_events = [e for e in event_ledger.events() if e["event"] in ("ATTEMPT_SUCCEEDED", "ATTEMPT_FAILED", "ATTEMPT_NOT_DISPATCHED")]
    terminal_ordinals = {e["sample_ordinal"] for e in terminal_events}

    dangling = reserved - terminal_ordinals
    if dangling:
        raise B2NP3CError(f"B2N_P3G_DANGLING_RESERVATION_NO_TERMINAL_EVENT:{sorted(dangling)}")

    dispatched_ordinals = {e["sample_ordinal"] for e in terminal_events if e["event"] in ("ATTEMPT_SUCCEEDED", "ATTEMPT_FAILED")}
    if dispatched_ordinals != attempt_ordinals:
        raise B2NP3CError(
            f"B2N_P3G_LEDGER_EVENT_LEDGER_MISMATCH:dispatched={sorted(dispatched_ordinals)}:attempt_ledger={sorted(attempt_ordinals)}"
        )

    return attempt_ordinals


def _reconcile_single_member(*, client: "DurableAccountingClient", member: B2NMember,
                              run_id: str, provider: str) -> dict:
    """Replicates EXACTLY the per-member reconciliation body of
    B2NExecutor.run() (src/acquisition/b2n_qualification.py lines ~215-244:
    before/after counter read, outcome validation, counter-delta check,
    request_count validation, cache-hit semantics, entry construction) for a
    SINGLE member, without invoking B2NExecutor itself.

    This exists ONLY because B2NExecutor.run() structurally cannot process
    any ordinal other than 1 for the migration-lineage-only adapter (see
    docs/audits/b2n_p3g_partial_live_run_reconciliation.json part3_diagnosis
    and part4_fix_decision: B2NManifest.validate() requires the fixed 1..20
    member order and B2NExecutor.run() always starts iteration at index 0 and
    halts after the first non-qualifying outcome, which is unconditional for
    this adapter). B2NExecutor itself is NOT modified. This function is the
    B2N-specific, request-scoped equivalent of that reconciliation logic,
    reused verbatim in intent (same checks, same exception names) so a
    continuation step is held to the identical correctness bar as a normal
    B2NExecutor.run() invocation."""
    before = getattr(client, "provider_request_count", 0)
    response = client.acquire_once(mint=member.mint)
    if response.outcome not in B2N_VALID_OUTCOMES:
        raise ValueError("B2N_INVALID_REQUEST_OUTCOME")
    after = getattr(client, "provider_request_count", before)
    if (delta := after - before) not in {0, 1}:
        raise RuntimeError("B2N_CLIENT_REQUEST_COUNTER_INVALID")
    request_count = int(response.provider_request_count)
    if request_count not in {0, 1}:
        raise ValueError("B2N_REQUEST_COUNT_INVALID")
    if response.outcome == OUTCOME_CACHE_HIT and request_count != 0:
        raise ValueError("B2N_CACHE_HIT_MUST_HAVE_ZERO_REQUESTS")
    if request_count == 0 and response.outcome != OUTCOME_CACHE_HIT:
        raise ValueError("B2N_REQUEST_COUNT_MISMATCH")
    if request_count != delta:
        raise RuntimeError("B2N_CLIENT_REQUEST_COUNTER_MISMATCH")
    return {
        "contract_version": CONTRACT_VERSION, "run_id": run_id,
        "manifest_digest": _load_frozen_manifest().digest(), "sample_ordinal": member.sample_ordinal,
        "mint": member.mint, "observation_required": True, "provider": provider,
        "request_count": request_count, "request_outcome": response.outcome,
        "provider_signature": response.provider_signature, "provider_slot": response.provider_slot,
        "provider_block_time_utc": response.provider_block_time_utc,
        "request_started_utc_ns": None, "response_received_utc_ns": None,
        "elapsed_monotonic_ns": 0, "evidence_observed": response.evidence_observed,
        "provenance_complete": response.provenance_complete, "error_class": response.error_class,
    }


def resume_run(*, ledger_path: Path | None = None, event_ledger_path: Path | None = None) -> dict:
    """P3G safe continuation: reads the EXISTING (non-empty, durable) event
    and attempt ledgers for the fresh P3E run_id, derives completed ordinals,
    and processes AT MOST ONE additional remaining member per invocation.

    Never invokes B2NExecutor.run() for continuation steps (that shared code
    always starts at ordinal 1 and halts there for this adapter -- see
    docs/audits/b2n_p3g_partial_live_run_reconciliation.json part3_diagnosis).
    Instead calls DurableAccountingClient.acquire_once() directly for exactly
    ONE targeted remaining member, then reconciles it via
    _reconcile_single_member() (the identical logic B2NExecutor.run() uses
    per-member) before durably appending to the CUMULATIVE ledger (never
    reset, never truncated; AppendOnlyLedger.append() independently rejects
    a duplicate sample_ordinal as a second guard against redispatch)."""
    endpoint = os.environ.get(CREDENTIAL_ENV_VAR)
    if not endpoint:
        raise B2NP3CError(f"B2N_P3C_CREDENTIAL_ENV_VAR_MISSING:{CREDENTIAL_ENV_VAR}")

    ledger_path = (ledger_path or DEFAULT_LEDGER_PATH).resolve()
    event_ledger_path = (event_ledger_path or DEFAULT_EVENT_LEDGER_PATH).resolve()

    manifest = _load_frozen_manifest()
    manifest.validate()
    _verify_authorization_digest()
    reviewed = _load_reviewed_binding()
    if reviewed["closure_state"] != {"COMPLETE": 20, "PARTIAL": 0, "MISSING": 0, "CONFLICTING": 0}:
        raise B2NP3CError("B2N_P3C_PROVENANCE_CLOSURE_NOT_QUALIFIED")

    projection = build_projection(manifest, reviewed)

    durable_ledger = B2NAttemptLedger(ledger_path)
    event_ledger = PreDispatchEventLedger(event_ledger_path)

    completed = _completed_ordinals_from_ledgers(durable_ledger, event_ledger)
    remaining = sorted(m.sample_ordinal for m in manifest.members if m.sample_ordinal not in completed)

    physical_requests_consumed = event_ledger.physical_requests_attempted()

    if not remaining:
        return {
            "mode": "RESUME",
            "run_id": EXPECTED_RUN_ID,
            "status": "ALL_MEMBERS_COMPLETE",
            "completed_ordinals": sorted(completed),
            "remaining_ordinals": [],
            "physical_requests_consumed": physical_requests_consumed,
        }

    if physical_requests_consumed >= B2N_MAX_PHYSICAL_REQUESTS:
        raise RuntimeError("B2N_BUDGET_EXHAUSTED")

    next_ordinal = remaining[0]
    next_member = next(m for m in manifest.members if m.sample_ordinal == next_ordinal)

    transport = RedactingJsonRpcTransport(endpoint)
    adapter = MigrationGetTransactionAdapter(transport, projection)
    durable_client = DurableAccountingClient(
        adapter=adapter, transport=transport, event_ledger=event_ledger,
        run_id=EXPECTED_RUN_ID, provider=EXPECTED_PROVIDER, method=EXPECTED_METHOD,
    )
    # DurableAccountingClient fails closed on a second reservation for an
    # already-reserved ordinal (B2N_P3E_DUPLICATE_ORDINAL_ATTEMPT) -- an
    # independent guard even though `next_member` is already guaranteed to
    # be an uncompleted ordinal by construction above.

    entry = _reconcile_single_member(
        client=durable_client, member=next_member, run_id=EXPECTED_RUN_ID, provider=EXPECTED_PROVIDER,
    )

    if entry["sample_ordinal"] in completed:
        raise RuntimeError(f"B2N_P3G_REFUSING_TO_MERGE_ALREADY_COMPLETED_ORDINAL:{entry['sample_ordinal']}")
    durable_ledger.append(entry)

    new_completed = completed | {entry["sample_ordinal"]}
    return {
        "mode": "RESUME",
        "run_id": EXPECTED_RUN_ID,
        "status": "STEP_COMPLETE" if len(new_completed) < 20 else "ALL_MEMBERS_COMPLETE",
        "processed_ordinal": next_ordinal,
        "completed_ordinals": sorted(new_completed),
        "remaining_ordinals": sorted(set(range(1, 21)) - new_completed),
        "physical_requests_consumed": event_ledger.physical_requests_attempted(),
        "physical_requests_remaining_budget": B2N_MAX_PHYSICAL_REQUESTS - event_ledger.physical_requests_attempted(),
        "ledger_path": str(ledger_path),
        "event_ledger_path": str(event_ledger_path),
        "results": [entry],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="B2N-P3C isolated migration-lineage bounded-run runner.")
    parser.add_argument("--live", action="store_true", help="Perform the live network run. Default is dry-run.")
    parser.add_argument(
        "--resume", action="store_true",
        help="P3G: process at most one additional remaining member against EXISTING non-empty ledgers. "
             "Mutually exclusive with --live (fresh 20-member run).",
    )
    parser.add_argument(
        "--output", default=None,
        help=f"Path to write the JSON result. Defaults to {DEFAULT_RESULT_PATH} (never /tmp).",
    )
    parser.add_argument(
        "--ledger-path", default=None,
        help=f"Path to the durable attempt ledger. Defaults to {DEFAULT_LEDGER_PATH} (never /private/tmp).",
    )
    parser.add_argument(
        "--event-ledger-path", default=None,
        help=f"Path to the pre-dispatch reservation event ledger. Defaults to {DEFAULT_EVENT_LEDGER_PATH}.",
    )
    args = parser.parse_args()

    if args.live and args.resume:
        print(json.dumps({"error": "B2NP3CError", "message": "B2N_P3G_LIVE_AND_RESUME_MUTUALLY_EXCLUSIVE"}))
        return 1

    ledger_path = Path(args.ledger_path).resolve() if args.ledger_path else None
    event_ledger_path = Path(args.event_ledger_path).resolve() if args.event_ledger_path else None

    try:
        if args.resume:
            result = resume_run(ledger_path=ledger_path, event_ledger_path=event_ledger_path)
        elif args.live:
            result = live_run(ledger_path=ledger_path, event_ledger_path=event_ledger_path)
        else:
            result = dry_run(ledger_path=ledger_path, event_ledger_path=event_ledger_path)
    except (B2NP3CError, RuntimeError, ValueError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}))
        return 1

    text = json.dumps(result, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(text + "\n")
    elif args.live or args.resume:
        # The live/resume result is authoritative and must land on durable
        # repo storage by default -- never silently printed-only, never /tmp.
        DEFAULT_RESULT_PATH.write_text(text + "\n")
        print(json.dumps({"result_written_to": str(DEFAULT_RESULT_PATH)}))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
