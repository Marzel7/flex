# DEV004 Final Price Reference Compatibility

DEV004_PRICE_REFERENCE_STATUS: **PASS** for the exact price record. Full migration rehearsal: **HOLD** at a different existing Watchtower reference. Production unchanged.

Qualified source `20b0f4e159e2c05b6bfcd54084178ad6a5cc4862`; baseline FLEX_STABLE_V2 `2d825bbcf27f802bd0bfcef90cedeba43cc968eb` preserved. Continuation from `202e30b4` / `34276f46`. No complete runtime/deployment manifest or cutover readiness is claimed.

## EXACT_ORPHAN_CAUSE

Retained `token_market_cap_peaks` row13, mint `BEy3GeKMC3J9dr2eabXYTUuD37YGiSnwWFq13hSgpump`, is field-for-field equal to V1. Both lack its `tracked_tokens` parent. This is a real schema mismatch between persistent price evidence and optional monitoring membership. The exact historical deletion event is not inferred: accepted `token_inactivity_manager.cleanup_stopped_tokens` explicitly permits removing stopped registry entries after seven days.

## AUTHORITATIVE_TOKEN_IDENTITY

An indexed primary-key lookup finds the same full `token_analysis` row in V1 and retained Core. Its creator, CREATE signature, migration signature, opening observation/provenance and price facts are retained exactly. Source row digests and selector plans are in price_result.json. No missing identity is manufactured.

## TRACKED_TOKEN_SEMANTICS

`PriceWorkerRegistry` owns tracking admission, active/priority state, refresh timing and stop reasons; stopped memberships are removable. The accepted `_read_peak_and_current` audit reader and API peak queries read persistent peaks by mint without requiring tracking membership. Accepted `price_service` writes compact facts against token identity. The former FK literally targets `tracked_tokens`; no pre-existing alternate FK is falsely claimed. This DEV introduces the explicitly authorized compatibility profile linking the same peak table to canonical `token_analysis(mint)` instead.

## PRICE_EVIDENCE_PRESERVATION

All nine peak columns remain exact: ATH101,219.0, timestamp1788333468, raw/effective/candidate values101,219.0, candidate count1 and raw timestamp1788333468. Opening baseline remains101,219.0 at1788333468, source `price_snapshot:dexscreener`. No source substitution, historical recalculation or finalization identity change. This table has no separate finalization-ID field; none is invented. Numerical relative difference0, within0.1% valuation-only tolerance. Provenance/creator/attribution tolerance remains zero.

Only the disposable Core bootstrap gains `price_identity_parent="token_analysis"`, with a distinct version/contract marker. Default `tracked_tokens` behaviour is unchanged. It never reparents an existing incompatible/nonempty table or inserts a monitoring row. Birdeye credentials, endpoints, budgets, windows, retries, checkpoint/finalization and lifecycle algorithms are unmodified; accepted acquisition/recovery evidence is reused.

## FOREIGN_KEY_INTEGRITY

The qualified target uses `FOREIGN KEY(mint) REFERENCES token_analysis(mint)`, with original columns/defaults/PK unchanged. FK remains ON. Missing canonical identity and deleting a referenced identity are rejected. All retained peak identities satisfy the new parent relationship. The six Byzantine imports also pass with their typed real-parent FKs enforced. Full-seed transactions defer checks until commit; constraints are never disabled.

## DISPOSABLE_IMPORT_RESULT

Exact-case import and child-process reopen PASS; duplicate inserts add0; canonical price remains unchanged. The actual installed audit reader returns exactly the V1 result. Actual accepted price UPSERT preserves equal observations; a rolled-back prospective control advances a peak correctly without monitoring membership. Original bootstrap15test groups PASS.

Returned directly to full-seed qualification:28,429 indexed V1 selectors found,29 documented synthetic controls excluded,15 accepted external-reference records retained under their existing authority. Direct-source compact import attempted; no full V1 copies. All216 selected Core peaks imported before the later OPS failure. An initial harness error attempted INSERT into generated `transfer_index.amount_sol`; corrected to insert writable columns and verify generated values exactly. No schema or source data was changed to bypass it.

## PROVIDER_FREE_TEST_RESULTS

**21 focused controls PASS**, including the exact price case, canonical identity FK, original reader/writer compatibility, repeat initialization, incompatible-profile rejection, fresh-process restart/idempotency, six Byzantine imports and retained peak-parent closure. **15 original bootstrap groups PASS**. No unrelated creator/Watchtower/Deep/Birdeye matrices rerun. Network blocked; provider calls0.

## REMAINING_MIGRATION_BLOCKERS

Full import fails closed after27,288 selected rows at `operator_launch_membership`, mint `24rX1SutpnUTcDpYLdE6Bg2aK16m2kWYrdyKTfuFpump`, operation `04265d9f-6eb2-568c-a49e-9253091a4dbb`. Its preserved `event_id` is `38YEpU7wyRf1hXra7tD9ZfVXhV2QCwRbbitfFD1GcTtFvcf2RUxwMxaVGnQaDYSVeSgY2T6TsVBzgXyZPVh7j9Np`, population `watchtower_behavioural_paginated_lineage_20260825`. Exact indexed V1 lookups find0identity events and0confirmed matches. The typed guard therefore rejects `unknown or ambiguous membership reference`. No fallback identity, synthetic event or changed assignment is introduced.

Both disposable transactions rolled back; verified Core token_analysis rows0 and OPS membership rows0. No paired activation/transfer or matched-pair rollback is claimed from this failed import. Existing accepted transfer evidence remains closed, but the full operational-seed rehearsal is not PASS.

Price fixture118,784allocated bytes; largest81,920bytes. Full-import sampled peak allocation14,077,952bytes, free `3833876480` before / `3830632448` after; both above2GB+64MiB. Sampled peak is not an absolute measured high-water mark. Enforced per-file OS limit64MiB; total allocation sampled against128MiB; no absolute WAL/lock duration claim. No new file near500MB, full database copies, provider payload retention, production writes/source changes/restarts, deletion or cutover.

## NEXT_ACTION

Qualify only the accepted typed-reference authority for that exact Watchtower membership before resuming the full disposable import. Do not reopen this resolved price case or invent a parent. Overall migration remains HOLD; price compatibility is closed PASS. DEV-only qualification is not deployed; runtime deployment gate QUALIFIED_NOT_DEPLOYED, no production authorization implied.
