# X27.0 — Walkback Coverage Audit

Status: Investigation only, per scope. No detection, migration, walkback,
attribution, Discovery, operation identity, or schema logic changed.

**Headline finding**: the naive all-time coverage number (21.96%) is
severely misleading — it is dominated by migrations that predate the
walkback mechanism's existence. **Current live coverage is ~99%**, but a
real, unexplained multi-day coverage dip (10-72%) occurred 2026-07-09
through 2026-07-12 and has not been root-caused from available logs. A
genuine structural code-path gap exists (creator-known migrations are
never enqueued at all) but currently affects a vanishingly small fraction
of volume (13 of 17,734 migrated tokens all-time). The architectural
conclusion is **NO** — the invariant does not hold as a structural
guarantee — **but with a documented, currently-small practical impact**
and a clear operational risk (the walkback worker's `autostart=false`)
that could make the gap much larger if it ever recurs.

---

## Phase 1 — Complete pipeline trace

```
CREATE (pump.fun bonding curve)
  ↓
MigrateV2 detected (WS listener, src/core/pumpfun_curve_listener.py)
  ↓
handle_migration()
  ↓
_mark_token_migrated_in_db()  ── on failure: INSERT INTO migration_persist_queue, EARLY RETURN
  ↓ (success)                     (see Phase 3, gap #3 — no enqueue on this path even after later retry)
_check_watchtower_migration(mint, migrated_at, migration_tx, source)
  │  (also independently called from the "fast path pool register" handler, source='fast_path_register')
  ↓
  SELECT creator_wallet FROM wt_creator_launches WHERE mint_address=?
  │
  ├─ creator KNOWN (row exists, or resolved via wt_staged_wallets fallback)
  │     → UPDATE wt_creator_launches, INSERT watchtower_events, advance_lifecycle_migrated
  │     → enqueue_migration() is NEVER CALLED on this branch (Phase 3, gap #1)
  │
  └─ creator UNKNOWN
        → enqueue_migration(conn, mint=mint, creator=<earliest_tx_creator or None>)
              (wrapped in try/except that logs-and-swallows any exception)
        ↓
        classify_creator() → walkback_class (FULL_WALKBACK / LINK_ONLY / SKIP / etc.)
        ↓
        INSERT OR IGNORE INTO wt_walkback_queue (status='pending'|'complete'|'skipped', ...)
              ↓
        walkback_worker.py drain_batch(): SELECT ... WHERE status='pending' AND attempts<MAX_ATTEMPTS
              ↓
        classify / RPC walkback → mark_complete() → intelligence_outcome, status='complete'/'failed'
```

Every early-return identified:
- `_mark_token_migrated_in_db()` failure → falls back to `migration_persist_queue`, returns before `_check_watchtower_migration` is ever called for that attempt.
- `_check_watchtower_migration`'s creator-known branch → returns after updating `wt_creator_launches`/lifecycle, never reaching `enqueue_migration`.
- `enqueue_migration()`'s own idempotency guard → returns early (no-op, by design) if the mint is already queued.
- Both `enqueue_migration()` call sites wrap the call in `try/except` — one logs and continues, one (`watchtower_attribution.py`, dead code) is a bare `except: pass`.

## Phase 2 — Every enqueue path

**Exactly one function inserts into `wt_walkback_queue`**:
`enqueue_migration()` (`src/core/walkback_queue.py:303-346`).

Two call sites:
1. `src/core/pumpfun_curve_listener.py:816`, inside `_check_watchtower_migration`'s creator-unknown branch. Routed through `database_write_service.submit(...)` (an async dispatcher), not a direct synchronous write. Guard: `if not creator_wallet:` — only reached when creator is unresolved.
2. `src/core/watchtower_attribution.py:146-149`, inside `store_migration()`, called unconditionally. **Confirmed dead code** — `store_migration` has no callers anywhere in `src/` outside its own file. Not a live guarantee path.

**Migration is the only trigger** for enqueue — no separate reconciliation/backfill sweep independently inserts into `wt_walkback_queue`; the reconciler loops (`_migration_reconciler_loop`, `_reconcile_one`, `_birth_reconciler_loop`) all funnel back through `handle_migration`/`_check_watchtower_migration`, not a separate insert path.

**Duplicate handling**: `INSERT OR IGNORE` plus an idempotency pre-check (existing row short-circuits unless `force=True`) — safe against double-enqueue.

**Retry behaviour**: none specific to enqueue failures. `migration_persist_queue` exists only for `_mark_token_migrated_in_db` write failures, and its drain function (`drain_migration_persist_queue`) never re-triggers `_check_watchtower_migration`/`enqueue_migration` after a successful retry (Phase 3, gap #2) — a compounding gap on top of gap #3.

## Phase 3 — Every exclusion, with exact code references

| # | Exclusion | Location | Verified impact |
|---|---|---|---|
| 1 | Creator-known migrations never call `enqueue_migration` at all | `pumpfun_curve_listener.py:753-882`, the `if not creator_wallet:` branch (line 803) gates the only call | **Confirmed structurally real** but currently tiny: only 13 of 17,734 all-time migrated tokens have a `wt_creator_launches` row at all, and all 13 are among the missing set (100% miss rate for this narrow path, ~0.07% of total volume) |
| 2 | `drain_migration_persist_queue` (retry path) never re-triggers enqueue | `pumpfun_curve_listener.py:10805-10851` | Confirmed by code trace; a migration recovered via this queue gets `token_analysis` correctly marked migrated but never reaches `wt_walkback_queue` |
| 3 | `_mark_token_migrated_in_db` failure → early return before `_check_watchtower_migration` | `pumpfun_curve_listener.py:2053-2061` | Combines with #2 — the retry path that recovers from this failure doesn't itself call enqueue |
| 4 | Exception swallowing around both `enqueue_migration` call sites | `pumpfun_curve_listener.py:828-829` (logged), `watchtower_attribution.py:145-149` (bare `except: pass`, dead code) | No dead-letter/retry queue specific to enqueue failures exists |
| 5 | `walkback_worker` supervisor `autostart=false` | `config/supervisor/supervisord.conf:250`, comment: "start explicitly after confirming queue health" | **Operational risk, not currently active** — confirmed via `ps aux` the worker is currently running (pid 11183); enqueue is unaffected by this flag (rows still land in the queue), but *processing* ("a walkback attempt") would silently stop across any restart/reboot unless manually restarted |
| 6 | `LISTENER_MIGRATION_RECONCILER_ENABLED=0` would disable the reconciler sweep that recovers dropped WS migration events | `pumpfun_curve_listener.py:1102-1106` | Currently defaults to `"1"` (enabled); if a migration event is never detected at all (WS drop + reconciler disabled), it can never reach any part of this pipeline |
| 7 | `classify_creator()`'s `SKIP` classification | `walkback_queue.py` | **Not actually a missing-row case** — still inserts a row with `status='skipped'`, so it counts correctly in the coverage measurement; only excluded from RPC processing, not from the table |

No feature flag gates `enqueue_migration` itself directly — confirmed via explicit search, none found.

## Phase 4 — Live database reconciliation

Comparison population: `pumpfun_migration_verification` (core DB) — confirmed to be the correct canonical migrated-token population (every one of 17,734 rows has both `migrated_at` and `migration_tx` populated; `mint` is the table's `PRIMARY KEY`, so no duplicates).

| Metric | Value |
|---|---|
| Migrated tokens total (all-time) | 17,734 |
| `wt_walkback_queue` distinct mints | 3,905 |
| Overlap | 3,895 |
| **All-time coverage %** | **21.96%** |
| Missing count (all-time) | 13,839 |
| Walkback rows for mints not in the migrated population | 10 (likely test/manual-enqueue rows, not investigated further as out of scope) |

**This all-time number is not representative** — see Phase 5.

## Phase 5 — Historical analysis (not speculative — measured)

**Split by walkback-mechanism maturity**: the earliest migration with a
`wt_walkback_queue` row is `2026-06-02 06:23`. Of the 13,839 all-time
missing mints:
- **6,473 predate 2026-06-02** — genuinely expected historical exceptions;
  the walkback mechanism did not exist yet for these migrations.
- **7,366 fall at or after 2026-06-02** — within the period walkback was
  already operating, so these are not explained by "feature didn't exist
  yet."

**Weekly coverage trend since 2026-06-02** (measured, not estimated):

| Week | Total migrations | Covered | Coverage % |
|---|---|---|---|
| 2026-W22 | 829 | 19 | 2.3% |
| 2026-W23 | 955 | 32 | 3.4% |
| 2026-W24 | 727 | 0 | 0.0% |
| 2026-W25 | 1,148 | 0 | 0.0% |
| 2026-W26 | 2,786 | 401 | 14.4% |
| 2026-W27 | 3,227 | 2,096 | 65.0% |
| 2026-W28 | 1,589 | 1,347 | 84.8% |

**Daily breakdown, most recent 7 days** (measured directly against `now`):

| Day | Total | Covered | Coverage % |
|---|---|---|---|
| 07-15 (day-0) | 502 | 497 | 99.0% |
| 07-14 (day-1) | 499 | 497 | 99.6% |
| 07-13 (day-2) | 269 | 268 | 99.6% |
| 07-12 (day-3) | 433 | 100 | 23.1% |
| 07-11 (day-4) | 364 | 38 | 10.4% |
| 07-10 (day-5) | 455 | 274 | 60.2% |
| 07-09 (day-6) | 443 | 319 | 72.0% |

**A sharp, real discontinuity exists around 2026-07-13**, not a gradual
lag artifact — re-checked directly: of the 798 migrations in the
day-3/day-4 window, **659 are still missing as of right now** (days
later), and the migrations that *did* get enqueued in that window did so
with a median lag of essentially 0 seconds (min -67s, median -1s, max
144s) — meaning enqueue, when it happened, was near-instant. This rules
out "still catching up" as an explanation for the low-coverage days; those
migrations were durably skipped, not delayed.

**Root cause of the day-3/day-4 dip: not conclusively determined.**
Checked `git log` for the relevant files in that window — only one commit
touches `pumpfun_curve_listener.py`/`walkback_queue.py`/
`watchtower_attribution.py` between 2026-07-10 and 2026-07-15
(`394dbd9`, a bundled X21E feature commit not obviously related to
enqueue logic), and it does not obviously explain a multi-day dip
followed by full recovery. Checked whether the creator-known code gap
(Phase 3, gap #1) explains it — **ruled out**: `creator_known=0` for
every single day in this 7-day window (the `wt_creator_launches` table
has only 13 rows total, spread across all of history, not concentrated in
this window). Supervisor logs from that period were not available (log
rotation/truncation) to check for a worker outage or restart. **Per the
brief's "do not speculate" instruction, this dip is reported as measured
but unexplained** — a genuine limitation of this investigation, not a
root-caused historical exception. It is flagged as the single most
important open question for a future investigation, since it demonstrates
the coverage rate can and did fall to ~10% for a multi-day period without
any code change being obviously responsible.

**Classification of the 13,839 all-time missing mints**:
- **Expected/historical** (predates walkback): 6,473 (46.8%)
- **Genuine structural design gap** (creator-known at migration, Phase 3 gap #1): 13 (0.09%)
- **Unexplained historical dip** (2026-07-09 through 2026-07-12, and the lower-coverage weeks 22-26 generally): the remaining ~7,353 — a mix of the early-rollout ramp-up (weeks 22-25, plausibly attributable to the mechanism still maturing, though not conclusively proven either) and the specific unexplained dip documented above.

## Phase 6 — Failure modes: before vs. after queue insertion

These are architecturally distinct guarantees, and the platform's behavior
differs sharply between them:

**Before queue insertion** (can prevent a row from ever existing):
- Migration event never detected at all (WS drop with reconciler disabled — gap #6).
- `_mark_token_migrated_in_db` failure with no successful re-enqueue on retry (gaps #2, #3, compounding).
- Creator-known branch never calling enqueue at all (gap #1).
- Exception during `enqueue_migration`'s own execution, swallowed by the caller (gap #4).
- **No dead-letter/backfill mechanism exists for any of these** — a failure here is permanent unless a human notices and manually backfills.

**After queue insertion** (row exists, but may never be "attempted" in the RPC-classification sense):
- `walkback_worker` process not running (gap #5) — rows accumulate as `status='pending'` indefinitely; this is recoverable (starting the worker processes the backlog), unlike the before-insertion gaps.
- `attempts >= MAX_ATTEMPTS` (default 3) — `finalize_exhausted_pending()` sweeps these to `status='failed'`, so they are never silently lost from the count, just terminally unresolved.
- `SKIP`-classified rows are marked `status='skipped'` at enqueue time by design — present in the table, correctly excluded from RPC attempts, not a coverage gap.

**The key distinction for the invariant**: a before-insertion failure is a
true coverage gap (the mint never appears anywhere and `COUNT(*)` on
`wt_walkback_queue` will never reflect it without manual backfill); an
after-insertion failure is a processing delay/backlog, not a coverage gap
— the row exists and can still eventually receive a walkback attempt once
the worker resumes.

## Phase 7 — Architectural conclusion

**Answer: NO** — the platform cannot legitimately claim "every migrated
token receives a walkback attempt" as a structural guarantee.

Supporting evidence:
1. A genuine, verified code-path gap exists (creator-known migrations
   never call `enqueue_migration`) — currently small in practice (13
   mints all-time) but structurally unconditional; it will scale with
   however large `wt_creator_launches` coverage grows in the future.
2. No retry/dead-letter mechanism exists for enqueue-side failures
   (exceptions are logged-and-swallowed or silently swallowed).
3. A measured, real, multi-day coverage collapse (10-72%) occurred as
   recently as five days before this audit, and its root cause could not
   be determined from available evidence — meaning the platform has no
   demonstrated resilience against this recurring, only an
   after-the-fact recovery to ~99% that itself is unexplained.
4. The walkback worker's `autostart=false` supervisor configuration is a
   standing operational risk for the "attempt" half of the guarantee,
   even though enqueue itself is unaffected by it.

However, **current live coverage is genuinely strong**: ~99% for each of
the last three days measured, and the two structural code gaps found
affect a currently negligible fraction of volume. This is not "NO" in the
sense of "the system is broken" — it is "NO" in the sense of "there is no
enforced guarantee, only an empirically strong but not architecturally
proven correlation," and the one clearly measured historical failure
episode (07-09 through 07-12) demonstrates that correlation can and does
break down for multi-day periods without an identified cause.

**Can Discovery and Operational Behaviour safely treat `wt_walkback_queue`
as the canonical historical dataset?** With an important caveat: for
*current/recent* launches (the population these features are primarily
used to investigate), coverage is high (~99%) and the dataset is a
reasonable proxy. For *older* launches, especially anything from before
2026-06-02 or falling within the 2026-06 ramp-up weeks or the
2026-07-09/07-12 dip, `wt_walkback_queue` is a substantially incomplete
proxy for "all migrated tokens" and should not be treated as exhaustive —
consistent with the `coverage_note` X26.9.1 already added to the
infrastructure-activity metrics, which correctly hedges this exact
limitation ("not an exhaustive chain-wide total").

## Deliverables checklist

- [x] Complete pipeline diagram — Phase 1.
- [x] All enqueue sources — Phase 2 (one function, two call sites, one dead).
- [x] All exclusion paths — Phase 3 (7 identified, exact code references).
- [x] Database reconciliation — Phase 4 (21.96% all-time, ~99% current).
- [x] Missing-mint inventory/classification — Phase 5 (6,473 pre-dates
      mechanism / 13 structural design gap / ~7,353 unexplained
      historical ramp-up-and-dip).
- [x] Failure-mode analysis — Phase 6 (before-insertion vs. after-insertion,
      materially different guarantees).
- [x] Final architectural conclusion — Phase 7: **NO**, not a structural
      guarantee, though current practical coverage is strong (~99%) and
      the code-level gaps found are currently small in absolute impact.
