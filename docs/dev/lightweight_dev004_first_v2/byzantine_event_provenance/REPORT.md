# DEV004 — Byzantine event provenance closure

DEV004_BYZANTINE_EVENT_STATUS: PARTIAL — original provenance resolved6/6; FK-compatible assignment import HOLD6/6. No existing identity event or accepted match-to-identity import contract was found. Stop without manufacturing one. Production unchanged.

FIXED_SIX_ASSIGNMENTS:0/6 assignment FK dependencies repaired;6/6 original source identities independently established. This is not a claim that the six memberships can yet be imported. The exact fixed population remains the dab00bbf ledger and all six membership row digests still match it.

| Mint | Existing event_id / confirmed match_id | Result |
| --- | --- | --- |
| AYpi7MmUuyyBZ8kGwPMfJo4NMXo2haxrSj8KXCx7pump | 498b37fe-9718-5a49-b916-2884e2672da9 | Existing confirmed match; assignment import HOLD |
| ETLrwm8hLbTAXvqvgSZyM7Gg7iZrvdKrXPN5w5YKpump | 71d01928-dd9a-5564-b2fd-e974c8eec69e | Existing confirmed match; assignment import HOLD |
| EQcSJDxbZMNQoJYr1KeQdFP9YZiHLMy7zPsCmT3Kpump | cff71e45-2825-5bd1-b96f-68f72035684f | Existing confirmed match; assignment import HOLD |
| 7NBemqQEqYtXnthmDhKt32mkwCZGihwXhGDFi75Qpump | c87bbf2a-960f-5f1f-88a1-4d1e5ae0e1de | Existing confirmed match; assignment import HOLD |
| FrjBb3eSfKLf4SMGb6vLw1jRS8XAf8URwBLnWxUkpump | 57bd0ec3-af69-5bb9-89c5-c5bf6ab3228a | Existing confirmed match; assignment import HOLD |
| DVaQQGX48qnLmc2xSEoh1J64GSny3VFXdMjMJyEnpump | 5d4976b4-e19a-552f-bdef-7e001fabc858 | Existing confirmed match; assignment import HOLD |

## MISSING_EVENT_CAUSE

The field is overloaded by the accepted V1 producer. `wsol_10_sol_four_step_operation.project_completed_walkback` creates a deterministic `confirmed_operation_matches.match_id = uuid5(NAMESPACE_URL, detector_version + ':' + mint)`, inserts that confirmed match, then writes the SAME match_id into `operator_launch_membership.event_id`. It does not create a launch identity-event row. The membership DDL separately declares event_id REFERENCES operator_identity_events(event_id).

All six rows use source_population_id=BYZANTINE_PROVEN_CREATOR_CONTINUITY and detector_version=BYZANTINE_PROVEN_CREATOR_CONTINUITY.v1. All six UUID formulas, mint/operator pairs and detected_at/assigned_at values match exactly. Their original authority is a confirmed detector match, not a missing governance identity event. No confirmed-match payload is lost or missing from V1. The incompatibility already exists between V1's producer reference namespace and its declared FK; it is not caused by omitting existing confirmed match rows in the DEV004 importer.

This identifies the accepted producer contract and exact record origin; no unobserved historical process execution or deletion is inferred from row metadata.

## AUTHORITATIVE_EVENT_EVIDENCE

All six exact IDs resolve uniquely through indexed primary-key reads in authoritative OPS V1 `confirmed_operation_matches`, with state CONFIRMED_MATCH and the same Byzantine operation UUID. Existing evidence_json includes source=proven_creator_continuity and its retained creator proof. For every case, bounded indexed reads also verify:

- Canonical Core creator, CREATE signature and migration signature match the recorded proof.
- The earlier proof mint has existing canonical Byzantine membership.
- The referenced atomic-flow evidence row exists with the exact proof mint/signature.
- The source operator and every imported field retain CONFIRMED/CERTAIN authority. No candidate is promoted and no confidence is recomputed.

Exact identity-event lookups remain0/6 in OPS V1 and in the accepted retained reference OPS; Core V1 has no identity-event table. These checks are limited to declared authorities and exact keys, not a claim of global filesystem absence. No unindexed historical search or provider acquisition was attempted.

Frozen source: FLEX_STABLE_V2 2d825bbcf27f802bd0bfcef90cedeba43cc968eb; manifest hash f0d3d933109f3572023af677729feef4db6e432657c7b7095b5bae05f2821dc7. Existing candidate0013ccbd and accepted corrections remain unchanged. Qualification source `4a9fba09c0b838cbaf60f619bea3de50a9954838`. Candidate writer SHA256 c490d537d2278e12ef1250fdaf1ec9e6d8e032283d9c68b6723fa5544e57fcb1 matches all three existing installed listener/main-worker/Walkback packages. The packaged writer's last Git change is add62f1f04329876ec977d4e8ac1c8c1a6c71fce. No installation/reload or production-source change occurred.

## IMPORT_MAPPING

Proven mapping: membership.event_id → confirmed_operation_matches.match_id for these six specific producer-version records. Preserve the original ID, canonical assignment, detector version, evidence JSON, timestamps and operation authority. Importing the existing confirmed match records into THEIR OWN table is valid and succeeds; calling them governance identity events is not an existing accepted transformation.

The inspected frozen writer/governance/manual-promotion contracts supply no accepted adapter that makes these match references satisfy the separate identity-event FK. The governance bootstrap creates an operation-level CONFIRMED event with a different deterministic identity, not a launch match event; substituting it would be unrelated identity substitution. No such routine was run. No new event_type/analyst/reason/payload was inferred, no IDs were nulled/remapped and no schema bridge or compatibility policy was invented.

## FOREIGN_KEY_INTEGRITY

Disposable target uses the EXACT current source definitions for operators, operator_identity_events, confirmed_operation_matches and operator_launch_membership. PRAGMA foreign_keys=ON throughout. First import the real operator and all six original confirmed-match rows. Then attempt the exact memberships. Each still raises FOREIGN KEY constraint failed despite its confirmed-match row being present. Each attempt rolls back under a savepoint; zero invalid memberships remain. Target foreign_key_check is clean before and after restart. No constraint bypass, schema weakening, placeholder event or source repair.

## DISPOSABLE_IMPORT_RESULTS

-6/6 original match records import field-for-field, retaining evidence/provenance and operator CONFIRMED/CERTAIN state.
-6/6 repeated exact match imports add0duplicates;6wrong-mint identity controls are rejected by the authority validator.
-One fresh child interpreter reopens the target and rechecks all six source record digests, exact target rows and idempotency. Exit0; six matches remain, zero duplicate records, zero invalid assignments; FK integrity clean.
-6/6 membership imports remain HOLD: all fail the exact identity-event FK. No successful full-assignment import, restart or equivalence is claimed.

These directly affected controls resolve provenance and reproduce the remaining mapping boundary. Prior100creator/616Watchtower/26Deep/11frozen/Walkback/Birdeye/freshness qualifications are reused unchanged, not rerun. No attribution predicates, budgets, credentials, retry controls or runtime source are changed. BIRDEYE authority remains intact; provider calls0.

Storage: target49,152bytes; final five diagnostic files81,920allocated bytes. Available3,843,026,944bytes before,3,842,584,576after diagnostic; later observation3,831,668,736. All exceed2GB+64MiB. Enforced8,000,000byte process file limit,256SQLite pages/1MiB target,64MiB total cap and32file cap. No raw provider retention or unbounded path, no file near500,000,000bytes, no source copy or historical deletion. Production/source/database/configuration/service mutations0; no restart of production, WebSockets, credentials read or cutover.

## REMAINING_SEED_BLOCKERS

One concrete remaining contract: a qualified, explicitly accepted typed legacy confirmed-match reference import representation that preserves these canonical IDs/decisions AND enforces FK integrity. Existing match evidence is recovered; no further generic event search or attribution research is required for these six records. Full operational-seed migration remains blocked until that representation is authorized and qualified. This task does not implement a new bridge or repair V1.

GO_HOLD_DECISION: HOLD for all six assignment imports. Provenance authority PASS6/6; FK import closure0/6. The accepted legacy compatibility contract needed for the third permitted resolution path does not yet exist in the inspected frozen owners. Producing new identity events would violate this task's stop rule.

NEXT_ACTION: Define and separately qualify only that DEV import compatibility contract for existing confirmed-match references, preserving canonical assignments/IDs/evidence and foreign keys. Do not manufacture identity events, clear event_id, drop assignments, disable FKs, repair production or resume the full seed under an unqualified mapping.

RUNTIME_DEPLOYMENT_REQUIRED=false: bounded read-only qualification and disposable test/evidence only. Authority manifest SHA256 `771175ae2eece503dbc1aeff7594dc6338ddd4d52637fe910c55ce2be3fd4491`. All source row/digest/lookups and fresh-process results are retained beside this report; no database payload file is published.
