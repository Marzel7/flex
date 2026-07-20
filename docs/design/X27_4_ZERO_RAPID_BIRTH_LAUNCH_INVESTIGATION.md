# X27.4 Follow-up — Why Rapid Birth→Launch Shows 0 Today

**Investigation only. No code, schema, or data changes were made.**

## Retraction

My earlier statement that "Rapid Birth→Launch legitimately shows 0
today" was **not adequately verified** — I confirmed the archetype's own
logic was correct (no inference, honest coverage reporting) but did not
check whether the *evidence source itself* was still being populated for
current launches. It was not. The correct, fuller statement is below.

## Phase A — Complete lifecycle trace (live CREATE → Behaviour Queue)

```
1. Pump.fun CREATE tx observed on WS      → src/core/ws_cascade.py (WSS listener)
2. Funding-chain classification            → _classify_recipient(), _handle_cdc_tx()
3. Candidate armed for WS watch            → wt_candidate_websocket_watches (state=WATCHING)
4. CREATE instruction matched to candidate → candidate closed, state=FIRED_CREATE
5. Authoritative launch record written     → src/core/ws_cascade_store.py::record_launch()
                                              -> wt_watchtower_launches
                                              (create_time = tx.blockTime,
                                               birth_to_launch_seconds computed
                                               from wrap_close_time)
6. Behaviour Queue reads this table        → src/ops/behaviour_queue.py::rapid_birth_launch_lookup()
```

`create_time` and `birth_to_launch_seconds` are written in exactly one
place: `record_launch()` (`ws_cascade_store.py:2148-2195`), called from
`ws_cascade.py:3720` with `create_time=btime` (the CREATE transaction's
own `blockTime`, fetched via RPC) and `birth_to_launch_s=btl` (`btime -
birth_time`, where `birth_time` is the wrap-close funding event's own
`blockTime` — `ws_cascade.py:3699-3717`). Both fields are only ever
populated together, at the moment a watched candidate's CREATE is
detected and confirmed.

## Phase B — Is this table still being written to?

**No — not in the last 53 hours.**

```sql
SELECT MAX(recorded_at) FROM wt_watchtower_launches;
-- 1784048671  =>  2026-07-14 17:04:31 UTC

-- now (investigation time): 2026-07-16 22:54:18 UTC  (~53.8 hours later)
```

```sql
SELECT COUNT(*) FROM wt_watchtower_launches WHERE recorded_at >= (now - 86400);
-- 0
```

```sql
SELECT MAX(closed_at) FROM wt_candidate_websocket_watches WHERE state='FIRED_CREATE';
-- 1784048671  (same timestamp — same underlying event)

SELECT COUNT(*) FROM wt_candidate_websocket_watches WHERE state='FIRED_CREATE';
-- 39 total, out of 3,052,865 candidate watches ever created (≈0.0013%)
```

## Phase C — Is the pipeline itself running?

**Yes, the process is alive**, but showing clear signs of degradation:

- `ws_cascade --loop` process confirmed running (PID active, accumulating CPU).
- 31 new candidate watches were armed in the last 24h (most recent at
  2026-07-16 13:36:58) — **all 31 expired via TTL timeout without ever
  reaching CREATE** (`close_reason='ttl'` for all 31; `watch_mode='LIVE'`
  for all 31 — these were genuine live watches, not `INTEL_ONLY`).
- 347 sessions were `LIVE_ARMED` in the last 24h (i.e., WS-subscribed),
  vs. 7,746 `INTEL_ONLY` — the `INTEL_ONLY` majority is confirmed
  **by-design** resource conservation (`ws_cascade.py:2879-2903`: only
  `NEW_SUBPROV` or a proven continuing/reactivated subprov with real
  wrap-close/seeded-account history gets `LIVE_ARMED`; ambiguous
  recipients are deliberately not WS-subscribed to conserve RPC/WS
  budget). This ratio alone is not evidence of a fault.
- Log evidence of genuine degradation, independent of the above:
  - `⏭ sweep cycle skipped — previous cycle still running
    (skipped_overlap_count=1704)` — the process cannot keep pace with
    its own sweep cadence.
  - `⚠ dropped cold pending subscription ... (unconfirmed >10.0s)` —
    1,811 occurrences in the visible log, but concentrated on only 2
    unique wallets (`EF11p7bnxFZMCk…`, `5JW1HStyNushon…`), repeatedly
    retried and dropped — a stuck retry loop on two specific
    subscriptions, not a full WS outage.
  - `[RPC_DEADLINE] late-result cache evicted ... (max_entries=1000
    exceeded)` and `[DB_CONNECT_SLOW]`/`[DB_COMMIT_SLOW]` entries
    (1-3+ second connect times) recurring throughout — consistent with
    the `subprov_sig_gap_detected` warnings already visible in this
    session's routine monitor notifications, which I had been treating
    as background noise without verifying their actual impact.

## Phase D — Population funnel (exact counts)

| Stage | Count |
|---|---|
| 1. Launches migrated in last 24h (`token_analysis.migrated_at`) | **526** |
| 2. `wt_watchtower_launches` rows *recorded* in last 24h | **0** |
| 3. `wt_watchtower_launches` rows with `create_time` (all-time) | 42 |
| 4. `wt_watchtower_launches` rows with `birth_to_launch_seconds` (all-time) | 41 |
| 5. Considered by `rapid_birth_launch_lookup()` (all-time) | 41 |
| Intersection: today's 526 migrated mints ∩ the 41-row lookup set | **0** |

## Phase E — Example trace: do today's actual creators appear anywhere in the cascade?

Three of today's real creators were checked directly against every
cascade-side table:

```
F5XvCe4233m6mHRbkkq2ZsFvqrPAnRrDBExeQy2fwagQ  candidate_watches=0  sessions_as_subprov=0  wrap_close_as_creator=0
7vy1LbBLqJ8rBHqXN3gyhoNG6Hgf9JMPwPco5uDarDgY  candidate_watches=0  sessions_as_subprov=0  wrap_close_as_creator=0
HgU6tMjL8r3paML6AytLdAQtA6JJHYmovvV8DnX5d4bR  candidate_watches=0  sessions_as_subprov=0  wrap_close_as_creator=0
```

None of today's checked creators were ever seen by the cascade at any
stage — not armed, not sessioned, not wrap-close-matched. This is
consistent with WATCHTOWER's known extremely low base rate (39
`FIRED_CREATE` out of 3.05M candidate watches, ever — ≈0.0013%), so
absence for these 3 specific creators is not surprising on its own.

## Phase F — Why is the intersection zero: design limitation or regression?

**Both, layered, and this must be stated plainly rather than picking one:**

1. **Base-rate limitation (expected, by design)**: WATCHTOWER-pattern
   launches are and always have been extremely rare relative to total
   launch volume (0.0013% historical `FIRED_CREATE` rate). Even a fully
   healthy pipeline would very plausibly produce 0 matches in many
   individual 24-hour windows purely from this rarity. X27.4's original
   framing ("high precision, honest partial coverage") remains correct
   as a *design* description.

2. **Genuine pipeline degradation (not expected, needs attention)**:
   independent of the base-rate question, the *complete absence of any
   write* to `wt_watchtower_launches` for 53+ hours — combined with
   1,704 skipped overlapping sweep cycles, repeated stuck subscription
   drops, and RPC-deadline cache churn — indicates the live cascade is
   running in a degraded state. **I cannot conclusively separate "0
   matches because the pattern is rare" from "0 matches because the
   detection pipeline is too degraded to catch a real match were one to
   occur"** without either (a) a longer historical baseline of typical
   `FIRED_CREATE` inter-arrival time to compare the current 53-hour gap
   against, or (b) direct engineering triage of the sweep-overlap and
   subscription-drop symptoms. Neither was performed in this
   investigation (out of scope for a read-only audit).

## Phase G — Is the Behaviour Queue measuring live behaviour or a historical corpus?

**The Behaviour Queue's Rapid Birth→Launch archetype is currently
measuring only a historically-accumulated corpus (41 rows, most recent
2026-07-14), not any behaviour occurring in the live 24-hour window.**
`rapid_birth_launch_lookup()` and `build_behaviour_queue()` themselves
are functioning exactly as designed — they correctly report 0 matches
and correctly disclose 7.8%(-ish) coverage rather than inferring a
result — but the *evidence source* they depend on has not received a new
row in over two days, so "coverage today" is effectively 0%, not the
~8% the panel currently displays (that percentage is computed against
the table's all-time size, not scoped to the 24h window — a
presentation nuance worth revisiting, noted below).

## Recommendation (not implemented — this is an investigation)

1. **Immediate**: flag the `ws_cascade` process for engineering triage —
   specifically the 1,704 skipped-overlap sweep cycles and the two
   repeatedly-dropped subscriptions — independent of anything to do with
   X27.4's Behaviour Queue itself.
2. **Behaviour Queue presentation fix (future, separately scoped)**: the
   `coverage_pct` for `RAPID_BIRTH_LAUNCH` should ideally be computed
   against evidence *freshness within the queried window*, not just
   the lookup table's all-time row count vs. today's launch count — so a
   stalled writer is visible in the panel itself (e.g. "0 launches with
   canonical timing recorded in the last 24h" rather than a coverage
   percentage that silently conflates "rare" with "stopped").
3. Re-run this same funnel check after the `ws_cascade` process's health
   is restored, to establish whether Rapid Birth→Launch begins matching
   again once fresh candidate-watches resume completing.

## Confirmation

No code, schema, or data was changed in this investigation. This is a
diagnostic report only.
