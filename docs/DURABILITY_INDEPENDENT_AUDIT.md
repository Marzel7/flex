# WATCHTOWER Durability — Independent Audit
**Date:** 2026-06-19  
**Method:** Read actual code, attempt to falsify prior report findings. Every claim verified against source lines.

---

## Part 1: Challenge Every Score

---

### Treasury Hits — Prior score: 1/10

**Verification:** `main.py:33806–33810`
```python
_wt_infra_queue.put_nowait(payload)   # in-memory queue
return "ok", 200                       # 200 before any durable write
```
Queue is `queue.Queue(maxsize=2000)` at line 31977. Full → `except _queue_mod.Full: pass` — silent drop, no log, no DB record. No `wt_treasury_webhook_inbox` exists anywhere in the codebase or DB.

**Attempt to find a hidden recovery path:** None found. Helius returns 200 and does not retry. There is no second channel for treasury webhook delivery. The ws_cascade treasury WS subscription is a *parallel detection path* for real-time treasury outbounds, but it only covers the 12 confirmed-treasury wallets and operates independently — it is not a replay mechanism for missed webhooks.

**Is the consequence model overstated?** No. A missed treasury event means `start_session()` is never called for the funded subprov, so ws_cascade never opens an `accountSubscribe` for that subprov wallet, so all wrap-close events on that subprov are invisible. The multiplier effect is real: one missed webhook silences an entire provisioning chain.

**Verdict:** Score confirmed. **1/10 is correct.**  
**Confidence:** High — code is unambiguous.

---

### Migrations — Prior score: 3/10

**Claim 1: migration inbox reverted, ghost table.**  
Verification: `grep -rn "migration_inbox" src/` → zero results. DB has the table with 3 rows (PROCESSED, PROCESSED, RETRY-stuck). **CONFIRMED: ghost table.**

**Claim 2: processing_migrations mutex blocks second path on RPC failure.**  
Verification: `handle_migration` line 9406 adds to `processing_migrations` before any RPC. The `finally` block at line 9488:
```python
finally:
    if signature in self.completed_migrations:
        self.processing_migrations.discard(signature)
```
If an exception occurs BEFORE `completed_migrations.add(signature)` (line 9476), the `finally` condition is FALSE → `processing_migrations` retains the sig. The PumpPortal path (line 9913) and Helius path (line 9679) both check this guard before calling `handle_migration` — so the stuck sig blocks both live paths for the remainder of the session.

**Critical new finding: processing_migrations is session-scoped, not permanent.**  
`self.processing_migrations: Set[str] = set()` at line 795 — initialized fresh on every listener startup. A stuck sig is only stuck for the current session. On the next restart, the reconciler will re-discover it.

**Critical new finding: the reconciler is running despite `LISTENER_MIGRATION_RECONCILER_ENABLED=0`.**  
`run_listener.sh` sets `LISTENER_MIGRATION_RECONCILER_ENABLED=0`. But the reconciler loop (`_migration_reconciler_loop`) is launched unconditionally at line 907:
```python
asyncio.create_task(self._migration_reconciler_loop())
```
`_wait_for_launch_toggle("PUMPSWAP")` is a no-op (line 9515: `pass`). The loop body does not check `LISTENER_MIGRATION_RECONCILER_ENABLED`. **The reconciler is actively running every 120 seconds.** The env flag has no effect on this code.

**What the reconciler covers:** `_reconciler_unseen_sigs` queries `token_analysis WHERE migration_tx IN (...)` — checking against the persistent DB, not the in-memory sets. It fetches the last 100 sigs from `PUMPFUN_MIGRATION_ACCOUNT (39azUYFW…)` every 120s. Any migration that transits this authority and isn't in `token_analysis.migration_tx` gets re-processed via `handle_migration`.

**What the reconciler does NOT cover:** Direct-to-pool migrations (PumpSwap-pool route, no `39azUYFW…` touch). The prior report flagged this. It is confirmed.

**Revised score assessment:** The prior report scores migrations 3/10 assuming the reconciler is the only backstop AND that it's limited/unreliable. The reconciler is actually:
- Active (env flag is ignored)
- DB-backed dedup (persistent across restarts)
- 2-minute cadence on a low-volume account
- Correctly handles the race condition (WS mid-flight vs reconciler sweep)

For `39azUYFW…`-path migrations, the actual durability is better than 3/10. The permanent loss case is:
1. RPC returns null → delayed 45s recheck → process crash during 45s → LOST (even reconciler won't help if the 45s task dies before it writes)
2. Direct-to-pool migrations → no reconciler coverage at all

**Verdict:** Prior score is **underestimated for 39azUYFW path, accurate for direct-to-pool path.**  
**My score: 5/10** — better than reported because reconciler is active and DB-backed; lower than ideal because no durable intake and direct-to-pool has zero recovery.  
**Confidence:** High.

---

### Births — Prior score: 4/10

**Claim: silent drop on write failure.**  
Verified at `_insert_bonding_curve_token` exception handler (line ~5275):
```python
except Exception as e:
    log_print(f"[BIRTH] ⚠ Failed to insert ...: {e}", flush=True)
    return
```
And caller in `listen_pumpportal_websocket` (line ~9884):
```python
except Exception as e:
    log_print(f"[PUMPPORTAL] ⚠ Birth insert error ...: {e}", flush=True)
```
**CONFIRMED: silent drop, no queue, no retry.**

**Claim: consumed-before-task bug in drainer.**  
Verified at `drain_webhook_birth_queue` (lines 9943–9953):
```python
with conn:                                          # commits here
    conn.execute("UPDATE ... SET consumed = 1 ...")
conn.close()
for row in rows:
    asyncio.create_task(self.handle_birth(sig, []))  # task fires after commit
```
**CONFIRMED: consumed=1 is committed before handle_birth is dispatched.** A process crash between those two operations permanently loses the birth with no recovery path.

**Is this actually a high-frequency risk?** Currently zero rows enter `webhook_birth_queue` — the Helius birth webhook is not registered. The bug exists but the trigger path is inactive. The PumpPortal path (primary) is NOT protected by this queue; it's a separate path.

**Is the Helius birth logsSubscribe path active?** Yes — `listen_pumpfun_websocket` runs. It routes to `handle_birth(sig, [])` which calls `_get_transaction_cached(sig)` before any write. If RPC fails → bare `return` → birth lost. This path has no recovery mechanism.

**Verdict:** Score of 4/10 is reasonable but the primary risk driver is different than stated. The consumed-before-task bug matters only if Helius birth webhook is registered. The real current risk is the silent drop on DB write failure in the PumpPortal path (active, high frequency). **Score: 4/10 — correct number, partially wrong reasoning.**  
**Confidence:** Medium (risk profile correct, mechanism attribution partially off).

---

### Cascade Launches — Prior score: 5/10

**Claim: `_seen()` dedup fires before write.**  
Verified at line 394: `self._processed.add(key)` runs before `store.record_launch()` at line 622. **CONFIRMED.**

**But is this actually a permanent loss risk?**

Key distinction the prior report does not make clearly: `_processed` is **in-memory and session-scoped**. On restart, `_processed = set()` at line 370. `catch_up_candidate` then re-scans. The actual idempotency guarantee is `INSERT OR IGNORE` on `wt_watchtower_launches` (OPS DB, survives restarts). So the `_seen()` pre-write dedup creates a **within-session risk** not a permanent loss risk — IF the process restarts and the candidate is still in WATCHING state.

**When does it become permanent?** If `_handle_candidate_tx` calls `_get_tx(sig)` → RPC fails → returns `(None, None)` → `_seen` already set → sig blocked for session → process never restarts (long-running session). In practice, the listener restarts frequently (81 times observed). The catch-up scan on reconnect re-processes.

**CATCHUP_SIG_LIMIT=8:** Confirmed at line 68. For a single-use creator wallet (the WATCHTOWER pattern), the CREATE is typically the 1st or 2nd transaction. 8 is almost always sufficient. Risk is narrow.

**Verdict:** Prior score of 5/10 is slightly **pessimistic**. The `_seen` pre-write creates a within-session gap, not permanent loss in most scenarios. Catch-up on reconnect is a real backstop. **My score: 6/10.**  
**Confidence:** Medium.

---

### Wrap-Close Detection — Prior score: 6/10

**`_handle_subprov_tx`:** calls `_get_tx(sig)` before `open_candidate_watch`. RPC failure → returns `[]` → event gone from the WS notification path. BUT `subprov_sweep_pass` runs every 6s via `catch_up_subprov`, which re-fetches the last 8 sigs on all ACTIVE subprovs. This is a genuine recovery mechanism that runs continuously, not just on reconnect.

**Is the sweep backstop reliable?** For active subprovs (in WATCHING state, session open), yes. The sweep runs every 6 seconds. If the wrap-close is within the last 8 sigs when the sweep runs, it's recovered. The risk window is: wrap-close arrives, RPC fails, AND the subprov emits >8 more txs before the next sweep. On typical subprov wallets (wrap-close then wait), this is rare.

**Verdict:** Prior score of 6/10 is **reasonable, possibly conservative.** The sweep mechanism provides genuine continuous coverage. **My score: 6/10 — confirmed.**  
**Confidence:** High.

---

### Subprov Discovery — Prior score: 4/10

**The report chains this to treasury hit durability.** This is mostly correct but overstated — the ws_cascade path (`_handle_treasury_tx`) is a fully independent detection path that does NOT depend on the webhook queue. `_handle_treasury_tx` (line 464) writes `treasury_ws_record_notif` even on RPC failure (confirmed: lines 471–473 write on null tx). The operation_scheduler also provides periodic backward discovery.

The webhook path dependency is real but the ws_cascade WS path provides meaningful parallel coverage for confirmed treasuries.

**Verdict:** Prior score of 4/10 is **slightly pessimistic.** The ws_cascade WS path for the 12+ confirmed treasuries is a genuine parallel coverage layer. **My score: 5/10.**  
**Confidence:** Medium.

---

### Future Bound — Prior score: 2/10

**`LISTENER_PORTAL_VSOL_FLUSH_ENABLED=0`:** Confirmed in `run_listener.sh`. This flag IS checked by the listener (unlike `LISTENER_MIGRATION_RECONCILER_ENABLED`). `_flush_portal_vsol_periodic` is launched at line 904 but the flag gates actual flushing behavior. Currently `portal_vsol.json` is stale (April 2026).

**Verdict:** Score confirmed. **2/10 — parked, UX-only impact.**  
**Confidence:** High.

---

### Telemetry — Prior score: 3/10

**By design. No change warranted.**  
**Verdict: 3/10 confirmed.**

---

## Part 2: Migration Reality Check

**Is migration durability actually 3/10?**

**No. It is higher for the primary path.**

Evidence:

1. **Reconciler is running** (env flag `LISTENER_MIGRATION_RECONCILER_ENABLED=0` is ignored by the code — `_migration_reconciler_loop` launches unconditionally, flag is never read in `src/`).

2. **Reconciler dedup is DB-backed** — `_reconciler_unseen_sigs` queries `token_analysis.migration_tx`, a persistent column. This means the reconciler correctly identifies missed sigs even after restarts. The prior report implied in-memory dedup as the reconciler's dedup — that is wrong.

3. **Reconciler cadence is practical** — 120s interval, 100-sig limit on a low-volume authority account. The authority account (`39azUYFW…`) carries ONLY migrations, so the 100-sig window is effectively a sliding time window of recent migrations (at current migration rates of ~5/24h, 100 sigs covers weeks, not hours).

4. **processing_migrations stuck-sig is session-scoped** — when the listener restarts, both in-memory sets clear. The reconciler then picks up any sig that didn't reach `token_analysis.migration_tx`.

**What migration durability actually is:**

| Path | Durability | Reason |
|------|-----------|--------|
| 39azUYFW…-transiting migrations | **~7/10** | Reconciler provides genuine DB-backed recovery within 120s + restart recovery |
| Direct-to-pool (pAMMBay…) migrations | **2/10** | No reconciler coverage, no inbox, WS-only |
| 45s delayed recheck crash window | **4/10** | Narrow window; on restart the reconciler re-catches from DB |

**Blended score: ~5/10** (weighting by estimated path frequency — most migrations transit `39azUYFW…`).

**The report overstates migration risk for the primary path.**

---

## Part 3: Treasury Hits Audit — Attempt to Disprove

**Is there a hidden recovery path?**

Searched for: any secondary delivery mechanism, any logging of received-but-unprocessed webhooks, any Helius retry configuration, any audit table written BEFORE the 200 response.

**Found nothing.** The webhook handler (lines 33782–33818) does one thing: `put_nowait` then `return "ok", 200`. There is no write to any table before the 200. There is no mechanism to detect that a webhook arrived and wasn't processed.

**Are treasury events actually as critical as claimed?**

The consequence chain: treasury webhook → `_process_wt_infra_payload` → `watchtower_infra_events` INSERT → `start_session()` call for the funded subprov → ws_cascade subscribes that subprov.

**Is the ws_cascade treasury WS path a compensating control?** Partially. The ws_cascade monitors confirmed treasury wallets via `accountSubscribe`. If a treasury outbound is detected via WS (independent of the webhook), `_handle_treasury_tx` opens the subprov session directly. This means: for confirmed treasury wallets that are ALREADY subscribed in ws_cascade, a missed webhook has reduced impact — the WS path provides parallel coverage.

**But:** The webhook path covers ALL infrastructure wallets, not just confirmed treasuries. New or unconfirmed wallets only get detected via the webhook. The WS path is limited to the 12+ confirmed treasuries in `wt_confirmed_treasuries`. A missed webhook on an unconfirmed treasury address = permanent miss, no WS backstop.

**Are there event classes with worse durability?**

Direct-to-pool migrations: **2/10**, narrower but zero recovery.  
Births via Helius WS: **3/10**, RPC before write, no recovery mechanism.

These have lower consequence per event. Treasury hits have higher consequence per event. The combination of consequence × probability × zero recovery makes treasury hits the correct #1.

**Verdict: Treasury Hits at 1/10 is confirmed as the weakest link.** The ws_cascade WS path is a partial compensating control for confirmed treasuries but does not cover the full webhook scope.

---

## Part 4: Birth Queue Review

**Is the infrastructure truly present?**

`webhook_pumpfun_birth` in `main.py` — yes, writes sig to `webhook_birth_queue` BEFORE returning 200 (correct pattern). `drain_webhook_birth_queue` runs unconditionally. Table schema exists. This is confirmed infrastructure.

**Are there hidden blockers?**

1. **No Helius webhook registered.** Cannot be verified from code alone — requires checking Helius dashboard. The DB shows 0 rows, consistent with no webhook delivery. INSUFFICIENT EVIDENCE on exact registration status.

2. **Consumed-before-task bug** (confirmed real — see Part 1 Births section). Mark consumed inside `with conn:` commits before `create_task(handle_birth(...))`.

3. **handle_birth requires RPC** — the drainer calls `handle_birth(sig, [])` with empty logs, so `handle_birth` must call `_get_transaction_cached(sig)` to get the mint. If RPC fails after a sig is marked consumed, the birth is permanently lost. The inbox survives process crash but not RPC failure after drain.

**Is "80% built" accurate?**

Yes. The missing 20%:
- Helius webhook registration (external, ~1 hour)
- Fix consumed-before-task: use `await handle_birth(...)` instead of `create_task` (or add intermediate `processing=1` status)

**Effort estimate realistic?** Yes — ~2 hours of code + 1 hour Helius config.

**Would completing this materially improve durability?**

For Helius-webhook-delivered births: yes, from ~0% to ~85% durable (remaining gap: RPC failure after drain marks consumed).

For PumpPortal-delivered births (primary path): no improvement — the queue is only for Helius webhook births. PumpPortal births still have no inbox.

**Assessment:** The report says "80% built, highest ROI." This is true for the Helius birth webhook path but overstated as a system-wide improvement — PumpPortal births (the majority) remain fragile. The ROI is more limited than claimed.

---

## Part 5: Inbox Pattern Search (Receive → RPC → First Durable Write)

All remaining occurrences:

| # | File | Function | Event | RPC Before Write | First Durable Write | On RPC Failure | Recovery | Severity |
|---|------|----------|-------|-----------------|-------------------|----------------|----------|----------|
| 1 | `main.py` | `webhook_watchtower` | Helius treasury webhook | None (but queue non-durable) | `watchtower_infra_events` (after async drain) | N/A — queue loss, not RPC | None | **Critical** |
| 2 | `pumpfun_curve_listener.py` | `handle_migration` | logsSubscribe + PumpPortal migration | `_get_transaction_cached` | `token_analysis.migration_tx` | Stuck in `processing_migrations` this session; reconciler recovers on restart (39azUYFW path) | Reconciler (120s, DB-backed) | **High (primary path), Critical (direct-to-pool)** |
| 3 | `pumpfun_curve_listener.py` | `handle_birth` | Helius logsSubscribe birth | `_get_transaction_cached` | `token_analysis` | Bare `return`, event lost | None | **High** |
| 4 | `pumpfun_curve_listener.py` | `_insert_bonding_curve_token` | PumpPortal `tx_type=="create"` | None | `token_analysis` | Silent drop (log only) | None | **Medium** |
| 5 | `pumpfun_curve_listener.py` | `drain_webhook_birth_queue` | Helius birth webhook (via queue) | `handle_birth` → `_get_transaction_cached` | `token_analysis` | `consumed=1` before task → permanent loss | None | **Medium** |
| 6 | `ws_cascade.py` | `_handle_subprov_tx` | logsSubscribe wrap-close | `_get_tx(sig)` | `wt_candidate_websocket_watches` (OPS DB) | Returns `[]` silently | `subprov_sweep_pass` every 6s | **Medium** |
| 7 | `ws_cascade.py` | `_handle_candidate_tx` | logsSubscribe candidate CREATE | `_get_tx(sig)` | `wt_watchtower_launches` (OPS DB) | Returns `(None, None)` | `catch_up_candidate` on reconnect | **Medium** |
| 8 | `ws_cascade.py` | `_handle_treasury_tx` | accountSubscribe treasury | `_get_tx(sig)` | `wt_treasury_ws_usage` (OPS DB) | **Writes notification even on null tx** | Best-behaved path | **Low** |

**#8 is the only path that follows the correct pattern: it persists the notification regardless of RPC result.** All other paths share the anti-pattern.

---

## Part 6: Fresh Risk Ranking

Built independently from code review, not from prior reports.

| Rank | Risk | Consequence | Probability | Recovery | Score |
|------|------|------------|------------|---------|-------|
| 1 | **Treasury webhook queue loss** (in-memory queue between 200 and drain) | 5 — multiplier failure | 5 — every restart, queue-full | 0 — Helius no-retry on 200 | **0/10** |
| 2 | **Direct-to-pool migration miss** (no 39azUYFW touch, no reconciler) | 5 — migration permanently missed | 3 — subset of all migrations | 0 — no coverage | **2/10** |
| 3 | **PumpPortal birth DB write failure** (silent drop in _insert_bonding_curve_token) | 3 — token never in system | 4 — write serializer at 60/60 capacity | 1 — Helius logsSubscribe partial backstop | **3/10** |
| 4 | **Helius birth logsSubscribe RPC failure** (getTransaction before first write) | 3 | 3 | 0 — no retry, no queue | **4/10** |
| 5 | **processing_migrations mutex stuck** (exception leaves sig in set, blocks both live paths this session) | 4 — both live migration paths blocked this session | 3 — RPC failures are common | 3 — reconciler re-catches on next sweep | **5/10** |
| 6 | **Cascade launch _seen pre-write** (RPC fail + within-session loss) | 5 — WATCHTOWER detection gap | 2 — narrow window, low event frequency | 4 — catch_up_candidate on reconnect | **5/10** |
| 7 | **39azUYFW migration WS drop** (reconnect window) | 4 | 3 | 5 — reconciler DB-backed, 120s | **6/10** |
| 8 | **Birth queue consumed-before-task** (mark consumed before handle_birth completes) | 3 | 2 — path inactive (no Helius webhook) | 0 — consumed rows not replayed | **6/10** |
| 9 | **Wrap-close RPC failure** (no candidate watch opened) | 4 | 2 | 5 — subprov_sweep every 6s | **7/10** |
| 10 | **Treasury WS RPC failure** (_handle_treasury_tx) | 3 | 2 | 5 — writes notification regardless | **8/10** |

---

## Part 7: What Would You Actually Build Next?

Ranked by risk-reduction ÷ engineering effort:

---

### 1. Treasury Webhook Inbox — Risk: CRITICAL / Effort: M

**Why this first:** Highest consequence (multiplier failure), zero existing recovery, in-memory queue is the only barrier. The ws_cascade WS path covers confirmed treasuries but the webhook is the only coverage for unconfirmed infrastructure wallets.

**Implementation:**
- Add `wt_treasury_webhook_inbox (received_at, payload TEXT, status)` to `wt_ops_v2.db`
- In `webhook_watchtower()`: replace `_wt_infra_queue.put_nowait(payload)` with synchronous INSERT to ops DB, then return 200
- Background drainer reads PENDING rows, calls existing `_process_wt_infra_payload`, marks PROCESSED
- On startup: replay PENDING rows within SESSION_TTL_SEC window

**Hidden risk:** The synchronous ops DB INSERT is in the Flask request thread. If ops DB is locked, the webhook handler blocks and Helius may see a slow response. Mitigation: ops DB has minimal write contention (separate from hot DB). Acceptable risk.

**Expected improvement:** Treasury hits: 1/10 → 8/10.  
**Prerequisites:** None.  
**Effort:** ~1 day.

---

### 2. Fix `processing_migrations` Stuck-Sig Bug — Risk: HIGH / Effort: XS

**Why second:** One `finally` block, 30 minutes of work, meaningfully improves migration durability. The stuck-sig problem collapses two live migration paths into one within a session. Fixing it restores the redundancy that was designed in.

**Implementation:**
```python
# handle_migration, currently:
finally:
    if signature in self.completed_migrations:
        self.processing_migrations.discard(signature)

# Fix:
finally:
    self.processing_migrations.discard(signature)  # always discard; completed_migrations is the success gate
```
This allows the PumpPortal path to retry a sig that the Helius path failed on, and vice versa.

**Expected improvement:** Migration durability on the `39azUYFW` path: 5/10 → 7/10 (reconciler still handles misses; fix removes the within-session block).  
**Prerequisites:** None.  
**Effort:** 30 minutes.

---

### 3. Reimplement Migration Inbox (correctly) — Risk: HIGH / Effort: M

**Why third:** The direct-to-pool migration gap (rank #2 in risk ranking) has zero existing recovery. The previous implementation failed due to a signature truncation bug — the fix is to validate `len(signature) >= 80` before inserting.

**Implementation:**
- Reuse `migration_inbox` table (already exists, correct schema)
- In `listen_pumpswap_websocket` (line 9679) and `listen_pumpportal_websocket` (line 9913): before `asyncio.create_task(handle_migration(...))`, write `(signature, source, 'PENDING')` to inbox — only if `len(signature) >= 80`
- Background worker drains PENDING rows via `handle_migration`
- On startup: replay PENDING/RETRY rows

**Expected improvement:** All migration paths (including direct-to-pool): 5/10 → 8/10.  
**Prerequisites:** `processing_migrations` bug fix (Project 2) should go in first so the inbox drain doesn't hit the same mutex bug.  
**Effort:** ~2 days.

---

## Part 8: Brutal Conclusion

**What is the most likely way WATCHTOWER still loses an important event today?**

**A treasury webhook payload in `_wt_infra_queue` at the moment the gunicorn API process restarts.**

Evidence:
- The API process (gunicorn) was observed with uptime 0:24:26 during this session — it restarted recently. 81 listener restarts also observed. These are not rare events.
- `_wt_infra_queue` is a pure in-memory `queue.Queue`. It does not survive any process exit.
- Helius has already received 200 and moved on.
- There is no `wt_treasury_webhook_inbox`, no backup table, no audit log that the webhook arrived.
- The background drain thread may have a 5-second polling gap; any webhook that arrives and sits in the queue while the process restarts is gone.

**Why this matters more than a migration miss:**  
A missed treasury event doesn't just lose one signal — it silences all detection downstream of that treasury's provisioning chain for the duration of the missed session window. The operation_scheduler may re-discover the subprov via backward walk within 15 minutes, but by then the subprov's INSTANT-mode launch has already happened. The detection window is closed permanently.

**Second most likely:** A direct-to-pool migration arriving during a PumpPortal WS reconnect window. The reconciler does not cover this path. If both the Helius `logsSubscribe` and PumpPortal WS are in reconnect at the same moment (observed during the OOM period), the migration is permanently unrecorded. This is the cleanest permanent loss scenario in the system — no recovery, no retry, no inbox.

**What would give false confidence:** Seeing the migration reconciler log line `[RECONCILER] ✅ Migration reconciler started` and assuming migrations are safe. The reconciler only covers `39azUYFW…`-path migrations. Direct-to-pool migrations are invisible to it. The log line does not distinguish between these cases.
