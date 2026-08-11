# X78.35 — Outgoing Transfer Ownership & Completion-Barrier Separation

Date: 10 August 2026  
Branch: `classification-attribution-axis`  
Baseline HEAD: `cb1fc110e105436c4baa9fe15f956628f80db3ce`

> Baseline note: the completed X78.34 implementation exists in the current
> working tree but has not been committed into the named HEAD.

Machine-readable audit: [x78_35_qualification.json](./x78_35_qualification.json)

## Separation gate

**BLOCKED — required consumers currently interpret missing outgoing rows as
negative evidence, and the system has no atomic durable handoff plus explicit
pending/completeness contract that would prevent that interpretation.**

No production or runtime changes were made. Outgoing scanning remains inside
the Creator Funding completion barrier.

## Current producer and completion contract

`extract_funding_for_new_token` first performs authoritative creator-history
and funding extraction. It then awaits Jito, deBridge, Axiom and
`extract_outgoing_transfers` concurrently. The latter performs
`getSignaturesForAddress`, fetches each post-migration transaction, derives SOL
outflows and writes `creator_outgoing_transfers` one transfer/commit at a time.

Only after the complete gather returns does `_process_job`:

1. verify `creator_funders` exists;
2. mark `creator_funding_queue.status='complete'`;
3. enqueue the funding rescore;
4. update `token_analysis.funding_extracted_slot`.

Therefore terminal funding status currently means that an outgoing scan was
*attempted before completion*. It does not prove outgoing completeness:
`extract_outgoing_transfers` catches exceptions and returns partial results,
and no scan status or coverage is persisted. Consumers cannot distinguish:

- complete scan with zero outflows;
- partial scan;
- failed scan;
- scan not yet run.

This weakness already exists, but moving the scan would make the ambiguous
window normal and materially larger.

## Consumer findings

| Consumer | Class | Blocking behaviour |
|---|---|---|
| WATCHTOWER detector/lifecycle | C — recomputable derived | Missing row leaves `has_profit_relay=false`; no pending state |
| Discovery value / cluster integrity | C | Zero counterparties can return `coverage=1.0`, “fully mapped” |
| C2C edge projection | C | Incremental rebuild deletes source edges then writes zero |
| Outbound classifications | C | Missing rows produce no return/shared-payout/hub relationship |
| Risk and launch scoring | C | Score omits outgoing signal and may be published current |
| Network / Operational Intelligence | C | Downstream projections can refresh before evidence arrives |
| Evidence reconciliation / settlement | B — durable eventual | “Unavailable” does not distinguish pending from absent |
| API/UI outgoing views | B | Empty list/count has no pending quality state |
| Monitoring | B | Creator Funding exposes no outgoing completeness signal |
| Second-hop enqueue | D — optional | Depends on `creator_funders`, not outgoing rows |

All dependencies are mapped, but most are only recomputable in principle.
There is no durable outgoing-complete event that invokes those incremental
builders in the required order.

## Existing `creator_outbound_worker` is not an equivalent owner

The repository has a separate queue and worker, but it cannot be substituted:

- disabled by default;
- optional API thread, not an independently supervised process;
- direct synchronous `requests` rather than Shared Transaction Acquisition;
- outside the existing global async RPC semaphore;
- recent 100-signature lookback, not strictly post-migration semantics;
- ignores transfers below 0.1 SOL;
- different exclusions and transfer parser;
- empty/failed RPC result can be marked `done`;
- `scanning` has no stale-running recovery;
- no atomic handoff from Creator Funding;
- generic enqueue requires a pre-existing classification;
- no universal pending/completeness state;
- no deterministic dependent-recompute notification.

Its current queue contains 312 pending and 233 done creators. Reusing it would
change evidence semantics and could introduce uncontrolled RPC concurrency.

## Crash boundary

The required ordering is not available today:

```text
authoritative funding persistence
    ↓
durable outgoing obligation
    ↓
funding terminal transition
    ↓
slot release
```

There is no transaction that atomically creates the obligation and marks the
funding row terminal. Moving the call now creates a crash window in which the
funding row is complete and the required outgoing obligation never exists.

## Counterfactual capacity model

Using 117 X78.34 per-job ledgers and the actual sequential/concurrent path:

| Slot occupancy | Mean | p50 | p95 |
|---|---:|---:|---:|
| Current | 26.865s | 22.034s | 65.153s |
| Modeled without outgoing barrier | 9.385s | 6.470s | 28.035s |
| Outgoing critical-path contribution | 17.480s | 12.883s | 58.288s |

The idealized two-slot model is approximately 767 funding jobs/hour, versus
222/hour measured in X78.34. This is not a deployable capacity result: it omits
the RPC and persistence cost of an independent outgoing owner, which does not
yet exist.

X78.34's 276/hour “arrival” figure was all unique queue state entries, not
pure organic migrations:

- 42/hour: live `pf_ws_creator_existing_migration` obligations;
- 234/hour: Creator Resolution recovery obligations;
- 12/hour: retry events;
- 222/hour: logged terminal completions;
- 1,000/hour: expiries.

This explains the discrepancy with organic migration telemetry and prevents a
false claim that current chain births alone generate 276 new obligations/hour.

## What a future separation must add

Before this can be implemented safely, a separately approved contract must
provide all of the following together:

- atomic funding-terminal/outgoing-obligation handoff;
- creator-keyed idempotent queue identity;
- pending/running/retry/complete/failed and stale-running recovery;
- exact current post-migration scan semantics;
- Shared Transaction Acquisition and global ceiling participation;
- explicit outgoing completeness visible to every reader;
- pending-safe WATCHTOWER and Discovery logic;
- deterministic incremental C2C/outbound/risk/network/intelligence refresh;
- bounded shutdown, cancellation, restart and dead-letter behaviour.

Until then, absence of rows can become evidence of absence, triggering the
milestone's mandatory stop condition.

Validation remained read-only: 61 focused regression tests passed across the
funding scheduler, creator dedupe, fast paths, freshness, accounting, read and
cancellation boundaries, extractor concurrency, risk scoring, evidence
semantics and intelligence-refresh single-flight. All supervised production
services remained on their existing PIDs; no qualification restart occurred.

## Final verdicts

Outgoing Ownership: **A — FULLY_MAPPED**

Completion-Barrier Separation: **D — BLOCKED_BY_UNPROVEN_SEMANTICS**

Durability: **C — MATERIAL_GAP**

WATCHTOWER Correctness: **C — MATERIAL_RISK**

Two-Slot Capacity: **D — NOT_MEASURABLE**

Production Health: **C — HEALTHY_SERVICES_CAPACITY_NOT_READY**

Evidence Activation: **HOLD**

Acquisition: **HOLD_ACQUISITION**

## Final answer

Outgoing-transfer scanning cannot currently be removed from the scarce Creator
Funding completion barrier without weakening evidence semantics. The blocker
is not a hard need for synchronous execution; it is the absence of durable
ownership, completeness signalling and recomputation contracts needed to make
eventual execution safe.

The next performance investigation should target **DEEP_HISTORY_PAGINATION**.
A third extraction slot remains unauthorized.
