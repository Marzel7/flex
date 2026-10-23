# DEV004 consolidated V2 cutover readiness

`DEV004_CONSOLIDATED_CUTOVER_STATUS: HOLD — accepted component qualifications retained; complete operational replacement not yet qualified.`

Read-only consolidation against source candidate `54ece3ebc77471a85acac2b7b282b3b184cc7da4`. No functional test was repeated. Only Git ancestry, retained evidence, selected file identities and static package wiring were inspected. No production DB connection, source/config deployment, service action, provider call, socket connection, seed expansion or cutover. This report and handoff are documentation only: `RUNTIME_DEPLOYMENT_REQUIRED=false`.

## REPRODUCIBLE_CANDIDATE_COMPOSITION

All eight accepted corrections coexist in one immutable source commit, `54ece3eb`: freshness `ace58aba`, roles `4ff48a00`, compact creator `87d33147`, creator graph closure `968432da`, price provenance `20d1025e`, projection recovery `09ae1394`, outage recovery `54ece3eb`, and credential correction `dd481183`. See `evidence.json` for full identities and ancestry. FLEX_STABLE_V2 remains `2d825bbcf27f802bd0bfcef90cedeba43cc968eb`; manifest SHA256 remains `f0d3d933109f3572023af677729feef4db6e432657c7b7095b5bae05f2821dc7`.

**Source coexistence is verified; complete reproducible runtime composition is not.** The committed manifest explicitly describes provisional FLEX_STABLE_V2 recovery packaging, contains no DEV004 configuration binding, and calls itself neither a golden baseline nor a deployment instruction. There is no qualified single manifest pinning all DEV004 entrypoints, import/overlay rules, flags, dependency environment, seed/schema hashes, compact history and candidate freshness mapping together.

Concrete compatibility gap: the compact evaluator/helper are in `runtime/creator_resolution/src/ops/`; `runtime/walkback/src/ops/attribution_outcome.py` is a different, non-compact copy, and its compact helper is absent. The accepted full Walkback harness explicitly injects the creator package's evaluator through `sys.modules` (see `walkback_provider_outage/outage-restart-child.py`, lines 36–38). Ordinary package launch has not been shown to select that composition. Likewise, projection recovery requires `DEV004_WALKBACK_PROJECTION_RECOVERY_ENABLED=1`; compact history requires `DEV004_CREATOR_HISTORY_PATH`; the freshness mapping requires its explicit binding. These are qualified mechanisms, not a frozen daemon launch manifest. Do not label these separate test assemblies a golden baseline.

All nine deployed Birdeye payload hashes still match the existing deployment manifest; credential deployment remains closed. Accepted price-worker commit `71b6e338cfb54057ec4984d41bfbe02477c90dba` remains retained in Git. The three previously absent price-runtime directories remain absent; their absence does not establish obsolete functionality or require recreation of those paths.

## ACCEPTED_FUNCTIONAL_EQUIVALENCE

| Accepted surface | Retained evidence / scope |
|---|---|
| 100/100 creator profiles and classifications | `creator_classification_closure/REPORT.md`; graph references preserve FORMING/0.4; no historical launch bulk required |
| 616/616 Watchtower routes, including all 99 repaired cases | `watchtower_roles/REPORT.md`, `all_621_routes.json`; four separate unknown cases below are excluded from the exact-comparison denominator |
| 26/26 Deep cases | Retained diagnostic routes match; REVIEW_ONLY/capital-continuity UNQUALIFIED remains unchanged |
| 11/11 frozen comparisons | Latest `walkback_provider_outage/frozen.json`; known case, not whole-product certification |
| Potential Operations freshness | `freshness_correction/REPORT.md`; two Living candidates, generation continuity, duplicate suppression, assessment/review history and restart |
| Opening provenance / ATH | `price_provenance/REPORT.md`; exact opening policy; ATH difference 0.0025616522% within 0.1%; canonical values, timestamps, provenance and finalization identity unchanged |
| Fresh birth/migration and queue interfaces | Existing intake controls plus `walkback_end_to_end/REPORT.md`; actual installed interfaces with synthetic inputs, not complete daemon bootstrap |
| Complete normal Walkback | Actual decoding, treasury → SubProvider → funding account → creator → launch, HIGH canonical attribution and membership; nine synthetic requests |
| Post-commit projection recovery | `walkback_projection_recovery/REPORT.md`; 24 groups, canonical evidence unchanged, bounded missing-projection replay |
| Provider-outage recovery | `walkback_provider_outage/REPORT.md`; 11 outage and five fresh-process/partial-request groups; no false evidence verdict; bounded retry exhaustion |
| Direct dependency regressions | Latest outage evidence retains creator/route/freshness/frozen/Deep/price and queue/handoff/quarantine passes |

These accepted scopes are closed. Earlier report HOLDs for stale cursors, 99 role regressions, 100 creator classifications, opening provenance and the two Walkback recovery defects are superseded by their accepted corrections, not carried forward as failures.

## MANDATORY_UNQUALIFIED_GATES

Six remaining acceptance packages cover the outstanding requirements without another full audit:

| Gate | Exact missing acceptance / blocker | Smallest exit evidence |
|---|---|---|
| G1 — One executable runtime composition | No DEV004 launch/config/seed manifest; concrete cross-package compact-evaluator wiring gap; full listener/resolver/Walkback bootstrap and shared-writer compatibility not qualified. Accepted dormant price monitor/bridge/API/UI composition and response contracts are not assembled. | Pin existing source entrypoints/imports, flags, dependencies and artifacts in one DEV configuration manifest; exercise only missing daemon/bootstrap and accepted API/UI/monitor/bridge boundaries offline. Stop at any unavailable required package; do not restore services here. |
| G2 — Operational reference authority | Four exact role cases lack retained indexed witnesses; broader quarantine/verified-edge selection caps and external review/ranking/census dependencies are not fully closed. Existing independent snapshots are not current authoritative cutover state. | Preserve all current evidence and fail-closed statuses; provide retained keyed witnesses/frozen V1 decisions for the four, or an explicitly accepted unchanged historical limitation. Certify only required unresolved consumers and final reference delta. No speculative seed expansion. |
| G3 — Global Potential Operations | Two-candidate freshness and generic publisher pass; no accepted full new-operation discovery case through upstream builder → candidate identity/association → assessment/publication/review → conditional post-Walkback integration. | One frozen genuinely new candidate through the accepted installed discovery/publication path, plus duplicate/conflict/no-promotion and restart controls; identify its exact retained input closure. No invented detector or scheduler activation. |
| G4 — Unfinished Birdeye acquisition | Finalized restart suppression passes; durable unfinished per-window cursor/completion map, pending/retry envelope and consumed account-budget continuity do not. | Interrupt/reopen one bounded multi-window synthetic acquisition, preserving completed windows, request identity, retry ceiling and consumed budget without granting fresh allowance. Keep existing bounded at-least-once transport semantics. |
| G5 — Sustained storage safety | Compact fixtures and observed WAL do not demonstrate enforceable production file/transient bounds for Core/OPS, ongoing evidence/observations, WAL or receipts. | Qualify existing cap/fail-closed and recovery mechanisms below 500,000,000 bytes per file, including WAL/temporary outputs; finite queue/provider growth. If existing mechanisms cannot enforce this, HOLD rather than expand into new retention infrastructure. |
| G6 — Consistent state transition and rollback | No coherent final V1 snapshot/delta boundary; live pending/retry/quarantine/handoff, receipts/dedup, generation/budget state and cross-database authority switch are not reconciled. Source-only rollback is not database rollback. | Disposable rehearsal of a writer-quiesced boundary, exact final reconciliation, consistent Core/OPS binding, single-consumer resumption and rollback preserving post-switch writes. Separate production authorization remains mandatory. |

## DEMONSTRATED_REGRESSIONS_REMAINING

No unresolved demonstrated V1/V2 decision regression remains in the accepted fixed populations. The concrete packaging/import gap is an **unqualified composition boundary**, not evidence that the deployed V1 service is broken. G2's four cases are insufficient retained evidence, not demonstrated unequal decisions. G3–G6 are missing mandatory acceptance, not inferred failures. Unknown row disposability is not a functional defect.

## REFERENCE_SEED_AUTHORITY

The retained graph-closure reference fixture contains **28,473 rows**: original operational seed 28,454 + 10 role-reference rows + nine creator graph rows. Core is 8,970,240 bytes; OPS 13,754,368 bytes; combined 22,724,608 bytes (21.67 MiB), plus immutable creator history 1,218,041 bytes. Current hashes are recorded in `evidence.json`; these files were not changed. Synthetic per-test descendants add only their case/schema/control data and are not a final production initializer. No final size extrapolation is made.

The seed retains ten identities: WATCHTOWER, WATCHTOWER_DEEP, Byzantine, Nexus, 3SW2, P3R_13A04, P3R, P3R_EC1, WSOL_PROVISION_CLOSE_1_SOL_MINUS_15K, FOUR_STEP_30_SOL_14_479K_WSOL_LADDER. All 1,165 assignments, including 762 NULL event_id rows, retain their original authority/status; no invented provenance or confidence promotion. Treasury/role evidence, selected/alternative parents, exclusions, candidate history, compact creator statistics and price/lifecycle facts remain intact. Existing reference manifests and extensions remain the row authority; this consolidation does not recategorize or minimize them.

Four separately unqualified Watchtower mints:

- `2NjuvDtoj4HqSNGqMYBs4wBotq1e92gyHCCyEPKZpump`
- `2Nu94jFVHUE7MWVsS6V9zfqYi9js8UYd93yydkbvpump`
- `2UKkazisyXfWUyLT4eqCgvEV6KNaxqwkHU49htQppump`
- `2YGcLWQ5NZ5qijs9UNBoFcAWREYa1Hpc6QwouAPPpump`

Exact treasury/recipient predicates and bounded lookup limits remain in `watchtower_roles/role_results.json`. A fifth formerly interrupted case has a verified shared witness, but is not relabelled as an exact historical V1 comparison. Do not infer absent evidence from an interrupted/unindexed search. Unknown seed dependencies are preserved, not grounds for bulk acquisition.

Historical limitations that need not block by themselves: missing original ATH candles after numerical PASS; NULL event IDs with preserved authority; unchanged REVIEW_ONLY Deep outcomes; 19,115 earlier UNKNOWN disposal classifications; historical inactive runtime paths. The four role cases remain mandatory HOLD until preservation of their operational behaviour or an explicit limited-coverage acceptance is established; no invented roles.

## BIRDEYE_PRICE_READINESS

Credential authority is complete and remains `BIRDEYE` exclusively, fail closed; no credential values were read. Opening policy, stored lifecycle/ATH evidence, finalized suppression and selected synthetic acquisition controls pass. G4 remains the unfinished acquisition/budget/restart blocker. G1 covers dormant executable monitor/bridge/API/UI compatibility. Missing directories are `/Users/kevinkeaveney/.codex/worktrees/dev020-runtime/flex`, `/private/tmp/flex-dev014-early-minimum-worker`, and `/Users/kevinkeaveney/.codex/worktrees/watchtower-monitor-bounded-soak/flex`; accepted source is retained, but a source revision alone does not prove a complete runnable composition. No stopped component needs activation for offline qualification. Do not reset budget or silently use legacy credentials.

## POTENTIAL_OPERATIONS_READINESS

The two Living candidates retain their identities, assessment history, confidence, review/promotion guards and generation mapping. This is not global discovery acceptance. Existing candidate association/workflow and external review overrides must remain authoritative. G3 is one missing end-to-end discovery/publication qualification, not a request for broad historical reconstruction.

## QUEUE_AND_HANDOFF_READINESS

Accepted synthetic admission, leases/retries, dedup, quarantine, Core→OPS commit/ACK, complete Walkback, missing-membership repair and provider-outage recovery remain PASS. Reference queue rows are inert historical evidence, not runnable final queues. G6 must reconcile claims/lease ownership, attempts/next_retry, quarantines/exclusions, CREATE/migration authority, admission/receipt and handoff ACK identities, completed suppression, member/outbox state, candidate generations and price budget/checkpoints at one final boundary. Do not copy stale leases or clear attempts. Core and OPS commits are not assumed atomic.

## STORAGE_GROWTH_BOUNDS

No new storage path is introduced by this consolidation. Earlier qualification used OS file cap 16,000,000 bytes and SQLite fixture page caps; bounded freshness metadata, compact history and projection cursor limits qualify those mechanisms only. Ongoing `token_analysis`, Walkback evidence, monitor observations, assessment history and receipt/WAL retention remain runtime-wide coverage gaps. Seed size and post-transaction WAL measurements do not prove future maxima. DEV003 telemetry work is not silently installed or assumed to supply those bounds. G5 is required before sustained V2 writes; no new infrastructure or compaction is proposed here.

## CUTOVER_AND_ROLLBACK_FEASIBILITY

**Conceptually feasible; executable safety unqualified (G6). No transition step was executed.** Minimum sequence for a later separately authorized maintenance window:

1. Freeze final source/configuration/schema/reference/history/freshness manifests and V1 authority identities; record all writer/consumer database bindings and rollback prerequisites.
2. Quiesce only authorized writers at a known completion boundary; drain or explicitly account for in-flight transactions/claims and handoff/receipt acknowledgements. Keep stopped services stopped.
3. Reconcile canonical/reference deltas and operational state against that boundary. Map database-local generations through the accepted mechanism; preserve budgets and completed windows. Verify no missing admissions or duplicate consumers.
4. With writers still quiesced, bind **both** Core and OPS consistently for every intended component and verify identities before resumption. Do not expose a mixed V1/V2 pair.
5. Resume the authorized listener/resolver/Walkback composition and verify natural births/migrations, resolution, completed investigation/projection and queue health. No deliberate test provider request is implied.
6. Verify separately authorized Birdeye acquisition and accepted API/UI capability using the qualified composition and unchanged budgets; dormant activation requires explicit scope.
7. Preserve V1 as rollback authority, with an explicit journal/delta reconciliation plan for any V2 writes after switching. Before rollback, quiesce writers and reconcile accepted new records/acknowledgements; a path flip alone is unsafe. No dual writers.
8. Retire V1 only after explicit operational acceptance and rollback release. No deletion or compaction is authorized by readiness or switch approval alone.

## MINIMUM_NEXT_ACTION

**One bounded DEV004 executable-composition qualification (G1), without rerunning the passing matrices:** pin `54ece3eb`, the current compact seed/history/freshness artifacts and intended source/entrypoint/import/flag bindings in a single reproducible manifest. Resolve the observed Walkback compact-evaluator selection boundary in DEV only, then test those missing bootstrap/module/shared-writer boundaries with blocked transports. Name the accepted dormant price/API/bridge source composition or stop with its exact missing package/dependency. This creates the stable target needed for G3/G4 and a later cutover rehearsal; it does not authorize restoration, production deployment or another DEV.

## GO_HOLD_DECISION

**HOLD.** The accepted component work is retained; six mandatory acceptance packages above remain. No evidence supports a production cutover GO or a golden runtime baseline yet. No resolved defect is reopened, no historical optimization is requested, and no live action is authorized.
