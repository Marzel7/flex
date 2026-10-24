# DEV004 Complete Seed Reference Preflight

DEV004_SEED_REFERENCE_PREFLIGHT_STATUS: **COMPLETE — HOLD FOR IMPORT.** All findings are collected; no full import, correction or exclusion was implemented. Tool revision `f2752eaf7d7bd02e080c18ad6beedeac5b3e263b`; unchanged runtime authority `20b0f4e1`; FLEX_STABLE_V2 preserved.

TOTAL_SELECTED_ROWS: **28,462** across70tables:28,444 accepted selected operational/reference rows (including15 retained external-price evidence records), plus18 already-qualified closure rows for the six Byzantine cases. The29 previously documented synthetic controls are outside the operational selection. Every selected row has source/retained authority; missing selected-source rows0.

TOTAL_REFERENCE_EDGES_CHECKED: **3,635 non-null edges**:2,490 declared-FK edges under accepted V2 profiles,409 typed membership references and736 explicitly named reader/ownership edges. **3,517 resolve;118 do not.** Another794 nullable references impose no parent requirement, including762 legitimate membership NONE references. All70tables' declared FK definitions were examined; references additionally cover immutable assessment/association ownership, current-generation links, merge/review/history operators, monitoring observation-to-facts, and durable outbox-to-membership ownership. Exact contracts are in contracts.json.

UNRESOLVED_REFERENCE_COUNT: **118 edges on118 distinct rows**, all belonging to Watchtower operator `04265d9f-6eb2-568c-a49e-9253091a4dbb`. No other checked table/contract has an unresolved edge. This is relational preflight for the existing accepted schema and enumerated reader contracts, not proof of every opaque JSON/narrative reference or final functional/cutover acceptance. Opaque provenance is retained unchanged rather than assigned invented FK semantics.

## UNRESOLVED_BY_TABLE

| Table | Existing reference population | Count | Cause |
|---|---|---:|---|
| OPS `operator_launch_membership` | `walkback_watchtower_confirmed_projection_v1` |104|Membership commit IDs with durable outbox authority are outside the currently supported identity-event/confirmed-match namespaces.|
| OPS `operator_launch_membership` | `watchtower_behavioural_paginated_lineage_20260825` |14|Legacy IDs equal the retained same-mint creator-funding signature, outside those two namespaces.|

All118 exact mints, IDs, row hashes, source populations, parent lookup results and operational states are listed in findings.json. No first-row stop or next-record-only conclusion.

## MISSING_PARENT_CAUSES

Each of118 references has0matching V1 `operator_identity_events` and0matching `confirmed_operation_matches`. Therefore copying more rows from those two parent tables cannot resolve them. **Incomplete selection of a matching parent under the currently supported types:0. Missing selected source identity:0.** Missing expected-type parents118 does **not** mean all historical attribution evidence is absent:

-104 matching durable outbox records are already selected. The accepted `project_watchtower_confirmed_membership` constructs the original `WATCHTOWER_CONFIRMED:mint:assigned_at` ID and calls `commit_membership_and_outbox`. All104 membership IDs, operation IDs, mints, commit timestamps and deterministic outbox-event hashes match that accepted source contract.99outbox rows are DELIVERED;5remain PENDING.
-14 IDs exactly equal the retained same-mint queue `funder_sig`; all14 have `CANONICAL_OPERATOR_REACHED` outcomes for Watchtower. These are funding-evidence references, not governance identity-event IDs. Indexed atomic-flow signature probes returned0for all118; no broader RPC/history search was attempted.

The linkages explain both legacy types but do not create or independently qualify a new FK import branch. No parent row is invented, ID altered or constraint disabled.

## OPERATIONAL_DEPENDENCIES

All118 have retained selected Walkback queue **and** attribution-outcome rows. Allqueues are complete, so these are not disposable stale leases or unneeded queue records by default. The memberships carry established operation recognition and attribution/provenance; dropping them would weaken Watchtower continuity and known-operation separation. The104outbox-linked records additionally carry monitoring-admission authority, including5pending entries. Research/evidence continuity applies to both groups. No Potential Operations assessment/association ownership reference failed; no price/lifecycle FK failed under the accepted canonical-identity profile. Indirect known-operation separation must still preserve these memberships.

**Counterevidence requiring preservation:**10 of the104 outbox-backed memberships coexist with current `UNKNOWN_INFRASTRUCTURE` outcomes (confidenceMEDIUM);108 of118 outcomes are canonical Watchtower/HIGH. This is an existing state discrepancy, not an additional broken FK and not permission to promote, revoke or overwrite a membership. All118 selected lineage-quarantine lookups are absent; no quarantine records were removed. The10 exact discrepancy mints appear in verification.json. A future compatibility mapping must retain both the original membership and current counterevidence/review safeguards.

## QUALIFIED_COMPATIBILITY_MAPPINGS

Preserved existing profiles validate180 confirmed-match references (including the six Byzantine cases),111 real identity-event references and762 explicitly nullable NONE references. The216 selected peak records all resolve to canonical `token_analysis` identity using the accepted price profile; no tracked-token record is fabricated. Original price/ATH provenance and0.1% valuation-only tolerance remain unchanged.

The104 outbox and14 queue/signature relationships are **source links verified, typed-import representation NOT qualified**. They must not be passed off as confirmed-match or governance events. No new correction is included in this milestone.

## QUALIFIED_EXCLUSIONS

**New operational exclusions:0.** Only the29 previously classified explicit synthetic controls remain outside selection; they are preserved in the existing test corpus. No dependency-free exclusion is proven for any of118 unresolved records. P3R populations were neither expanded nor retired; unknown historical dependencies were preserved.

## UNKNOWN_REFERENCES

118 references remain unsupported by the current typed import contract, although their retained legacy linkage is now identified. No other unindexed/unavailable source lookup or selected-parent omission was found in the checked contracts. Opaque provenance/JSON fields are not reinterpreted or newly claimed to be foreign keys; the15 retained external records pass their existing payload-digest/context checks, without reopening historical acquisition.

## REMAINING_IMPORT_BLOCKERS

One consolidated schema-compatibility boundary with two existing legacy reference types:104durable membership/outbox identities and14funding-signature identities. Preserve the10 current outcome discrepancies and5pending monitor admissions during any future mapping. None is resolved by discarding the assignment. All other checked reference contracts pass. This preflight makes no claim that live source state forms an atomic Core/OPS generation or that production cutover is ready.

Bounded execution used128-key batches and289durable progress checkpoints, indexed source lookups only, max2rows per authority lookup and33per supplementary signature probe. Completion and finding hashes are fenced in checkpoint.json. Sources remain separate read-only SQLite snapshots; restart reuse requires replay/digest agreement, not a connection-local data_version assumption. **11 verification groups PASS**: count completeness, uniqueness, indexed plans, checkpoint/digest consistency, preserved mappings/exclusions and15retained payload digests. No unrelated accepted functional matrices were rerun.

Diagnostics allocated about352KB, largest file309,547bytes when measured; enforced16MiB/file and64MiB total metadata budget. Free space observed `2676412416` before / `2672058368` after, above2GB+64MiB reserve. Concurrent filesystem changes are not attributed to these small files. No new unbounded storage producer or raw source-row payload retention. Production writes/source changes, providers, restarts, target databases, full copies, imports, deletion and cutover:0. Runtime deployment required=false for read-only diagnostic work.

## NEXT_ACTION

Authorize one bounded DEV-only compatibility qualification for **both118-record groups together**, using the existing durable outbox and same-mint funding/outcome authorities, preserving original IDs/memberships, negative evidence, confidence, pending state and valid FKs. Include the10 outcome discrepancies as explicit fail-closed controls; do not admit or promote on a legacy ID alone. Then re-use this consolidated inventory rather than discover another orphan through repeated full imports. No implementation or exclusion is performed here.
