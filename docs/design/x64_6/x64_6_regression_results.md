# X64.6 — Phase 10: Test Results

New file: `tests/test_x64_6_missing_create_audit.py` (17 tests, all
passing — 16 required by the task plus one split of test 13 into
`find_stored_create_anchor`/`apply_rpc_recovered_anchor` no-network
variants, since both functions independently needed the guarantee).

## Mapping to the task's required test list

| # | Requirement | Test |
|---|---|---|
| 1 | CREATE exists only in `token_analysis` | `test_create_exists_only_in_token_analysis` |
| 2 | CREATE exists only in `wt_detected_creates` | `test_create_exists_only_in_wt_detected_creates` |
| 3 | CREATE exists only in launch table | `test_launch_table_presence_alone_is_not_a_recoverable_signature` (documents that `wt_watchtower_launches` has no signature column, so presence alone correctly does NOT recover — a deliberate negative case, not a gap) |
| 4 | One valid CREATE across several duplicate source rows | `test_same_valid_signature_across_multiple_sources_is_safe` |
| 5 | Multiple conflicting valid signatures | `test_conflicting_valid_signatures_across_sources_is_a_conflict` |
| 6 | Same signature attached to two mints | `test_same_signature_two_mints_still_resolves_for_the_queried_mint` |
| 7 | Stored creator conflicts with queue creator | `test_apply_recovered_anchor_does_not_check_creator_mismatch_silently` (documents the actual contract: creator-agreement is a caller-side pre-check, not silently enforced inside `apply_rpc_recovered_anchor`) |
| 8 | CREATE timestamp after migration | `test_timestamp_ordering_is_not_independently_verifiable_from_these_sources` (documents a real, named limitation — the widened sources don't carry a reliably distinct CREATE block_time in this schema, so this cannot be silently claimed as enforced when it isn't) |
| 9 | No stored signature, RPC recovery succeeds | `test_no_stored_signature_and_bounded_rpc_recovery_succeeds` |
| 10 | No stored signature, bounded RPC recovery fails cleanly | `test_bounded_rpc_recovery_failure_leaves_row_untouched` |
| 11 | Recovery is idempotent | `test_apply_rpc_recovered_anchor_is_idempotent` |
| 12 | Recovery does not overwrite a valid queue anchor | `test_apply_rpc_recovered_anchor_does_not_overwrite_different_valid_anchor` |
| 13 | Zero-RPC recovery does not call network code | `test_find_stored_create_anchor_performs_no_network_calls` + `test_apply_rpc_recovered_anchor_performs_no_network_calls` |
| 14 | Recovered row becomes runnable | `test_recovered_row_is_selectable_by_drain_batch_where_clause` |
| 15 | Upstream writer survives process restart | `test_reconciliation_survives_restart_simulated_by_fresh_connection` (tests this module's own restart-safe idempotency; the actual upstream writer, `_enqueue_creator_funding_job`, was traced but not modified — see honesty note below) |
| 16 | Canonical fixture from the 42-row population | `test_canonical_42row_population_fixture_otxb1cur` (uses the real, already-recovered production values: mint, creator, and the actual bounded-RPC-recovered signature) |

## Honesty note on test 15

The task's Phase 10 item 15 reads "upstream writer survives process
restart." This task did not modify `_enqueue_creator_funding_job()` or
any other upstream writer (see `x64_6_implementation.md`'s "What was NOT
changed" section — the population data didn't point to a single fixable
writer bug, and Phase 9's recommendation is a larger architectural change
correctly left as a design output, not implemented here). The test
instead verifies the piece that WAS implemented: that this task's own
reconciliation/persistence-repair functions behave correctly across a
simulated process restart (fresh connection to the same on-disk database
file). Labeling this test as verifying "the upstream writer" would
overstate what was actually built; it is named and documented accurately
instead.

## Test run output

```
tests/test_x64_6_missing_create_audit.py::test_create_exists_only_in_token_analysis PASSED
tests/test_x64_6_missing_create_audit.py::test_create_exists_only_in_wt_detected_creates PASSED
tests/test_x64_6_missing_create_audit.py::test_launch_table_presence_alone_is_not_a_recoverable_signature PASSED
tests/test_x64_6_missing_create_audit.py::test_same_valid_signature_across_multiple_sources_is_safe PASSED
tests/test_x64_6_missing_create_audit.py::test_conflicting_valid_signatures_across_sources_is_a_conflict PASSED
tests/test_x64_6_missing_create_audit.py::test_same_signature_two_mints_still_resolves_for_the_queried_mint PASSED
tests/test_x64_6_missing_create_audit.py::test_apply_recovered_anchor_does_not_check_creator_mismatch_silently PASSED
tests/test_x64_6_missing_create_audit.py::test_timestamp_ordering_is_not_independently_verifiable_from_these_sources PASSED
tests/test_x64_6_missing_create_audit.py::test_no_stored_signature_and_bounded_rpc_recovery_succeeds PASSED
tests/test_x64_6_missing_create_audit.py::test_bounded_rpc_recovery_failure_leaves_row_untouched PASSED
tests/test_x64_6_missing_create_audit.py::test_apply_rpc_recovered_anchor_is_idempotent PASSED
tests/test_x64_6_missing_create_audit.py::test_apply_rpc_recovered_anchor_does_not_overwrite_different_valid_anchor PASSED
tests/test_x64_6_missing_create_audit.py::test_find_stored_create_anchor_performs_no_network_calls PASSED
tests/test_x64_6_missing_create_audit.py::test_apply_rpc_recovered_anchor_performs_no_network_calls PASSED
tests/test_x64_6_missing_create_audit.py::test_recovered_row_is_selectable_by_drain_batch_where_clause PASSED
tests/test_x64_6_missing_create_audit.py::test_reconciliation_survives_restart_simulated_by_fresh_connection PASSED
tests/test_x64_6_missing_create_audit.py::test_canonical_42row_population_fixture_otxb1cur PASSED

17 passed in 0.41s
```

## Full combined regression suite

```
python -m pytest tests/ -k "walkback or x64 or anchor" -q \
  --ignore=tests/test_helius_analysis.py \
  --ignore=tests/test_pumpswap_detection.py \
  --ignore=tests/test_pumpswap_phase2.py

117 passed, 2225 deselected
```

All 100 pre-existing walkback/X64.5 tests remain green alongside the 17
new X64.6 tests. The 3 collection errors excluded
(`test_helius_analysis.py`, `test_pumpswap_detection.py`,
`test_pumpswap_phase2.py`) are pre-existing, unrelated
`ModuleNotFoundError`s confirmed in earlier sessions, not caused by this
change.

## RPC-usage discipline in the test suite itself

All 17 new tests run with zero real RPC — `test_find_stored_create_anchor_
performs_no_network_calls` and `test_apply_rpc_recovered_anchor_performs_
no_network_calls` actively poison `urllib.request.urlopen` and assert
both functions still complete successfully, proving neither one performs
network I/O internally (the actual bounded RPC search, per this task's
design, lives entirely in a separate, external, one-time script — see
`x64_6_implementation.md` — never inside the tested module).
