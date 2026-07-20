# X24.9 — Subscription Target Validation & Watchlist Integrity

## Objective

Prevent malformed wallet addresses from ever entering the websocket
subscription pipeline. X24.8 confirmed the two permanently non-acknowledging
subscriptions were invalid 33-byte pubkeys, not a websocket, provider, or
retry defect. This sprint hardens the boundary so that class of bug is
rejected immediately, everywhere, rather than discovered later by hand.

## Phase 1 — Canonical pubkey validator

`src/utils/pubkey_validation.py`:

- `is_valid_pubkey(wallet) -> bool` — `True` iff the string base58-decodes to
  exactly 32 bytes. Deterministic, no I/O, no network.
- `invalid_reason(wallet) -> str | None` — a machine-readable reason code
  (`EMPTY_OR_NOT_STRING` / `BASE58_DECODE_ERROR` / `WRONG_LENGTH_N_BYTES`) for
  audit/health-metric reporting; not used for control flow.

One implementation, imported everywhere a subscription target is validated —
no duplicated length/decode logic anywhere else in the codebase.

## Phase 2 — Subscription-source inventory

Every websocket-subscription source funnels through exactly two functions in
`src/core/ws_cascade.py`, confirmed by direct code search (`grep` found a
single `def` for each, and 14 call sites total, all calling one of these
two):

| Source | Table | Reaches subscription via |
|---|---|---|
| Treasury | `wt_confirmed_treasuries` | `SubscriptionManager.subscribe(kind="treasury")` |
| Session subprov | `wt_active_subprov_sessions` | `SubscriptionManager.subscribe(kind="subprov"/"subprov_account"/"hot_subprov")` |
| Promoted subprov (`WS_PROMOTE_DISCOVERED`) | `wt_discovered_subprovs` (via `_promotable_subprovs()`) | `SubscriptionManager.subscribe(kind="subprov")` |
| Dust marker | `wt_dust_markers` | `SubscriptionManager.subscribe(kind="dust")` |
| CDC | `wt_capital_distributor_candidates` | `SubscriptionManager.subscribe(kind="cdc")` |
| Wrap-close candidate (creator) | decoded on-chain `closeAccount.destination` | `ProgramCreateWatcher.add_candidates()` |

Before this sprint, **none** of these six sources performed any pubkey
validation, and duplicate handling varied only by incidental `SELECT
DISTINCT`/set usage — there was no invalid-address handling anywhere.

Because every WS-subscription source converges on
`SubscriptionManager.subscribe()`, and every candidate-arming source
converges on `ProgramCreateWatcher.add_candidates()`, validating at those two
functions covers all six sources with zero duplicated logic — this is the
approach Phase 4 implements.

## Phase 3 — Startup integrity audit

`Cascade.__init__` (`ws_cascade.py`) now runs
`src.ops.subscription_target_audit.startup_validation_summary()` once at
process start, immediately after schema-ensure, read-only:

```
[X24.9] startup validation — valid=62498 invalid=3 duplicates=68601
disabled=130230 by_source={'treasury': 0, 'session_subprov': 0,
'promoted_subprov': 0, 'dust': 3, 'cdc': 0}
```

The result is stored on the `Cascade` instance (`self._startup_validation`)
and surfaced through the existing heartbeat (Phase 5) — nothing is silently
dropped; every invalid/duplicate/disabled row is counted and attributed to
its source table.

## Phase 4 — Runtime protection

`SubscriptionManager.subscribe()` (`ws_cascade.py`) now rejects an invalid
wallet as the very first check, before `pending_req`, `wallet_kind`,
`_subs_sent_total`, or any retry-count state is touched, and before any
`ws.send()` call:

```python
if not is_valid_pubkey(wallet):
    self._invalid_rejected_total += 1
    self._invalid_rejected_by_kind[kind] = self._invalid_rejected_by_kind.get(kind, 0) + 1
    _log(f"⛔ REJECTED invalid subscription target kind={kind} "
         f"reason={invalid_reason(wallet)} wallet={str(wallet)[:20]}…")
    return
```

`ProgramCreateWatcher.add_candidates()` has the identical guard for the
wrap-close-candidate population, for defense-in-depth — this population is
sourced from live on-chain transaction decoding rather than a hand-maintained
list, so it is structurally far less likely to be malformed, but the brief's
scope explicitly covers "any standing watchlists."

An invalid wallet, by construction:
- never enters `pending_req` (nothing to time out or retry)
- never appears in `wallet_kind` (no duplicate-subscribe confusion)
- never reaches `ws.send()` (Helius never sees it)
- never enters `sweep_stale_pending()`'s retry/exhaustion cycle
- is excluded from `_subs_sent_total`/`_sent_by_kind` (X24.8's per-kind
  breakdown stays a clean signal of genuine subscription health, not noise
  from unsubscribable addresses)

## Phase 5 — Health metrics (existing heartbeat, no second dashboard)

Added to `_meta()` / `/api/ops-v2/intel/ws-cascade`:

- `invalid_subscription_targets` — lifetime runtime-rejected count
  (`SubscriptionManager`).
- `invalid_targets_by_source` — the same, broken down by `kind`.
- `startup_validation_failures` — total invalid rows found at the Phase 3
  startup audit.
- `startup_validation_by_source` — the same, broken down by source.
- `runtime_validation_failures` — combined `SubscriptionManager` +
  `ProgramCreateWatcher` runtime rejections.

## Phase 6 — Data audit (live, read-only, 2026-07-17)

```
total_valid:      62498
total_invalid:    3
total_duplicates: 68601
total_disabled:   130230

invalid_by_source: {'treasury': 0, 'session_subprov': 0,
                     'promoted_subprov': 0, 'dust': 3, 'cdc': 0}
```

| Source | Table | Rows | Valid | Invalid | Duplicates | Disabled |
|---|---|---|---|---|---|---|
| treasury | `wt_confirmed_treasuries` | 58 | 58 | 0 | 0 | 0 |
| session_subprov | `wt_active_subprov_sessions` | 129,433 | 60,832 | 0 | 68,601 | 129,232 |
| promoted_subprov | `wt_discovered_subprovs` | 1,295 | 1,295 | 0 | 0 | 693 |
| dust | `wt_dust_markers` | 11 | 8 | **3** | 0 | 1 |
| cdc | `wt_capital_distributor_candidates` | 305 | 305 | 0 | 0 | 304 |

**Every invalid wallet in the entire system is in `wt_dust_markers`**, and
they are exactly the three already identified across this and the prior
sprint:

```
EF11p7bnxFZMCktB73bBp55MnoLHFHMzxiTv5GhEFggq3   WRONG_LENGTH_33_BYTES  (active — X24.8)
5JW1HStyNushonoqZ6ceooMuWgqFpmhQpE49U5ZxifXBi   WRONG_LENGTH_33_BYTES  (active — X24.8)
G212UVo8uwfcAKvU2eb4TtWpa1cJzE3DJQRP1fqf2ewPZ   WRONG_LENGTH_33_BYTES  (already active=0 — an earlier session's partial fix)
```

`session_subprov`'s large duplicate/disabled counts are expected and benign
— that table accumulates one row per funding event over the system's
lifetime (129,433 historical sessions), not one row per distinct wallet;
`state != 'ACTIVE'` correctly marks the ~129K closed/expired historical
sessions as "disabled" for this audit's purposes. No production data was
modified to produce this table.

## Phase 7 — Remediation recommendations (not applied)

```json
[
  {
    "source": "dust", "table": "wt_dust_markers",
    "wallet": "EF11p7bnxFZMCktB73bBp55MnoLHFHMzxiTv5GhEFggq3",
    "reason": "WRONG_LENGTH_33_BYTES", "occurrences": 1,
    "recommendation": "Disable row (set active=0 / equivalent) or correct the address; malformed pubkeys can never receive a websocket acknowledgement and will retry/exhaust indefinitely if left active."
  },
  {
    "source": "dust", "table": "wt_dust_markers",
    "wallet": "G212UVo8uwfcAKvU2eb4TtWpa1cJzE3DJQRP1fqf2ewPZ",
    "reason": "WRONG_LENGTH_33_BYTES", "occurrences": 1,
    "recommendation": "Disable row ... [already active=0 — recommendation is informational only, no action needed]"
  },
  {
    "source": "dust", "table": "wt_dust_markers",
    "wallet": "5JW1HStyNushonoqZ6ceooMuWgqFpmhQpE49U5ZxifXBi",
    "reason": "WRONG_LENGTH_33_BYTES", "occurrences": 1,
    "recommendation": "Disable row (set active=0 / equivalent) or correct the address; malformed pubkeys can never receive a websocket acknowledgement and will retry/exhaust indefinitely if left active."
  }
]
```

`recommend_remediation()` (`src/ops/subscription_target_audit.py`) contains
no `UPDATE`/`DELETE`/`INSERT` — it only reads and returns suggestions. **No
production data was modified by this sprint.** Two concrete, actionable
options exist for the two still-active malformed rows, matching the pattern
already established for `G212…`:

1. **Disable**: set `wt_dust_markers.active = 0` for both wallets — same
   treatment as `G212…`, zero risk, immediately stops the perpetual
   retry/exhaust cycle observed in X24.8 (12+ full cycles).
2. **Correct**: obtain the true 32-byte address for the `EF11`/`5JW1` vanity
   companions and replace the stored value — restores the intended dust
   observation coverage for those two treasury companions, which disabling
   would permanently forgo.

Neither was applied this sprint; this is an explicit decision for whoever
owns `wt_dust_markers` data quality, not an engineering call this
investigation makes unilaterally.

## Phase 8 — Tests

`tests/test_x24_9_subscription_target_validation.py` (21 tests):

- Validator: valid pubkey accepted, 33-byte pubkey rejected, malformed
  base58 rejected, empty/`None` rejected, deterministic across repeated
  calls.
- Runtime protection: invalid wallet never enters `pending_req`, never
  reaches `ws.send()`, never consumes retry budget (`sweep_stale_pending()`
  sees nothing to retry), excluded from subscription counts, counted as
  rejected; `add_candidates()` rejects/accepts symmetrically; **valid**
  wallet subscription behavior is provably unchanged (same send, same
  `pending_req` shape, zero rejection count) — the explicit "no behavior
  change for valid subscriptions" requirement.
- Source audit: detects invalid pubkeys, duplicates, and disabled rows on an
  isolated in-memory schema (no dependency on production data); covers all
  five known sources; startup summary reports every source even when empty
  (proves no silent drop); remediation recommendations never mutate the
  database and are empty when all wallets are valid.

All 21 pass.

**Fixture fallout (expected, fixed):** the new runtime rejection gate broke
16 pre-existing tests across `test_x24_1_mechanism_aware_subscription.py`,
`test_x24_8_sub_kind_breakdown.py`, and `test_x27_7_restore_lifecycle_capture.py`
— all of them used human-readable placeholder strings
(`"TREASURY_WALLET_1"`, `"SUBPROV_1"`, `"HOT_1"`, etc.) as stand-in wallet
values, which correctly fail `is_valid_pubkey()`. Fixed by adding a small
`_pubkey(seed)` helper to each file (`base58(sha256(seed))`, 32 bytes,
deterministic) so fixtures stay self-documenting while satisfying the new
validator; assertions that checked the exact wallet string were updated to
reference the same derived value rather than the raw seed. No test's actual
assertions about behavior (RPC method selection, retry timing, per-kind
breakdown counts) were changed — only the wallet literals feeding them.
Full regression re-run clean: 281 passed (260 pre-existing + 21 new), 0
failures, across `ws_cascade`/`x24`/`x27` suites.

## What was not changed

No detection thresholds, classification logic, Investigation Queue priority,
attribution, treasury logic, or database schema were touched.
`COLD_SUB_STALE_SEC`/`COLD_SUB_RETRY_MAX` (X27.7) and the per-kind
sent/confirmed/exhausted breakdown (X24.8) are unmodified — this sprint adds
a rejection gate *before* those mechanisms would ever see an invalid wallet,
rather than changing how they handle one. No production data was mutated;
`wt_dust_markers` still contains all three malformed rows exactly as found.

## Success criteria — met

> Invalid subscription target → Rejected immediately → Never reaches
> websocket layer → Never enters retry → Never consumes runtime resources

Confirmed by direct test (`test_invalid_wallet_never_enters_pending`,
`test_invalid_wallet_never_sends_to_provider`,
`test_invalid_wallet_never_consumes_retry_budget`) and by the code path
itself: the validation check is the first statement in `subscribe()`, before
any state mutation.
