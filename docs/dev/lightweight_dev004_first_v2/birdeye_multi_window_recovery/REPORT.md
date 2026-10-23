# DEV004 Birdeye Multi-Window Recovery

DEV004_BIRDEYE_MULTI_WINDOW_STATUS: HOLD — mandatory authority precondition fails in candidate `889f2399914ca5e0a79924fb8ea45bcfd096d32d`. Stop rule applied before constructing or running the multi-window acquisition. No executable correction, new database, provider call or production action.

## ACQUISITION_CHECKPOINT_AUTHORITY

The selected `runtime/creator_resolution/src/ops/watchtower_terminal_ath_finalizer.py` writes each successfully acquired chunk into `operation_monitor_observations` using deterministic chunk request identities. However, it does not read those completed identities on restart. It rebuilds the entire window list and loops over every request. Its retained-15m shortcut requires coverage of the complete finalization range; it does not establish unfinished-prefix resume.

Accepted price-worker revision `71b6e338cfb54057ec4984d41bfbe02477c90dba` contains `_completed_chunk_ids`, `_completed_chunk_candles`, a filtered remaining-window list and a `before_dispatch` hook. These are absent from the selected DEV004 finalizer. Exact source SHA256s and AST comparisons are recorded in authority_result.json. This is a concrete accepted-source composition gap, not a missing historical ATH candle or a claim that currently running V1 services failed.

## COMPLETED_WINDOW_PRESERVATION

Stored chunk observations are retained, but completed-window non-reacquisition is NOT QUALIFIED: candidate restart selection does not consume their identities. INSERT OR IGNORE can suppress duplicate stored rows; it cannot suppress repeat external requests. A pure `birdeye_window_ledger.py` identity/eligibility helper also exists, but it supplies no persistence owner or selected finalizer integration. No invented completion map or copied history was substituted.

## BUDGET_CONTINUITY

The candidate MonitorQueue persists provider_backoff.json and retry envelope fields, not consumed provider allowance. Its finalizer has no pre-dispatch callback and the selected queue has no `admit_provider_dispatch`. No selected durable debit/state-path binding can be established for this acquisition path.

Revision71b6e338 retains `MonitorQueue.admit_provider_dispatch` and `DevProviderBudget`, with pre-dispatch integration in its finalizer/worker. That budget uses provider_budget.json plus a lock and records calls with at/mint/class in a rolling60-second window; its defaults are20global/4per-token and the hook is DEV-runtime gated. These are retained source contracts, not proof that the current candidate uses them or that they are account-wide/lifetime counters. Missing/corrupt state and expiry semantics require qualification before any such integration can be accepted. No existing provider budget values were read, reset, recreated or increased.

Exact missing authority: a selected, persisted consumed-dispatch state owner and binding that is consulted before each acquisition and survives restart, together with the selected completed-window read/skip path. A test-only counter or provider-backoff timestamp would not satisfy this gate.

## INTERRUPTION_AND_RESTART_RESULTS

All five requested runtime interruption cases are NOT RUN under the stop rule: before provider request; after synthetic response/before persistence; after checkpoint commit; between windows; after finalization commit/before ACK. There is no qualified resume/budget composition to exercise without assembling different executable behaviour. No fake PASS is inferred from source comparison.

If a response occurred without durable outcome acknowledgement, external execution remains uncertain. Checkpoint evidence and conservative consumed allowance must govern any later bounded retry; exactly-once external HTTP execution is not claimed.

## RETRY_AND_DUPLICATE_SUPPRESSION

Existing assignment-derived queue identities and finalizer logical-job identity remain unchanged. EvidenceIntakeQueue deduplicates identities while present and preserves attempts through recover(); selected MonitorQueue configures max_attempts3. Birdeye binding is one attempt with45-second timeout. The pure same-window rate-limit helper retains an8-retry/60-second-wait ceiling; the selected queue also has a distinct429 deferral path, so that helper ceiling is not assumed to govern every queue retry. End-to-end restart/outage/exhaustion behaviour is not newly certified.

BIRDEYE credential authority remains the accepted exclusive, fail-closed helper; no fallback was added. Its existing synthetic credential qualification is reused, not repeated. No actual credential values/configuration were inspected or changed. Endpoints, request scheduling, rate policy and budgets were not modified.

## FINALIZATION_INTEGRITY

Existing ALREADY_FINALIZED suppression remains accepted and present. Logical terminal-job identity, committed canonical ATH and observation uniqueness are unchanged. No new finalization or observation was written. Exactly-once committed finalization across the requested interrupted multi-window lifecycle is NOT newly qualified; prior finalized-request suppression is not enough to assert it.

## PRICE_LIFECYCLE_EQUIVALENCE

Reuse the accepted opening-policy/provenance and lifecycle results and ATH numerical PASS within0.1%. Canonical ATH/timestamps/provenance/finalization identity remain untouched. No historical recomputation or original-candle recovery was attempted. These closed results do not prove incomplete-acquisition recovery.

## PROVIDER_FREE_TEST_RESULTS

Bounded static/AST authority qualification confirms the missing candidate methods/hooks versus retained accepted source. FLEX_STABLE_V2 peeled identity and ten accepted correction ancestors verified. Retained reference Core/OPS SHA256s match their accepted identities (8970240/13754368bytes); they were read for identity only, never opened for mutation or copied. No acquisition lifecycle tests or large prior matrices were rerun. Actual module imports/provider transports were not invoked. Provider calls0; live sockets0; new databases0; executable changes0.

Qualification artifacts are small source/JSON/Markdown only, with16,000,000-byte OS file cap. Raw retention false; new unbounded growth paths0. RUNTIME_DEPLOYMENT_REQUIRED=false — source-contract qualification and documentation only, no executable change or deployment.

## REMAINING_CUTOVER_GATES

M2 now has exact composition blockers: missing completed-chunk resume selection and missing consumed-budget admission binding in the chosen runtime. M1 discovery/publication, M3 final manifests/dormant contracts, M4 sustained file/WAL safety and M5 state reconciliation/rollback remain unchanged. Previously passing startup,100creator,616route, freshness, Walkback,11frozen and26Deep scopes are not reopened.

## NEXT_ACTION

A separately authorized minimum DEV004 composition correction should connect the accepted completed-window read/skip and pre-dispatch consumed-budget owner to this existing acquisition path, preserving the established budget scope and failure semantics. Do not transplant the full later Monitor runtime. Once authority is explicit, run the five requested provider-free interruption boundaries. This qualification stops here; no restoration, new ledger, schema change or implementation is performed.
