# DEV004 Potential Operations discovery and publication

DEV004_POTENTIAL_OPERATIONS_STATUS: HOLD — accepted new-candidate admission/publication boundary is not established.

CANDIDATE_SHA: 506fe25c941eadf379ca98e4f0d42c1a2486dc9c. Inspection HEAD 4470b89dfede6ff043a6245f8ca9be4101648d50 differs only by the prior collision report. FLEX_STABLE_V2 resolves to 2d825bbcf27f802bd0bfcef90cedeba43cc968eb; freshness correction ace58aba is ancestral. No executable source changed during this qualification.

## FRESH_DISCOVERY / CANDIDATE_IDENTITY
Actual accepted pure matcher `runtime/creator_resolution/src/ops/potential_candidate_matcher.py:21` matches supplied qualified specifications only. Synthetic unseen signature ((1, PLAIN_XFER, 200),) returns NO_MATCH; empty registry also produces no candidate. The accepted P3R constructor can deterministically derive p3r-v2-c7e8bff748aafc42110f, but computing an ID is not candidate admission. This matcher is byte-identical to the installed main-worker copy.

## ASSESSMENT_AND_ASSOCIATIONS / PUBLICATION
`living_potential_operations.py:_generic_living_registry` explicitly contains the two retained WSOL/transfer candidates. `_generic_runtime` resolves events through their existing association ledger before assessment. Freshness mapping preserves their generations; it is not global discovery.
`potential_operations.py:normalize_potential_operation_workflows` reads RANKING families from the frozen run p3r-v2-2dec1d40604c1f7c08c8. `rows` reads workflow provenance/frozen_row or frozen defaults. This module is byte-identical to the installed main-worker copy.
`scripts/materialize_potential_route_activity_snapshot_v2.py:build` calculates route IDs but explicitly skips IDs absent from the existing projected candidate population. It is an activity snapshot, not new-candidate admission, and contains whole-population reads; it was not executed.
The generic atomic assessment publisher does not establish the missing new-candidate discovery → workflow/publication connection. Manually inserting a new identity or registry entry would fabricate the very boundary under qualification.

## REVIEW_AND_PROMOTION_SAFETY / KNOWN_OPERATION_SEPARATION
Focused controls preserve INSUFFICIENT_INPUT, MULTI_MATCH and qualification-state exclusion. No promotion, membership, confidence, review or negative-evidence rows were changed. No claim is made that these isolated controls qualify full new-candidate review/publication. Watchtower/Deep/Byzantine/Nexus and prior frozen equivalence remain unchanged; their passing matrices were not repeated.

## CRASH_RESTART_RECOVERY
New-candidate before/after commit, assessment continuity and repeated-restart tests are blocked before admission. Existing retained-candidate freshness/restart evidence is reused but does not satisfy this distinct gate. No crash results were invented.

## STORAGE_BOUNDS
No fixture/database created or opened. No historical scan, producer, scheduler or worker executed. Only compact report/result artifacts were written (each below 32 KiB); no raw retention or new unbounded growth path. Candidate-generation runtime/storage bounds remain unqualified because the accepted admission owner is not established.

## PROVIDER_FREE_TEST_RESULTS
8/8 focused pure-interface checks pass: known match, unseen no-match, empty registry, insufficient input, collision, unqualified exclusion, deterministic ID, distinct ID. These are boundary diagnostics, not end-to-end acceptance. result.json records source hashes. Imports were restricted to the two pure stdlib modules; zero database connections or provider calls. Installed Living differs from DEV because DEV contains the accepted freshness mapping correction; it was not deployed. Configuration references the existing scheduler package and enables bounded Living dispatch for Walkback; no service or flag was changed. Current scheduler process state was not revalidated and is not claimed as evidence of discovery functionality.

## REMAINING_CUTOVER_GATES / NEXT_ACTION
HOLD for this gate: qualify the accepted bounded owner that turns a genuinely unseen selected-route pattern into a persisted candidate, associations and a projected workflow, including admission threshold, review state and deduplication authority. If no such accepted owner exists, obtain a narrowly scoped DEV implementation contract for that bridge; do not substitute frozen-row normalization or existing-candidate updates. Then run new-candidate publication/crash controls. Other consolidated cutover gates remain separate and unchanged.

RUNTIME_DEPLOYMENT_REQUIRED=false — test/read-only qualification and documentation only; no runtime implementation or deployment. Production/V1 unchanged. No provider calls, service actions, WebSockets, seed expansion or cutover.
