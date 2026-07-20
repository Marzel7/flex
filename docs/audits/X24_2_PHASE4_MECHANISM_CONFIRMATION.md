# X24.2 Phase 4 — Mechanism-Aware Observation Confirmation

**Method: code inspection + existing test suite only. No live daemon action taken.**

## Claim 1: PLAIN_TRANSFER, WSOL_WRAP_CLOSE, and SEEDED_ACCOUNT_CLOSE still select their intended observation strategies

Confirmed by inspection of `SubscriptionManager.subscribe()` (`src/core/ws_cascade.py:1652`), unchanged since X24.1 and not touched by this sprint's Phase 2 edits (verified via `git diff` — the function body containing this branch does not appear in the diff for this sprint):

```python
if kind in ("treasury", "subprov_account"):
    # accountSubscribe — for PLAIN_TRANSFER-funded sessions and treasuries,
    # whose funding move is a plain system::transfer with no matching program log.
    msg = {"method": "accountSubscribe", ...}
else:
    # logsSubscribe — for WSOL_WRAP_CLOSE / SEEDED_ACCOUNT_CLOSE sessions,
    # whose terminal instruction is spl-token::closeAccount, a real program
    # instruction that DOES emit a matching log.
    msg = {"method": "logsSubscribe", ...}
```

Kind selection is made once, at the call site in `subscribe_live_armed()`:
```python
kind = "subprov_account" if funding_mechanism == "PLAIN_TRANSFER" else "subprov"
```

Proven by test (`tests/test_x24_1_mechanism_aware_subscription.py`, all passing):
- `test_subprov_account_kind_uses_accountsubscribe` — PLAIN_TRANSFER → accountSubscribe
- `test_ordinary_subprov_kind_still_uses_logssubscribe` — WSOL_WRAP_CLOSE/SEEDED_ACCOUNT_CLOSE (both use the default `"subprov"` kind, since SEEDED_ACCOUNT_CLOSE also terminates in `spl-token::closeAccount` — confirmed in X24 investigation) → logsSubscribe unchanged

## Claim 2: all routes converge on the existing `_handle_subprov_tx()` and candidate pipeline

Confirmed by inspection of every call path that reaches a subprov's funding transaction:

| Path | Entry point | Converges on |
|---|---|---|
| Live WS notification (any mechanism) | `_on_message`'s `elif kind == "subprov":` branch | `_process_subprov_sig_durable` → `_handle_subprov_tx` |
| Live WS notification (PLAIN_TRANSFER, accountSubscribe) | `_on_message`'s new `accountNotification` / `kind == "subprov_account"` branch (X24.1) | `_handle_subprov_tx` directly |
| RPC sweep (Phase 2, this sprint) | `subprov_sweep_pass()` → `catch_up_subprov()` | `_process_subprov_sig_durable` → `_handle_subprov_tx` |
| Retry queue | `subprov_retry_pass()` | `_process_subprov_sig_durable` → `_handle_subprov_tx` |
| HOT burst fallback | `_hot_subprov_burst` | `_process_subprov_sig_durable` → `_handle_subprov_tx` |

**Every path calls the same, single, unmodified `_handle_subprov_tx()` function** — there is no second detection engine. `_handle_subprov_tx` itself was not touched by Phase 2's changes (confirmed via `git diff`, no lines inside its body appear in this sprint's diff). It, in turn, calls `store.open_candidate_watch()` / `add_candidates()` — also unmodified.

Proven by test (`tests/test_x24_1_detection_path.py`):
- `test_plain_transfer_funded_launch_reaches_record_launch_and_persists` — proves the PLAIN_TRANSFER path reaches the real `record_launch()` and persists identically to any other mechanism.
- `test_wrap_close_funded_launch_unchanged_behaviour` — regression guard confirming WSOL_WRAP_CLOSE's persisted shape is unchanged.

## Claim 3: idempotency and cleanup semantics are unchanged

- **Idempotency**: `record_launch()` is `INSERT OR IGNORE` on `(creator_wallet, create_signature)` — unchanged, not touched by this sprint. Proven by `test_duplicate_create_signature_is_idempotent` (X24.1 suite): calling `record_launch` twice with identical args returns `True` then `False`, with exactly one row persisted.
- **Cleanup (unsubscribe)**: `SubscriptionManager.unsubscribe()` sends `accountUnsubscribe` for `kind in ("treasury", "subprov_account")` and `logsUnsubscribe` otherwise — a real bug fix delivered in X24.1 (the old code always sent `logsUnsubscribe` regardless of subscription method, silently leaking the server-side subscription for the treasury tier too). Unchanged by this sprint. Proven by `test_unsubscribe_sends_accountunsubscribe_for_account_based_kinds`, `test_unsubscribe_sends_logsunsubscribe_for_logs_based_kinds`, `test_subprov_account_unsubscribe_uses_accountunsubscribe`.
- **New in Phase 2 — `mark_swept()` idempotency**: repeated calls accumulate `sweep_count` correctly and never regress `first_swept_at`, proven by `test_mark_swept_is_idempotent_bookkeeping` (X24.2 suite).

## Conclusion

No regression to X24.1's mechanism-aware behaviour was introduced by X24.2's Phase 1/2/5 work. This is confirmed by (a) `git diff` showing zero overlap between the functions Phase 2 modified (`subprov_sweep_pass`, plus new functions `fair_sweep_candidates`/`mark_swept`/`sweep_coverage_snapshot`) and the functions X24.1's mechanism logic lives in (`subscribe`, `unsubscribe`, `_handle_subprov_tx`, `record_launch`), and (b) the full X24.1 test suite (12 tests) still passing unchanged.
