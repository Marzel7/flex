# X27.7 — Restore WATCHTOWER Lifecycle Capture

## Objective

Determine exactly why `ws_cascade.py` stopped writing new rows to
`wt_watchtower_launches` and restore reliable lifecycle capture for live
launches, without changing detection thresholds, classification semantics,
candidate qualification, X27.5 bucket priority, DB schema, or idempotency
guarantees.

## Root cause (confirmed live, not inferred)

Live detection was **starved, not dead**. Three independent pieces of live
evidence converge on the same defect:

1. `GET /api/ops-v2/intel/detection-path-health` (a pre-existing endpoint,
   30-day window):
   ```
   primary_live_path: 0
   catch_up_path:     26
   retry_recovery_path: 3
   other_unclassified:  5
   total_live_detected_launches: 34
   ```
   Every launch WATCHTOWER detected in the last 30 days came from the sweep
   backstop (`catch_up_path`/`retry_recovery_path`), never the intended live
   WS notification for the subprov itself.

2. `GET /api/ops-v2/intel/ws-cascade` (live):
   ```
   sub_avg_ack_ms:    11660.5
   sub_p0_avg_ack_ms: 32829.4
   ```
   Helius subscription-acknowledgment latency has drifted to **11–33
   seconds** — far above what the code assumed.

3. `SubscriptionManager.sweep_stale_pending()`
   (`src/core/ws_cascade.py`, pre-fix) unconditionally dropped any COLD
   (non-`hot_subprov`) pending subscribe request once it exceeded
   `COLD_SUB_STALE_SEC` (**10 seconds**), popping the wallet from
   `wallet_kind`/`pending_req` with **no retry** — unlike `hot_subprov`,
   which already had an immediate resubscribe path
   (`ws_cascade.py::cleanup_pass`, the `stale_hot` branch).

**The chain**: Helius now typically takes 11–33s to ack a `logsSubscribe` for
a subprov wallet. The code's own stale-timeout (10s) fires first, so
`sweep_stale_pending()` drops the subscription request *before Helius's ack
ever arrives* — the subprov ends up completely unsubscribed, and because cold
drops had no retry, it never gets resubscribed until the next full
`resync_subscriptions()` reconnect or the slow `subprov_sweep_pass` rediscovers
it via `active_sessions()`. Confirmed via 393 "dropped cold pending
subscription" log lines and a live sweep-cycle snapshot showing
`never_swept` growing (103→140→179) while cycles take 110–380 seconds each.

This is why `wt_candidate_websocket_watches` still received a low trickle of
writes (4/24h) even while `wt_watchtower_launches` had gone stale since
2026-07-14: the sweep backstop was still finding *some* wrap-closes via slow
RPC polling, but the live WS path — the one capable of sub-second detection —
had not fired successfully in 30 days.

## What was ruled out

- **Not a dead asyncio task.** `ProgramCreateWatcher`'s three background loops
  (`_expire_loop`, `_persist_loop`, `_pending_create_fetch_loop`) and the WS
  reader/processor/maintenance split are all defensively wrapped in
  `try/except` and self-heal or trigger a full reconnect on failure — none of
  them can silently die from an unhandled exception.
- **Not a process crash.** `ws_cascade --loop` (PID 93017) was alive and
  CPU-active throughout the investigation; other subsystems (RPC_DEADLINE
  cache, CDC outbound, sweep cycles) kept logging normally the entire time.
- **Not `wt_wrap_close_candidates` being structurally broken** — that table's
  3-week staleness (since 2026-06-23) was a red herring from an earlier,
  narrower investigation; the actual write path
  (`wt_candidate_websocket_watches` via `open_candidate_watch()`) was
  confirmed still functional, just fed almost entirely by the slow sweep
  instead of live WS.
- **Not `resync_subscriptions()`'s reload-on-reconnect logic** — confirmed
  correct given its inputs; it had nothing new to reload because nothing new
  was being armed live.
- **Not `armed_mode.txt` / `SAVE_CANDIDATE_FANOUT`** — confirmed `"1"` since
  2026-07-03, predating the outage.

## Fix (minimal, `src/core/ws_cascade.py`)

1. `COLD_SUB_STALE_SEC`: `10` → `45` (env-tunable via
   `WS_COLD_SUB_STALE_SEC`), comfortably above the measured 32.8s p0 average
   ack latency, with headroom.
2. New `COLD_SUB_RETRY_MAX` (default `3`, env-tunable via
   `WS_COLD_SUB_RETRY_MAX`).
3. `SubscriptionManager.sweep_stale_pending()` now returns
   `(wallet, kind, attempts_so_far)` for cold drops instead of a bare wallet
   list, so callers know how many retries have already been spent.
4. `Cascade.cleanup_pass()` resubscribes a cold-dropped wallet immediately if
   it hasn't exhausted `COLD_SUB_RETRY_MAX` attempts (mirroring the existing
   `hot_subprov` retry at the same call site); once exhausted, the wallet is
   abandoned (no infinite retry against a wallet Helius will never ack) and
   `_COLD_RETRY_EXHAUSTED_COUNT` is incremented.
5. `SubscriptionManager.on_subscribe_confirmed()` clears the wallet's retry
   counter on success, so a wallet that flakes once and later recovers
   doesn't carry a stale count into its next legitimate stall.

No detection thresholds, classification logic, candidate qualification
criteria, X27.5 bucket priority, or database schema were touched.
`hot_subprov`'s existing 2-second stale/retry behavior is unchanged (see
regression test `test_hot_subprov_retry_behaviour_unchanged`).

## Health metrics added (`src/core/ws_cascade.py::_meta`, surfaced via the
existing `/api/ops-v2/intel/ws-cascade` heartbeat — no second dashboard)

- `subprov_ws_sig_seen` — lifetime count of live `kind=="subprov"` WS
  notifications actually dispatched to `_process_subprov_sig_durable`. If
  this stays flat while `wt_candidate_websocket_watches` keeps growing, live
  dispatch is starved again even though the process looks healthy — this is
  the exact signal that was missing during this investigation and had to be
  reconstructed by hand from log line-counts.
- `cold_sub_stale_sec` — the currently effective threshold (so a future
  investigation doesn't have to re-read source to find it).
- `cold_retry_active` — live count of wallets currently mid-retry.
- `cold_retry_exhausted` — lifetime count of wallets that exhausted all
  retries and were abandoned; a sustained climb here means Helius is
  consistently failing to ack certain wallets, not just running slow.

## Tests (`tests/test_x27_7_restore_lifecycle_capture.py`)

7 tests, all against `SubscriptionManager` directly (fake WS, no network, no
live daemon):

- `COLD_SUB_STALE_SEC` stays ≥45s (regression guard against reintroducing the
  race with observed Helius ack latency).
- `sweep_stale_pending()` returns `(wallet, kind, attempts)` for cold drops.
- A cold drop within retry budget gets resubscribed (the core fix).
- A cold drop that exhausts `COLD_SUB_RETRY_MAX` is abandoned, not retried
  forever.
- Confirmation clears the retry counter.
- `hot_subprov`'s existing 2s stale/retry behavior is unchanged.

Full regression: 256 passed (all `ws_cascade`/`x24`/`x27` suites), 0
failures. 3 pre-existing, unrelated collection errors
(`test_helius_analysis.py`, `test_pumpswap_detection.py`,
`test_pumpswap_phase2.py` — all fail on `ModuleNotFoundError: No module named
'main'`, unrelated to this change) were excluded and confirmed pre-existing.

## Deployment and live validation

Baseline captured before restart (2026-07-17 ~14:xx UTC):
```
primary_live_path: 0           (30-day window)
sub_avg_ack_ms:    11660.5
sub_p0_avg_ack_ms: 32829.4
last wt_watchtower_launches write: 1784048671 (2026-07-14 17:04:31 UTC)
```

`ws_cascade` restarted under supervisord (old PID 93017 → new PID 46371) to
load the fix. Also found and fixed in the same pass: the new metrics weren't
reaching `/api/ops-v2/intel/ws-cascade` because that route filters
`meta_json` through an explicit field allowlist that predated this sprint —
added the four new keys to it and reloaded gunicorn (`SIGHUP` to the master).

### Phase 11A — Runtime validation: COMPLETE

Observed directly post-restart, single connect cycle (`reconnect_gen=1`, no
reconnect churn):
```
subs_sent_total: 95   subs_conf_total: 67   sub_ack_count: 67
sub_avg_ack_ms:  9930.6   sub_p95_ack_ms: 31661.1   sub_max_ack_ms: 31831.8
```
67 of 95 subscriptions sent (treasuries + dust markers + promoted subprovs,
the full `resync_subscriptions()` startup replay) confirmed successfully,
with p95 ack latency (31.7s) comfortably inside the new 45s window that
previously would have raced every one of them out at the old 10s threshold.
This directly confirms the fix addresses the measured defect for the bulk of
subscription traffic.

The retry-on-drop path was also observed firing correctly and exactly as
designed: two wallets each received 3 resubscribe attempts at the expected
~45s spacing, then were cleanly abandoned (`cold_retry_exhausted`
incrementing, `cold_retry_active` returning to 0 per wallet) — no infinite
retry loop, no silent hang.

**Secondary finding (out of scope for this fix, investigated separately in
X24.8 — see `docs/design/X24_8_NONACK_STANDING_WATCHLIST_INVESTIGATION.md`):**
the two wallets that hit the retry path and never acked at all
(`5JW1HStyNushon…`, `EF11p7bnxFZMCk…`) are not session-tier subprovs — they
have no `wt_active_subprov_sessions` row. **Correction:** they were
provisionally attributed here to `WS_PROMOTE_DISCOVERED`/
`_promotable_subprovs()`; X24.8 found this was wrong — neither wallet
appears in `wt_wrap_close_candidates` or `wt_discovered_subprovs` at all.
They are `wt_dust_markers` (`kind="dust"`, P5 tier) entries that decode to
**33-byte malformed pubkeys** (one byte too long) instead of the valid
32-byte length every other dust marker and every treasury/CDC wallet
decodes to — Helius accepts the subscribe request but can never emit a
matching notification for an invalid pubkey, hence permanent, deterministic
non-ack. This is a data-entry defect in `dust_observatory.py`'s
`SEED_DUST_WALLETS`, not a WS_PROMOTE_DISCOVERED/staleness issue, and not
related to the ack-latency race this sprint fixes.

### Phase 11B — End-to-end production validation: PENDING

No treasury has funded a new subprov since the restart (`active_subprovs: 0`,
`sessions: []`, `subprov_ws_sig_seen: 0` as of last check), so the full chain
(cold subscribe → ack → candidate armed → live CREATE observed →
`process_candidate_sig` → `record_launch` → new `wt_watchtower_launches` row
via `PROGRAM_LOGS`) has not yet been exercised by a genuine organic event.
This depends on real on-chain treasury-funding timing, which cannot be
scheduled or forced. **No row was manually inserted and none will be** —
this criterion will only be marked complete when a real launch is observed
flowing through end-to-end. Monitoring continues; this section will be
appended with the confirming evidence (mint, creator, subprov,
`detection_source=PROGRAM_LOGS`, `recorded_at`) the first time it occurs,
rather than reopening the investigation.

## Phase 12 — Investigation Queue re-verification post-fix

Checked live against current production data (24h window) after the fix and
restart, to confirm X27.2/X27.5's guarantees are untouched:

```
GET /api/ops-v2/investigation-pipeline?window=24h
conserved: true   total_launches: 585
Known Operation           0
Known Infrastructure     59
Repeat Creators         132
Rapid Birth → Launch      0   (unchanged — expected; see Phase 11B)
Burst Launches           89
Unknown Infrastructure   53
Lineage Gap              50
Insufficient Evidence   202
sum: 585
```

Independently re-ran X27.5's own drill-down overlap check
(`launches_in_bucket()` across all 8 `BUCKET_ORDER` entries): zero
cross-bucket overlap, `len(seen) == total_launches` exact match. Bucket
priority order, labels, and reasons are byte-for-byte unchanged from X27.5.
`RAPID_BIRTH_LAUNCH` remaining at 0 is expected and consistent — it will only
become non-zero once Phase 11B's first genuine live-detected launch lands in
`wt_watchtower_launches`, which is what this sprint restores the capability
for, not something this sprint fabricates.

## Confirmation

No thresholds, detection logic, priority order, attribution outcomes,
walkback logic, candidate qualification, or database schema were changed.
`hot_subprov` behavior is provably unchanged (dedicated regression test). No
manual `wt_watchtower_launches` row was inserted. No second dashboard was
created — new metrics extend the existing `/api/ops-v2/intel/ws-cascade`
heartbeat payload.
