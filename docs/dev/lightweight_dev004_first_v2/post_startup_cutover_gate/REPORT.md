# DEV004 Post-Startup Cutover Gate

DEV004_POST_STARTUP_CUTOVER_STATUS: HOLD — startup compatibility is closed; five mandatory acceptance packages remain. No unresolved demonstrated regression was identified in the accepted fixed populations. Missing mandatory acceptance is identified below; historical uncertainty alone is not treated as a failure.

Candidate: `889f2399914ca5e0a79924fb8ea45bcfd096d32d`. This is read-only consolidation: no tests rerun, fixtures changed, providers/WebSockets used, production writes/deployment/restarts or cutover. RUNTIME_DEPLOYMENT_REQUIRED=false (documentation and read-only evidence only).

## ACCEPTED_RUNTIME_COMPOSITION

All accepted corrections coexist in this immutable commit: freshness ace58aba, Watchtower roles 4ff48a00, compact creator history 87d33147, 100-creator graph closure 968432da, price provenance 20d1025e, Walkback projection recovery 09ae1394, outage recovery 54ece3eb, Birdeye authority dd481183, ordinary compact packaging 712f1840, Core bootstrap ac37ec07 and startup integration 889f2399. Ancestry and all six startup source hashes freshly verified. Nine installed credential-payload hashes still match the accepted deployment manifest. FLEX_STABLE_V2 remains the immutable authority; no correction is reverted.

Reuse accepted 100/100 creator classifications, 616/616 Watchtower routes, 26 Deep cases, 11 frozen comparisons, two-candidate freshness, opening provenance/ATH tolerance and both Walkback recovery corrections. No matrix repetition is needed. Exact runtime Git trees and observation identities are in evidence.json.

The older runtime_composition/runtime_manifest.json is explicitly partial and pins 712f1840. Its old missing-schema/compact-reader/startup HOLD entries are superseded by startup_integration/REPORT.md, not current defects. It is not the final V2 deployment manifest or a golden baseline.

## ACTIVE_SERVICE_READINESS

Read-only Supervisor snapshot: listener RUNNING PID94632; resolver RUNNING PID52248; Walkback RUNNING PID94577. Current Supervisor configuration SHA256 still matches the accepted authority 0f1d9f3c03c422727a8e7bdb2ffe623f1c77e817910c0fbe063a4c954356623e. These are existing V1 processes, not deployed V2 processes; status alone does not prove prospective V2 operational acceptance.

Accepted startup boundaries are precise: listener actual constructor and listen() initialization through task-dispatch/gather, with background tasks held before execution; resolver actual initialization then clean zero-cycle stop; Walkback actual schema/startup recovery to first-cycle boundary. Fresh/repeat Core/OPS, compact reader, canonical CREATE/migration schema persistence, price snapshot persistence and quarantine packaging pass. Listener's existing separate CreatorRepository initializes on an explicitly declared disposable creator.sqlite (98304 bytes), not an unexpected package-local file. Final authority for this secondary store must be included in cutover; Core/OPS-only path substitution is insufficient.

These boundaries do not certify live subscription resumption, sustained daemon work or a database switch. Those are final separately authorized transition acceptance, not a reason to reopen schema startup.

## DORMANT_CAPABILITY_PRESERVATION

Supervisor currently shows API, monitor worker, monitor bridge, funding and Cascade STOPPED; related schedulers remain stopped. They must remain stopped during qualification and initial three-service cutover unless separately authorized.

No dormant service must be restored or activated merely to switch the active trio. Mandatory preservation is its accepted source, dependencies, evidence/schema, configuration authority and correct V2 bindings, with provider-free qualification of required price/operation/candidate projections. API/UI response shape, missing-evidence behaviour and price/lifecycle access cannot silently disappear from a claimed full replacement. Existing imports/selected readers are accepted; complete dormant application/bridge composition is still not pinned/qualified. Retained source revision 71b6e338 is accepted price-worker authority; absent historical worktree directories do not prove obsolete functionality and need not be recreated.

Funding/Cascade activation and monitor/bridge/API deployment are deferrable. A documented, reproducible preserved composition and data closure are required before claiming all accepted functionality retained; production activation itself is not a gate. Stop at an unavailable required artifact, rather than restoring unrelated runtimes.

## POTENTIAL_OPERATIONS_GATE

MANDATORY acceptance gap M1: one genuinely new candidate through the accepted upstream discovery/builder → stable candidate identity and associations → assessment/publication/review path → conditional post-Walkback dispatch. Existing tests prove both retained Living candidates, freshness mapping, generic publisher/restart, duplicate/conflict controls and post-commit callback isolation. The installed reverse resolver explicitly matches retained associations; this is not proof of global new-candidate discovery.

Exit: one bounded provider-free unseen-candidate case using the existing accepted discovery path, preserving assessment generations, rejection/review states and no automatic promotion, plus duplicate/restart and irrelevant/conflicting input controls. Do not invent a detector or activate a scheduler. If the upstream accepted path is unavailable, name that exact dependency and HOLD.

## BIRDEYE_RECOVERY_GATE

MANDATORY acceptance gap M2: unfinished acquisition windows/checkpoints, pending/retry state and consumed-budget recovery remain unqualified. Finalized-request ALREADY_FINALIZED suppression, canonical opening/ATH/lifecycle, BIRDEYE-exclusive authorization and accepted retries are closed. Missing original ATH candles are not a blocker after numerical PASS within 0.1%.

Exit: interrupt/reopen one bounded multi-window synthetic acquisition through accepted interfaces. Preserve completed-window identities and evidence, outstanding work, retry eligibility/ceiling and previously consumed allowance. Exercise failures before request, after request/before durable commit, and after commit/before ACK, distinguishing safe bounded at-least-once reacquisition from an impossible exactly-once external transport guarantee. Do not invent missing historical checkpoints or reset budgets. Check the retained finalized case stays suppressed. No real provider calls, credential inspection or budget change.

This is mandatory functionality preservation even while price monitoring remains dormant; live activation can wait.

## WATCHTOWER_UNRESOLVED_CASES

Four cases retain INSUFFICIENT_RETAINED_EVIDENCE and V2 TREASURY_EVIDENCE_CONFLICT: 2NjuvDto…pump, 2Nu94jFV…pump, 2UKkazis…pump and 2YGcLWQ5…pump (full identities and predicates in watchtower_roles/role_results.json). No bounded historical V1 oracle establishes unequal outcomes. All 99 demonstrated regressions are closed; all 616 qualified routes and accepted known-pattern controls pass.

These four may remain explicitly unqualified historical cases. Recovering missing proofs is not independently mandatory and no historical reconstruction is requested. Preserve their existing canonical outcomes, source rows, confidence/provenance, alternative-parent/quarantine/exclusion evidence and unresolved status in final reconciliation; never synthesize confirmed role links or overwrite accepted outcomes with a fresh conflict. Future launches relying only on those unresolved role pairs must remain fail-closed. Equivalence of confirmation for that uncovered pattern is not claimed. A demonstrated future-use dependency or lost canonical result would be a concrete blocker; uncertainty by itself is not one. This narrows the older blanket four-case HOLD to required state preservation and unchanged safety semantics.

## QUEUE_RECONCILIATION_GATE

MANDATORY part of M5, at one writer-quiesced boundary, not by copying every historical queue:

- Reconcile final canonical CREATE/migration/reference and compact-history deltas, plus candidate generations/mapping. Retain completed-investigation and projection identities needed to suppress reprocessing.
- Transfer pending/retry work with original stable identities, payload/provenance, attempts, next eligibility and last error. Preserve quarantine, exclusions and unresolved conflicts without reactivation.
- Account for each running claim/in-flight transaction. Expire/reclaim only leases whose old owner is confirmed quiesced, through existing recovery semantics; never copy stale live ownership or clear attempt counts.
- Reconcile Core handoff intent against OPS admission/queue/canonical attribution/membership/projection and acknowledgements. Reuse bounded projection recovery; do not re-run completed investigations or assume cross-database commits atomic.
- Match receipt/dedup/ACK identities and pending temporary receipts; preserve price queue/checkpoint/finalization and consumed-budget state, including filesystem queues/outboxes and secondary CreatorRepository authority.

Exit: disposable bounded rehearsal with no missing admissions, duplicate consumers/provider work, reopened completed jobs or lost protected state; then final read-only/delta checks in the separately authorized maintenance window. Reference fixtures are not a current live cutover image.

## STORAGE_GROWTH_GATE

MANDATORY acceptance gap M4 before sustained V2 writes: enforceable operating bounds for Core/OPS, WAL, receipts, logs and transient files below 500000000 bytes per single file. Current filesystem availability is approximately 4.41 GB decimal at observation, not a reserved capacity guarantee.

Existing protections are real but scoped: compact receipts cap each record at4096 bytes, file population at4096 and require 2000000000 free bytes; pending temp files are included and capacity refusal preserves evidence rather than deleting it. Diagnostic JSONL handlers rotate at10MiB defaults with two backups; settings require final manifest pinning. Resolver's64MB WAL threshold requires a stalled checkpoint gap; this is detection/recovery logic, not a pre-commit hard WAL/file bound. Provider batches/attempt ceilings bound acquisition per invocation, not lifetime database growth. The qualification16MB RLIMIT/page limits apply only to disposable tests. No production hard cap for all Core/OPS/WAL growth has been demonstrated. Small seed size and successful startup do not satisfy this gate. Receipt admission failure must remain explicit and restart-safe; no receipt retirement is authorized.

Exit: qualify existing fail-closed capacity controls for the intended write paths and transients. If an enforcing mechanism is absent, record that exact safety gap; do not expand into new infrastructure/retention engineering. DEV003 telemetry controls are not assumed deployed or sufficient.

## CUTOVER_ROLLBACK_GATE

MANDATORY M3: freeze one deployment manifest for candidate889f2399: per-component package/entrypoint and target preimages; Python/dependency authority; accepted schemas/bootstrap order; seed/reference/history/freshness hashes and permanent paths; all Core/OPS/secondary-store and filesystem-queue bindings; feature flags and unchanged stopped states; BIRDEYE credential name only; existing budgets/consumed state; receipts/quarantine, logs and enforceable limits. Pin accepted dormant component source/dependency authority and required offline projections without restoring services. Current partial manifest and /private/tmp fixtures alone do not meet this requirement.

MANDATORY M5: a consistent switch/rollback rehearsal, followed only with separate production authorization. Freeze V1/final candidate; quiesce writers; reconcile final deltas and claims; bind both databases and all consumers consistently; verify identities before one-consumer resumption; verify natural intake/resolution/Walkback plus relevant retained projections. Preserve V1 unchanged for rollback. Before resumption establish rollback handling of any post-switch V2 writes: either a verified bounded reverse reconciliation or a defined abort-before-new-writes boundary. Merely flipping paths back after writes is unsafe; source rollback does not roll back databases. V1 retirement remains separately authorized after acceptance and rollback release.

## MANDATORY_VS_DEFERRABLE

| Required before controlled full operational replacement | Can remain deferred |
|---|---|
| M1 New-candidate discovery/publication continuity | Broad historical discovery reconstruction or scheduler activation |
| M2 Unfinished Birdeye checkpoint/retry/budget continuity | Live monitor activation; exact missing original ATH candles |
| M3 Final reproducible config/artifact bindings and preserved offline dormant contracts | Restoring absent historical paths; starting API/bridge/funding/Cascade |
| M4 Enforceable ongoing single-file/transient safety | Further seed minimization, telemetry compaction, V1 retirement |
| M5 Final state reconciliation and paired authority/rollback rehearsal | Production execution until separately authorized |

Four evidence-limited Watchtower cases may retain their explicit historical limitation with required protected-state preservation. No new proof search, broad database audit or repeated functional matrix is required. No new demonstrated regression was found by this consolidation.

## GO_HOLD_DECISION

HOLD for M1–M5 missing mandatory acceptance, not for closed startup/schema work or dormant service state. No cutover GO is justified yet. Closed milestones remain closed.

## NEXT_ACTION

One bounded DEV004 provider-free unfinished Birdeye acquisition/restart qualification (M2), using one synthetic multi-window job and the existing accepted adapter/queue/persistence interfaces. Demonstrate completed-window suppression, retry/crash recovery and consumed-budget continuity without new allowance. This directly closes the remaining non-negotiable price-pipeline gap; stop with the exact missing persisted contract if it cannot be demonstrated. Do not implement, activate services or fetch provider evidence as part of this read-only gate.
