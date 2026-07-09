# Post-Implementation Durability Review (2026-06-19)

**Premise to review:** "Migration Inbox is now implemented and live."

**Finding:** The migration inbox table (`migration_inbox`) exists in the hot DB with 3 rows (2 PROCESSED, 1 RETRY-stuck). The implementation code was shipped in commit `edd61b4` but **reverted entirely in commit `158b0d5`** due to a bug (infinite RETRY loop on 16-char truncated signatures). No Python code currently writes to or reads from `migration_inbox`. It is a ghost table. The code is back to the pre-inbox baseline.

**This review therefore treats the migration inbox as NOT implemented** and assesses current system state.

---

## Part 1: Re-Ranked Durability Scores

---

### TREASURY HITS — Score: 1/10 (unchanged)

**Consequence of loss:** 5/5 — multiplier failure. Treasury missed → no subprov session opened → ws_cascade never subscribes that subprov → every launch via that subprov is permanently undetectable. One lost treasury hit can mask an entire launch campaign.

**Probability of loss:** 5/5 — Flask handler at `main.py:33807` does `_wt_infra_queue.put_nowait(payload)` then immediately returns 200. The payload is in a `queue.Queue(maxsize=2000)` in the gunicorn process heap. 81 listener restarts observed. API process also restarts. Queue-full silent drop on any DB lock storm (no exception, no 5xx to Helius, Helius does not retry on 200).

**Recovery:** 0/5 — None. Helius delivers once on 200. No replay, no audit that the webhook arrived.

**Remaining failure windows:**
1. Crash between `put_nowait` (line 33807) and background drain write to `watchtower_infra_events`
2. Queue full (2000-item capacity, maxsize) → `except Full: pass` → silent drop with no record

**Updated score:** 1/10 — no change. No inbox has been implemented.

---

### MIGRATIONS — Score: 3/10 (unchanged)

**Consequence of loss:** 5/5 — core system function. 5 migrations captured in 24h vs estimated 500+/day.

**Probability of loss:** 4/5 — both paths (Helius `logsSubscribe` + PumpPortal `subscribeMigration`) call `handle_migration(sig)` → immediately add to `processing_migrations` (in-memory) → call `_get_transaction_cached(sig)` (RPC) → then write. On RPC failure: 45-second delayed recheck fires and returns — if process dies during that window, event gone. `processing_migrations` and `completed_migrations` are in-memory; they are lost on restart. The reconciler covers `39azUYFW…`-transiting migrations within 100-sig window, 120s cadence. PumpSwap-direct migrations: no reconciler coverage.

**Critical bug still present:** `processing_migrations.add(sig)` fires at line 9406, BEFORE the RPC. If the Helius path fails its RPC, the sig stays in `processing_migrations` this session, blocking the PumpPortal path from attempting it (line 9403 guard fires). Two live paths collapse to one effective path.

**Recovery:** 2/5 — reconciler for `39azUYFW…` within 100-sig window only.

**Updated score:** 3/10 — identical to previous assessment. Migration inbox code reverted; no improvement.

---

### BIRTHS — Score: 4/10 (unchanged)

**Consequence of loss:** 3/5 — missed birth = token never in system. At ~1000 births/hour, individual loss is lower consequence than treasury or migration, but systematic birth loss degrades all downstream enrichment.

**Probability of loss (PumpPortal path — primary):** 3/5 — no RPC before write (PumpPortal delivers all data inline). Risk is DB write failure: write serializer queue currently at 60/60 (maxed), `busy_timeout` exhaust → silent drop in `_insert_bonding_curve_token` exception handler (bare `log_print`, no queue, no retry). No record that the birth arrived.

**Probability of loss (Helius `logsSubscribe` — secondary):** 4/5 — `handle_birth` calls `_get_transaction_cached(sig)` before any write. RPC failure → bare `return` at line 5594, event gone.

**Birth drainer (webhook path):** Active, but contains a critical bug: the drainer (`drain_webhook_birth_queue`, line 9926) marks rows `consumed = 1` in a committed DB transaction, then calls `asyncio.create_task(handle_birth(sig, []))` outside that transaction. A crash between commit and task execution = consumed but never processed. Additionally, the Helius birth webhook (`/api/webhook/pumpfun-birth`) is not currently registered — zero rows are entering `webhook_birth_queue` from the live system.

**Recovery:** 2/5 — Helius `logsSubscribe` is a partial backstop for PumpPortal misses; no backstop for Helius failures; birth drainer exists but has a consumed-before-task bug and no live feeder.

**Updated score:** 4/10 — unchanged.

---

### CASCADE LAUNCHES — Score: 5/10 (unchanged)

**Consequence of loss:** 5/5 — WATCHTOWER's core detection output. But frequency is low: 9 rows in `wt_watchtower_launches` total.

**Probability of loss:** 2/5 — `_seen(candidate, sig)` adds to `_processed` BEFORE `store.record_launch()` is called. If `_get_tx(sig)` fails or `record_launch` fails, the sig is in `_processed` this session and won't retry. On restart, `_processed` clears and `catch_up_candidate` re-scans — but `CATCHUP_SIG_LIMIT = 8` (default, not raised). This fix from the roadmap was **not implemented**.

**Recovery:** 4/5 — `catch_up_candidate` on reconnect re-scans last 8 sigs. Single-use creator wallets typically have <8 txs, so this covers most cases. `wt_candidate_websocket_watches` (OPS DB) survives restart and drives re-subscription.

**Updated score:** 5/10 — unchanged. Neither roadmap fix (dedup order nor CATCHUP_SIG_LIMIT raise) was implemented.

---

### WRAP-CLOSE DETECTION — Score: 6/10 (unchanged)

**Consequence of loss:** 4/5 — missed wrap-close = no candidate watch opened = WATCHTOWER launch undetectable via that creator.

**Probability of loss:** 2/5 — `_handle_subprov_tx` calls `_get_tx(sig)` before `open_candidate_watch`. RPC failure → returns `[]`, event gone. However, `subprov_sweep_pass` runs every 6s via `catch_up_subprov`, re-fetching the last 8 sigs on each active subprov.

**Recovery:** 4/5 — sweep mechanism is real and active. `CATCHUP_SIG_LIMIT = 8` is the main risk for high-velocity subprovs or long outages.

**Updated score:** 6/10 — unchanged.

---

### SUBPROV DISCOVERY — Score: 4/10 (unchanged)

**Consequence of loss:** 4/5 — inherits treasury hit consequence for the webhook path. WS path (cascade `_handle_treasury_tx`) writes `treasury_ws_record_notif` even on RPC failure — this is the best-behaved path in the system.

**Probability of loss:** 3/5 — webhook path inherits the full treasury hit fragility (in-memory queue between 200 and DB write). WS path is more durable (writes notification regardless of RPC result).

**Recovery:** 3/5 — operation_scheduler backward walk discovers subprovs within ~15 min for known treasuries. Not zero, but delayed.

**Nuance:** `_handle_treasury_tx` (ws_cascade.py:464) is the best-implemented handler in the codebase. On RPC failure at line 471 (`if not tx:`), it still calls `store.treasury_ws_record_notif(conn, treasury, sig, opened_session=False)` — the notification is persisted even when the tx can't be fetched. The subprov session isn't opened (no tx = no recipient parsed) but the treasury activity is recorded. This is the correct pattern.

**Updated score:** 4/10 — unchanged for the webhook path dependency.

---

### FUTURE BOUND — Score: 2/10 (unchanged, currently parked)

**Consequence of loss:** 2/5 — UX only, no detection dependency.

**Probability of loss:** 5/5 — `LISTENER_PORTAL_VSOL_FLUSH_ENABLED=0`, `portal_vsol.json` stale since April, `tracked_trade_mints` entirely in-memory.

**Recovery:** 1/5 — trade re-seed on reconnect rebuilds subscriptions but not vSol values.

**Updated score:** 2/10 — parked. Not a detection risk.

---

### TELEMETRY — Score: 3/10 (by design, no change warranted)

**Consequence of loss:** 1/5 — no detection impact.

**Updated score:** 3/10 — acceptable, do not invest.

---

## Part 2: The New Weakest Link

**The weakest link is still Treasury Hits.**

This is not a default assumption. Here is the proof by elimination:

**Migrations (3/10):** Have a reconciler backstop covering the primary path within 100 sigs. Loss is probable on direct-to-pool migrations, but most migrations transit `39azUYFW…` and are recoverable. The consequence is high but the partial recovery elevates it above treasury hits.

**Births (4/10):** High frequency means individual births have lower per-event consequence. The system can tolerate some birth loss without losing detection capability — WATCHTOWER launches are detectable via wrap-close/cascade regardless of whether the birth was captured. Birth loss degrades enrichment, not detection.

**Treasury Hits (1/10):** 
- **Consequence multiplier:** One lost treasury event silences ALL subsequent detection downstream. It doesn't just lose one event — it loses the entire subprov chain that treasury was about to fund.
- **Zero recovery:** `watchtower_infra_events` has no reconciler. There is no "catch up on missed treasury webhooks." Helius has already delivered and moved on.
- **Loss mechanism is active right now:** The `_wt_infra_queue` is in-memory. The API process (gunicorn, 24 threads) restarts on supervisor reload, OOM events, config changes. Every restart since the system was deployed has been a potential treasury event loss window.
- **Measurable evidence of risk:** 81 listener restarts logged. The API process restarted within this session (uptime 0:24:26 at time of measurement). Every restart = any in-flight treasury webhooks in `_wt_infra_queue` at that moment = permanently lost.

**Conclusion:** Treasury Hits remain the single highest-risk permanent-loss path. Score 1/10 with zero recovery.

---

## Part 3: Inbox Pattern Audit

Every remaining "Receive → RPC → First Durable Write" pattern:

| # | File | Function | Event Type | RPC Before Write | First Durable Write | On RPC Failure | Severity |
|---|------|----------|-----------|-----------------|-------------------|----------------|----------|
| 1 | `main.py:33807` | `webhook_watchtower()` | Helius treasury webhook | **None** — but queue is non-durable | `watchtower_infra_events` (after queue drain) | N/A — failure is queue loss, not RPC | **Critical** |
| 2 | `pumpfun_curve_listener.py:9679` | `listen_pumpswap_websocket` → `handle_migration` | Helius logsSubscribe migration | `_get_transaction_cached` | `_process_migration_with_mint` → `token_analysis` | 45s retry then permanent loss | **High** |
| 3 | `pumpfun_curve_listener.py:9913` | `listen_pumpportal_websocket` → `handle_migration` | PumpPortal subscribeMigration | `_get_transaction_cached` | `token_analysis` | Same as above | **High** |
| 4 | `pumpfun_curve_listener.py:5592` | `handle_birth` | Helius logsSubscribe birth | `_get_transaction_cached` | `_insert_bonding_curve_token` → `token_analysis` | Bare `return`, event lost | **Medium** |
| 5 | `pumpfun_curve_listener.py:9869` | `listen_pumpportal_websocket` tx_type=="create" | PumpPortal birth | **None** | `_insert_bonding_curve_token` → `token_analysis` | Exception handler: `log_print` only, silent drop | **Medium** |
| 6 | `ws_cascade.py:543` | `_handle_subprov_tx` | logsSubscribe wrap-close | `_get_tx(sig)` | `wt_candidate_websocket_watches` (OPS DB) | Returns `[]`, event lost (sweep backstop exists) | **Medium** |
| 7 | `ws_cascade.py:582` | `_handle_candidate_tx` | logsSubscribe candidate CREATE | `_get_tx(sig)` | `wt_watchtower_launches` (OPS DB) | Returns `(None, None)`, event lost | **Medium** |
| 8 | `pumpfun_curve_listener.py:9944` | `drain_webhook_birth_queue` | Helius birth webhook → queue drain | `handle_birth` → `_get_transaction_cached` | `token_analysis` | `consumed=1` set BEFORE `create_task` — event marked done but processing never ran | **Medium** |

**#1 is different from all others:** The treasury webhook path has no RPC problem — it's a queue durability problem. The payload is safe from RPC failure but not from process restart.

**Best-behaved path not on this list:** `_handle_treasury_tx` in `ws_cascade.py` — it writes `treasury_ws_record_notif` on RPC failure. It does not follow the "RPC → first write" anti-pattern.

---

## Part 4: Birth Durability Review

**Is the infrastructure present?**
- `webhook_birth_queue` table: **Yes** — schema created by `_ensure_webhook_birth_queue_schema` at `pumpfun_curve_listener.py:774`.
- `drain_webhook_birth_queue` drainer: **Yes** — implemented at line 9926, started unconditionally at line 10471.
- Flask endpoint `/api/webhook/pumpfun-birth`: **Yes** — `webhook_pumpfun_birth()` in `main.py` writes sigs to the table BEFORE returning 200 (correct pattern — write then 200, unlike treasury hits).

**Is it partially implemented?**
Yes. The birth inbox is more complete than the treasury inbox. The write-before-200 pattern is already correct. The drainer is live. The table is being maintained.

**What prevents it from functioning today?**

Two problems:

1. **No Helius birth webhook registered.** Zero rows enter `webhook_birth_queue` because Helius is not configured to POST to `/api/webhook/pumpfun-birth`. Only 2 Helius webhooks exist, both pointing to `/api/webhook/watchtower`. The drainer runs every 5s polling an empty table.

2. **Consumed-before-task bug.** The drainer marks `consumed = 1` in a committed transaction at line 9944–9947, then calls `asyncio.create_task(handle_birth(sig, []))` at line 9953 — outside the commit. A crash between those two lines permanently loses the birth even though it was "received." The fix is to mark `consumed = 1` only after `handle_birth` completes, not before dispatching it.

**Estimated effort to make operational:**
- Register Helius birth webhook: ~1 hour (API call to Helius)
- Fix consumed-before-task bug: ~1 hour (move update to after `create_task` returns successfully, or use a separate `processing` status)
- Total: ~2 hours

**Durability improvement:** Births via the Helius webhook path would go from 0% durable to ~95% durable (write before 200 is already correct; the consumed bug is the only remaining gap). PumpPortal birth path remains fragile (still no fallback for DB write failure).

**Assessment:** This is the lowest-effort highest-completion durability project in the system. The infrastructure is 80% built. It is blocked by two small issues, one of which is a Helius configuration item.

---

## Part 5: Treasury Capture Review

**Is it still highest ROI?**

By consequence × recovery-gap, yes. But the birth fix is competitive on effort-adjusted ROI because it requires ~2 hours vs the treasury inbox which requires schema design + code changes to both the Flask handler and the drainer.

**Exact code paths that would change:**

```
CURRENT:
main.py:33807  _wt_infra_queue.put_nowait(payload)
main.py:33810  return "ok", 200
main.py:33557  [background thread] _process_wt_infra_payload(payload)

PROPOSED:
main.py:33807  INSERT INTO wt_treasury_webhook_inbox (received_at, payload, status='PENDING')
main.py:33810  return "ok", 200
[background thread] SELECT * FROM wt_treasury_webhook_inbox WHERE status='PENDING'
                    → process → UPDATE SET status='PROCESSED'
[on startup]        drain any PENDING rows from last 24h
```

**Hidden risks:**

1. **Which DB?** The ops DB (`wt_ops_v2.db`) is the proposed target. But the background drainer in `main.py` ultimately writes to `watchtower_infra_events` in the hot DB. The inbox write (ops DB) and the enrichment write (hot DB) would be in different processes/DBs — this is correct architecture but adds cross-DB coordination.

2. **The inbox write is synchronous in the Flask request thread.** If the ops DB is locked when a treasury webhook arrives, the Flask handler blocks, the webhook endpoint hangs, and Helius may retry or timeout. This replaces one failure mode (crash) with another (slow webhook response). Mitigation: ops DB has far less contention than hot DB — this is acceptable.

3. **Startup replay window.** Proposed replay is "PENDING rows from last 24h." This means a 24h-stale treasury event would be replayed. Treasury outbound sessions have TTL (`SESSION_TTL_SEC`); replaying a 20h-old event may open a session that should be expired. The replay window should match the session TTL, not 24h.

**Is ops DB correct persistence location?**
Yes. The ops DB has a single writer concern (ws_cascade primarily), but the inbox write is from the Flask process — adding a second writer. However, the write pattern is a simple INSERT with no contention (one row per webhook event, infrequent). This is safe. The alternative (writing to hot DB) adds more contention to the already-saturated hot DB serializer queue.

**Should it be implemented before other durability work?**
Yes — on consequence grounds. But given the birth inbox is ~2 hours to fix and the treasury inbox is ~1 day, the birth fix should be done first and in parallel with treasury inbox design.

---

## Part 6: Updated Top 5 Projects (Current System)

Ranked strictly by risk-reduced ÷ implementation-effort.

---

### Project 1 — Fix Birth Drainer Consumed-Before-Task Bug + Register Helius Webhook

**Effort:** S (~2 hours)  
**Risk reduced:** Births go from fragile (Helius secondary path = 0% durable via webhook) to durable. The write-before-200 pattern already exists in `webhook_pumpfun_birth()` — it's the only handler in the system that does it correctly.

**What to do:**
1. Register Helius webhook for birth events pointing to `/api/webhook/pumpfun-birth` (Helius dashboard, ~1 hour)
2. Fix `drain_webhook_birth_queue`: replace `asyncio.create_task` with `await self.handle_birth(sig, [])` so the drainer only marks `consumed=1` after processing completes — or add a `processing=1` intermediate status

**Risk reduced ÷ effort:** Highest in the system. Infrastructure is already built.

---

### Project 2 — Treasury Webhook Inbox

**Effort:** M (~1 day)  
**Risk reduced:** Eliminates the highest-consequence permanent-loss vector. Treasury hits move from 1/10 to 9/10.

**What to do:**
1. Add `wt_treasury_webhook_inbox` table to `wt_ops_v2.db`
2. In `webhook_watchtower()` (main.py:33807): replace `_wt_infra_queue.put_nowait` with synchronous INSERT to inbox table, then return 200
3. Background drainer reads PENDING rows from inbox, processes (existing `_process_wt_infra_payload` logic), marks PROCESSED
4. On startup: replay PENDING rows from last N hours (where N = SESSION_TTL_SEC / 3600)

**Risk reduced ÷ effort:** Second highest. Consequence is a multiplier across all downstream detection.

---

### Project 3 — Fix `processing_migrations` Cross-Path Mutex Bug

**Effort:** XS (~1 hour)  
**Risk reduced:** Partially restores migration durability. Currently if the Helius path sets `processing_migrations.add(sig)` then its RPC fails, the PumpPortal path is blocked from attempting the same sig this session. A `finally: processing_migrations.discard(sig)` on RPC failure unblocks the second path.

**What to do:**
```python
# handle_migration, around line 9406:
self.processing_migrations.add(signature)
try:
    tx_data = await self._get_transaction_cached(signature)
    ...
except Exception:
    self.processing_migrations.discard(signature)   # ← add this
    raise
```

The second path (PumpPortal or reconciler) then has a live retry opportunity.

**Risk reduced ÷ effort:** Very high. One `finally` block provides meaningful improvement.

---

### Project 4 — Reimplement Migration Inbox (correctly this time)

**Effort:** M (~2 days)  
**Risk reduced:** Migrations move from 3/10 to 8/10. The original implementation (`edd61b4`) had a signature truncation bug. The fix is to validate signature length before inserting, and to use the full signature as the unique key.

**What to do:**
1. Keep `migration_inbox` table (already exists in DB, schema is correct)
2. In `listen_pumpswap_websocket` (line 9679) and `listen_pumpportal_websocket` (line 9913): before calling `handle_migration`, write `(signature, source, 'PENDING')` to `migration_inbox` — validate `len(signature) >= 80` before insert
3. Background drainer: reads PENDING rows, calls `handle_migration`, marks PROCESSED or RETRY on failure
4. Startup: replay PENDING/RETRY rows

The previous bug was a 16-char truncated signature being stored and then failing the `getTransaction` call in a loop. Fix: validate before inserting; if truncated, skip inbox and call `handle_migration` directly (it will fail gracefully with the delayed recheck path).

**Risk reduced ÷ effort:** High — addresses the core migration capture gap.

---

### Project 5 — Raise CATCHUP_SIG_LIMIT from 8 to 25

**Effort:** XS (~5 minutes — one env var)  
**Risk reduced:** Wrap-close detection and cascade launch catch-up scans look back only 8 sigs. Raising to 25 costs at most one extra RPC page on reconnect and covers high-velocity subprov wallets and longer outage windows.

**What to do:** In `supervisord.conf`, add `WS_CATCHUP_SIG_LIMIT="25"` to ws_cascade environment.

**Risk reduced ÷ effort:** Extremely high per minute of effort.

---

## Part 7: Brutal Assessment

**If WATCHTOWER crashed right now, which events could be permanently lost?**

---

**PERMANENTLY LOST — NO RECOVERY:**

1. **Any treasury webhook payload in `_wt_infra_queue` at crash time.**  
   Evidence: `_wt_infra_queue` is a `queue.Queue` in the gunicorn process heap. Contents are not persisted anywhere. Helius has already received 200. There is zero indication the event arrived. The `watchtower_infra_events` table has no entry. No subsequent process will know to look for it. **Permanently gone.**

2. **Any treasury webhook that arrived while the queue was at 2000 items.**  
   Evidence: `put_nowait` raises `Full`, which is caught by `except _queue_mod.Full: pass` at approximately line 33808. The exception is swallowed. No log, no DB record, no retry. **Permanently gone with no trace.**

3. **Any migration sig currently in the delayed 45-second recheck window (`delayed_mint_recheck`).**  
   Evidence: This is an `asyncio.create_task` that sleeps 45s then retries the RPC. If the process crashes during those 45 seconds, the task is gone, `processing_migrations` is gone, and neither reconciler nor the PumpPortal path will see the sig again (the PumpPortal WS doesn't redeliver). The only recovery is the migration reconciler — which only covers `39azUYFW…`-transiting migrations within 100 sigs. Direct-to-pool migrations in this window: **permanently gone.**

4. **Any birth that failed `_insert_bonding_curve_token` due to serializer exhaustion (queue_depth=60/60).**  
   Evidence: Exception handler is `log_print` only. `completed_launches.add(sig)` runs after the write succeeds — a failed write means the sig isn't in the dedup set. But PumpPortal doesn't redeliver. The Helius `logsSubscribe` path is a backstop only if the Helius WS is healthy AND the same birth comes through that path. There is no guaranteed replay. **Likely gone** unless the Helius path catches it within seconds.

5. **Any birth in `webhook_birth_queue` marked `consumed=1` before `handle_birth` completed.**  
   Evidence (INSUFFICIENT DATA on frequency): The drainer marks `consumed=1` in a committed transaction before calling `asyncio.create_task(handle_birth(...))`. The window is tiny (a few microseconds between the commit and the task dispatch). But it exists. A birth falling in this window is marked consumed and never processed. Recovery requires manual inspection of `webhook_birth_queue` — there's no automatic replay of consumed rows. Note: currently zero rows in `webhook_birth_queue` because no Helius birth webhook is registered, so this bug is theoretical until the webhook is configured.

---

**PROBABLY RECOVERABLE (within constraints):**

6. **Any wrap-close notification currently in the ws_cascade `inbox` queue.**  
   Evidence: `subprov_sweep_pass` runs every 6s. On reconnect, `catch_up_subprov` re-fetches the last 8 sigs. If the wrap-close sig is within the last 8 signatures on that subprov wallet, it will be replayed. **Recoverable for most cases; vulnerable for high-velocity wallets or long outages.**

7. **Any candidate CREATE notification currently in the ws_cascade `inbox` queue.**  
   Evidence: `catch_up_candidate` on reconnect re-scans last 8 sigs. Single-use creator wallets typically have very few txs. **Mostly recoverable** — but the `_seen` dedup-before-write bug means a crash mid-RPC leaves the sig blocked for the next session if the candidate wallet has emitted more than 8 txs before reconnect.

8. **Migrations transiting `39azUYFW…` in the last 100 signatures.**  
   Evidence: Reconciler covers this specifically. 120s cadence. **Recoverable within the 100-sig window.** Older than 100 sigs or direct-to-pool: not recoverable.

---

**VERIFIED WORKING (would survive a crash):**

9. **All `wt_candidate_websocket_watches` rows** — OPS DB, survive restart, drive re-subscription.
10. **All `wt_active_subprov_sessions` rows** — OPS DB, survive restart, drive WS session restoration.
11. **All `wt_watchtower_launches` rows** — OPS DB, survive restart, `INSERT OR IGNORE` idempotent.
12. **Treasury WS notifications** — `_handle_treasury_tx` writes `treasury_ws_record_notif` even on RPC failure. The notification is persisted regardless of whether the tx was fetched.
