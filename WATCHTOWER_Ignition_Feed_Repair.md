# WATCHTOWER Ignition Feed Repair Audit

**Date:** 2026-06-04 · **Method:** live code trace + live WS reproduction + Helius API + DB/log evidence. No speculation.

**Root cause (one line):** `logsSubscribe` with `{"mentions":[wallet]}` **accepts the subscription (returns a sub_id) but never delivers notifications for a plain wallet** — it only works for *program* accounts. The ignition WS therefore silently no-ops, and the webhook fallback has been dead since March with zero ignition wallets enrolled.

---

## Part 1 — Feed path trace

**Path A — WebSocket (the only live pipe):**

| Stage | Function / file:line | Success condition | Status |
|-------|---------------------|-------------------|--------|
| Subscribe | `_monitor_ignition_wallet` `create_interceptor.py:1777` (`logsSubscribe {"mentions":[address]}` :1786-1790) | receives `logsNotification` | **FAILED** — 431 subscribes, 0 notifications |
| Notification handler | same fn, :1797-1808 (`method == "logsNotification"` → spawn `_handle_ignition_tx`) | fires per tx | **never reached** (no notifications arrive) |
| Tx parse | `_handle_ignition_tx` :1831 | extracts source→dest | never reached |
| Dispatch | `_dispatch_ignition_check` :684 | routes TREASURY/SIGNALLER | never reached |
| PENDING | `on_treasury_transfer`/`on_signaller_transfer` :708/731 | candidate recorded | never reached |
| ARM | `_arm` :507 → `wt_armed_operations` | row written | **0 rows ever** |

**Path B — Webhook (documented fallback):**

| Stage | Function / file:line | Status |
|-------|---------------------|--------|
| Route | `/helius/webhook` → `handle_helius_webhook` (`webhook_integration.py:28`) | — |
| Dispatch call | `webhook_handler.py:547` `_ci._dispatch_ignition_check(...)` | wired, but never invoked (no events) |

## Part 2 — Webhook enrollment (Helius API, live)

```
active webhooks: 1
  id=106e20f6-f542-42b0-83d5-ca8c7b1a7162
  url=https://…ngrok-free.dev/api/webhook/watchtower
  type=enhanced  accountAddresses=0
  TREASURY enrolled: NO
  S1 enrolled:       NO
  S2 enrolled:       NO
```

| Wallet | In webhook? | Webhook ID | URL |
|--------|-------------|-----------|-----|
| TREASURY | **NO** | 106e20f6-… | …/api/webhook/watchtower |
| S1 | **NO** | — | — |
| S2 | **NO** | — | — |

Two faults: (1) **0 addresses enrolled**; (2) the webhook posts to **`/api/webhook/watchtower`**, but the code path that calls `_dispatch_ignition_check` is **`/helius/webhook`** — a different route. Even with addresses, ignition events wouldn't reach the dispatch.

## Part 3 — Webhook delivery (logs + DB)

Webhook events land in `sol_transfers`:
```
total rows: 40,581
first event: 2026-03-03
last event:  2026-03-09 18:03:41   ← ~3 months stale
queue_size:  545 (stale backlog)
```

| Wallet | Events received | Last event | Handler invoked? |
|--------|----------------|------------|------------------|
| TREASURY | 0 (ignition) | webhook dead since 2026-03-09 | No |
| S1 | 0 | — | No |
| S2 | 0 | — | No |

**The webhook pipe has received nothing since 2026-03-09.** It cannot be the feed.

## Part 4 — WebSocket delivery (live reproduction)

| Q | Finding | Evidence |
|---|---------|----------|
| **A** Subscriptions succeeding? | Yes — sub_id returned every time | `ignition WS TREASURY sub_id=…` (431×) |
| **B** Correct address? | Yes — `mentions:[address]` uses the real wallet | code :1786-1790 |
| **C** Filtering incorrectly? | **YES — this is the bug** | see F |
| **D** Notifications arriving but discarded? | No — none arrive at all | live test: 0 in 15s |
| **E** Reconnect breaking subs? | No — 0 WS errors logged; 431 = restarts×4 wallets | log grep |
| **F** Does Helius support this mode? | **NO for plain wallets** | live test below |

**Live reproduction (exact ignition subscription vs control):**
```
pump.fun  mentions/processed: subscribed=True  notifications=787 in 10s   ← WS works
TREASURY  mentions/processed: subscribed=True  notifications=0   in 15s   ← silent no-op
accountSubscribe(TREASURY):   sub_id=23717056                              ← correct primitive for wallets
```
TREASURY had real on-chain activity as recently as 2026-06-04 02:47 (S1/S2 on 06-03 22:47), so "0 notifications" is **not** an absence-of-traffic artifact. **`logsSubscribe`+`mentions` is a documented Solana limitation: it surfaces logs for *program* accounts in instructions, not for ordinary wallets moving SOL through the System Program.** Helius returns a sub_id (so the code's success check passes) but emits nothing.

## Part 5 — Where the chain breaks

```
TREASURY tx (real, 06-04 02:47)
  ↓ expected WS logsNotification  ← BREAKS HERE: mentions=[wallet] never delivers
  ↓ expected webhook event        ← also dead: 0 addresses enrolled, route mismatch, dead since March
  ↓ _dispatch_ignition_check      ← never called
  ↓ PENDING → ARMED               ← never happens
```
Both feeds are broken, independently, at the very first hop.

## Part 6 — Poller feasibility

`getSignaturesForAddress` on TREASURY+S1+S2 every 15s:
```
3 wallets × (86400/15)         = 17,280 calls/day
+ getTransaction on new sigs   ≈ 40/day (≈ignition events)
total                          ≈ 17,320 RPC calls/day
```
- **Latency:** ≤15s added (poll interval).
- **Fits the arm window:** min ARMED→seed window is **53s** → a 15s poll gives ≥3 chances inside the smallest window. Comfortable.
- **Complexity:** low — reuses `_dispatch_ignition_check` unchanged; a single background thread tracking last-seen signature per wallet.

## Part 7 — Metrics blind spot

431 subscribes / 0 notifications went unnoticed because the metrics are **in-memory only** (`_ignition_metrics` dict, reset every restart, surfaced only via `get_status`, never persisted or logged).

**Proposed table `wt_ignition_metrics`:**
```sql
CREATE TABLE wt_ignition_metrics (
  ts INTEGER, ignition_ws_subscribes INTEGER, ignition_ws_received INTEGER,
  ignition_ws_errors INTEGER, ignition_webhook_received INTEGER,
  ignition_dispatch_count INTEGER, ignition_arm_count INTEGER
);  -- one row per hour (interceptor flushes counters)
```
**Dashboard card:** "Ignition Feed (24h)" — subscribes / received / dispatched / armed.
**Alert thresholds:**
- `ignition_ws_received == 0 for 6h` while TREASURY/S1/S2 had on-chain activity → **CRITICAL (feed dead)** — this exact alert would have caught the bug.
- `ignition_dispatch_count == 0 for 24h` → WARNING.
- `ignition_arm_count == 0 for 7d` → INFO (no launches, expected) vs CRITICAL if dispatches > 0 (logic gap).

---

# Deliverables

## A. Exact root cause (evidence, not theory)
**The WS feed uses `logsSubscribe` with `{"mentions":[wallet]}`, which Solana/Helius does not deliver for non-program (plain-wallet) accounts.** Proven by live reproduction: identical subscription to pump.fun (a program) yields 787 notifications/10s; to TREASURY (a wallet) yields 0/15s despite TREASURY transacting hours earlier; the subscription returns a sub_id in both cases (silent no-op). The webhook fallback is independently dead: 1 webhook, **0 addresses enrolled**, posting to a non-ignition route, no events since 2026-03-09.

## B. Smallest safe production fix
```
Combination — but minimal:
  Primary: switch the ignition WS from logsSubscribe(mentions=wallet)
           → accountSubscribe(wallet)   [correct primitive, confirmed supported]
  Safety net: add the 15s getSignaturesForAddress poller feeding the SAME
           _dispatch_ignition_check (covers WS gaps; fits the 53s window)
```
Webhook enrollment is **not** the recommended fix (the WS is the live mechanism; webhook is stale infra with a route mismatch). Fixing the subscription primitive is the single change that makes the existing pipeline fire.

## C. Implementation plan
1. **`create_interceptor.py` — `_monitor_ignition_wallet` (~15 LOC):** replace the `logsSubscribe`/`mentions` block with `accountSubscribe(address, {commitment:"processed", encoding:"jsonParsed"})`; on `accountNotification`, the notification gives the balance delta — fetch the signature via `getSignaturesForAddress(address, limit=1)` (or parse from a paired `logsSubscribe` on the System Program) and hand to `_handle_ignition_tx`. (`accountSubscribe` returns balance, not sig, so the sig fetch is the one added step.)
2. **`create_interceptor.py` — new `_poll_ignition_wallets()` (~40 LOC):** background thread; per-wallet last-seen sig cursor; every 15s `getSignaturesForAddress(limit=10)`, for each new sig call `_handle_ignition_tx`. Started from `start()` alongside the WS monitors.
3. **Metrics persistence (~30 LOC):** `wt_ignition_metrics` table + hourly flush of `_ignition_metrics` + the CRITICAL "received==0 with activity" alert.
4. **Rollout:** ship poller first (independent, immediately restores the feed, low risk) → verify `wt_armed_operations` starts populating on the next live launch → then swap the WS primitive → then add the metrics/alert. Each step independently verifiable; total ≈ 85 LOC, no architectural change.

## D. Final verdict
```
Both broken
```
- **WS broken:** `logsSubscribe(mentions=wallet)` is the wrong primitive for wallets — subscribes but never delivers (live-proven: 0 vs 787 control).
- **Webhook broken:** 0 ignition addresses enrolled, route mismatch (`/api/webhook/watchtower` vs `/helius/webhook`), dead since 2026-03-09.

Both fail at the first hop, independently. The downstream pipeline (dispatch → PENDING → ARM → WS-monitor → CREATE) is correct and untouched — restoring either feed (recommend: accountSubscribe + 15s poller) makes it fire, identifying the creator ~1s before CREATE within a median 132s window.

*Evidence sources: create_interceptor.py, webhook_handler.py, webhook_integration.py; Helius webhook API (1 hook, 0 addresses); sol_transfers (last 2026-03-09); live logsSubscribe reproduction (pump.fun 787 vs TREASURY 0); accountSubscribe live confirmation; wt_armed_operations/wt_detected_creates = 0.*
