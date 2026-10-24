# DEV004 Bounded Watchtower Reference Compatibility

DEV004_STATUS: **PASS — requested isolated DEV qualification complete.**

REFERENCE_COMPATIBILITY: **PASS**. Same28,462selected rows;3,635/3,635reference edges supported. UNRESOLVED_REFERENCE_COUNT: **0**. MISSING_PARENT_COUNT: **0**. New operational exclusions0.

Qualified source `0ace7186a302d7b04222b4000157f6b61ecde4b9`. Starting evidence95f3a891 and handoff960119a4; FLEX_STABLE_V2 and accepted source ancestry retained. All source identifiers, assignment rows, provenance/confidence and negative evidence remain unchanged. No full V2 import, promotion or deployment.

## Smallest contract correction

Three byte-identical DEV companion owners extend the existing typed sidecar with distinct `MEMBERSHIP_COMMIT` and `FUNDING_SIGNATURE` branches. The original five-column membership interface and original typed-v1 owner are unchanged. The profile is explicit fresh-disposable-only; it does not rewrite or upgrade an existing populated v1 sidecar.

-104 commit references retain their original membership IDs and the exact selected outbox event, membership ID, operation, mint and commit timestamp through a composite FK. The same-mint attribution-outcome row is also a required FK parent. The accepted deterministic outbox hash is independently checked by the read-only resolver. PENDING/DELIVERED state is not part of reference identity and is never rewritten here.
-14 funding references retain their original source-population ID and signature, with a composite FK to the same-mint queue funding signature and an outcome-mint FK. Initial mapping requires the existing canonical Watchtower outcome; it does not create an outcome or change the retained queue `LINEAGE_GAP` evidence. The funding signature is not relabelled as a governance event or confirmed match.

Ambiguous, unknown, wrong-operation/mint/population/timestamp and absent-parent references fail closed. Nullable provenance cannot bypass exclusive namespace selection. Existing NONE/IDENTITY_EVENT/CONFIRMED_MATCH eligibility is reused. New CHECK constraints bound reference fields; no fake parent records, cleared IDs, FK disabling or orphan exclusions.

A directly affected synthetic test reproduced the accepted writer's membership-before-outbox ordering failure under the extended schema. `commit_membership_and_outbox` now detects only the explicit extended sidecar, creates the outbox parent first, then the membership, within one savepoint. The caller still owns the outer transaction and commit. Failed child persistence rolls back both writes; the default/legacy ordering remains unchanged. No provider, queue consumer, attribution predicate or new background owner is introduced.

## Preserved conflict and recovery state

UNKNOWN_INFRASTRUCTURE_CONFLICTS_RETAINED: **10/10**, with their MEDIUM confidence and existing membership preserved exactly. The new type is MEMBERSHIP_COMMIT, never CONFIRMED_MATCH; no discrepancy is suppressed or resolved. All118 membership hashes match the prior preflight. Full current outcome/queue row digests match the captured evidence, and baseline outcome type/operator/confidence fields are unchanged.14 legacy queue LINEAGE_GAP states also remain intact.

PENDING_OUTBOX_ADMISSIONS_RETAINED: **5/5**, same original event/membership identities and PENDING state;99other outbox entries remain DELIVERED. No real admission is consumed or acknowledged. Independent synthetic tests show that normal delivery-state updates remain possible without changing immutable reference identities, then roll back that control update.

## Verification

The read-only preflight uses the same selected-key population,128-row batches, bounded indexed lookups and completion fencing. All118 new branches resolve to existing selected parents:104MEMBERSHIP_COMMIT,14FUNDING_SIGNATURE. Original counts remain180CONFIRMED_MATCH,111IDENTITY_EVENT and762NONE. No target database or selected-row import is performed by preflight.

**26 focused synthetic control groups PASS:** FK-on schema initialization, all original branches, both new branches, conflict/pending retention, unknown and ambiguous rejection, source/timestamp/parent protections, direct sidecar rewrite rejection, duplicate suppression, repeat bootstrap, actual accepted writer compatibility, no implicit commit, savepoint rollback, fresh-process reopen and process crash before caller commit, plus unchanged legacy writer ordering. A nullable-population ambiguity control passes. An initial writer-order counterexample was corrected; a test-fixture quote error was corrected before the final run. No remaining test failures.

BYZANTINE_PRICE_REGRESSION: **PASS**. The three accepted typed-v1 owner files and accepted canonical-price bootstrap are byte-for-byte unchanged from95f3a891. The complete selected reference preflight retains the six Byzantine closure cases, all180confirmed-match references and216canonical price-identity links. Original price values, provenance and0.1% valuation-only ATH tolerance are untouched; unrelated functional matrices were reused rather than rerun.

## Safety and scope

IMPORT_EXECUTED: **NO** — no selected V1 rows are imported into V2. SQLite write tests contain explicitly synthetic control records only. No full V1 copies, production writes/source changes, provider calls, restarts, operational exclusions, deletion or cutover.

PRODUCTION_MUTATIONS: **0**. Source DB access is read-only. Birdeye credential authority/endpoints/budgets/retries remain unchanged; no credentials read. Storage: largest synthetic file `118784`bytes, allocation `188416`bytes. Synthetic process file cap8,000,000bytes; profile requires OS file bound<=64MiB and max50,000memberships with bounded new reference fields. No new raw-provider retention or unbounded growth path. Free space observed `2683842560` before preflight, `2680107008` after, `2670596096` at preservation verification, all above2GB. Concurrent filesystem changes are not attributed to these compact fixtures.

This is qualification, not production installation or a complete cutover decision. Runtime deployment status QUALIFIED_NOT_DEPLOYED. Separate read-only Core/OPS snapshots are not claimed to form an atomic cutover generation. SQL FKs enforce exact parent identities; deterministic outbox hash validation is an additional preflight/source-owner check. Future imports must use the explicit extended owner and revalidate source bindings if source state changes.

NEXT_ACTION: **NONE — requested correction complete; stop.** A full disposable migration is a subsequent separately requested step. Do not execute it under this milestone.
