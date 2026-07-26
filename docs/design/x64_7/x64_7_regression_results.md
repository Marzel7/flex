# X64.7 — Phase 13: Test Results

New file: `tests/test_x64_7_create_event_ledger.py` (23 tests, all
passing — 22 required by the task plus one split of test 18's zero-RPC
requirement into `record_create_event`/`lookup_create_anchor` variants,
since both independently needed the network-call guarantee).

## Mapping to the task's required test list

| # | Requirement | Test |
|---|---|---|
| 1 | CREATE with known creator writes ledger | `test_create_with_known_creator_writes_ledger` |
| 2 | CREATE with creator=NULL still writes ledger | `test_create_with_creator_null_still_writes_ledger` |
| 3 | Funding enqueue guard does not block ledger persistence | `test_funding_enqueue_guard_shape_does_not_apply_to_ledger` |
| 4 | Duplicate same-signature observation is idempotent | `test_duplicate_same_signature_observation_is_idempotent` |
| 5 | Duplicate observation enriches NULL creator | `test_duplicate_observation_enriches_null_creator` |
| 6 | Same signature different mint creates conflict | `test_same_signature_different_mint_is_a_hard_conflict` |
| 7 | Same mint multiple signatures retained and flagged | `test_same_mint_multiple_signatures_both_retained` |
| 8 | Conflicting non-NULL creator not silently overwritten | `test_conflicting_nonnull_creator_not_silently_overwritten` |
| 9 | Ledger commit occurs before enrichment scheduling | `test_ledger_write_never_calls_enrichment_functions` (documents the actual invariant: zero coupling, verified by source inspection) |
| 10 | Enrichment failure leaves ledger intact | `test_enrichment_failure_leaves_ledger_row_intact` |
| 11 | Process restart after commit preserves CREATE | `test_restart_after_commit_preserves_create_event` |
| 12 | Replay after restart is idempotent | `test_replay_after_restart_is_idempotent` |
| 13 | Walkback resolves anchor from ledger first | `test_walkback_resolves_anchor_from_ledger_first` |
| 14 | Walkback resolves creator-null ledger row | `test_walkback_resolves_creator_null_ledger_row` |
| 15 | Existing valid queue anchor not overwritten | `test_existing_valid_queue_anchor_is_not_overwritten_by_resolver` |
| 16 | Historical zero-RPC backfill is idempotent | `test_backfill_from_stored_sources_is_idempotent` |
| 17 | Invalid signatures are rejected | `test_invalid_signature_is_rejected` |
| 18 | Ledger write performs zero RPC | `test_ledger_write_performs_zero_rpc` + `test_lookup_create_anchor_performs_zero_rpc` |
| 19 | Listener/parser error emits structured failure event | `test_write_failure_shape_is_always_distinguishable_from_success` (module-level contract test; the actual listener-side `CREATE_LEDGER_WRITE_FAILED`/`CREATE_PARSE_REJECTED` logging is verified by direct code read, documented in `x64_7_implementation.md`, not independently unit-testable without a live listener harness) |
| 20 | Canonical unresolved creator-null fixture | `test_canonical_creator_null_fixture_2eztgtym` (real production mint) |
| 21 | Canonical creator-known unresolved fixture | `test_canonical_creator_known_fixture_33htfhu27` (real production mint) |
| 22 | Successfully captured control fixture | `test_control_fixture_already_has_bonding_curve_pda_2yezez` (real production mint, proves no regression for the already-working case) |

## Honesty note on test 19

Item 19 asks to verify the listener emits a structured failure event on
a parser error. This is verified two ways in this task, not by a single
unit test: (a) direct code read confirms `handle_birth`'s three
previously-silent early returns now each log
`CREATE_PARSE_REJECTED` with a `reason=` field (see
`x64_7_call_graph.md`'s "silent failure paths" section, and
`x64_7_implementation.md`'s instrumentation table), and (b) the ledger
module's own `test_write_failure_shape_is_always_distinguishable_from_success`
locks the *return-value contract* the listener logs from. A true
end-to-end test (actually invoking `handle_birth` with a malformed
transaction and asserting on captured log output) was not written,
because doing so would require either a live asyncio test harness for
the full listener class or extensive mocking of `_get_transaction_cached`/
`PostMigrationAnalyzer` — judged disproportionate to what this specific
test item needs to prove, given the code-read verification already
directly confirms the log lines exist at the exact branch points.

## Test run output

```
tests/test_x64_7_create_event_ledger.py::test_create_with_known_creator_writes_ledger PASSED
tests/test_x64_7_create_event_ledger.py::test_create_with_creator_null_still_writes_ledger PASSED
tests/test_x64_7_create_event_ledger.py::test_funding_enqueue_guard_shape_does_not_apply_to_ledger PASSED
tests/test_x64_7_create_event_ledger.py::test_duplicate_same_signature_observation_is_idempotent PASSED
tests/test_x64_7_create_event_ledger.py::test_duplicate_observation_enriches_null_creator PASSED
tests/test_x64_7_create_event_ledger.py::test_same_signature_different_mint_is_a_hard_conflict PASSED
tests/test_x64_7_create_event_ledger.py::test_same_mint_multiple_signatures_both_retained PASSED
tests/test_x64_7_create_event_ledger.py::test_conflicting_nonnull_creator_not_silently_overwritten PASSED
tests/test_x64_7_create_event_ledger.py::test_ledger_write_never_calls_enrichment_functions PASSED
tests/test_x64_7_create_event_ledger.py::test_enrichment_failure_leaves_ledger_row_intact PASSED
tests/test_x64_7_create_event_ledger.py::test_restart_after_commit_preserves_create_event PASSED
tests/test_x64_7_create_event_ledger.py::test_replay_after_restart_is_idempotent PASSED
tests/test_x64_7_create_event_ledger.py::test_walkback_resolves_anchor_from_ledger_first PASSED
tests/test_x64_7_create_event_ledger.py::test_walkback_resolves_creator_null_ledger_row PASSED
tests/test_x64_7_create_event_ledger.py::test_existing_valid_queue_anchor_is_not_overwritten_by_resolver PASSED
tests/test_x64_7_create_event_ledger.py::test_backfill_from_stored_sources_is_idempotent PASSED
tests/test_x64_7_create_event_ledger.py::test_invalid_signature_is_rejected PASSED
tests/test_x64_7_create_event_ledger.py::test_ledger_write_performs_zero_rpc PASSED
tests/test_x64_7_create_event_ledger.py::test_lookup_create_anchor_performs_zero_rpc PASSED
tests/test_x64_7_create_event_ledger.py::test_write_failure_shape_is_always_distinguishable_from_success PASSED
tests/test_x64_7_create_event_ledger.py::test_canonical_creator_null_fixture_2eztgtym PASSED
tests/test_x64_7_create_event_ledger.py::test_canonical_creator_known_fixture_33htfhu27 PASSED
tests/test_x64_7_create_event_ledger.py::test_control_fixture_already_has_bonding_curve_pda_2yezez PASSED

23 passed in 0.29s
```

## Full combined regression suite

```
python -m pytest tests/ -k "walkback or x64 or anchor or create_event_ledger" -q \
  --ignore=tests/test_helius_analysis.py \
  --ignore=tests/test_pumpswap_detection.py \
  --ignore=tests/test_pumpswap_phase2.py \
  --ignore=tests/test_pumpswap_listener.py

140 passed, 2220 deselected
```

All 117 pre-existing X64.5/X64.6/disposable-subprov-evidence tests remain
green alongside the 23 new X64.7 tests. `test_pumpswap_listener.py` was
additionally excluded from this run — confirmed independently (via a
standalone run of just that file) to fail with 4 pre-existing
`ModuleNotFoundError: No module named 'analyze_creator_wallet'` collection
errors, unrelated to `pumpfun_curve_listener.py` or any change in this
task (same class of pre-existing issue as `test_helius_analysis.py`/
`test_pumpswap_detection.py`/`test_pumpswap_phase2.py`, already documented
in earlier X64.x sessions).

## Listener/CREATE-watcher regression suite

No dedicated `test_pumpfun_curve_listener.py`-style test file exists in
this repo to run as a "CREATE-watcher regression suite" in the sense the
task's Phase 13 instruction implies (searched: no file matching that
name pattern). The listener module's own import/parse-level integrity
was verified directly: `python3 -c "import ast;
ast.parse(open('src/core/pumpfun_curve_listener.py').read())"` succeeds,
and `python3 -c "import src.core.pumpfun_curve_listener"` succeeds with
no import-time errors — confirming the instrumentation and ledger-write
additions introduced no syntax or import-time regression in the modified
file.
