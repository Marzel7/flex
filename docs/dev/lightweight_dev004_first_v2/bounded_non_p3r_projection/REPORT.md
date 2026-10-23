# DEV004 Bounded Non-P3R Projection — exact-ranking stop boundary

DEV004_BOUNDED_DISCOVERY_STATUS: HOLD — missing accepted indexed derived-population/generation continuation contract. No runtime correction implemented. The requested stop rule applies; this is not a claim that resumable exact computation is mathematically impossible, but that the existing retrieval/continuation contracts cannot provide it within the authorized narrow correction.

Frozen inspection: dc3472b601bb945b9fb939743f75c973b36eb502; unchanged executable candidate 506fe25c941eadf379ca98e4f0d42c1a2486dc9c. FLEX_STABLE_V2=2d825bbcf27f802bd0bfcef90cedeba43cc968eb. Relevant accepted corrections remain ancestral. Reused 19/19 accepted provider-free controls from dc3472b6; no executable change warrants repeating them. No P3R requirement reopened.

## ORIGINAL_UNBOUNDED_PATH

EmergingOperatorService._compose evaluates identity proposals, loads canonical families and all discovery profiles, calls InvestigationPopulationBuilder.build, evaluates projected candidates against canonical families, applies confirmation overlays, globally assigns attention rank/stage and globally sorts families. _list_uncached and reconciliation construct the full result before output slicing. build_emerging_operators_snapshot retains full families, reconciliation and list payload; MAX_LIST_LIMIT=500 constrains list output, not this upstream work. Existing scheduler isolation/cache cadence reduces request latency; it does not constrain rows, comparisons, memory or derived evidence.

The accepted builder iterates wallets in sorted order and tries each existing group, requiring pair cohesion with every member; build_group additionally materializes every within-group cohesion pair. Anchor-derived family identity depends on complete grouping. Derived launch/creator/signature unions, exclusions, canonical absorption, attention eligibility and final rank cannot be read in global rank order from source-wallet/parent/mint indexes.

## INDEXED_SELECTION_PLAN

Reused prior metadata evidence: parent-specific selected-edge selection SEARCHes ix_wwec_parent, but whole-population selected descendants SCAN that index and require a temporary DISTINCT B-tree. Neither this nor queue/mint indexes persist family membership/completeness or the derived attention tuple (significance, completeness, last-material-activity, family ID), final stage/order, or canonical-merge results. Analyst intake LIMIT20 is an output contract, not a substitute for these computations. No indexes were created or population data scanned in this task.

Exact missing retrieval/continuation contract: (1) a consistent declared source generation covering canonical/review/edge/session/exclusion/confirmation inputs; (2) complete profile extraction with stable keyset position and bounded per-root evidence handling; (3) durable sorted-wallet/group/member and pair-evaluation continuation preserving the accepted greedy all-member grouping; (4) canonical-merge, overlay and rank-stage continuation plus complete deterministic global-order retrieval; (5) validated completion and generation fencing before publication. Existing snapshot_version increments only at publication; it does not identify a resumable source generation. Such a contract was not found in the accepted builder/snapshot owner. Merely paging candidate parents or adding LIMIT changes semantics.

## BATCH_AND_MEMORY_BOUNDS

Three-profile counterexample: actual accepted builder produces one population; independently processing batches of two and one produces two populations with different identities. Thus per-batch results cannot be published or ranked independently. A 256-profile disjoint synthetic input performs exactly 32640 pair checks even though each final group has one member. The accepted builder also stores full group/evidence/cohesion outputs, so retaining only a cursor does not bound memory or checkpoint size. These bounded diagnostics establish the dependency; they do not establish production limits. No maximum runtime source rows/candidates/memory/output bytes is qualified.

## RANKING_EQUIVALENCE

NOT QUALIFIED for a bounded replacement. Current attention ranking can change stage to EMERGING for the globally eligible top budget, then final order sorts by stage, significance, last_seen and family identity. Later evidence can affect group membership, scores, canonical absorption and eligibility. The diagnostic proves a population/identity mismatch for naive batching before ranking even starts. No partial ranking, alternate tie-break, truncated evidence or new discovery threshold was introduced.

## CHECKPOINT_AND_RESTART

No existing checkpoint owner for this complete derived computation was established; no new checkpoint table/file or protocol implemented. Source-key continuation alone does not restore all-member grouping or certify a consistent cross-table generation. Restart-mid-projection tests are therefore blocked at this exact contract boundary, not marked passing. Existing discovery reopen/duplicate controls remain accepted and separate.

## LAST_GOOD_PUBLICATION

No snapshot write was attempted; every existing publication is unchanged. Existing refresh writes only after a returned build, and write_snapshot uses sibling temp JSON, file fsync and atomic replace, cleaning failed temporaries. Those mechanisms protect against partial bytes, not semantic partial populations. The pure accepted _passes_sanity_check accepts family_count70 against previous100 at its default 50% drop tolerance; a paginated partial result could therefore pass. Completion fencing must precede the existing publisher. Count sanity must not be treated as proof that enumeration/ranking finished. No partial snapshot was created or supplied to the publisher.

## SNAPSHOT_RETENTION

The fixed (function,window=0) target is replaced atomically, so superseded final payloads are replaced rather than accumulated per generation. Failed temporary writes attempt cleanup. There is no qualified serialized-byte cap for the complete families/list/reconciliation payload, maximum full evidence per family, checkpoint state, temp allocation, or bounded restart cleanup contract for a new resumable builder. No expiry/deletion or global cache pruning executed.

## WAL_AND_STORAGE_BOUNDS

Diagnostics are pure Python with RLIMIT_FSIZE16000000; compact JSON/script/report only. Zero database connections, WAL writes, provider calls, service actions or raw payload retention. Runtime WAL/transaction/checkpoint bounds are NOT QUALIFIED because no replacement exists. Current disk-headroom check (default 10GiB free) is a pre-write availability floor, not a file-size or cumulative V2 cap. It does not reserve space or bound JSON serialization. The proposed 5GiB combined V2 ceiling has no independently enforcing aggregate allocation owner in this path; nor does it override the strict single-file <500000000-byte rule. No behavior at that ceiling or absolute hard cap is claimed. Raising/lowering a threshold would not supply these missing guarantees.

## PROVIDER_FREE_TEST_RESULTS

3/3 targeted diagnostics pass: cross-batch population/identity mismatch, 256-profile pair-work count, and partial-count sanity acceptance. The actual accepted pure InvestigationPopulationBuilder and _cohesion_pair were used; no simplified alternate discovery algorithm. The snapshot sanity function was extracted unchanged for pure invocation. result.json records exact IDs, counts and source hashes. Existing 19 discovery/review controls and unrelated accepted matrices reused. Large-population bound/restart/storage-pressure/full-ranking acceptance tests were not run against an unimplemented replacement. No historical or live provider data processed.

## EXACT_SOURCE_DELTA / CANDIDATE_SHA

Executable source delta NONE. Candidate remains 506fe25c941eadf379ca98e4f0d42c1a2486dc9c; diagnostic/test documentation only. RUNTIME_DEPLOYMENT_REQUIRED=false because no executable runtime behavior or production installation changed.

## REMAINING_CUTOVER_GATES

Non-P3R callable discovery/review functionality remains PASS. M1/M4 exact complete-population execution and growth safety remain HOLD; final runtime/configuration bindings and consistent state/rollback rehearsal remain separate gates. No new engine, scheduler, P3R qualification or infrastructure repair was introduced.

NEXT_ACTION: Define and qualify only the missing consistent-generation, resumable grouping/ranking retrieval contract for the existing projection, including bounded checkpoint/output state and explicit complete-generation publication fencing. Stop before implementation unless this narrowly scoped extension is authorized; do not add broad indexes or change ranking/discovery semantics.
