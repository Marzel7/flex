# DEV004 Potential Operations admission authority

DEV004_POTENTIAL_DISCOVERY_AUTHORITY_STATUS: ACCEPTED_OWNER_INCOMPLETE

GO_HOLD_DECISION: HOLD for new Potential Operation lifecycle qualification; read-only authority assessment complete. Accepted discovery and publication pieces exist, but no complete accepted bounded Walkback-to-new-candidate admission owner is established in the examined composition. This is not proof that all retained discovery code is absent.

## Frozen authority
Engineering inspection: 9d7d2aa67dd44200d852b3b194724446c7d0e37c; executable candidate 506fe25c941eadf379ca98e4f0d42c1a2486dc9c. FLEX_STABLE_V2 resolves to 2d825bbcf27f802bd0bfcef90cedeba43cc968eb. Verified ancestry of ace58aba, 4ff48a00, 87d33147, 968432da, 20d1025e, 09ae1394, 54ece3eb, dd481183, 889f2399, aabf24c1 and 506fe25c. No source modified. Discovery/fingerprint/promotion artifact hashes match the accepted run manifest for p3r-v2-2dec1d40604c1f7c08c8. The packaged constructor enters the frozen baseline in add62f1f; accepted source inclusion does not establish a live admission hook.

## ACCEPTED_DISCOVERY_OWNER
Two distinct accepted discovery lineages must not be conflated:

* P3R_V2_CANDIDATE_DISCOVERY_CONTRACT.v1, retained under docs/agent_handoff/p3r/v2/p3r-v2-2dec1d40604c1f7c08c8, accepts selected OPS Walkback edges with non-null queue creator/direct funder; family minimums are three members, two creators and two direct funders. Its frozen regeneration manifest records 220 families/2357 member mints, source high-waters and constructor/materializer hashes. This qualifies a snapshot lineage, not bounded event admission or a daemon recovery contract. No materializer was executed.
* src/discovery/local_operation_discovery_projection.py:run_discovery is an accepted provider-free Core-to-separate-corpus funding-family generator, with retained execution qualification and 10 original tests recorded in handoff. Its subsequent accepted source history includes 84648017 (CEX/infra lineage). It reads token_analysis, migration verification, transfer_index and creator_funding_queue; it does not consume the OPS selected-route event contract. It publishes candidate_families in local_operation_discovery_corpus.db, not Living identities. Source is also retained in the packaged runtime. This is real discovery functionality, not an experimental substitute; its outputs explicitly represent funding structures rather than confirmed operational identity.
* Label-blind funding-architecture discovery at bf1baac7 is retained PASS research/replay, but its handoff explicitly requires separate review before any candidate/membership action. It is not accepted automatic admission authority. The frozen generic-wallet Walkback design explicitly prohibits attribution and is not a publication owner.

## CANDIDATE_IDENTITY_CONTRACT
P3R constructor runtime/creator_resolution/src/ops/p3r_v2_tiering.py:base_fingerprint normalizes sorted unique (depth,mechanism,amount), with zero/null not positive evidence; stable_candidate_id uses canonical JSON SHA256 truncated to 20 hex characters with p3r-v2- prefix. This is fingerprint-family identity. It must not silently replace a durable Living candidate identity as evidence evolves. The retained Living model investigation expressly separates stable candidate identity from snapshot hash. Core corpus direct-funder identity is DFF_ plus the first 16 SHA256 hex characters of funder; run identity is wall-clock local_discovery_ timestamp. These namespaces and semantics are distinct; there is no accepted cross-lineage identity mapping for automatic new Living admission.

## ADMISSION_AND_PUBLICATION_PATH
| Stage | Accepted owner / I/O and authority | Missing or bounded limitation |
|---|---|---|
| New evidence | Walkback post-commit notification supplies mint/creator/funder/mechanism context; canonical outcome already committed | Callback is conditional enrichment, not global discovery |
| Match | potential_candidate_matcher.match_signature(signature,specs) returns UNIQUE_MATCH, MULTI_MATCH, NO_MATCH or INSUFFICIENT_INPUT | Pure registry lookup; NO_MATCH deliberately conveys no admission permission |
| Discover family | P3R selected-edge family contract, constructor; separate Core corpus run_discovery | Snapshot/corpus population processing, no qualified incremental selected-route admission owner |
| Identity/admission | Existing Living bootstraps seed two explicitly approved PAUSED candidates; corpus stores DFF/USF families | No accepted rule connects a new P3R family to stable Living identity/review-state admission |
| Associations/assessment | generic_living_pipeline_v2 computes declared associations, metrics, generation and immutable digest for supplied spec | Caller must already supply candidate spec and evidence authority |
| Persist | generic_living_active_components.publish_generic_assessment_atomic commits assertions, assessment, lineage, bindings and current pointer in OPS | Does not create or authorize a new discovery spec/workflow; bounded SQLite concurrency retries (3) only |
| Potential publication | potential_operations.normalize_potential_operation_workflows imports frozen ranking rows; rows/detail project those workflows | New dynamic Living admission does not automatically become a ranked Potential workflow |
| Separate Analyst Queue | discovery_intake.fetch_discovery_intake_candidates reads strong AND attributable corpus families, LIMIT 20; existing operator_routes renders review funding structures | Accepted review publication, not equivalent to new Potential Operation admission; no automatic promotion |
| Review | Existing confidence/review/governance remain authoritative | Discovery labels, activity and shared funding cannot promote or change canonical attribution |

Required evidence includes exact selected-edge provenance, creator/funder roles, competing/alternative parents, exclusions and immutable association ownership. A generated fingerprint or recurring amount alone is insufficient to establish operation authority.

## FEATURE_FLAG_AUTHORITY
LIVING_GENERIC_BOUNDED_DISPATCH_ENABLED defaults false; the accepted Supervisor Walkback configuration sets it true. It selects generic versus legacy processing, not global discovery. LIVING_V2_FRESHNESS_MAPPING_PATH is the explicit DEV004 generation mapping; absence retains installed behavior. No accepted global-new-candidate enable flag or scheduler ownership was established. Scheduler configuration exists separately; no process state or activation was inferred from configuration and no services were queried/started. Dormant scheduler restoration is outside scope.

## RESTART_AND_DEDUPLICATION
Existing Living associations use deterministic immutable content IDs; assessments use candidate + evidence digest, current pointer advances by generation; publication rolls back on failure and retries only recognized SQLite concurrency conflicts within its fixed ceiling. Reverse index rebuild reads associations only for configured registry identities. ace58aba mapping preserves historical generations; it does not register new identities. These protections are reusable components, not proof of admission exactly-once behavior.
The separate corpus pipeline commits intermediate stages, uses INSERT OR REPLACE and a timestamp run ID, and starts a new run on rerun; it has no durable event continuation or atomic candidate/assessment/publication completion contract. It streams batches and limits each upstream lookup, but whole-population COUNT, GROUP BY, in-memory sets/fetchall and retained repeated runs are not a hard total scan/output/file bound. It was not run against V1 or V2 here. LIMIT 20 bounds intake output only.

## MISSING_COMPOSITION_BOUNDARY
The prior four findings are intended limits of their modules: matcher lookup, two-candidate dispatcher, frozen workflow normalization and existing-candidate-only activity snapshot. None is evidence that NO_MATCH should create an operation. Missing is an accepted owner linking qualifying newly observed selected-route families to stable identity admission, persisted assessment/associations and the correct Potential review projection, with explicit restart and resource authority. Simply enabling a flag or wiring the corpus intake would change semantics. Therefore a safe composition-only correction is not established.

## MINIMUM_BRIDGE_IF_REQUIRED
Future authorization should be limited to an OPS-local admission bridge using existing P3R discovery/evidence predicates and generic immutable publisher, with: (1) explicit bounded event/cursor input and stable admission identity separate from evolving fingerprint; (2) complete retained evidence and overlap/conflict/exclusion checks; (3) approved initial review/confidence state, never automatic promotion; (4) atomic/idempotent identity, association, assessment and publication handoff with crash recovery; (5) preserved old-generation mapping; (6) explicit row/byte/transaction/per-invocation and total storage caps below 500,000,000 bytes, fail-closed overflow, bounded retries; (7) provider-free unseen/duplicate/conflict/crash and known-operation separation controls. Numerical limits and initial FORMING/REVIEW_ONLY policy require explicit contract qualification; this report does not invent them or authorize implementation. Reuse accepted algorithms; do not create a new detector or scheduler.

NEXT_ACTION: Authorize only a bounded DEV admission/publication bridge contract closing the above gap, retaining existing P3R predicates and review authority, before implementing or claiming new-candidate lifecycle equivalence.

RUNTIME_DEPLOYMENT_REQUIRED=false — read-only source/contract assessment. No tests repeated, database connections, production/source/configuration changes, provider requests, services, WebSockets, fixture changes or cutover. Only this compact report and required handoff were written; no large artifacts or unbounded storage introduced.
