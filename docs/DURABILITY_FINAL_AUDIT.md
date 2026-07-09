# WATCHTOWER Durability — Final Independent Audit
**Date:** 2026-06-19  
**Method:** Attack the prior audit. Verify every claim against current code and live data. Falsify where wrong.

---

## Upfront: What the Prior Audit Got Wrong

Before the stream-by-stream review, one finding overrides the entire priority recommendation:

**Sessions opened via the webhook path expire in 10 minutes, not 2 hours.**

`main.py:34717`:
```python
_ttl = int(os.getenv("WS_SESSION_TTL_SEC", "600"))
```

`ws_cascade.py:50`:
```python
SESSION_TTL_SEC = int(os.environ.get("WS_SESSION_TTL_SEC", "7200"))
```

The supervisord config sets `WS_SESSION_TTL_SEC="7200"` for the `ws_cascade` process. But the Flask API process (gunicorn) has its own env and its `WS_SESSION_TTL_SEC` defaults to **600** — it is not set in the `watchtower_api` supervisord block. Every session opened via the HTTP webhook expires in 10 minutes. Live evidence: the two most recent sessions show `TTL=600s`. 258 sessions are EXPIRED. The 2-hour window was designed but broken at the webhook path.

This means the treasury-webhook-to-launch pipeline is:
1. Treasury outbound arrives via webhook
2. `start_session(ttl=600s)` opens the subprov
3. ws_cascade subscribes the subprov for 10 minutes
4. Session expires → subscription torn down
5. Creator wallet (typically funded within 150s of the treasury outbound) is beyond the 10-minute window in STAGED mode cases → **candidate watch expires before creator creates**

This is not a durability problem. It is a **configuration bug that makes the entire detection pipeline ineffective** for the webhook-opened path. The prior audit did not find this.

---

## Part 1: Attack Every Score

---

### Treasury Hits — Prior Score: 1/10

**The score is correct for the wrong reason.**

The audit claims treasury hits are 1/10 because the in-memory queue loses events on crash. This is true. But the more operationally significant problem is the TTL mismatch: sessions opened via webhook expire in 600s, making the treasury-webhook → subprov-session → candidate-watch chain ineffective even when the webhook arrives correctly.

**Does ws_cascade compensate for webhook misses?**

Partially. `_handle_treasury_tx` calls `start_session()` with `SESSION_TTL_SEC=7200` (the cascade's env value). Sessions opened by the cascade live 2 hours. For confirmed treasuries already subscribed in ws_cascade, a missed webhook has limited impact — the cascade opens the session at full TTL.

**But:** The cascade only watches confirmed treasury wallets. The webhook covers ALL infrastructure wallets. New or unconfirmed treasury wallets are webhook-only. 78 distinct infra addresses have been seen via webhook; only 17 are confirmed treasuries in `wt_confirmed_treasuries`. The other 61 have no ws_cascade coverage.

**Measured state:** 19 webhook events in last 24h. 2 recent sessions (both 600s TTL). Zero active sessions right now.

**Revised score:** The in-memory queue issue (crash loss) + 600s TTL bug + 61/78 unconfirmed addresses = compound failure. **Score: 1/10 confirmed, but for compounded reasons the audit didn't fully identify.**

---

### Migrations — Prior Score: 5/10 (upgraded from 3/10)

**The upgrade is partially justified but the coverage split matters.**

**Measured migration sources (all-time):**
- `NULL` (pre-column, unclassifiable): 8,796
- `WEBSOCKET`: 14
- `RECONCILER`: 5
- `VALIDATION_FULL_INBOX`: 1
- `PUMPPORTAL_INBOX`: 1

Of 8,817 total migrations, only 21 have a classified source. The 8,796 NULLs are from before the `migration_source` column existed — they cannot be used to assess path coverage split. **INSUFFICIENT DATA** on the historical 39azUYFW vs direct-to-pool ratio.

**What is confirmed:**
- 747 migrations recorded in the last 7 days
- Reconciler has recovered 0 migrations (log grep returns 0 `♻ RECOVERED` entries)
- Both the Helius `logsSubscribe` and the reconciler subscribe to `39azUYFW…` only — `pAMMBay…` has no subscriber

**The reconciler finding from the prior audit:** The reconciler IS running (confirmed: `LISTENER_MIGRATION_RECONCILER_ENABLED=0` flag is ignored by the code). But 0 reconciler recoveries in the logs means either: (a) the WS is catching everything on the `39azUYFW` path so there's nothing to recover, or (b) the reconciler is running but finding nothing new. Both are plausible — the `39azUYFW` path appears to be working.

**The upgrade from 3→5 is justified for the 39azUYFW path.** For the direct-to-pool path: still 2/10, zero coverage. Blended score depends on unknown ratio. **Score: 5/10 (primary path), 2/10 (direct-to-pool). Blended: ~5/10 with caveat on unknown split.**

---

### Births — Prior Score: 4/10

**Score is approximately correct. The audit's mechanism is partially right.**

PumpPortal path (primary): no RPC before write, but silent drop on DB write failure (write serializer at 60/60 capacity). No inbox, no retry. 3,045 births logged this session = functioning but fragile under load.

Helius logsSubscribe path (secondary): RPC before write, bare `return` on failure.

`webhook_birth_queue`: Dead feeder. `VALIDATION_FULL_INBOX` and `PUMPPORTAL_INBOX` migration source tags (1 row each) suggest a brief period where inbox-pattern code ran — consistent with the reverted `edd61b4` commit. The birth queue drainer consumed-before-task bug is real but the table has 0 rows.

**Score: 4/10 confirmed.**

---

### Cascade Launches — Prior Score: 5/10 (audit revised to 6/10)

**Evidence contradicts 6/10 — it should be lower given the TTL bug.**

Even if the cascade correctly detects a CREATE, the surrounding session state has issues:
- 2821 candidate watches: 2173 EXPIRED, 638 BUY_SWARM, 7 FIRED_CREATE, ~3 WATCHING
- `CATCHUP_SIG_LIMIT=8` confirmed unchanged
- `_seen()` dedup-before-write confirmed

But more critically: the candidate watch itself expires in `CANDIDATE_TTL_SEC`. If the creator wallet doesn't CREATE within the candidate TTL, the watch expires and the CREATE is missed permanently. The 2173 EXPIRED candidates represent the dominant outcome — not BUY_SWARM rejection.

**Score: 5/10 — prior audit's revision to 6/10 is not supported by the 2173 EXPIRED candidate count.**

---

### Wrap-Close Detection — Prior Score: 6/10

**Score confirmed. The sweep mechanism is genuine.**

`subprov_sweep_pass` every 6s. `CATCHUP_SIG_LIMIT=8`. For active subprovs, short-outage wrap-close recovery is reliable. No evidence of systematic misses in this path. **Score: 6/10 confirmed.**

---

### Subprov Discovery — Prior Score: 5/10 (auditor's revision)

**The ws_cascade path is more durable than stated for confirmed treasuries. The session TTL bug applies here too.**

Discovered subprovs: 78 total, split unknown on treasury_known (column exists per schema). But the session TTL bug means even when a subprov IS discovered and a session opened via webhook, the 600s TTL may cause the session to expire before a launch occurs.

**Score: 4/10 — downgrade from 5/10 because the TTL bug directly affects this path's effectiveness.**

---

## Part 2: Treasury Inbox Challenge — Is It Overstated?

**The treasury inbox fixes the crash-loss problem. It does NOT fix the 600s TTL bug.**

If the treasury inbox is built (durable write before 200), the session opened from the inbox drain still uses `ttl=600s` (line 34717 with default 600). The subprov gets a 10-minute watch window. This is functionally inadequate for STAGED launches (median lead time 150s+ per project memory, some campaigns take 30min+).

**The ws_cascade path does compensate for the crash-loss risk on confirmed treasuries** — 17 confirmed treasuries, `SESSION_TTL_SEC=7200`. But 61/78 infra addresses are unconfirmed and webhook-only.

**Does missing a treasury webhook cause permanent blindness?** 

For unconfirmed treasury wallets: yes, immediate miss. No ws_cascade coverage.  
For confirmed treasury wallets: no — ws_cascade provides parallel coverage with 2h TTL.  
For the 600s TTL path: the webhook arrives correctly, but the session is too short to be useful for STAGED launches.

**The treasury inbox addresses the wrong problem.** The durability issue (crash-loss) exists but the TTL bug makes the path unreliable even when the webhook delivers correctly. Fixing the inbox without fixing the TTL produces a durable but short-lived session.

---

## Part 3: Migration Reality Check — Path Split

**Measured data:**

| Path | Coverage | Recovery | Estimated Durability |
|------|----------|---------|---------------------|
| 39azUYFW-transiting | Helius logsSubscribe + PumpPortal + Reconciler (120s, DB-backed) | Reconciler | **7/10** |
| Direct-to-pool (pAMMBay) | None — no subscriber, no reconciler | None | **1/10** |
| Unknown historical (8796 NULLs) | INSUFFICIENT DATA on path | Partly reconciler | UNKNOWN |

**The prior audit's blended 5/10 is reasonable given unknown split.** The 747 migrations in last 7 days at 0 reconciler recoveries suggests the 39azUYFW path is working well — the WS is catching nearly everything on that route.

**The real migration gap is not durability — it is direct-to-pool coverage.** This is a coverage problem (never receives the event) not a durability problem (receives and loses it).

---

## Part 4: ROI Ranking — One Sprint

| Rank | Project | Expected Lift | Effort | Confidence |
|------|---------|--------------|--------|-----------|
| 1 | **Fix webhook session TTL (600s → 7200s in API env)** | Subprov sessions from webhook live 2h instead of 10min. Directly extends detection window for STAGED launches. | XS — one line in supervisord.conf `watchtower_api` env block | High |
| 2 | **Subscription coverage expansion (promote 33 known subprovs to WS)** | 33 already-discovered treasury_known subprovs get persistent WS subscriptions. Based on 196/218 funders unwatched, this has direct coverage lift. | S — already designed, WS_PROMOTE_DISCOVERED=1 exists | High |
| 3 | **Fix `processing_migrations` finally block** | One line. Restores PumpPortal as independent retry path when Helius migration RPC fails. | XS — 30 minutes | High |
| 4 | **Raise CATCHUP_SIG_LIMIT from 8 to 25** | Env var change. Wider reconnect recovery window for wrap-close and candidate catch-up. | XS — 5 minutes | High |
| 5 | **Treasury Webhook Inbox** | Eliminates crash-loss window for webhook delivery. But does NOT fix TTL bug (see rank 1). Combined with rank 1, this becomes much more valuable. | M — ~1 day | Medium |
| 6 | **Birth queue completion (register Helius webhook + fix consumed-before-task)** | Activates already-built infrastructure. Adds Helius as birth fallback. | S — ~2 hours code + Helius config | Medium |
| 7 | **Migration Inbox v2** | Durable intake for 39azUYFW path. But reconciler already provides 120s DB-backed recovery for this path. Lower marginal value. | M — 2 days | Medium |
| 8 | **Direct-to-pool migration recovery (subscribe pAMMBay pool creation)** | Closes the genuine coverage gap for direct-to-pool migrations. Requires adding a new Helius subscriber. | M — 1-2 days | Medium |

---

## Part 5: Coverage vs Durability

**Measured baseline: 9 WATCHTOWER launches detected. Current active sessions: 0. Current WATCHING candidates: ~3.**

The 2.8% detection rate (9/322) is from a prior measurement period. The current rate is effectively 0% — all 258 subprov sessions are EXPIRED, meaning the real-time detection pipeline has no active surveillance.

**Quantitative case for coverage first:**

- Improving durability from 5/10 → 9/10 on the existing pipeline makes a pipeline that currently watches 0 subprovs more reliable at watching 0 subprovs. The marginal gain is zero.
- Adding 33 treasury-known subprovs to persistent WS subscriptions takes the watched set from ~0 to ~33, with each subprov potentially funding multiple creator wallets.
- Even if the durability of the newly covered subprovs is only 5/10, detecting 50% of something is infinitely better than detecting 100% of nothing.

**The correct answer is B: coverage first.** But coverage and some durability fixes are not mutually exclusive — the TTL fix (rank 1) is both a durability fix AND a coverage improvement (longer window = more launches captured per session).

**The specific framing of "durability vs coverage" obscures the real issue:** the TTL bug means existing coverage is squandered. Fix TTL → existing confirmed treasury subscriptions become 12x more effective → coverage improves without new subscriptions.

---

## Part 6: Three Changes to Make Now

---

### 1. Fix Webhook Session TTL (600s → 7200s)

**Problem:** Sessions opened via the HTTP webhook expire in 10 minutes (default 600s). Supervisord sets `WS_SESSION_TTL_SEC=7200` for ws_cascade but not for the API process. The webhook's `start_session` call uses the API process env where the default is 600.

**Change:** In `supervisord.conf`, add `WS_SESSION_TTL_SEC="7200"` to the `watchtower_api` environment block. Reload supervisord.

**Expected improvement:** Subprov sessions opened via webhook persist 2 hours instead of 10 minutes. For STAGED launches (the majority at 81%), the creator wallet is funded and creates within a 2h window. Currently those launches fall outside the 10-min session window.

**Measurement:** `wt_active_subprov_sessions.expires_at - detected_at` should shift from 600s to 7200s for new sessions. Launch detection rate should increase measurably within the next 24h.

**Risk:** Sessions persist longer → more WS subscriptions held open per treasury event → higher Helius WS connection load. Bounded by `WS_MAX_ACTIVE_SUBPROVS=10` and `WS_MAX_CANDIDATES=25`. Low risk.

**Effort:** 2 minutes. Supervisord reload.

---

### 2. Subscription Coverage Expansion (WS_PROMOTE_DISCOVERED already enabled)

**Problem:** 196+ launch funders are not subscribed. 33 discovered+treasury_known subprovs are not getting persistent WS sessions. The ws_cascade has `WS_PROMOTE_DISCOVERED=1` and `WS_MAX_PROMOTED_SUBPROVS=40` already in supervisord — but the promoted subprovs need active sessions to be watched.

**Change:** Verify the promotion logic in ws_cascade is running and producing sessions. If it is, the fix is simply ensuring those promoted sessions get the correct 2h TTL (blocked by the TTL bug — fix rank 1 first). If the promotion isn't running, diagnose and re-enable.

**Expected improvement:** 33 additional subprovs under continuous WS watch. Each subprov that funds a creator within the watch window is detected.

**Measurement:** `wt_active_subprov_sessions` rows with `subprov_known=1` should increase. `wt_candidate_websocket_watches` WATCHING count should increase.

**Risk:** Same as rank 1 — bounded by MAX_ACTIVE_SUBPROVS cap. Low risk.

**Effort:** S — verify promotion logic is running, possibly minor config change.

---

### 3. Fix `processing_migrations` Finally Block

**Problem:** On exception in `handle_migration`, the `finally` block only discards from `processing_migrations` if `signature in completed_migrations` — which is false on exception. The sig stays in `processing_migrations` this session, blocking both the Helius and PumpPortal paths from retrying.

**Change:**
```python
# Current:
finally:
    if signature in self.completed_migrations:
        self.processing_migrations.discard(signature)

# Fix:
finally:
    self.processing_migrations.discard(signature)
```

**Expected improvement:** When one path fails its `getTransaction` RPC, the other path (PumpPortal or Helius) can retry the same sig within the same session. The two live paths become genuinely independent again.

**Measurement:** 0 reconciler recoveries currently (all migrations caught by WS). This fix is preventive — keeps the redundancy intact for RPC failure scenarios.

**Risk:** None. The `completed_migrations` set plus `token_analysis.migration_tx` DB dedup prevent double-processing.

**Effort:** XS — one line change.

---

## Part 7: Brutal Final Verdict

**If WATCHTOWER misses an important launch tomorrow, what is the most likely root cause?**

**Answer: Subscription coverage gap — compounded by the 600-second session TTL bug.**

**Evidence:**

1. **Zero active subprov sessions right now.** The most recent sessions (`2asyZrF3CB` at 18:22:14, `Ww1Ssk5TA7` at 02:59:10) both expired in 600 seconds. No new treasury provisioning event has opened a session that survived to the time of this audit.

2. **2173 EXPIRED candidate watches vs 7 FIRED_CREATE.** The dominant outcome for candidate watches is expiry, not detection. This is consistent with sessions expiring before the creator creates.

3. **The TTL bug makes the webhook path structurally ineffective.** A treasury outbound arrives, the webhook correctly processes it, `start_session(ttl=600)` opens a 10-minute watch, ws_cascade subscribes the subprov. For INSTANT launches (median 1s), this works. For STAGED launches (81% of volume per project memory, lead time up to 30min+), the session expires before the creator creates.

4. **The cascade is watching candidates right now** (`👁 watching candidate G77JGrZc9PV5…`) but these are from catch-up scans of a single subprov (`4vuRbR3vVN…`). The detection pipeline is not dark — but it is running on one subprov, not 196+ funders.

5. **The 9 detected WATCHTOWER launches span 2026-06-11 to 2026-06-17.** Zero detections in the last 48 hours at the time of this audit. Either no WATCHTOWER treasury has funded a subprov in that window (possible), or sessions are expiring before detection (consistent with the TTL evidence).

**The durability improvements discussed across all prior audits (treasury inbox, migration inbox, birth queue) are real gaps — but they address the reliability of a pipeline that is currently ineffective due to a 600-second TTL misconfiguration.** Building a durable inbox for a 10-minute session window does not fix the 10-minute window.

**The treasury inbox is the wrong first project.** The TTL fix is a 2-minute supervisord config change that directly addresses the dominant mechanism of launch loss. It should be the first change made.

**Second most likely cause if TTL is fixed:** Subscription coverage. 196+ funders go undetected because no treasury WS subscription covers them and they don't trigger the webhook (or their webhook events have the old TTL). The promoted-subprov path (WS_PROMOTE_DISCOVERED=1) is the structural answer.

**Treasury webhook crash-loss is the third-ranked risk** — real but compensated for confirmed treasuries by ws_cascade's parallel coverage at full 2h TTL.
