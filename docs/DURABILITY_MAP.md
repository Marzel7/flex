# WATCHTOWER Durability Map (2026-06-19)

**Audit question:** *What events can permanently disappear if the process crashes at the wrong moment?*

**Method:** Traced each stream from external event arrival → first durable write. Measured synchronous work before that write. Identified in-memory structures that don't survive restart.

Legend:
- **CAPTURE SAFE** — event is durably written before any enrichment
- **CAPTURE FRAGILE** — event can be permanently lost before first write
- **PARTIALLY DURABLE** — some paths durable, some not

---

## Stream 1: BIRTHS

**Classification: CAPTURE FRAGILE**

| Step | What happens | Durable? |
|------|-------------|----------|
| PumpPortal WS delivers `tx_type=="create"` | Parsed in-process | No |
| `self._portal_vsol[mint] = {...}` | In-memory dict update | No |
| `await _insert_bonding_curve_token(...)` | First attempt to write | — |
| `INSERT INTO token_analysis` (live DB) | **First durable write** | Yes |

**Synchronous RPC before first write:** NONE (PumpPortal delivers mint, creator, symbol, sig inline — no `getTransaction` needed).

**DB lock exposure:** The write goes through `managed_db_connect` → write serializer. If `busy_timeout` exhausts (~10-20s), the exception is caught and the birth is **silently dropped**. The `completed_launches.add(sig)` runs AFTER the write, so a dropped birth does not enter the dedup set — but there is no replay path either (PumpPortal WS doesn't redeliver).

**Restart loss:** The entire PumpPortal WS connection tears down. Births that arrived between last write and crash are gone. The listener re-seeds trade subscriptions from `token_analysis` on reconnect but does not re-request missed birth events.

**Fallback path:** A parallel Helius `logsSubscribe` on the pump.fun program (`listen_pumpfun_websocket`) also fires on new token creation, but this path **does** require a `getTransaction` RPC before writing. If the PumpPortal WS is down, the Helius path is a partial backstop — but the Helius path has its own failure modes.

**`webhook_birth_queue`:** Dead. The table exists and a drainer is running, but nothing writes to it. Zero rows ever inserted. The drainer is wasted CPU.

---

## Stream 2: MIGRATIONS

**Classification: CAPTURE FRAGILE** (both primary paths)

### Path 2a — Helius `logsSubscribe` on `39azUYFW…`

| Step | What happens | Durable? |
|------|-------------|----------|
| `logsNotification` arrives | Signature + logs in-memory | No |
| `asyncio.create_task(handle_migration(sig, logs))` | Task scheduled | No |
| **`getTransaction(sig)`** | **Synchronous RPC call** | No — still not durable |
| Extract mint from tx | Parse in-memory | No |
| `INSERT token_analysis SET migrated_at=...` | **First durable write** | Yes |

**Synchronous RPC before first write:** YES — one `getTransaction` (1 Helius credit). If RPC times out or returns null, the migration is dropped without a write. The in-memory `processing_migrations` set is populated at entry to `handle_migration`, but if the process crashes during the RPC call, that set is gone and the reconciler becomes the only recovery path.

**DB lock exposure:** The migration write path does NOT use `_wt_exec` retry logic — it's a direct `conn.execute` + `conn.commit()`. A "database is locked" exception at the commit point raises, is caught at `handle_migration` level, logs, and discards. No retry. The `completed_migrations` set is populated after the write succeeds.

**Reconciler backstop:** `_migration_reconciler_loop` re-queries `getSignaturesForAddress` on `39azUYFW…` every 120s, limit 100 sigs. Anything in that window that isn't in `token_analysis.migration_tx` gets re-processed. This provides eventual durability for the Helius path — **but only for the last 100 signatures on the authority account.**

### Path 2b — PumpPortal `subscribeMigration`

Identical to 2a from the `handle_migration(sig, [])` call onward. Same RPC-before-write, same DB lock exposure, same dedup via `completed_migrations`. No reconciler backstop for PumpPortal-discovered migrations unless they also appear on `39azUYFW…`.

**Reconciler coverage gap:** The reconciler only covers migrations transiting `39azUYFW…`. Migrations routed directly to the PumpSwap pool (`pAMMBay…`) without touching that authority are permanently missed if both live WS paths drop them. This gap is confirmed in project memory (`migration-coverage-gap-pumpswap.md`).

---

## Stream 3: WRAP-CLOSE CREATOR DETECTION

**Classification: PARTIALLY DURABLE**

| Step | What happens | Durable? |
|------|-------------|----------|
| `logsNotification` for subscribed subprov | → `inbox.put_nowait(raw)` (in-memory queue, maxsize=1000) | No |
| `_processor` picks up the message | `_on_message(kind="subprov", wallet, sig)` | No |
| **`_get_tx(sig)` — `getTransaction` RPC** | Fetches full tx | No |
| `extract_close_destinations(tx)` | Parses wrap-close | No |
| `store.open_candidate_watch(conn, ...)` | `INSERT INTO wt_candidate_websocket_watches` (OPS DB) | **Yes** |

**Synchronous RPC before first write:** YES — one `getTransaction`.

**DB lock exposure:** `open_candidate_watch` uses the write serializer with `busy_timeout=20000`. If it times out, the candidate watch is lost. The `subprov_sweep_pass` re-runs every 6s and calls `catch_up_subprov` on all ACTIVE subprovs, which re-fetches recent signatures and retries — **this is the recovery mechanism**.

**Restart resilience:** `wt_active_subprov_sessions` (OPS DB) is the durable registry of active subscriptions. On reconnect, `resync_subscriptions` re-subscribes all sessions from this table and calls `catch_up_subprov` on each. The catchup fetches the last `CATCHUP_SIG_LIMIT` signatures and re-processes any wrap-close that wasn't already captured in `wt_candidate_websocket_watches`. This makes **short-outage recovery mostly reliable**, but the catchup is bounded — a long outage may fall outside the signature window.

**In-memory `_subprov_seen` dedup set:** Reset on restart. The OPS DB `wt_candidate_websocket_watches` (INSERT OR IGNORE) is the idempotency guarantee across restarts.

---

## Stream 4: CASCADE LAUNCHES

**Classification: CAPTURE FRAGILE** (with partial restart recovery)

| Step | What happens | Durable? |
|------|-------------|----------|
| `logsNotification` for subscribed candidate | → `inbox.put_nowait(raw)` (in-memory, maxsize=1000) | No |
| `_seen(candidate, sig)` dedup check | `_processed` set updated (in-memory) | No |
| DB idempotency check | `SELECT FROM wt_watchtower_launches` (read) | No |
| `await _ato_thread(_handle_candidate_tx, ...)` | Off-loop thread starts | No |
| **`_get_tx(sig)` — `getTransaction` RPC** | Fetches CREATE tx | No |
| `_tx_is_create(tx)` | Parse CREATE instruction | No |
| `store.record_launch(conn, ...)` | `INSERT INTO wt_watchtower_launches` (OPS DB) | **Yes** |

**Synchronous RPC before first write:** YES — one `getTransaction`.

**Critical dedup sequence problem:** `_seen(candidate, sig)` adds to `_processed` BEFORE the DB write. If the `getTransaction` RPC fails, or the `record_launch` DB write fails, the sig is in `_processed` and will NOT be retried this session. The launch is permanently dropped this session — only recovering on restart when `_processed` is cleared and `catch_up_candidate` re-scans.

**Restart recovery:** On reconnect, `resync_subscriptions` calls `catch_up_candidate` for any candidate still in WATCHING state in OPS DB. This re-scans recent sigs and replays. However, if the crash happened AFTER `record_launch` set the state to FIRED_CREATE, the candidate won't be re-subscribed — which is correct (launch was recorded). If it crashed BEFORE the write, the candidate stays WATCHING and catchup will re-process.

**DB lock exposure:** `record_launch` uses the write serializer. If it times out, the exception propagates. The candidate's WATCHING state in OPS DB is unchanged — it will be recovered on next sweep/reconnect. So a DB-lock failure here is recoverable, unlike births.

---

## Stream 5: TREASURY HITS

**Classification: CAPTURE FRAGILE — WORST CASE**

| Step | What happens | Durable? |
|------|-------------|----------|
| Helius HTTP POST arrives at `/api/webhook/watchtower` | Flask handler receives | No |
| `_wt_infra_queue.put_nowait(payload)` | **Return HTTP 200 to Helius** | **No — payload in memory only** |
| Background thread drains queue | `_process_wt_infra_payload(payload)` | No |
| RPC calls? | NONE — Helius enhanced payload is inline | No |
| `INSERT INTO watchtower_infra_events` | **First durable write** (live DB) | Yes |

**The gap:** The Flask handler returns 200 to Helius before any write happens. The payload sits only in `_wt_infra_queue` (in-memory `queue.Queue`, maxsize=2000). If the process crashes, restarts, or is OOM-killed between the 200 response and the background drain write, the treasury event is **permanently lost**. Helius does not retry on 200 responses.

**Synchronous RPC before first write:** NONE — the biggest win of this stream. The data is inline.

**DB lock exposure:** The background drainer uses `_wt_exec` with 12 retries (~23s total backoff). Individual row failures are logged and skipped. If all 12 retries fail on the `watchtower_infra_events` INSERT, the treasury event is dropped and the queue item is consumed (not re-queued). The queue item is consumed even on partial failure (only individual rows within it are retried, not the whole payload).

**Queue full behavior:** `put_nowait` — if the queue hits 2000 items (backed-up processing), the exception is caught and the webhook payload is dropped entirely with no record. This is a silent drop.

**Downstream consequence:** A missed treasury hit means:
- The treasury's outbound is not recorded
- `start_session` is not called → the subprov doesn't get a WS session opened
- The ws_cascade never subscribes to that subprov
- Any launches via that subprov are missed entirely

This is the highest-consequence capture failure in the system.

---

## Stream 6: SUBPROV DISCOVERY

**Classification: PARTIALLY DURABLE**

Three discovery paths with different durability profiles:

| Path | First durable write | RPC before write | Crash-safe? |
|------|-------------------|-----------------|------------|
| Webhook treasury outbound → `start_session` | `wt_active_subprov_sessions` (OPS DB) | None (after treasury hit write) | Only as durable as Stream 5 — inherits treasury hit fragility |
| ws_cascade treasury WS → `_handle_treasury_tx` → `start_session` | `wt_active_subprov_sessions` (OPS DB) | YES — `_get_tx(sig)` | No — in-memory inbox queue before RPC |
| operation_scheduler batch walk | `wt_discovered_subprovs` (OPS DB) | YES — multiple RPC calls | Yes — periodic batch, re-runs |

**The key subprov durability boundary is `wt_active_subprov_sessions`.** Once written there, ws_cascade will re-subscribe on any reconnect. The gap is the path TO that write — both the webhook path (in-memory queue between 200 and write) and the WS cascade path (in-memory inbox queue, then RPC, then write) have failure windows.

---

## Stream 7: FUTURE BOUND TRACKING (vSol)

**Classification: CAPTURE FRAGILE** (but operationally acceptable)

| Step | What happens | Durable? |
|------|-------------|----------|
| PumpPortal `buy`/`sell` event arrives | `self._portal_vsol[mint] = {...}` | No — in-memory |
| Every 5s: `_flush_portal_vsol_periodic` | Overwrites `portal_vsol.json` | **Yes — file on disk** |

**Synchronous RPC before write:** NONE.

**Durability window:** Up to 5 seconds of vSol state is lost on crash. The file is overwritten atomically (Python `json.dumps` + `open('w')` — NOT atomic on macOS, potential partial-write race). On restart, `_portal_vsol` is NOT re-loaded from `portal_vsol.json` — the dict starts empty. Re-seeding via `subscribeTokenTrade` for known bonding-curve tokens happens at PumpPortal WS connect time, but vSol values are unknown until new trade events arrive.

**DB write:** There is no DB table for near-migration threshold state. The "near migration" decision is made in-memory from `_portal_vsol` and tracked via `tracked_trade_mints` (in-memory set, lost on WS reconnect). **No durable record of "this token is near migration" exists anywhere.**

**`LISTENER_PORTAL_VSOL_FLUSH_ENABLED=0`:** Currently parked. The JSON file flush is disabled. The `portal_vsol.json` file is stale (pre-April data). The Future Bound panel shows zero tokens as a result.

---

## Stream 8: WATCHTOWER TELEMETRY

**Classification: CAPTURE FRAGILE** (by design — acceptable)

| Step | What happens | Durable? |
|------|-------------|----------|
| `emit_event(type, data)` called in ws_cascade | `_event_q.put_nowait(...)` (in-memory queue, maxsize=5000) | No |
| If queue full: | `except queue.Full: pass` | **Silently dropped** |
| Background `_event_writer_loop` drains | Writes to `watchtower_events` (OPS DB) | Yes |

**Synchronous RPC before write:** NONE.

**DB lock exposure:** Raw connection (bypasses serializer) with `busy_timeout=15000`, 5 retries. After 5 failures: logged and discarded. No further attempt.

**Design intent:** Telemetry is explicitly fire-and-forget. Loss is documented and acceptable. This is the ONLY stream where CAPTURE FRAGILE is architecturally correct.

---

## Top 10 Places Where Events Can Permanently Disappear

Ranked by consequence (permanent loss of intelligence × frequency × no recovery path).

### #1 — Treasury Hit: HTTP 200 Before Any Write
**Stream:** Treasury Hits  
**Mechanism:** `_wt_infra_queue.put_nowait(payload)` → return 200 → background drain writes DB. Any OOM kill, supervisor restart, or gunicorn timeout between these points permanently loses the treasury event. Helius does not retry on 200.  
**Consequence:** Treasury missed → no subprov session opened → ws_cascade never subscribes → all launches via that subprov missed forever.  
**Recovery:** None. The Helius webhook won't re-fire. The operation_scheduler may discover the subprov weeks later via backward walk, but the real-time window is gone.  
**Frequency risk:** High — the API process has been OOM-killed and restarted repeatedly.

### #2 — Migration: `getTransaction` RPC Failure Before First Write
**Stream:** Migrations  
**Mechanism:** Both Helius `logsSubscribe` and PumpPortal `subscribeMigration` call `getTransaction` before writing. If the RPC returns null (Helius sometimes returns null for very recent sigs before they're indexed at `confirmed` commitment) or times out, `handle_migration` catches the exception and discards.  
**Consequence:** Migration is permanently missed unless the reconciler catches it.  
**Recovery:** Reconciler runs every 120s, limit 100 sigs on `39azUYFW…`. If the miss is within the 100-sig window AND transits that account, it recovers. PumpSwap-direct migrations: never recovered.  
**Frequency risk:** Medium-high — known to cause the `NO_POOL` pattern on recent migrations.

### #3 — Migration: DB Write Failure After Successful RPC
**Stream:** Migrations  
**Mechanism:** `handle_migration` uses a raw `conn.execute` + `conn.commit()` (not `_wt_exec` retry). A single "database is locked" at commit time drops the migration. `completed_migrations` is not updated. `processing_migrations` still has the sig — meaning the same sig hitting via the OTHER path (PumpPortal or Helius) will skip processing because `processing_migrations` guard fires.  
**Consequence:** Both live paths skip the sig. The reconciler may recover it, but only within the 100-sig window and only for `39azUYFW…` migrations.  
**Note:** `processing_migrations` guard prevents the second path from even attempting the write — this is a bug. If Helius path fails and sets `processing_migrations`, PumpPortal path won't try.

### #4 — Birth: DB Write Failure (Silent Drop)
**Stream:** Births  
**Mechanism:** `_insert_bonding_curve_token` catches exceptions and returns silently. `completed_launches.add(sig)` runs AFTER the write — so a failed birth write leaves no dedup record. But PumpPortal doesn't redeliver, and the Helius birth-logsSubscribe path is a backstop only if the Helius WS is healthy.  
**Consequence:** Token never enters `token_analysis`. It will never appear in any feed, never be enriched, never be tracked for migration.  
**Recovery:** None if both PumpPortal and Helius paths fail. The token simply doesn't exist in the system.  
**Frequency risk:** Medium — write serializer queue currently at 60/60 (maxed). DB lock on birth writes is happening now.

### #5 — Cascade Launch: `_seen()` Dedup Before Write
**Stream:** Cascade Launches  
**Mechanism:** `_seen(candidate, sig)` adds to `_processed` BEFORE `getTransaction` RPC and BEFORE the DB write. If the RPC fails or the DB write fails, the sig is in `_processed` and won't be retried this session.  
**Consequence:** The launch detection is dropped for the current process lifetime.  
**Recovery:** On restart, `_processed` is cleared and `catch_up_candidate` re-scans. If the candidate is still in WATCHING state (which requires the DB write to have NOT happened), recovery is likely. This is a narrow window — usually recoverable, but not guaranteed.

### #6 — Subprov WS Discovery: In-Memory Inbox Queue
**Stream:** Subprov Discovery (ws_cascade path)  
**Mechanism:** Treasury WS notifications go to `inbox.put_nowait(raw)` (in-memory, maxsize=1000). If ws_cascade crashes with items in the inbox, those treasury outbound sigs are lost.  
**Consequence:** The specific treasury outbound sig is lost. The subprov might still be discovered if the same treasury funds another subprov later, or if the operation_scheduler walks the lineage. But the real-time session-open window for THIS funding event is gone.  
**Recovery:** Partial — operation_scheduler walks backward but on a 15-min cadence, not real-time.

### #7 — Wrap-Close Detection: WS Inbox Queue + RPC Gap
**Stream:** Wrap-Close Creator Detection  
**Mechanism:** `inbox.put_nowait(raw)` in ws_cascade, then `_get_tx(sig)`. If ws_cascade crashes during the RPC call, the sig is lost from the inbox.  
**Recovery:** `subprov_sweep_pass` (every 6s) and `catch_up_subprov` on reconnect re-fetch recent sigs and replay. This is a genuine backstop for short outages. For long outages (>CATCHUP_SIG_LIMIT sigs on the subprov), some wrap-closes may be outside the scan window.  
**Net risk:** Low-medium — the sweep mechanism is real and tested.

### #8 — Migration: PumpSwap-Direct (Reconciler Blind Spot)
**Stream:** Migrations  
**Mechanism:** Migrations that go directly to the PumpSwap pool (`pAMMBay…`) without touching `39azUYFW…` are NOT covered by the reconciler. If both PumpPortal `subscribeMigration` AND Helius `pumpswap_logs` miss them (WS down, reconnect window), no recovery path exists.  
**Evidence:** Documented in project memory. The `aeFSni25` and `Hepc74` cases were confirmed examples.  
**Recovery:** None.

### #9 — Future Bound: No Durable Near-Migration State
**Stream:** Future Bound  
**Mechanism:** `tracked_trade_mints` and `_portal_vsol` are both in-memory. A token that crosses the 60 SOL vSol threshold during a PumpPortal WS reconnect window will not trigger the threshold action. There is no DB record of "this token is near migration" — if the listener restarts while a token is at 80 SOL vSol, the token is re-seeded for trade tracking but its current vSol is unknown until the next trade event arrives.  
**Consequence:** Near-migration alert missed; token migrates without being in Future Bound.  
**Risk level:** Medium — mostly affects UX, not core detection.

### #10 — Treasury Hit: Queue Full Silent Drop
**Stream:** Treasury Hits  
**Mechanism:** `_wt_infra_queue.put_nowait(payload)` with `maxsize=2000`. If the background processor is backed up (DB lock storm), the queue fills and new webhooks are dropped with `except Full: pass`. No record, no retry, no 5xx to Helius.  
**Consequence:** Same as #1 — treasury event permanently lost.  
**Risk level:** Medium — current write contention makes this realistically possible during a DB lock storm.

---

## Durability Classification Summary

| Stream | Classification | RPC Before Write | Recovery Path | Worst Case |
|--------|---------------|-----------------|---------------|------------|
| Births (PumpPortal) | **CAPTURE FRAGILE** | None | Helius logsSubscribe (partial) | Birth permanently lost |
| Births (Helius) | **CAPTURE FRAGILE** | YES — getTransaction | None | Birth permanently lost |
| Migrations (Helius) | **CAPTURE FRAGILE** | YES — getTransaction | Reconciler (partial, windowed) | Migration lost if outside 100-sig window |
| Migrations (PumpPortal) | **CAPTURE FRAGILE** | YES — getTransaction | Reconciler (partial, `39azUYFW` only) | Direct-to-pool migrations: no recovery |
| Wrap-Close Detection | **PARTIALLY DURABLE** | YES — getTransaction | subprov_sweep every 6s | Long-outage wrap-closes outside catchup window |
| Cascade Launches | **PARTIALLY DURABLE** | YES — getTransaction | catch_up_candidate on reconnect | `_seen()` pre-dedup blocks retry same session |
| Treasury Hits | **CAPTURE FRAGILE** | None | None | Treasury permanently lost, all downstream missed |
| Subprov Discovery | **PARTIALLY DURABLE** | None (webhook) / YES (WS) | operation_scheduler backward walk | Real-time session window gone |
| Future Bound (vSol) | **CAPTURE FRAGILE** | None | Trade re-seed on reconnect | Near-migration state lost on restart |
| Telemetry | **CAPTURE FRAGILE** | None | None (by design) | Telemetry dropped — acceptable |

**One system-wide pattern explains most fragility:**
Events arrive → synchronous `getTransaction` RPC → DB write.
Every failure in that sequence discards the event.
The RPC call is the most common failure point under load, not the DB write.
