# OIP v2.2E.2B2N provider-qualification contract

## Status and decision boundary

This document is a design-only contract. It authorizes no provider/RPC request,
production observation or mutation, deployment, service restart, configuration
change, or activation of Evidence Mirror or Cohort mode. Executing any part of
the proposed qualification requires a later, explicit human authorization that
names the provider-work milestone and request budget.

B2N is limited to qualifying whether B2M's `observation_required` migration jobs
can obtain the evidence needed for creator-funding observation through the
existing acquisition path. It does not select a new provider, introduce a new
retry policy, change acquisition architecture, or establish production fitness.

## Local evidence basis

- B2M preserves `observation_required` from reviewed migration enqueue paths to
  `extract_funding_for_new_token`, bypassing only the known-creator fast path and
  retaining current-mint acquisition lineage.
- The preceding prospective census closed at 20 distinct qualified migrations;
  those first 20 qualifying records were a fixed, one-to-one mint cohort.
- OIP v2.1C demonstrated a tested physical-attempt budget guard and successful
  first-attempt historical acquisition, but it does not prove current provider
  behavior or authorize new requests.
- Production migration remains outside this design. The prior preflight failed
  closed with unresolved production Evidence authority and degraded platform
  health.

These facts bound the experiment; they do not imply that it may be run.

## Proposed sample contract

After separate human authorization, qualification would use exactly the first
20 distinct post-checkpoint migration mints from the already closed B2K census.
The cohort must be materialized locally before the first request as an ordered,
immutable manifest containing only `sample_ordinal`, `mint`, and the census
identity fields needed to prove membership. Its digest and source checkpoint
must be recorded before execution.

Eligibility requires a valid mint, a unique mint within the cohort, a B2M
reviewed-migration origin, and `observation_required = true`. No replacement,
backfill, cherry-picking, cohort growth, or live intake is permitted. An invalid,
missing, duplicate, or ambiguously sourced member stops the whole run before any
provider request.

## Request budget

The total budget is **20 physical provider requests**: at most one request for
each of 20 mints. A physical request means any outbound RPC/HTTP attempt that
reaches a provider client, whether it succeeds, times out, is rate-limited,
returns malformed data, or otherwise fails.

Retries, failover, pagination that creates another request, background
prefetching, concurrent duplicate requests, and requests for dependency
signatures outside the frozen member's single existing acquisition entry are
not allowed. Cache hits consume zero requests but must be reported separately
and cannot count as provider-qualified successes. The run must stop before
attempt 21. Uncertainty in the attempt counter is treated as budget exhaustion.

Authorization must identify the permitted provider and endpoint family. Missing
or different provider configuration is a stop, not permission to substitute a
provider.

## Record and field semantics

Each sample member must produce one append-only qualification result with these
logical fields:

| Field | Required semantics |
|---|---|
| `contract_version` | Literal version of this reviewed contract. |
| `run_id` | Identifier unique to the separately authorized run. |
| `sample_ordinal` | Frozen 1-based position in the 20-member manifest. |
| `mint` | Exact mint from the frozen B2K cohort; never provider-derived. |
| `observation_required` | Must be boolean `true`; otherwise stop. |
| `provider` | Human-authorized provider identifier actually addressed. |
| `request_count` | Physical requests attributable to this member: only `0` or `1`. |
| `request_outcome` | One of `NOT_ATTEMPTED`, `CACHE_HIT`, `SUCCESS`, `TIMEOUT`, `RATE_LIMITED`, `TRANSPORT_ERROR`, `RPC_ERROR`, `MALFORMED_RESPONSE`, or `BUDGET_STOP`. |
| `provider_signature` | Signature returned or resolved by the existing acquisition path; nullable on non-success. It must not replace `mint` as cohort identity. |
| `provider_slot` | Provider-reported chain slot, when present; nullable and never synthesized. |
| `provider_block_time_utc` | Provider-reported chain time normalized to UTC, when present; nullable and never substituted with receipt time. |
| `request_started_utc_ns` | Local wall-clock UTC nanoseconds captured immediately before the outbound attempt. |
| `response_received_utc_ns` | Local wall-clock UTC nanoseconds captured when the attempt terminates; nullable only when no request was made. |
| `elapsed_monotonic_ns` | Duration from a monotonic clock; authoritative for latency and non-negative. |
| `evidence_observed` | Boolean indicating that the existing acquisition path produced the required creator-funding evidence for this mint. |
| `provenance_complete` | Boolean indicating preservation of mint, signature, provider, acquisition scope, and observation lineage. |
| `error_class` | Stable local failure class; nullable only for `CACHE_HIT` or `SUCCESS`. |

Wall-clock timestamps order recorded events within this run but do not prove
chain ordering. `provider_slot` is authoritative for chain position when
available. `provider_block_time_utc` describes provider-reported chain time.
`request_started_utc_ns` and `response_received_utc_ns` describe local request
handling. Latency is calculated only from `elapsed_monotonic_ns`; wall-clock
subtraction must not be used. All UTC values use an explicit `Z`/UTC rendering
when serialized for review. Missing provider timestamps remain null.

## Success criteria

The qualification passes only if all of the following are true:

1. The frozen manifest contains exactly 20 eligible, distinct mints and its
   recorded digest remains unchanged.
2. The cumulative physical-request count is at most 20, every member consumes
   at most one request, and the attempt ledger reconciles exactly to client
   instrumentation.
3. All 20 members have `request_outcome = SUCCESS`, `evidence_observed = true`,
   and `provenance_complete = true` from an actual authorized provider request;
   cache hits are reported but make the run non-qualifying.
4. Every success retains the frozen mint and current-mint acquisition lineage;
   no result is attributed by timestamp proximity or cohort position alone.
5. Required local and provider fields obey the nullability, enumeration, and
   timestamp semantics above, with no synthesized provider values.
6. No retry, failover, replacement sample, unrelated provider request,
   production write, service/configuration change, or mode activation occurs.

Any lesser result is `HOLD_PROVIDER_QUALIFICATION`, not a partial pass. Failure
data may support a later reviewed design, but cannot widen this contract.

## Fail-closed stops

Stop before the first request if the human authorization, named provider,
endpoint family, immutable cohort, manifest digest, attempt ledger, or required
instrumentation is absent or ambiguous. Also stop if executing the design would
require provider-client changes, a new endpoint, new credentials, RPC behavior
changes, production access, or architectural expansion.

After execution begins under later authorization, stop immediately on the first
non-success outcome, cache hit, provenance mismatch, malformed required field,
timestamp-semantics violation, counter uncertainty, attempted retry/failover,
unexpected outbound request, or any production impact. Record the triggering
member and the requests already consumed; do not replace the member or resume
without another human decision.

## No-production boundary and later gate

Qualification, if later authorized, must run against an explicitly designated
non-production fixture or isolated qualification environment. It must not read
from or write to production databases or queues, attach to production services,
observe live production traffic, deploy code, restart processes, alter provider
plans or credentials, or enable Evidence Mirror/Cohort mode. A passing result
demonstrates only the bounded provider contract for the frozen sample.

Provider execution and every subsequent production observation, mutation, or
activation are separate milestones. Each requires new explicit human
authorization after review of this contract and, for production, renewed proof
of authoritative database identity and healthy production preconditions.
