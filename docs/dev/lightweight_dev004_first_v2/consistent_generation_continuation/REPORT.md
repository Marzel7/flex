# DEV004 Consistent-Generation Discovery Continuation

DEV004_DISCOVERY_CONTINUATION_STATUS: PARTIAL — capped normalized-input grouping/merge/rank feasibility PASS (15 controls); complete source-to-publication continuation contract HOLD.

GO_HOLD_DECISION: HOLD. Exact grouping is demonstrably resumable; it is no longer an impossibility blocker. The remaining mandatory gap is bounded consistent capture/fencing of the complete accepted Core/OPS source generation and continuation of all final projection/confirmation/reconciliation stages. This test-only interpreter does not supply or install that missing runtime authority.

Authority: engineering inspection 4d55ff7ba658ecfb3b6839d792d9274384e7379a; executable candidate remains 506fe25c941eadf379ca98e4f0d42c1a2486dc9c. Relevant frozen-baseline/correction ancestry and existing 19/19 functional results from dc3472b6 are reused. Source files are read from the existing accepted runtime/walkback package. No executable runtime files changed.

## GENERATION_IDENTITY

Proposed smallest contract: versioned manifest binds immutable normalized profiles, canonical families, rejection/exclusion authority, confirmation/disposition inputs, identity-evaluator results, all relevant source boundaries, accepted source hashes, flags/configuration, evaluation clock and serialized input digest. The generation ID is SHA256 of the canonical manifest/input. Checkpoints bind that ID; no raw wall-clock recomputation or silent source-generation substitution is allowed. Changed inputs invalidate this generation for publication; a later generation must be separately reconciled. Evaluation clock must remain fixed because accepted lifecycle/significance reads time.time().

Feasibility fixture implements the profile/canonical/rejection/configuration/clock/content-hash subset with a synthetic source revision. It detects changed revision and changed frozen bytes. This is not a real database-wide update counter and is not claimed to detect live Core/OPS changes.

## FROZEN_POPULATION_BOUNDARY

Capture must prove complete enumeration against declared consistent read boundaries for Core/OPS and every other required review/confirmation source. Persist the bounded input package before processing; later steps read only that immutable package. Stable wallet/source identities deduplicate exact duplicate input; conflicting duplicate content fails closed. Overflow rejects the entire generation, never truncates it.

The existing owners expose whole-population extraction, independently opened connections, resolver/governance/review/confirmation overlays and reconciliation reads. No existing bounded complete capture owner, shared read-generation fencing or cross-store reconciliation protocol was established here. Holding a SQLite read transaction open across restarts is not a durable checkpoint and could retain WAL indefinitely. Copying full production databases is not authorized. A source-row high-water alone misses updates/rejections to already-seen identities. Therefore fixed synthetic inputs establish algorithmic feasibility, but not this mandatory production-shaped input contract. No production or fixture database was opened during this milestone.

## GLOBAL_GROUPING_EQUIVALENCE

The test interpreter resumes the exact accepted greedy sorted-wallet traversal. State is (next-wallet index, candidate group index, member comparison index, ordered prior groups). It uses the unchanged accepted _cohesion_pair. On a failed member comparison it advances to the next group; only all-member success adds a wallet to a group. Partial groups remain internal, not published families. Source ordering is lexicographic and input de-duplication happens before ordering.

Three coherent profiles processed across three checkpoint steps produce the same one family and exact immutable revision as the full InvestigationPopulationBuilder.build oracle, correcting the previous independent-batch counterexample. The 256-disjoint-profile case completes in 257 steps with 32640 total cohesion evaluations, peak128 per grouping step. Each step reloads state/input from durable files; repeated reopen cannot reset evidence, skip wallets or split the identity. Final build_group uses accepted code only after grouping completes. Exact immutable population revision equality checks membership, provenance and exclusions, not just labels.

## CANONICAL_MERGE_CONTRACT

The accepted evaluate_merge and the exact canonical absorption block extracted from EmergingOperatorService._compose are executed unchanged against bounded synthetic canonical families. Exact-wallet-set absorption and rejected-wallet refusal match the full oracle. No confirmed membership is written. The fixture does not execute the complete identity resolver, database-backed confirmation overlays or reconciliation service. Their inputs/results must belong to the frozen generation and their work must be bounded before this becomes a full runtime qualification.

## BOUNDED_CHECKPOINT_STATE

Proposed/tested envelope: at most512 supplied records, 256 unique profiles, 16384 encoded bytes/profile, 4MiB total immutable input including canonical/configuration/rejection fields, and16 canonical families. Grouping step: at most8 profile admissions and128 accepted cohesion evaluations (cross-batch test intentionally uses1 admission). Checkpoint encoded cap32768 bytes, including cursor/groups/generation. Atomic sibling write, file fsync, replace and directory fsync are used for disposable checkpoint publication; failures propagate without granting progress. Checkpoint entries retain indices into the immutable bounded input, not copied history. A 257th unique profile fails closed; conflicting duplicates fail closed.

Observed largest input166618 bytes, checkpoint1585 bytes, final test publication1593221 bytes. Contract output cap8MiB; cap is checked before file mutation. One old publication8MiB + replacement temp8MiB + input4MiB + checkpoint/temporary64KiB yields proposed persistent/transient envelope20MiB+64KiB per generation, excluding interpreter/RSS and the source databases. Keep only one active generation plus last-good publication; superseded generation replacement/recovery cleanup is a required contract, not implemented retention.

These bounds are NOT all runtime guarantees. The fixture finalizes using accepted build_group and oracle comparison after the grouping loop: finalization may perform up to32640 within-family comparisons, and the qualification oracle adds more. Canonical merge and global ranking run as bounded whole phases on the capped inputs, not resumable128-comparison phases. Complete final snapshot/reconciliation payload is not included in the8MiB fixture result. Python RSS/temporary serialization allocation and real SQLite/WAL/transaction work are not independently enforced by these encoded caps. Runtime finalization checkpoints, complete snapshot byte accounting and preallocation/failure behavior remain unqualified. No claim of an absolute memory, WAL, combined V2 or500MB file cap follows from this test.

## COMPLETION_FENCING

The fixture requires unchanged input digest/revision, complete wallet traversal, exact once-only coverage of all profile indices, and population-revision plus ranked-output equality with the full oracle before publication. Input changes, incomplete state and injected storage pressure all leave the disposable last-good bytes unchanged. Repeated complete finalization gives identical bytes and no duplicate family IDs. Oracle execution here is qualification evidence, not a proposal to recompute the full population on every production publish.

Runtime completion must additionally attest that all source items were captured, all comparisons and canonical merge decisions completed, confirmation/reconciliation completed, global attention/rank finished, and the generation remained valid at atomic publication. A count-drop sanity check is not this proof. No existing global snapshot was read or written. Source changes after capture require explicit invalidation/reconciliation; synthetic revision checks do not replace actual owner fencing.

## RANKING_EQUIVALENCE

The existing LegacyFamilyProjectionAdapter and exact accepted attention-stage/final-order code extracted from _compose are used unchanged. Fixed evaluation clock/configuration produce byte-identical merge/rank results from full versus continued populations. Canonical confirmation/rejection inputs remain separate from discovery. The fixture tests actual rank keys and attention stage, not an invented score. This qualifies those stages for supplied bounded frozen inputs; it does not certify every public list/reconciliation overlay or a globally complete real source capture.

## RESTART_RECOVERY / PROVIDER_FREE_TEST_RESULTS

15/15 controls pass: cross-batch exact revisions/rank;256-profile full oracle; grouping comparison cap; reopen every step; duplicate identity; exclusion/alternative evidence; canonical merge; canonical rejection; incomplete fencing/last-good; changed revision; injected storage pressure/last-good; deterministic repeat finalization; total-population overflow; conflicting duplicate; changed content digest. Restart is serialized state reopening between calls, not a process-kill/power-loss durability claim. Storage pressure is injected refusal before publication, not an actual full-disk test. The first run showed the three-profile case fit one step; admission budget was then reduced to1 for that case and the corrected final run verified three checkpoints. Final fixture: /private/tmp/dev004-continuation-4zgq9kyh. Largest file1593221 bytes. RLIMIT_FSIZE16000000 applies to test artifacts. No provider code, subprocesses, database connections, network acquisition, raw payloads or production services used.

## NEXT_ACTION

Qualify one bounded complete-generation capture/fence contract through the existing Core/OPS discovery/review owners, including confirmation/reconciliation dependencies and source mutation detection. Reuse this proven grouping continuation. Stop at any source that cannot be captured/fenced within the fixed envelope; do not create a new detector, broad index project, long-lived restart-spanning read transaction or production database clone. Runtime implementation and deployment remain unauthorized in this milestone.

RUNTIME_DEPLOYMENT_REQUIRED=false — test-only contract/feasibility interpreter and documentation; runtime source, V1, installed corrections and last-good production publication unchanged. Prior passing functional matrices remain closed.
