# X64.5 — Phase 9: Test Results

New file: `tests/test_x64_5_anchor_reconciliation.py` (14 tests, all
passing). One pre-existing file updated:
`tests/test_walkback_worker_startup_resilience.py` (added one
`monkeypatch.setattr` line to stub the new self-healing pass, consistent
with that file's own established fixture pattern — no assertion or
behavior under test in that file was changed).

## Mapping to the task's required test list

| # | Requirement | Test |
|---|---|---|
| 1 | Signature exists before enqueue | `test_signature_exists_before_enqueue_row_is_created_anchored` |
| 2 | Signature commits immediately after enqueue | `test_enqueue_without_live_conn_falls_back_to_own_connection` (exercises the new self-opened-connection path with the real production call shape — no `live_conn` argument) |
| 3 | Signature appears several worker cycles later | `test_signature_appears_after_enqueue_reconciliation_recovers_it` (two reconciliation passes: first finds nothing, signature lands, second recovers it) |
| 4 | `live_conn` is absent | `test_enqueue_migration_live_conn_absent_and_unreachable_does_not_crash` |
| 5 | Signature is malformed | `test_malformed_signature_is_not_recovered` |
| 6 | Multiple signature rows exist for one mint | `test_multiple_funding_queue_rows_classified_ambiguous` |
| 7 | Queue already contains a valid anchor | `test_row_with_existing_valid_anchor_is_not_touched` |
| 8 | Duplicate reconciliation is idempotent | `test_reconciliation_is_idempotent` |
| 9 | Process restarts while row is PENDING_ANCHOR | `test_restart_mid_pending_anchor_state_survives` (fresh connection to the same on-disk DB file, simulating a restart) |
| 10 | Reconciliation performs zero RPC | `test_reconciliation_performs_no_network_calls` (poisons `urllib.request.urlopen` — the codebase's own RPC entry point — and asserts reconciliation still succeeds) |
| 11 | Recovery does not increment walkback attempts | `test_recovery_does_not_increment_walkback_attempts_counter` |
| 12 | Recovered row becomes runnable | `test_recovered_row_is_selectable_by_drain_batch_where_clause` (uses `drain_batch`'s own literal WHERE-clause shape) |
| 13 | Conflicting valid anchors are not silently overwritten | `test_conflicting_existing_valid_anchor_is_not_overwritten` |
| 14 | Canonical `H55qUAeK` regression fixture | `test_canonical_h55quaek_regression_fixture` |

Plus one additional test not in the required list, kept because it
verifies a real, load-bearing detail of the implementation:
`test_reconciliation_is_idempotent` also asserts the
`wt_anchor_reconciliation_log` row count stays at exactly 1 after two
reconciliation passes (not just that the queue row itself is
unaffected).

## Test run output

```
tests/test_x64_5_anchor_reconciliation.py::test_signature_exists_before_enqueue_row_is_created_anchored PASSED
tests/test_x64_5_anchor_reconciliation.py::test_enqueue_without_live_conn_falls_back_to_own_connection PASSED
tests/test_x64_5_anchor_reconciliation.py::test_signature_appears_after_enqueue_reconciliation_recovers_it PASSED
tests/test_x64_5_anchor_reconciliation.py::test_enqueue_migration_live_conn_absent_and_unreachable_does_not_crash PASSED
tests/test_x64_5_anchor_reconciliation.py::test_malformed_signature_is_not_recovered PASSED
tests/test_x64_5_anchor_reconciliation.py::test_multiple_funding_queue_rows_classified_ambiguous PASSED
tests/test_x64_5_anchor_reconciliation.py::test_row_with_existing_valid_anchor_is_not_touched PASSED
tests/test_x64_5_anchor_reconciliation.py::test_reconciliation_is_idempotent PASSED
tests/test_x64_5_anchor_reconciliation.py::test_restart_mid_pending_anchor_state_survives PASSED
tests/test_x64_5_anchor_reconciliation.py::test_reconciliation_performs_no_network_calls PASSED
tests/test_x64_5_anchor_reconciliation.py::test_recovery_does_not_increment_walkback_attempts_counter PASSED
tests/test_x64_5_anchor_reconciliation.py::test_recovered_row_is_selectable_by_drain_batch_where_clause PASSED
tests/test_x64_5_anchor_reconciliation.py::test_conflicting_existing_valid_anchor_is_not_overwritten PASSED
tests/test_x64_5_anchor_reconciliation.py::test_canonical_h55quaek_regression_fixture PASSED

14 passed in 0.77s
```

## Fixture note

`classify_creator()` (`walkback_queue.py`) joins/queries several tables
owned by other modules (`wt_ops_v2`, `wt_ops_v2_wallets`,
`wt_watchtower_launches`, `wt_wrap_close_candidates`,
`wt_discovered_subprovs`, `wt_candidate_websocket_watches`,
`wt_creator_birth_launch`, `watchtower_token_attribution`,
`creator_funders`) that aren't part of `walkback_queue.ensure_schema()`'s
own scope. The three tests that call `enqueue_migration()` directly
(rather than the lower-level `reconcile_waiting_create_anchors()`) needed
these declared empty, matching each table's real columns, so
`classify_creator()` runs exactly as it does in production (falling
through every case to `FULL_WALKBACK`, since all tables are empty) rather
than raising `OperationalError`.

## One pre-existing test required a fixture update

`tests/test_walkback_worker_startup_resilience.py::test_main_loop_starts_after_skipped_maintenance_task`
initially failed after the `run_loop()` change — its `stub_run_loop_dependencies`
fixture builds a bare 3-column `wt_walkback_queue` stub table
(`mint, status, attempts`) with everything else in `run_loop()` stubbed
inert, and the new anchor-reconciliation pre-pass (which queries real
columns like `creator`) doesn't fit that minimal shape. Fixed by adding
one `monkeypatch.setattr("src.ops.anchor_reconciliation.reconcile_waiting_create_anchors", ...)`
line to that fixture, returning an inert no-op result — exactly matching
the fixture's own existing pattern of stubbing every `run_loop()`
dependency not under test in that file. No assertion in that file was
changed; its own explicit docstring constraint ("must never change
attribution/capture/queue-decision logic — only startup robustness") is
still honored, since the new pass is orthogonal maintenance, not
capture/attribution logic.

## Full combined regression suite

```
python -m pytest tests/ -k "walkback or x64 or anchor" -q \
  --ignore=tests/test_helius_analysis.py \
  --ignore=tests/test_pumpswap_detection.py \
  --ignore=tests/test_pumpswap_phase2.py

100 passed, 2225 deselected
```

All pre-existing walkback/X64.x tests remain green; the two collection
errors excluded (`test_helius_analysis.py`, `test_pumpswap_detection.py`,
`test_pumpswap_phase2.py`) are pre-existing, unrelated `ModuleNotFoundError`s
confirmed in earlier sessions of this work, not caused by this change.
