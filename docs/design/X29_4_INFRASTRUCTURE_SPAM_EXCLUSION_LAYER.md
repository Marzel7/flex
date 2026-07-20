# X29.4 — Infrastructure Spam Exclusion Layer: Validation Report

Core principle: receiving SOL from a spam wallet is not evidence — it is environmental noise. A wallet has no control over who sends it SOL. This sprint removes Infrastructure Spam from operational attribution entirely and replaces it with a purely orthogonal **Wallet Quality** annotation dimension.

## Files changed

New:
- [src/ops/known_spam_wallets.py](../../src/ops/known_spam_wallets.py) — `wt_known_spam_wallets` manually-maintained registry (migrated seed wallet from X29.1.3's now-deleted `src/utils/spam_infrastructure_registry.py`)
- [src/ops/wallet_quality.py](../../src/ops/wallet_quality.py) — `wt_wallet_quality` annotation table + service (`mark_spam_sender`, `mark_spam_recipient`, `record_spam_transfer`, `get_wallet_quality`, `serialize_wallet_quality`)
- [tests/test_x29_4_wallet_quality.py](../../tests/test_x29_4_wallet_quality.py) — 22 tests covering all 10 validation requirements

Modified:
- [src/core/walkback_worker.py](../../src/core/walkback_worker.py) — `_find_funder_via_rpc` now skips known spam senders before they become funding candidates (`_is_known_spam_sender` check precedes `candidates.append`), records `IGNORED_SPAM_SENDER` diagnostic, calls `record_spam_transfer` (annotation only)
- [src/ops/funding_boundary.py](../../src/ops/funding_boundary.py) — `derive_funding_boundary` accepts an optional `known_spam_wallets` set; a spam-sourced `origin_wallet` is rejected entirely (`STATUS_UNRESOLVED` / `REASON_IGNORED_SPAM_SENDER`, all evidence fields nulled)
- [src/ops/funding_boundary_backfill.py](../../src/ops/funding_boundary_backfill.py) — passes the live `wt_known_spam_wallets` set into every derivation call
- [src/ops/provisioning_edges.py](../../src/ops/provisioning_edges.py) — `edges_for_wallet` gains `show_spam_transfers` (default `False`), filtering spam-sender edges out of graph reads by default
- [src/ops/provisioning_edges_routes.py](../../src/ops/provisioning_edges_routes.py) — exposes `?show_spam_transfers=1` developer toggle on `/api/ops-v2/provisioning-edges/<wallet>`
- [src/core/operation_dashboard_routes.py](../../src/core/operation_dashboard_routes.py) — additive `wallet_quality` field on the single-mint `/api/ops-v2/investigation-pipeline` response
- [templates/discovery.html](../../templates/discovery.html) — new "Wallet Quality" card, neutral grey accent (not purple/cyan/amber), explicit "Environmental annotation only" copy, rendered separately from Funding Boundary/attribution

Deleted:
- `src/utils/spam_infrastructure_registry.py` (X29.1.3) — its unused `INFRASTRUCTURE_SPAM` outcome-type constant is fully removed per the brief's migration instruction; the confirmed seed wallet and its evidence were carried forward into `known_spam_wallets.py`

## Schema

`wt_known_spam_wallets`: `wallet PRIMARY KEY`, `name`, `classification`, `reason`, `evidence`, `first_seen`, `last_seen`, `added_by`, `added_at`, `notes`. Manually maintained only — `seed_known_spam_wallets()` is idempotent (`INSERT OR IGNORE`) and never overwrites a manually-edited row.

`wt_wallet_quality`: `wallet PRIMARY KEY`, `spam_sender`, `spam_recipient`, `dust_marker`, `dust_recipient`, `high_unsolicited_inbound` (all `CHECK IN (0,1)`), `confidence`, `first_seen`, `last_seen`, `metadata`. Flags only ever accumulate (never clear once set) — annotations describe observed history, not current state.

## Validation — all 10 requirements demonstrated

1. **Spam funding a WATCHTOWER treasury does not affect attribution** — `record_spam_transfer` writes only to `wt_wallet_quality`; structural test confirms `wallet_quality.py` never contains `UPDATE`/`INSERT INTO wt_attribution_outcomes`. (`test_spam_wallet_funding_watchtower_treasury_does_not_affect_attribution`)
2. **Spam funding a CEX wallet does not affect Funding Boundary** — `derive_funding_boundary` with the spam wallet as `origin_wallet` returns `UNRESOLVED`/`IGNORED_SPAM_SENDER`, all boundary fields nulled. (`test_spam_wallet_funding_cex_wallet_does_not_affect_funding_boundary`)
3. **Walkback ignores known spam senders** — structural test confirms `_is_known_spam_sender` check runs *before* `candidates.append` in `_find_funder_via_rpc`, so a spam sender can never be selected as the funder; `IGNORED_SPAM_SENDER` string present in source. (`test_walkback_worker_checks_spam_sender_before_accepting_a_candidate`)
4. **Recipients annotated only** — `record_spam_transfer` sets `spam_recipient=1` and nothing else; verified for both a WATCHTOWER treasury and a Coinbase-style wallet (`test_coinbase_wallet_receiving_spam_does_not_become_spam_associated`) — neither becomes "spam-associated" beyond the flag.
5. **Unknown wallets never auto-classified** — `is_known_spam_wallet` returns `False` for any address not manually added to the registry, regardless of behaviour (`test_unknown_wallet_sending_unsolicited_sol_is_not_classified_as_spam`).
6. **No `INFRASTRUCTURE_SPAM` outcome possible** — AST-level guard confirms no module defines it as a live Python identifier; old registry file deleted entirely (`test_no_infrastructure_spam_constant_defined_anywhere`, `test_old_spam_infrastructure_registry_file_removed`).
7. **Graphs exclude spam edges by default** — `edges_for_wallet(conn, wallet)` (no kwarg) excludes spam-sender edges; `show_spam_transfers=True` restores them for forensic use (`test_edges_for_wallet_excludes_spam_by_default`, `test_edges_for_wallet_show_spam_transfers_toggle_includes_them`).
8. **X29.3 Funding Boundary behaviour unchanged** — omitting `known_spam_wallets` (the default, `None`) reproduces byte-identical classification to pre-X29.4 (`test_funding_boundary_behaviour_unchanged_when_no_spam_wallets_passed`).
9. **WATCHTOWER attribution unchanged** — `operator_ids` check still precedes `_boundary(` in `derive_outcome` (re-verified, same guard as X29.2/X29.3).
10. **Zero regressions** — 137/137 across the full X29 family (22 new + 30 Funding Boundary + 85 prior X29.1.x); full suite unchanged from pre-existing baseline (X24-family failures confirmed pre-existing in earlier sprints, `test_ops_x21b_routes.py`/`test_ops_x21c_routes.py` — the routes this sprint touched — pass 7/7 clean in isolation).

## Live verification (2026-07-19)

Seeded the live ops DB: `wt_known_spam_wallets` created with the confirmed seed wallet (`GF7YB1jGktkRQNnXU5YCuVRCQdoHcctkUw1q5bgbLLXc`); `wt_wallet_quality` created empty (populates as walkback encounters live spam senders going forward). Reloaded gunicorn:

- `/api/ops-v2/investigation-pipeline?mint=...` returns both `funding_boundary` (unchanged) and a new `wallet_quality` field (`null` when no annotation exists yet).
- `/api/ops-v2/provisioning-edges/<spam_wallet>` returns `show_spam_transfers: false` in its response, confirming the toggle is live and defaults OFF.

## Test results

`test_x29_4_wallet_quality.py`: 22/22 passed. Combined with `test_x29_3_funding_boundary.py` + the four earlier X29.1.x files: 137/137 passed.

## RPC calls introduced

**Zero.** Structural test confirms no RPC-related strings in `known_spam_wallets.py` or `wallet_quality.py`.
