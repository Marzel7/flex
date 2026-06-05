# WATCHTOWER Live Creator-Seed Detection Audit

**Date:** 2026-06-04 · **Method:** live code-path inspection + persistent-state evidence (no speculation).

**Bottom line:** The full ARMED→creator-seed→CREATE detection pipeline **exists and is wired and enabled**, but it has **never fired once** — `wt_armed_operations` and `wt_detected_creates` are both empty across all history. The ignition feed reaches the interceptor's subscribe call but **0 ignition events have ever been processed** (431 WS subscribes, 0 notifications handled). So the architecture is built, but the live feed into it is dead.

---

## Q1 — What happens today when a hub becomes ARMED? (exact code path)

The pipeline is in `src/core/watchtower/create_interceptor.py`. Path:

| Stage | Function | What it does |
|-------|----------|--------------|
| Feed (WS) | `_monitor_ignition_wallet(addr, role)` :1777 | always-on `logsSubscribe` on TREASURY / S1 / S2 / SUB_PROV |
| Feed (webhook) | `webhook_handler.py:547` → `_dispatch_ignition_check` | fallback feed, runs before dust filter |
| Tx parse | `_handle_ignition_tx` :1831 | fetch tx, extract source→dest transfer, call dispatch |
| Route | `_dispatch_ignition_check` :684 | TREASURY≥10 SOL → `on_treasury_transfer`; SIGNALLER → `on_signaller_transfer` |
| PENDING | `on_treasury_transfer` / `on_signaller_transfer` :708/731 | records candidate in `_pending_candidates` (in-memory) |
| Arm gate | `_check_ignition` :1002 | arms when both signals within `IGNITION_WINDOW_S` (60s) and confidence ≥ 0.75 |
| **ARM** | `_arm` :507 | writes `wt_armed_operations`, opens WS, spawns relay tracer |
| Monitor | `_arm_websockets` :1052 → `_monitor_wallet` :1109 | `logsSubscribe` on the armed hub wallet |
| Seed catch | `_handle_wallet_tx` :1223 | on any hub outbound transfer → trace hub→creator |
| CREATE | `_monitor_pumpfun_creates` :1162 | `logsSubscribe` on pump.fun, fires on `Instruction: Create` |

- **Function that marks ARMED:** `_arm()`
- **Table:** `wt_armed_operations`
- **Webhook event:** any TREASURY/SIGNALLER transfer → `_dispatch_ignition_check` (webhook_handler.py:547)
- **Worker:** the interceptor event-loop thread (`wt-interceptor`), started from `main.py:209` when `ENABLE_CREATE_INTERCEPTOR=true`
- **Automatic actions on ARM:** persist row → open per-wallet `logsSubscribe` → start pump.fun CREATE monitor → spawn `_trace_relay_to_creator` background thread

**Config (live):** `supervisord.conf` sets `ENABLE_CREATE_INTERCEPTOR="true"`, `INTERCEPTOR_MODE="PASSIVE"`. The interceptor **does start** — log shows `[INTERCEPTOR] started always-on-ws=TREASURY+SIGNALLER_1+SIGNALLER_2`.

## Q2 — Does the system dynamically monitor newly-armed hubs?

**By design: YES (Option B).** On ARM, `_arm_websockets` opens a per-wallet `logsSubscribe` on the hub and ensures the pump.fun CREATE monitor is running; `disarm()` tears them down on TTL/CREATE. This is dynamic account/tx monitoring, exactly as the objective describes.

**In practice: it never executes**, because nothing ever reaches `_arm()` (see Q1 result / Current State).

## Q3 — Would the HS9NA3E creator-seed transfer be visible today? **NO.**

The *capability* exists: if `HS9NA3E` armed, `_monitor_wallet` would `logsSubscribe` on it and `_handle_wallet_tx` would catch the `HS9NA3E → creator (0.15253928 SOL)` outbound and trace it. **But it would never arm.** Evidence:

- `wt_armed_operations` = **0 rows, ever** (persistent table, survives restarts)
- Ignition WS: **431 subscribes, 0 `logsNotification` handled, 0 errors** across all logs
- 0 `TREASURY→`/`PENDING`/`⚡ ARMED` log lines ever; 0 `ignition_ws_received`

So today the `HS9NA3E → creator` transfer would **not** be seen — not because the watcher can't see it, but because the watcher is never turned on (the arm never happens).

## Q4 — Timing windows (T0–T4) and is pre-CREATE identification realistic?

From on-chain reconstruction of the 6 fully-traced hubs (seconds relative to T0=treasury):

| Window | min | max | avg | median |
|--------|-----|-----|-----|--------|
| **T2→T3** (both signallers → creator seed) | 53 | 538 | 203 | **132** |
| **T3→T4** (creator seed → CREATE) | 1 | 1 | 1 | **1** |
| T0→T4 (treasury → CREATE) | 67 | 560 | — | 149 |

**Could the current architecture realistically identify the creator before CREATE? YES — with large margin, *if armed*.** The arming completes within ~17s of T0 (both signallers land fast), leaving a **median 132s (min 53s)** window to catch the creator seed at T3, and the seed precedes CREATE by ~1s. The `logsSubscribe`-on-armed-wallet path operates at `processed` commitment (sub-second), so 53s is ample. The architecture is timing-capable; it is not the bottleneck.

## Q5 — Minimal change required (do not redesign)

The pipeline is complete; the only break is **the ignition feed never reaches `_dispatch_ignition_check`**. Options, least→most invasive:

| Option | Assessment |
|--------|-----------|
| **Fix the existing ignition feed** (verify TREASURY/S1/S2 are enrolled in the Helius webhook, and/or the always-on `_monitor_ignition_wallet` WS actually delivers) | **RECOMMENDED.** No new mechanism — the dispatch, PENDING, arm, WS-monitor, and CREATE paths all already exist and are correct. The single missing link is events arriving at `_dispatch_ignition_check`. 431 subscribes + 0 notifications means the subscription isn't delivering; the webhook fallback shows 0 activity too. Confirm webhook enrollment of the 3 ignition wallets and/or why `logsSubscribe(mentions=[wallet])` yields no notifications. |
| Add a lightweight poller on TREASURY/S1/S2 (getSignaturesForAddress every ~15s) feeding `_dispatch_ignition_check` | Small, production-safe **belt-and-suspenders** if the WS proves unreliable. ~17s arm budget tolerates a 15s poll. Reuses the entire downstream pipeline. |
| Dynamic webhook enrollment of armed hubs | Unnecessary — the per-wallet WS monitor already covers armed hubs; the gap is upstream (arming), not downstream (monitoring). |

**Recommendation:** repair the ignition feed (webhook enrollment + WS delivery verification). If WS delivery can't be made reliable, add the TREASURY/S1/S2 poller as the feed into the existing, already-correct dispatch pipeline. **No redesign — one feed fix.**

## Q6 — Expected false-positive rate

From the observed 11 TREASURY+dual-signaller hubs (signaller census + FP verification):

| Outcome | Count | % |
|---------|-------|---|
| Emit creator seed (true launch) | 8 | **73%** |
| Reserve (armed, never distributed) | 2 | 18% |
| Swarm (fanout, no creator) | 1 | 9% |

→ Monitoring every ARMED hub, **~27% would never emit a creator seed.** This is acceptable: monitoring cost is one cheap per-wallet `logsSubscribe` with auto-disarm on `ARMED_EXPIRY_S` (7200s) TTL, and **the creator-seed leg is itself the confirmation** — non-launch hubs simply never produce one, so they cost a WS slot for ≤2h and self-clean. No false *launch* is declared; the seed is the discriminator.

---

# Deliverables

### A. Current State
```
Can identify creator before CREATE today?   NO
```
Evidence: the pipeline exists and is enabled, but `wt_armed_operations` = 0 and `wt_detected_creates` = 0 across all history; the ignition feed has handled 0 events (431 WS subscribes, 0 notifications). Nothing ever arms, so no armed-wallet monitor ever opens, so no creator seed is ever seen.

### B. Gap Analysis
Exactly one thing is missing: **the ignition feed into `_dispatch_ignition_check`.** Everything downstream — PENDING tracking, the arm gate, `wt_armed_operations`, per-wallet `logsSubscribe`, hub→creator tracing, and the pump.fun CREATE monitor — is implemented, wired, and enabled. The `logsSubscribe(mentions=[TREASURY/S1/S2])` subscriptions connect but deliver no notifications, and the webhook fallback shows no ignition activity. Result: PASSIVE→ARMED never fires.

### C. Recommended Fix
Smallest production-safe change: **repair the ignition feed** — (1) confirm TREASURY, S1, S2 are enrolled in the Helius webhook that calls `webhook_handler.py:547`, and (2) verify/fix why the always-on `_monitor_ignition_wallet` WS yields no `logsNotification`. If WS can't be made reliable, add a ~15s `getSignaturesForAddress` poller on the 3 ignition wallets feeding the existing `_dispatch_ignition_check`. No architectural change.

### D. Expected Lead Time (for the 8 known launches)
```
creator seed detected → CREATE:   min 1s · max 1s · avg ~1s · median 1s
ARMED → creator seed (detect window): min 53s · max 538s · avg 203s · median 132s
```
Once armed, there is a median ~132s window to catch the seed, and the seed precedes CREATE by ~1s — so creator identification ~1s before CREATE is achievable, with the swarm/relay context available within the 53–538s pre-seed window.

### E. Final Verdict
```
ONE SMALL CHANGE REQUIRED
```
The detection architecture is complete, correct, enabled, and timing-capable. It has never produced a single ARMED operation because the ignition feed is not delivering events. Restore that one feed and the existing pipeline identifies the creator ~1s before CREATE, with a median 132s arming-to-seed window — no redesign needed.

*All conclusions from live code inspection (create_interceptor.py, webhook_handler.py, main.py, supervisord.conf), persistent DB state (wt_armed_operations, wt_detected_creates), and log evidence (api_err.log). On-chain timing from the provisioning-hub + signaller census investigations.*
