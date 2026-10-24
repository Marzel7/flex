> PROVENANCE UPDATE: all six references resolve to existing confirmed-operation match records; the remaining blocker is the typed-reference/identity-event FK compatibility contract, not lost match evidence. See [Byzantine event provenance closure](../byzantine_event_provenance/REPORT.md). Historical diagnostic below remains valid.

# DEV004 — Full operational seed migration authority gate

STATUS: HOLD_MISSING_CANONICAL_EVENT_DEPENDENCIES. Six source memberships cannot be imported with their exact schema and foreign-key enforcement. Stopped before building the full compact seed; no source repair, provider acquisition, source snapshot, deletion, service action or deployment.

SOURCE: qualification `ce9604de6a5c73bf2eaec648ba5fc8efb0b00e06`; runtime candidate0013ccbd unchanged. Earlier direct37row compact PASS, all accepted runtime corrections and the100creator/616Watchtower/26Deep/price/recovery evidence remain scoped and valid. No accepted matrices were rerun. RUNTIME_DEPLOYMENT_REQUIRED=false: isolated diagnostic and evidence only, not executable runtime changes or production deployment.

## Full scope authority check

The accepted final reference pair contains28,473rows and22,724,608bytes, plus compact history/freshness mappings. It includes declared synthetic controls and15separately sourced immutable price-reference metadata rows; it cannot be blindly relabelled as entirely current V1 data. V1-backed records require direct indexed reads; separately retained compact evidence and mapping authority must remain explicit. No full V1 database copy was proposed or made.

A bounded metadata check found identical column contracts for all existing Core14/OPS56reference tables except the intentionally DEV-only retained-reference metadata table. Current V1 has all six retained behavioural profiles and ten operation identities within the bounded65row probes. This is not a new schema inventory or a declaration that every dependency is complete.

The mandatory current-membership root check uses the accepted operator index and <=2,048rows per identity. Across the ten retained identities, current V1 has1,171memberships versus1,165in the accepted seed. The difference is six Byzantine assignments:171current versus165retained. All other membership sets match; no old membership disappeared. None of the six new launches, their event rows or price facts is represented in the old seed. Core has their canonical creator/CREATE anchors, but that does not substitute for canonical assignment provenance. Omitting these new source assignments would fail the requested full-current-seed scope.

## Exact source dependency blocker

The current source `operator_launch_membership.event_id` is explicitly declared REFERENCES operator_identity_events(event_id). In one read-only source transaction, all six new memberships have a non-null event_id and each indexed primary-key lookup returns zero identity events. The accepted source governance schema also declares this relationship. The table canonical_manual_operation_commit_intents is absent in this source; no alternative accepted event owner was inferred.

| Canonical launch mint | Unresolved event_id |
| --- | --- |
| AYpi7MmUuyyBZ8kGwPMfJo4NMXo2haxrSj8KXCx7pump | 498b37fe-9718-5a49-b916-2884e2672da9 |
| ETLrwm8hLbTAXvqvgSZyM7Gg7iZrvdKrXPN5w5YKpump | 71d01928-dd9a-5564-b2fd-e974c8eec69e |
| EQcSJDxbZMNQoJYr1KeQdFP9YZiHLMy7zPsCmT3Kpump | cff71e45-2825-5bd1-b96f-68f72035684f |
| 7NBemqQEqYtXnthmDhKt32mkwCZGihwXhGDFi75Qpump | c87bbf2a-960f-5f1f-88a1-4d1e5ae0e1de |
| FrjBb3eSfKLf4SMGb6vLw1jRS8XAf8URwBLnWxUkpump | 57bd0ec3-af69-5bb9-89c5-c5bf6ab3228a |
| DVaQQGX48qnLmc2xSEoh1J64GSny3VFXdMjMJyEnpump | 5d4976b4-e19a-552f-bdef-7e001fabc858 |

The operator is Byzantine (`d8ee4d7a-fcd6-5a5b-b897-24f6ab56e334`). The source membership contents are hashed in authority_gate_manifest.json. These are existing canonical assignments, not candidates to be demoted or deleted. Their missing non-null referenced rows differ from the explicitly preserved legacy NULL event-ID case; no null or placeholder event may be invented here.

## Disposable enforcement proof

One tiny diagnostic uses exactly the source CREATE TABLE definitions for operators, operator_identity_events and operator_launch_membership, with PRAGMA foreign_keys=ON. It inserts the real existing operator row so the operator FK is satisfied, then attempts each exact membership under a savepoint. All6/6 attempts raise FOREIGN KEY constraint failed because the event parent is absent. Every attempt rolls back; zero invalid memberships remain, and the diagnostic foreign-key check is clean. No constraints, IDs, classifications, provenance or review states are changed.

This is a concrete schema-preserving migration blocker, not an inference from an incomplete old report. Disabling FK checks, clearing IDs, fabricating event rows, dropping the assignments or silently moving them out of canonical membership would change the accepted contract. No such workaround is implemented.

## Size and storage result

Full operational seed output size: NOT QUALIFIED. Full migration peak additional storage: NOT QUALIFIED. The migration was stopped at the mandatory dependency gate, so the prior22.7MBreference size/32MiBreference planning envelope must not be presented as a measured full-current target.

Measured diagnostic only:

- SQLite file28,672bytes; final three diagnostic files45,855logical/49,152allocated bytes.
- Available before4,942,516,224bytes; after diagnostic4,942,454,784bytes; later read4,939,649,024bytes. All exceed2,000,000,000+67,108,864bytes. No unrelated disk growth was investigated or attributed to the test.
-8,000,000byte process file limit;256pages per diagnostic SQLite file (1MiB),64MiB aggregate cap, bounded16files and indexed source read budgets. No file near500,000,000bytes, raw retention=false, new unbounded paths0.
- Full V1 copy/snapshot bytes0; source/production mutations0; provider calls0. Only source metadata and small keyed rows were read, never a full source database clone or unrestricted source scan.

This diagnostic allocation is not a measured peak for a full migration. There is no disk-headroom blocker now; the missing canonical dependency blocks completion first.

## Preservation and next action

Attribution, price/ATH0.1%tolerance, compact creator history, Watchtower/Deep roles, review safeguards, journals, retry/quarantine, handoff, budget and rollback contracts remain unchanged. Full-target equivalence/startup/restart/peak qualification cannot be claimed until the current source-root dependency is resolvable. No full candidate was activated and no production database was rebound or repaired.

NEXT_ACTION: One bounded DEV authority resolution for these six memberships: establish their independently accepted event/provenance owner and FK-compatible import contract. If the events are genuinely unavailable, an explicit preserved-unresolved-state contract must be qualified without inventing provenance or changing canonical decisions. Stop here; do not broaden into research, repair production, bypass constraints or run a partial seed as if it were the full operational seed.

Evidence manifest SHA256: `bc46f194d60a6ce77eec13720ceb1d1c59bda42ddd2972c03fecef13a8ec8eb8`. Source/table/key lookup plans and all six deterministic enforcement results are retained in the accompanying JSON. No runtime database payload files are committed.
