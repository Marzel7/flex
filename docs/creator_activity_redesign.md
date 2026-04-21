# Creator Activity Pipeline — Redesign

## 1. High-level architecture

```
                    ┌────────────────────────────────────────────────────┐
                    │                 TIER A  (hot path)                 │
                    │  curve_complete / migration / pre-tracked          │
                    │                                                    │
  accountSubscribe  │  _ensure_pf_ws_creator()                          │
  logsSubscribe  ───┤    │                                              │
                    │    ├─ DB lookup: create_tx_signature              │
                    │    ├─ getTransaction (1 RPC, Path A)              │
                    │    ├─ infer creator from fee payer                │
                    │    ├─ write pf_ws_creator                         │
                    │    ├─ upsert creator_profile                      │
                    │    └─ enqueue creator_funding_queue (existing)    │
                    │                                                    │
                    │  _process_repeat_creator_launch()                 │
                    │    ├─ increment token_count_seen                  │
                    │    ├─ pass through creator_funding_queue cache    │
                    │    └─ maybe enqueue incremental_reconcile         │
                    └────────────────────────────────────────────────────┘
                                          │
                                          │  enqueues jobs
                                          ▼
                    ┌────────────────────────────────────────────────────┐
                    │              creator_activity_jobs                 │
                    │  (baseline | incremental_reconcile | backfill | refresh) │
                    └────────────────────────────────────────────────────┘
                                          │
                                          │  polled by
                                          ▼
                    ┌────────────────────────────────────────────────────┐
                    │             TIER B  (background worker)            │
                    │                                                    │
                    │  baseline:              full getSignaturesForAddress scan │
                    │  incremental_reconcile: bounded gap-fill           │
                    │  backfill:              delayed deep scan          │
                    │  refresh:               re-classify existing data  │
                    │                                                    │
                    │  Writes: creator_funders, creator_profile,        │
                    │          creator_activity_state                    │
                    └────────────────────────────────────────────────────┘
                                          │
                           webhook/subscription
                                          │
                    ┌────────────────────────────────────────────────────┐
                    │          _handle_creator_stream_event()            │
                    │  Updates creator_activity_state cursor             │
                    │  Flags gaps → enqueues incremental_reconcile       │
                    └────────────────────────────────────────────────────┘
```

### Key principles

- **Tier A** runs synchronously on curve_complete / migration. Zero expensive RPC. Safe under latency constraints.
- **Tier B** runs entirely in the background. One active heavy job per creator enforced by DB unique index.
- **creator_profile** is the single source of truth for "do we know this creator?". Replaces `SELECT COUNT(*) FROM creator_funders` as the cache signal.
- **Streaming fills forward cheaply**. Historical gaps get a one-time baseline scan. After that, only bounded reconciliation is needed.

---

## 2. Data model

### New tables

#### `creator_profile`
One row per creator. Summary, classification, coverage metadata.

```sql
creator_address          TEXT PRIMARY KEY
history_status           TEXT  -- unknown | partial | baselined | stale
coverage_mode            TEXT  -- forward_only | full
classification_status    TEXT  -- clean | suspicious | cex_funded | ...
token_count_seen         INTEGER
first_seen_at            INTEGER
last_seen_at             INTEGER
last_launch_at           INTEGER
last_create_tx_signature TEXT
last_activity_at         INTEGER
last_full_scan_at        INTEGER
last_incremental_scan_at INTEGER
webhook_started_at       INTEGER
webhook_status           TEXT  -- active | stopped | error
baseline_version         INTEGER
```

#### `creator_activity_state`
Durable stream/reconcile cursor. Survives restarts.

```sql
creator_address          TEXT PRIMARY KEY
last_seen_signature      TEXT
last_seen_slot           INTEGER
oldest_scanned_signature TEXT
newest_scanned_signature TEXT
last_reconciled_at       INTEGER
needs_backfill           INTEGER (0/1)
needs_reconcile          INTEGER (0/1)
last_gap_detected_at     INTEGER
resume_cursor            TEXT (JSON: {"signature": ..., "slot": ...})
stream_health_status     TEXT  -- healthy | lagging | gap_detected | unknown
```

#### `creator_activity_jobs`
Creator-centric job queue. Unique pending/running per (creator, job_type).

```sql
id               INTEGER PRIMARY KEY AUTOINCREMENT
creator_address  TEXT
job_type         TEXT  -- baseline | incremental_reconcile | backfill | refresh
status           TEXT  -- pending | running | complete | failed | cancelled
priority         INTEGER  -- lower = runs first
attempt_count    INTEGER
next_attempt_at  INTEGER
locked_at        INTEGER
source_mint      TEXT
source_reason    TEXT
```

### Existing tables — unchanged

| Table | Role | Changed? |
|---|---|---|
| `token_analysis` | Per-mint lifecycle, creator columns | Additive: `creator_profile_resolved`, `creator_activity_job_id` |
| `creator_funders` | Extracted funder records | No |
| `creator_funding_queue` | Token-scoped extraction queue | No — kept running through Phase 3 |

### Relationships

```
token_analysis.pf_ws_creator ──────────────► creator_profile.creator_address
                                                      │
                                             creator_activity_state
                                             creator_activity_jobs
                                             creator_funders (1:N)
```

### Compatibility view

`v_creator_funding_summary` joins `creator_profile` + `creator_funders` so
existing queries that use `COUNT(creator_funders)` can migrate incrementally.

---

## 3. Lifecycle flow

### Token discovered (bonding curve phase)

1. Token enters DB via logsSubscribe detection.
2. `bonding_curve_pda` is registered; token gets `is_about_to_migrate = 1` near graduation.
3. Curve watcher subscribes to PDA via `accountSubscribe`.

### Curve complete event

1. `accountSubscribe` push: `complete` bit flips false → true.
2. `_handle_curve_complete_transition(mint, slot)`:
   - Persist `curve_complete=1`, `curve_completed_at`.
   - **Tier A**: `_ensure_pf_ws_creator(mint, reason="curve_complete")`.
     - Path A: `getTransaction(create_tx_signature)` → infer creator.
     - If create_tx unavailable: fall back to DB column or schedule baseline job.
   - Enqueue `creator_funding_queue` row (existing mechanism).
   - `dual_write_creator_resolved()` → upsert `creator_profile`, maybe enqueue `baseline` job.

### Creator resolved (pf_ws_creator written)

1. `creator_profile` row upserted with `first_seen_at`, `last_launch_at`, `token_count_seen++`.
2. `_start_creator_watch_if_needed()` registers webhook/subscription if not active.
3. If `history_status = unknown` → enqueue `baseline` job (Tier B, low priority, delay=30s).

### Creator watch started

1. Webhook or wallet subscription registered.
2. `creator_profile.webhook_status = active`, `webhook_started_at = now`.
3. Forward events will update `creator_activity_state` cursor.

### Queue/job decision — baseline

```
history_status == unknown                 → enqueue baseline
history_status == partial                 → enqueue baseline (resume cursor)
history_status == baselined               → skip unless > 7 days stale
history_status == stale                   → enqueue baseline
token_count_seen >= 2                     → repeat creator path instead
```

### Baseline scan (Tier B worker)

1. Worker picks up `baseline` job from `creator_activity_jobs`.
2. Marks `creator_profile.history_status = partial` immediately.
3. Calls `run_baseline_scan_fn(creator, resume_cursor)`.
4. If timeout (90s): save cursor to `creator_activity_state.resume_cursor`, re-enqueue.
5. On completion: set `history_status = baselined`, `coverage_mode = full`, `last_full_scan_at`.

### Streaming maintenance

1. Webhook delivers event for creator wallet.
2. `_handle_creator_stream_event(event)`:
   - Update `creator_activity_state.last_seen_signature / slot`.
   - Detect slot gap > threshold → flag `needs_reconcile = 1`.
   - Enqueue `incremental_reconcile` job if gap detected.

### Reconciliation (Tier B)

1. Worker picks up `incremental_reconcile` job.
2. `_reconcile_creator_gap()`:
   - `getSignaturesForAddress(creator, before=last_seen_signature, limit=50)`.
   - Walk sigs until hitting `oldest_scanned_signature`.
   - Process and persist each.
   - Clear `needs_reconcile`, update cursor.

### Repeat creator relaunch

1. Migration detected for creator with `token_count_seen >= 1`.
2. **Tier A**: `_process_repeat_creator_launch()`:
   - Increment token count.
   - Pass through existing `creator_funding_queue` (cache hit skips extraction).
   - If incremental scan stale (> 6h): enqueue `incremental_reconcile` (not baseline).
3. Never re-runs full historical scan.

---

## 4. Decision trees

### When to run baseline

```
_should_run_creator_baseline(profile, state):
  if profile is None:                           → YES
  if history_status == unknown:                 → YES
  if history_status == partial:                 → YES (resume)
  if history_status == stale:                   → YES
  if history_status == baselined:
    if last_full_scan_at > 7 days ago:          → YES
    else:                                       → NO
```

### When to skip extraction (repeat creator)

```
profile exists AND history_status IN (baselined, partial)
  AND creator_funders COUNT > 0
  → skip full extraction, return cached result
  → record RPC savings
```

(Controlled by `should_use_profile_cache()` in `migration_bridge.py` — disabled in Phase 1.)

### When to enqueue incremental_reconcile

```
stream event received AND slot_delta > GAP_THRESHOLD      → YES
job finished, needs_reconcile flag set in DB              → YES
repeat creator launch AND last_incremental_scan > 6h      → YES
```

### When to enqueue backfill

```
creator resolved but create_tx_signature unavailable      → YES (delayed)
explicit operator request                                 → YES
```

### When to upgrade forward_only → full

```
coverage_mode == forward_only AND baseline job completes  → set coverage_mode = full
```

---

## 5. Pseudocode

### `_ensure_pf_ws_creator()`

```python
async def _ensure_pf_ws_creator(mint, reason, ...):
    row = await db.get(mint, cols=["create_tx_signature", "pf_ws_creator", "earliest_tx_creator"])

    if row.pf_ws_creator:
        await touch_creator_profile(row.pf_ws_creator)
        return row.pf_ws_creator

    creator = None

    if row.create_tx_signature:                              # Path A
        tx = await get_transaction(row.create_tx_signature)
        if tx and validate_create_tx(tx).is_pumpfun_create:
            creator = infer_creator_from_fee_payer(tx)

    if not creator:
        if row.earliest_tx_creator:                          # Path C
            creator = row.earliest_tx_creator
        elif reason not in HOT_PATHS:                        # Path B — not on hot path
            creator = await get_creator_from_earliest_tx(mint)
        else:
            await enqueue_baseline_job(mint=mint, reason=reason)
            return None

    await db.update(mint, pf_ws_creator=creator)
    await upsert_creator_profile(creator, last_launch_at=now)
    await increment_token_count(creator)
    await enqueue_funding_job(creator, mint=mint)            # existing queue
    await start_creator_watch_if_needed(creator)
    await dual_write_creator_resolved(creator, mint, reason=reason)
    return creator
```

### `_enqueue_creator_activity_job()`

```python
async def _enqueue_creator_activity_job(creator_address, mint, reason, job_type, *, priority=100):
    if not creator_address:
        return None
    job_id = await repo.enqueue_creator_activity_job(
        creator_address, job_type,
        priority=priority, source_mint=mint, source_reason=reason,
    )
    # Returns None if UNIQUE index blocked duplicate — that is correct behaviour
    return job_id
```

### `_should_run_creator_baseline()`

```python
def _should_run_creator_baseline(profile, activity_state):
    if profile is None:
        return True
    if profile.history_status in (UNKNOWN, PARTIAL, STALE):
        return True
    if profile.history_status == BASELINED:
        return (now() - profile.last_full_scan_at) > STALE_THRESHOLD
    return False
```

### `_start_creator_watch_if_needed()`

```python
async def _start_creator_watch_if_needed(creator_address, ...):
    profile = await repo.get_creator_profile(creator_address)
    if profile and profile.webhook_status == ACTIVE:
        return
    ok = await webhook_register(creator_address)
    await repo.upsert_creator_profile(creator_address,
        webhook_status=ACTIVE if ok else ERROR,
        webhook_started_at=now() if ok else None,
    )
```

### `_handle_creator_stream_event()`

```python
async def _handle_creator_stream_event(event, ...):
    state = await repo.get_creator_activity_state(event.creator_address)
    gap = state and (event.slot - state.last_seen_slot) > GAP_SLOT_THRESHOLD
    await repo.upsert_creator_activity_state(event.creator_address,
        last_seen_signature=event.signature,
        last_seen_slot=event.slot,
        stream_health_status=GAP_DETECTED if gap else HEALTHY,
        needs_reconcile=gap or None,
    )
    if gap:
        await enqueue_job(event.creator_address, INCREMENTAL_RECONCILE, priority=20)
```

### `_reconcile_creator_gap()`

```python
async def _reconcile_creator_gap(creator_address, ...):
    state = await repo.get_creator_activity_state(creator_address)
    sigs = await get_signatures(creator_address,
        before=state.last_seen_signature, limit=50)
    for sig in sigs:
        if sig == state.oldest_scanned_signature:
            break
        await process_signature(creator_address, sig)
    await repo.upsert_creator_activity_state(creator_address,
        last_reconciled_at=now(), needs_reconcile=False,
    )
```

### `_process_repeat_creator_launch()`

```python
async def _process_repeat_creator_launch(creator_address, mint, create_tx_signature, ...):
    await increment_token_count(creator_address)
    await enqueue_funding_job(creator_address, mint=mint)   # cache hit expected
    profile = await repo.get_creator_profile(creator_address)
    if is_stale_for_incremental(profile):
        await enqueue_job(creator_address, INCREMENTAL_RECONCILE, priority=50)
```

---

## 6. Migration plan

### Phase 1 — dual write, no behaviour change (safe to deploy now)

Files: `src/creators/` module, `database/migrations/creator_activity_redesign.sql`.

1. Apply SQL DDL (`ensure_schema()` called at startup).
2. Run `migrate_existing_creator_funders()` once to populate `creator_profile` from existing `creator_funders`.
3. Call `dual_write_creator_resolved()` from `_ensure_pf_ws_creator()` after creator is confirmed.
4. `CreatorActivityWorker` is instantiated but **not started** — jobs enqueue but no worker consumes them yet.
5. Old `creator_funding_queue` + `extract_funding_for_new_token()` continues unchanged.
6. `should_use_profile_cache()` returns `False` — cache still uses `COUNT(creator_funders)`.

**Risk: zero.** All new code is additive.

### Phase 2 — activate worker, switch cache check

1. Start `CreatorActivityWorker` as an `asyncio.create_task` in `main_loop`.
2. Flip `PHASE_2_ACTIVE = True` in `migration_bridge.py`.
   - Cache check now reads `creator_profile.history_status` instead of `COUNT(creator_funders)`.
   - Repeat creator extraction short-circuits immediately.
3. Baseline jobs begin executing for unknown creators.
4. Monitor `creator_activity_jobs` queue depth and error rate.

**Risk: low.** Only affects creators whose profile is already populated from Phase 1 bootstrap.

### Phase 3 — retire token-scoped queue

Once `creator_activity_jobs` has stable coverage (> 95% of creators have `history_status != unknown`):

1. Remove the `COUNT(creator_funders)` early-return from `extract_funding_for_new_token()`.
2. Route all new extraction through `CreatorActivityWorker`.
3. Keep `creator_funding_queue` read-only for historical records.
4. Remove `migration_bridge.py`.

---

## 7. RPC savings

| Path eliminated | Frequency | Credits saved per occurrence |
|---|---|---|
| Full `getSignaturesForAddress` scan for repeat creator | Every repeat-creator launch | ~100 |
| Duplicate baseline on same creator from different mints | Multiple mints same creator | ~100 per dup |
| Stale reconcile on unchanged creator | Repeat launch < 6h after last | ~20–50 |
| Path B fallback on hot path | When `create_tx_signature` missing | ~5–20 |

**Before redesign:** every token enqueued a potential full extraction.  
**After redesign:** one baseline per creator lifetime. Incremental reconcile (~50 sigs) for forward gaps.

A creator with 10 tokens would previously trigger up to 10 full scans.  
Post-redesign: 1 baseline + at most 9 incremental reconciles (50 sig lookups each vs 500+).  
Conservative estimate: **80–90% reduction** in `getSignaturesForAddress` calls for prolific creators.

---

## 8. Recommendation — implement first

**Start with Phase 1 only.** It is zero-risk and provides immediate observability value.

Priority order:
1. Run `ensure_schema()` on startup — creates the three new tables.
2. Run `migrate_existing_creator_funders()` once — gives every known creator a `baselined` profile row.
3. Add `dual_write_creator_resolved()` call inside the existing `_ensure_pf_ws_creator()` after the creator write — no behaviour change, just populates the new tables.
4. Watch `creator_profile` populate over a few hours. Confirm token counts, history_status values.
5. Then start Phase 2: activate the worker and flip the cache flag.

The biggest single win is **step 2** — bootstrapping `creator_profile` from `creator_funders` means repeat creators are immediately identifiable without any RPC at all, the moment Phase 2 cache check goes live.
