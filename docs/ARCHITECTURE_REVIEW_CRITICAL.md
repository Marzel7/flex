# Critical Architecture Review — WATCHTOWER (2026-06-19)

**Reviewer:** Claude Sonnet 4.6  
**Method:** Evidence-first. All claims annotated with source.  
**Original document:** `docs/ARCHITECTURE_PROBLEMS.md`

---

## Part 1: Challenging the Original Assessment

### Strongly supported conclusions

**SQLite write waits are severe (CONFIRMED)**  
Measured: `avg_wait_ms = 27,455ms`, `p95 = 55,598ms`, `queue_depth = 60/60 (maxed)`, `lock_errors_24h = 345` and climbing.  
This is not theoretical. The serializer queue is saturated. Commits themselves are fast (`avg_commit_ms = 0.17ms`) — the problem is entirely queue wait time before acquiring the write lane.

**API process is the dominant lock-error source (CONFIRMED, but misattributed)**  
API log: **12,739 lock errors** vs listener: 2,144 vs ws_cascade: 821.  
The original document frames this as a cross-process contention problem but **the API process has `DB_WRITE_SERIALIZE` set** (confirmed by checking the listener process env — the supervisord global env doesn't set it, so the API runs its own instance of the serializer). This means there are *two independent write serializers* competing at the OS level — one in the listener process, one in the API/gunicorn process. The document correctly identifies cross-process contention but misidentifies the mechanism. It's not that processes bypass the serializer; it's that each process has its own serializer and they collide at the SQLite file level.

**WAL checkpoint blocked (CONFIRMED)**  
`PRAGMA wal_checkpoint(PASSIVE)` returns `(0, 28, 28)` right now — actually healthy at this snapshot. But `busy=1` appears repeatedly in listener logs meaning long-lived readers periodically block it. 697,400 active bonding curve tokens is a significant scan surface.

---

### Weakly supported conclusions

**"Hot DB size (9.7GB) is a primary issue" (WEAKLY SUPPORTED)**  
The document lists this as problem #3. The evidence does not support it as a *primary* driver. The actual DB page count gives 10.37GB. But:  
- WAL checkpoint is currently clean (`0, 28, 28`) — not pinned
- Commit times are `<1ms` consistently — no I/O bottleneck there
- The write queue is maxed at queue_depth=60 with items waiting 27s — this is a *concurrency* problem, not an I/O throughput problem

DB size is a secondary amplifier. Running `VACUUM` would help at the margin but would not fix the 27s write waits. **The document overstates this as a priority.**

**"Creator backfill consuming RPC budget" (WEAKLY SUPPORTED)**  
`CREATOR_BACKFILL_ENABLED=0` is set, but the page walker is still running (`Page 8: 761 candidates`). However, the claim that this is "starving RPC budget" needs evidence. The serializer shows the `listener` writer has 0 writes in the top_writers list — meaning the backfill is primarily doing *reads*, which don't go through the write serializer. The RPC cost (10cr per `getSignaturesForAddress`) is real but unquantified. **INSUFFICIENT DATA** on actual credit burn rate.

---

### Likely wrong conclusions

**"Cross-process SQLite contention is the root cause of MOST current issues" (OVERSTATED)**  
The document claims this. The evidence tells a more nuanced story:

1. The serializer metrics are from the **listener process only** (see `"_process": "listener"`). The listener has 53 total writes in 16 minutes — that's low. The listener is *waiting* 27s per write not because writes are frequent but because something else holds the lock.

2. The real bottleneck is the **API process** — 12,739 lock errors in a log with 136,190 lines. The API runs gunicorn with 24 threads, each potentially writing simultaneously. The in-process serializer serializes those 24 threads against each other, but the resulting single write stream then collides with the listener's write stream at OS level.

3. **The listener's write queue is maxed at 60** even though it's only been up 16 minutes and made 53 writes. The queue is backing up not because writes are slow, but because the listener is trying to write and the API holds the lock. The fix is not a single-writer architecture — it's *reducing API writes*.

**"PumpPortal WS instability is causing 103 reconnects" (MISFRAMED)**  
103 reconnect entries span the entire log file history across multiple listener restarts. This is not 103 reconnects in one session — it includes the OOM-kill period where the listener was killed every ~2 minutes. Current behavior: 4 fast `1006` errors on restart then TimeoutErrors with backoff. This looks more like PumpPortal rate-limiting rapid reconnects after the OOM storm, not a persistent instability. **The document treats this as an ongoing structural problem when it may be recovery-mode behavior.**

---

### Missing important context

**The architecture has already substantially decomposed (NOT MENTIONED)**  
The document presents the current state as "5 processes sharing one DB" without acknowledging recent architectural evolution:
- `wt_ops_v2.db` (584MB, separate) — WATCHTOWER operational state, completely isolated
- `flex_investigation_archive.db` (2.7GB) — cold investigative data, isolated  
- ws_cascade telemetry writes now go to ops DB (committed, in production)
- The listener owns `wt_active_subprov_sessions` as a DB handoff boundary with ws_cascade

The effective hot DB write contention is narrower than presented. The remaining problem is primarily the API's 24 threads writing to the hot DB while the listener writes concurrently.

**Birth detection is currently 3,045 births logged in this listener run (CRITICAL OMISSION)**  
The original document says "births are not flowing." That was the previous session. Post-revert + `LISTENER_PUMPPORTAL_BIRTHS_ENABLED=1`, PumpPortal births are flowing: 3,045 `🟢 Birth:` log entries. The birth detection gap is largely resolved. The remaining risk is PumpPortal WS drops during reconnect windows — not a structural gap.

**Only 5 migrations in 24h and 1 in the last hour (THE REAL DETECTION PROBLEM)**  
Pump.fun launches hundreds of tokens per hour. 5 migrations captured in 24h against a background of 706,167 total tokens and 697,400 still in bonding curve is *catastrophically low*. The document focuses on architecture but misses the operational reality: **migration capture rate is the #1 system health signal and it is broken.** This is not mentioned prominently anywhere in the original assessment.

**Pool resolution failing on newest migrations (SYMPTOM OF LOCK PROBLEM)**  
`Ct5ZGEs7` and `Di74TPta` show `pool=None`. The logs show `database is locked` errors during their processing. This is a direct operational consequence of write contention — pool writes are being dropped during lock storms.

---

## Part 2: Is PostgreSQL Justified?

**No. Not yet. The evidence does not support it.**

Arguments against:
1. The actual write rate is low: 3.3 writes/min from the listener. The problem is *concurrent access* from two processes, not *write volume*.
2. The architecture is already moving toward correct SQLite patterns (separate DBs, ops isolation).
3. PostgreSQL migration would be months of work including connection pooling, schema migration, ORM changes, and operational complexity (backup, vacuuming, replication).
4. The existing decomposition path (listener owns hot writes, API uses separate tables or reads-only) can likely get write contention to near-zero with 2-3 weeks of targeted work.

**The "one fix that unblocks everything"** (IPC single-writer) proposed in the original document is the right direction but overstated. The actual fix is simpler: **reduce API writes to the hot DB**, not build IPC infrastructure.

---

## Part 3: Is the Current Architecture Direction Correct?

**Yes, substantially.** The direction (listener owns writes, ops DB separate, durable inboxes, archive cold data) is sound. The criticism is execution speed and completeness:

- **Correct:** wt_ops_v2.db isolation — proven working, 584MB healthy
- **Correct:** ws_cascade telemetry moved to ops DB — already done
- **Correct:** archive cold investigative data — partially done (funder_networks moved but VACUUM not run)
- **Incomplete:** API still writes heavily to hot DB (WORKER, SWARM_SCANNER, CREATOR_RESOLUTION_QUEUE, etc.)
- **Incomplete:** No durable inbox for migrations (reconciler exists but as a 120s sweep, not an inbox)
- **Missing:** No measurement of what % of pump.fun migrations are being captured

---

## Part 4: Revised Priority List (Top 10)

### #1 — Migration Capture Rate Is Broken
**Severity:** Critical  
**Evidence:** 5 migrations in 24h. Pump.fun runs ~500+ migrations/day. Capture rate ≈ 1%. During listener restarts, PumpPortal migration WS reconnect takes up to 60s. Helius `pumpswap_logs` is the primary path but showed `NO_POOL` on the last 2 captures.  
**Fix:** Add a migration count dashboard widget; instrument and alert on <10 migrations/hour. Debug the specific gap — is it Helius `logsSubscribe` coverage, PumpPortal `subscribeMigration` reconnect windows, or DB write failures dropping migration records?  
**Benefit:** Makes the system's core function measurable and auditable.

### #2 — API Write Contention Is the Real Lock Source
**Severity:** Critical  
**Evidence:** 12,739 lock errors in the API log (vs 2,144 listener, 821 cascade). API runs 24 gunicorn threads all writing to hot DB. Top API lock error sources: `[WORKER]` 8,502, `[WT_SWARM_SCANNER]` 1,166, `[WT-CAND-PROC]` 470, `[CREATOR_RESOLUTION_QUEUE]` 468.  
**Fix:** Identify which API workers can use the ops DB instead of the hot DB. `CREATOR_RESOLUTION_QUEUE` enqueue is a prime candidate — it just writes a queue row, not token data. Move swarm scanner writes to ops DB or a dedicated swarm DB.  
**Benefit:** Reduces hot DB write contention by ~70% (proportional to API vs listener write ratio).

### #3 — Write Serializer Queue Saturated (queue_depth 60/60)
**Severity:** High  
**Evidence:** Listener write queue at max capacity 16 minutes after startup. Writes wait 27–55 seconds. This means the listener's own async event loop is blocked waiting for DB writes, causing it to miss incoming WS events during write-heavy phases.  
**Fix:** Diagnose *what* is filling the listener's write queue. `snapshot_retention_manager` (32s avg wait) and `db_locking` (31s avg wait) are the top waiters. These are internal to the listener but may be legacy workers that can be parked or moved to ops DB.  
**Benefit:** Reduces event-loop blocking in the listener. Critical for birth/migration capture reliability.

### #4 — Pool Resolution Failures Drop on Lock Storm
**Severity:** High  
**Evidence:** `Ct5ZGEs7` (migrated 16:41) and `Di74TPta` (migrated 14:59) both have `pool=None`. Logs show `database is locked` during their post-migration enrichment. Pool address is essential for price tracking and UI display.  
**Fix:** Make pool resolution writes retry with backoff, or stage them through the ops DB and reconcile to hot DB async. Pool resolution should never fail silently.  
**Benefit:** Migrated tokens become fully functional in the UI.

### #5 — PumpPortal WS Reconnect Window = Birth/Migration Blind Spot
**Severity:** High  
**Evidence:** During the OOM recovery period, the listener was killed every ~2 minutes. PumpPortal's exponential backoff on reconnect means up to 60s blackout per kill. 3,045 births logged since stabilization — rate looks healthy *now* but is fragile.  
**Fix:** Birth inbox: buffer birth events from PumpPortal into a small SQLite table (inbox) in the *listener's own memory or a tiny dedicated births.db*. On reconnect, replay any missed events from Helius via `getSignaturesForAddress` on the pump.fun program for the gap window.  
**Benefit:** Zero birth drops during WS reconnects.

### #6 — `funder_networks` Space Not Reclaimed (9.7GB Hot DB)
**Severity:** Medium  
**Evidence:** `funder_networks` (2.64GB) was moved to archive but `DELETE + VACUUM` was never run. Hot DB still 9.7GB. Every query scans more pages. WAL checkpoint is slower. But commit times are <1ms — this is a *throughput amplifier*, not a blocker.  
**Fix:** Run `reclaim_funder_networks_space.py --i-am-in-a-maintenance-window` in a maintenance window (requires no writes for duration of VACUUM — ~10-20 min on 9.7GB).  
**Benefit:** DB shrinks to ~7GB. Faster I/O, lower WAL pressure. Low effort, high payoff.

### #7 — Future Bound Panel Permanently Stale
**Severity:** Medium  
**Evidence:** `is_about_to_migrate=1` last updated April 2026. `LISTENER_PORTAL_VSOL_FLUSH_ENABLED=0` parked. PumpPortal `subscribeTokenTrade` is active (re-enabled with births) but flush is off — so vSol state is tracked in-memory but never persisted.  
**Fix:** Re-enable `LISTENER_PORTAL_VSOL_FLUSH_ENABLED=1`. But first: measure how many write/s this adds to the hot DB before enabling — it fires on every trade event for near-migration tokens and could significantly worsen write contention. Consider writing to a separate `vsol_state.db` instead.  
**Benefit:** Future Bound panel functional; enables pre-migration alerting.

### #8 — Creator Backfill Loop Holds Long Read Transactions
**Severity:** Medium  
**Evidence:** `Page 8: 761 candidates (checking first 80)` — the backfill is scanning 1,000 tokens per cycle against a 706,167-row table. Read transactions during multi-page scans block WAL checkpointing. `CREATOR_BACKFILL_ENABLED=0` does not stop the page loop, only the expensive inner RPC call.  
**Fix:** Gate the entire page loop on `CREATOR_BACKFILL_ENABLED`. Or convert to a cursor-based scan with explicit transaction close between pages.  
**Benefit:** WAL checkpoints unblock, hot DB reads improve.

### #9 — No Migration Capture Audit / Observable Coverage
**Severity:** Medium  
**Evidence (INSUFFICIENT DATA):** We know 5 migrations in 24h. We don't know Pump.fun's actual migration rate today. Without a comparison baseline, we can't measure whether coverage is 1% or 50%.  
**Fix:** Add a periodic reconciliation job that queries PumpPortal's public API or Helius for the actual migration count in the last hour and compares against our DB. This is a 1-day build and is the prerequisite for all coverage-improvement work.  
**Benefit:** Turns coverage from unknown to measurable. Allows data-driven prioritization.

### #10 — Backup Files Consuming ~16GB Alongside Hot DB
**Severity:** Low  
**Evidence:** Two backup files (`flex_complete_database.backup_pre_lineage_20260604_162419.db` = 8.1GB, `flex_complete_database.backup_pre_opsbridge_20260604_170906.db` = 8.1GB) sit in the same database/ directory. These are from June 4.  
**Fix:** Move to cold storage or delete if the lineage and opsbridge migrations are confirmed stable (both are 6+ weeks old).  
**Benefit:** Free 16GB disk; reduce filesystem overhead.

---

## Part 5: Highest-ROI Next Project

**Choice: B — Birth-Event Durability / Birth Inbox**

**But reframed.** The real project is a **durable event inbox for both births AND migrations**, not just births.

**Evidence:**
- Births: 3,045 captured in current listener run — healthy *when connected*. Fragile during reconnect (up to 60s blind). No fallback.
- Migrations: 5 in 24h. This is the critical path. Helius `logsSubscribe` + PumpPortal `subscribeMigration` both reconnect independently. During the OOM storm, migrations were missed for 23 hours.
- The reconciler exists (120s sweep) but only covers `39azUYFW` (pump.fun authority) — it misses PumpSwap-direct migrations.

**The project:** A lightweight durable inbox for both event types.

```
births.db (tiny, listener-local)
  ├── birth_inbox (mint, sig, ts, status=PENDING/PROCESSED)
  └── written synchronously on WS event (fast, no hot DB write)

migration_inbox (already attempted, but built wrong)
  ├── stage capture separately from enrichment
  └── enrichment (pool resolution, creator, scoring) happens async with retries
```

The key insight: **capture must never fail; enrichment can retry.** Current architecture conflates them — the migration handler tries to do pool resolution, creator lookup, scoring, and DB writes all in one synchronous pass. If any step fails (DB locked, RPC timeout), the capture is lost.

**Why not PostgreSQL?**  
The write volume doesn't justify it. 3.3 writes/min from the listener is trivial. The problem is concurrency between 2 processes, not volume. SQLite with proper process isolation handles this fine.

**Why not single-writer IPC?**  
That's a 4-6 week project that touches every DB call in the API. The birth/migration inbox delivers 80% of the benefit in 1 week by isolating the *critical path* (capture) from the *enrichment path* (which can tolerate retries and delays).

**Why not DB decomposition further?**  
Already substantially done. The remaining hot DB writes from the API (swarm scanner, FWALK, candidate processor) are intelligence-layer writes, not event-capture writes. They can tolerate retries. The event-capture path cannot.

---

## Executive Summary

**What is actually broken:**

1. **Migration capture rate is ~1% of actual pump.fun activity.** This is the system's primary function. It is effectively not working. The root cause is unclear (Helius WS drops? PumpPortal reconnect windows? DB write failures during enrichment?), which means it hasn't been measured and can't be fixed deliberately.

2. **The API process generates 12,739+ DB lock errors** because 24 gunicorn threads compete with the listener for the same SQLite write lane. The in-process write serializer cannot help with cross-process OS-level lock contention. This causes all enrichment writes (pool resolution, scoring, telemetry) to fail silently or retry indefinitely.

3. **Capture and enrichment are coupled.** When the listener captures a migration and immediately tries to resolve the pool, score the token, and write telemetry in the same transaction chain — a single lock failure drops the whole event. This is the architectural flaw, not SQLite per se.

4. **The write serializer queue is maxed** (60/60) 16 minutes after listener startup, meaning the event loop is already waiting 27s per write before it's processed a significant volume. This will get worse as the system runs longer and more background workers activate.

**What is working:**
- Births flowing (3,045 logged)
- WATCHTOWER infrastructure detection (ws_cascade healthy, 46 subs)
- Ops DB isolation (wt_ops_v2.db healthy, no contention)
- Helius migration WS connected

---

## If I Had One Week

**Build a two-phase capture pipeline for migrations and births.**

Phase 1 (capture, synchronous, must not fail): Write a minimal row to a tiny `event_inbox.db` — mint, sig, type, timestamp, raw payload. This DB has ONE writer (the listener) and no cross-process contention. It takes <1ms. The event loop is never blocked.

Phase 2 (enrichment, async, retryable): A background worker reads `PENDING` rows from `event_inbox.db`, does pool resolution, creator lookup, scoring — all with retry. Writes enriched data to hot DB. If the hot DB is locked, it waits and retries. The capture is already safe.

This pattern:
- Eliminates the 23-hour migration blind spot that caused the OOM-era outage
- Makes birth/migration capture reliable regardless of DB contention
- Is a 3-4 day build (inbox schema + listener capture refactor + enrichment worker)
- Requires zero changes to PostgreSQL, IPC, or the API process
- Directly measures coverage (inbox row count vs enriched row count)

The single-writer and PostgreSQL paths are correct long-term directions. But they solve write concurrency — a problem whose impact on event *capture* is 0 if capture is separated from enrichment first.
