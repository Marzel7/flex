# X64.7A — Phase 8: Test Results

New file: `tests/test_x64_7a_commit_hardening.py` (17 tests, all
passing — 15 required by the task plus 2 additional zero-RPC splits,
matching the same pattern X64.7 established for multi-function
requirements).

## Mapping to the task's required test list

| # | Requirement | Test |
|---|---|---|
| 1 | Ledger commit precedes creator inference | `test_two_stage_write_commits_pending_before_creator_known` |
| 2 | Creator inference exception leaves ledger intact | `test_creator_inference_exception_after_pending_write_leaves_ledger_row_intact` |
| 3 | Initial creator-null row is later enriched | `test_initial_pending_row_is_later_enriched_to_resolved` |
| 4 | Ledger-write failure creates a durable pending row | `test_ledger_write_failure_creates_durable_pending_row` |
| 5 | Pending row survives restart | `test_pending_row_survives_restart` |
| 6 | Retry commits ledger and removes pending row | `test_retry_commits_ledger_and_removes_pending_row` |
| 7 | Duplicate retry is idempotent | `test_duplicate_retry_pass_is_idempotent` |
| 8 | Same-signature conflict remains protected | `test_conflict_removes_pending_row_without_corrupting_ledger` |
| 9 | Migration with ledger row records PRESENT | `test_migration_with_ledger_row_records_present` |
| 10 | Migration with pending write records PENDING | `test_migration_with_pending_ledger_row_records_pending` |
| 11 | Migration without either records MISSING | `test_migration_without_ledger_row_records_missing` |
| 12 | Migration coverage check never blocks migration | `test_store_migration_succeeds_even_if_coverage_check_raises` |
| 13 | Ordinary worker recovers anchor from ledger | `test_ordinary_reconcile_function_recovers_ledger_only_signature` |
| 14 | Resolver conflict does not overwrite queue anchor | `test_ledger_conflict_does_not_overwrite_queue_anchor_via_ordinary_reconcile` |
| 15 | All retry and reconciliation paths perform zero RPC | `test_retry_pending_writes_performs_zero_rpc` + `test_migration_coverage_check_performs_zero_rpc` + `test_reconcile_ordinary_path_performs_zero_rpc` |

## A real bug caught during test-writing

`test_migration_with_pending_ledger_row_records_pending` and
`test_migration_without_ledger_row_records_missing` initially **failed**
with `sqlite3.OperationalError: no such table: wt_migration_ledger_coverage`
— not a test-authoring mistake, but a genuine bug in the implementation:
`_ensure_migration_coverage_schema()` used a process-wide module-level
boolean flag (`_MIGRATION_COVERAGE_SCHEMA_READY`) to avoid re-running
`CREATE TABLE IF NOT EXISTS` on every call. Since the flag is set `True`
the first time *any* connection successfully creates the table, a
*second, different* connection (a fresh in-memory DB in each test's own
fixture) would see the flag already `True` and skip schema creation
entirely — even though that specific connection's database genuinely
lacked the table. Fixed by removing the flag and running the (cheap)
`CREATE TABLE IF NOT EXISTS` unconditionally on every call, matching the
pattern every other `ensure_schema()`-style function in this session's
X64.x work already uses. This bug would have surfaced in production only
in a scenario with multiple SQLite connections to different files in the
same process — uncommon but not impossible (e.g. a test harness, a
worker managing multiple ops DBs) — caught here specifically because the
test suite exercises fresh connections per test, which is exactly the
shape that triggers it.

## Test run output

```
tests/test_x64_7a_commit_hardening.py::test_two_stage_write_commits_pending_before_creator_known PASSED
tests/test_x64_7a_commit_hardening.py::test_creator_inference_exception_after_pending_write_leaves_ledger_row_intact PASSED
tests/test_x64_7a_commit_hardening.py::test_initial_pending_row_is_later_enriched_to_resolved PASSED
tests/test_x64_7a_commit_hardening.py::test_ledger_write_failure_creates_durable_pending_row PASSED
tests/test_x64_7a_commit_hardening.py::test_pending_row_survives_restart PASSED
tests/test_x64_7a_commit_hardening.py::test_retry_commits_ledger_and_removes_pending_row PASSED
tests/test_x64_7a_commit_hardening.py::test_duplicate_retry_pass_is_idempotent PASSED
tests/test_x64_7a_commit_hardening.py::test_conflict_removes_pending_row_without_corrupting_ledger PASSED
tests/test_x64_7a_commit_hardening.py::test_migration_with_ledger_row_records_present PASSED
tests/test_x64_7a_commit_hardening.py::test_migration_with_pending_ledger_row_records_pending PASSED
tests/test_x64_7a_commit_hardening.py::test_migration_without_ledger_row_records_missing PASSED
tests/test_x64_7a_commit_hardening.py::test_store_migration_succeeds_even_if_coverage_check_raises PASSED
tests/test_x64_7a_commit_hardening.py::test_ordinary_reconcile_function_recovers_ledger_only_signature PASSED
tests/test_x64_7a_commit_hardening.py::test_ledger_conflict_does_not_overwrite_queue_anchor_via_ordinary_reconcile PASSED
tests/test_x64_7a_commit_hardening.py::test_retry_pending_writes_performs_zero_rpc PASSED
tests/test_x64_7a_commit_hardening.py::test_migration_coverage_check_performs_zero_rpc PASSED
tests/test_x64_7a_commit_hardening.py::test_reconcile_ordinary_path_performs_zero_rpc PASSED

17 passed in 0.80s
```

## Full combined regression suite

```
python -m pytest tests/ -k "walkback or x64 or anchor or create_event_ledger or create_ledger" -q \
  --ignore=tests/test_helius_analysis.py \
  --ignore=tests/test_pumpswap_detection.py \
  --ignore=tests/test_pumpswap_phase2.py \
  --ignore=tests/test_pumpswap_listener.py

157 passed, 2220 deselected
```

All 140 pre-existing tests (X64.5/X64.6/X64.7/disposable-subprov-evidence)
remain green alongside the 17 new X64.7A tests. Separately confirmed the
`test_walkback_worker_startup_resilience.py` suite (10 tests) still
passes unchanged — the new `retry_pending_writes()` call added to
`run_loop()`'s cycle runs safely against that test's bare stub DB (it
calls `ensure_schema()` internally, finds nothing to retry, and exits
cleanly) without needing any new stub, unlike the anchor-reconciliation
addition from X64.5 which did require one.

## Cross-module import/syntax verification

```
python3 -c "
import src.core.pumpfun_curve_listener
import src.core.watchtower_attribution
import src.core.walkback_worker
import src.ops.create_event_ledger
import src.ops.anchor_reconciliation
"
```
All five modified/extended modules import cleanly together with no
circular-import or syntax regressions.
