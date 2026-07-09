# Capture Safety Roadmap (2026-06-19)

**Scope:** Permanent event loss prevention only. No performance, no DB size, no UI, no PostgreSQL.  
**Source:** DURABILITY_MAP.md + live measurement.

---

## Scoring Model

**Consequence (1–5):** What is permanently broken downstream if this event is lost?  
**Loss probability (1–5):** How likely is a loss given current system behavior?  
**Recovery score (1–5):** How well does the existing recovery mechanism cover the gap?  
**Effort (S/M/L):** Engineering effort to close the gap. S=<1 day, M=2–3 days, L=1 week+.

**Durability score (0–10):** `(recovery score × 2) + (10 − loss_probability × 2)`. Higher = more durable.

---

## Stream Rankings

---

### 1. TREASURY HITS

**Current durability: 1 / 10**

| Dimension | Score | Evidence |
|-----------|-------|----------|
| Consequence | 5 | Missed treasury → no subprov session → ws_cascade never subscribes → all future launches via that subprov missed. Multiplier failure. |
| Loss probability | 5 | HTTP 200 returned before any write. `_wt_infra_queue` is in-memory, maxsize=2000. 81 listener restarts observed. API process (gunicorn) also restarts. Queue can fill during DB lock storms → silent drop. |
| Recovery | 0 | None. Helius does not retry on 200. No DB record that the webhook arrived. |
| Effort | S | — |

**Exact failure window:**  
`_wt_infra_queue.put_nowait(payload)` at `main.py:33807` → return 200 → background drain at `main.py:33557`. Any crash in this window: permanent loss.

**First durable write:** `watchtower_infra_events` (live DB) — happens AFTER the 200 response, in a background thread.

**Minimal inbox required:**  
A single SQLite table in `wt_ops_v2.db` (ops DB — separate process, no contention):
```sql
CREATE TABLE wt_treasury_webhook_inbox (
    id INTEGER PRIMARY KEY,
    received_at INTEGER NOT NULL,
    payload TEXT NOT NULL,        -- raw JSON from Helius
    status TEXT DEFAULT 'PENDING' -- PENDING | PROCESSED
);
```
Write `status=PENDING` **before** returning 200. Background drain reads PENDING rows, processes, marks PROCESSED. On startup, replay any PENDING rows from the last N hours.

**Target durability: 9 / 10**

---

### 2. MIGRATIONS

**Current durability: 3 / 10**

| Dimension | Score | Evidence |
|-----------|-------|----------|
| Consequence | 5 | Missed migration = token never appears in live feed, never tracked for pool, never priced. Core system function. Only 5 migrations captured in 24h vs estimated 500+/day on pump.fun. |
| Loss probability | 4 | `getTransaction` before first write. At `confirmed` commitment, Helius sometimes returns null for very recent sigs. 81 listener restarts × reconnect windows. `processing_migrations` mutex blocks the second path if the first fails mid-RPC. |
| Recovery | 2 | Reconciler covers `39azUYFW…` path only, last 100 sigs, 120s cadence. PumpSwap-direct migrations: zero recovery. |
| Effort | M | — |

**Exact failure window:**  
`handle_migration(sig, logs)` called → `processing_migrations.add(sig)` at line 9406 → `_get_transaction_cached(sig)` RPC → parse → `_process_migration_with_mint(...)` → first DB write to `token_analysis`. Any crash, RPC failure, or DB lock failure in this window: permanent loss (for the direct-pool path) or eventual recovery via reconciler (for `39azUYFW…` path, within 100-sig window).

**Critical bug:** `processing_migrations.add(sig)` at line 9406 runs BEFORE the RPC. If the RPC fails and the process doesn't crash (just a timeout), the sig stays in `processing_migrations` — blocking the second path (PumpPortal or Helius) from processing the same sig this session. Two paths become one de facto.

**First durable write:** `token_analysis.migration_tx` (live DB) — after RPC + parse.

**Minimal inbox required:**  
```sql
CREATE TABLE wt_migration_inbox (
    sig TEXT PRIMARY KEY,
    source TEXT NOT NULL,          -- 'HELIUS_LOGS' | 'PUMPPORTAL' | 'RECONCILER'
    received_at INTEGER NOT NULL,
    status TEXT DEFAULT 'PENDING'  -- PENDING | PROCESSING | DONE | FAILED
);
```
Write `status=PENDING` synchronously when the WS notification arrives — BEFORE calling `handle_migration`. This costs one write to ops DB (no hot DB contention). On restart, replay PENDING rows. Removes the 100-sig reconciler window as the sole backstop.

**Target durability: 8 / 10**

---

### 3. BIRTHS

**Current durability: 4 / 10**

| Dimension | Score | Evidence |
|-----------|-------|----------|
| Consequence | 4 | Missed birth = token never in system. Can't be tracked, enriched, or matched to a WATCHTOWER launch. But births occur at ~1000s/day — individual loss is lower consequence than a treasury or migration. |
| Loss probability | 3 | PumpPortal path has NO pre-write RPC (good). But write goes through saturated serializer (queue_depth=60/60). `busy_timeout` exhaust → silent drop. PumpPortal WS reconnect window = blind. 3,045 births logged this session = healthy when connected. |
| Recovery | 2 | Helius `logsSubscribe` on pump.fun program is a partial backstop but requires `getTransaction`, adding its own failure mode. No reconciler for births. `webhook_birth_queue` is a dead table. |
| Effort | S | — |

**Exact failure window:**  
`tx_type == "create"` received in `listen_pumpportal_websocket` → `_insert_bonding_curve_token(mint, ...)` → `INSERT INTO token_analysis`. The RPC-free nature of the PumpPortal path means the window is narrow (just the DB write). But on serializer exhaustion the write is silently dropped with no retry and no record.

**First durable write:** `token_analysis` (live DB) — PumpPortal delivers all data inline, no pre-write RPC.

**Minimal inbox required:**  
A lightweight birth inbox in a dedicated `births_inbox.db` (separate from hot DB, single-writer = listener only):
```sql
CREATE TABLE birth_inbox (
    sig TEXT PRIMARY KEY,
    mint TEXT NOT NULL,
    received_at INTEGER NOT NULL,
    raw_payload TEXT NOT NULL,     -- full PumpPortal JSON for enrichment
    status TEXT DEFAULT 'PENDING'
);
```
Write synchronously on WS arrival (this DB has no contention — listener only). Then enrich to `token_analysis` async with retries. On reconnect, replay PENDING rows.

**Target durability: 8 / 10**

---

### 4. CASCADE LAUNCHES (WATCHTOWER detections)

**Current durability: 5 / 10**

| Dimension | Score | Evidence |
|-----------|-------|----------|
| Consequence | 5 | Missed launch = WATCHTOWER detection gap. The entire value proposition. But these only fire when a WATCHTOWER creator actually creates — low frequency, high value per event. |
| Loss probability | 2 | The `catch_up_candidate` mechanism on reconnect is genuine. The risk window is narrow: `_seen(candidate, sig)` dedup BEFORE the DB write means a mid-write crash + session survival blocks retry. But: 9 rows in `wt_watchtower_launches` total — very low frequency reduces absolute loss rate. |
| Recovery | 4 | `catch_up_candidate` runs on reconnect via `resync_subscriptions`. **But CATCHUP_SIG_LIMIT = 8** — only the last 8 signatures on the candidate wallet are scanned. If a busy wallet has > 8 txs between crash and reconnect, the CREATE sig may be outside the window. |
| Effort | S | — |

**Exact failure window:**  
`_seen(candidate, sig)` → `_handle_candidate_tx` thread starts → `_get_tx(sig)` RPC → `_tx_is_create` → `store.record_launch(conn, ...)` at `ws_cascade.py:622`. The `_seen()` dedup runs BEFORE the RPC and DB write. If either fails, the sig is in `_processed` and blocked for this session.

**Critical issue with CATCHUP_SIG_LIMIT = 8:**  
A WATCHTOWER creator wallet is typically a fresh single-use wallet. 8 sigs covers the CREATE + any adjacent setup txs comfortably. But if the subprov funded multiple creator wallets rapidly (INSTANT mode), the scan may be too shallow. Raising to 20 is a trivial env change.

**First durable write:** `wt_watchtower_launches` (OPS DB) — after RPC + parse.

**Minimal inbox required:**  
Move `_seen(candidate, sig)` to AFTER the DB write succeeds. The `INSERT OR IGNORE` on `wt_watchtower_launches` provides idempotency. This eliminates the pre-write dedup block with zero new infrastructure.

**Target durability: 7 / 10**

---

### 5. WRAP-CLOSE CREATOR DETECTION

**Current durability: 6 / 10**

| Dimension | Score | Evidence |
|-----------|-------|----------|
| Consequence | 4 | Missed wrap-close = creator wallet never gets a WS subscription = WATCHTOWER launch missed. But miss is only permanent if the creator immediately creates with no further subprov activity to re-trigger. |
| Loss probability | 2 | `subprov_sweep_pass` runs every 6s and calls `catch_up_subprov` on all ACTIVE subprovs. `catch_up_subprov` is the same sweep with `CATCHUP_SIG_LIMIT = 8`. Short outages are well-covered. |
| Recovery | 4 | `subprov_sweep_pass` + `catch_up_subprov` on reconnect. Reliable for short outages. CATCHUP_SIG_LIMIT=8 is the risk for long outages or high-velocity subprovs. `wt_candidate_websocket_watches` (OPS DB, INSERT OR IGNORE) provides cross-restart idempotency. |
| Effort | S | — |

**Exact failure window:**  
WS notification → `inbox.put_nowait(raw)` → `_get_tx(sig)` RPC → `extract_close_destinations` → `open_candidate_watch` (OPS DB). The sweep mechanism is the real recovery — not an inbox pattern.

**First durable write:** `wt_candidate_websocket_watches` (OPS DB).

**Minimal inbox required:**  
Raise `WS_CATCHUP_SIG_LIMIT` from 8 to 25 via env var. No code change. Covers most long-outage scenarios. The existing sweep mechanism is otherwise sound.

**Target durability: 8 / 10**

---

### 6. SUBPROV DISCOVERY (webhook path)

**Current durability: 4 / 10**

| Dimension | Score | Evidence |
|-----------|-------|----------|
| Consequence | 4 | Missed subprov discovery = delayed WS subscription. Operation_scheduler eventually discovers it via backward walk (15-min cadence) so the miss is time-bounded, not permanent. |
| Loss probability | 3 | Inherits treasury hit fragility — the subprov's funding event is embedded in the treasury webhook payload. If that payload is lost (Stream 1), the subprov session is also lost. |
| Recovery | 3 | Operation_scheduler backward walk recovers within ~15 min for known treasuries. Not zero, but delayed. |
| Effort | S | — |

**Note:** Fixing treasury hits (Stream 1) directly improves subprov discovery durability. They share the same failure window.

**Target durability: 7 / 10** (achievable via Stream 1 fix alone)

---

### 7. FUTURE BOUND TRACKING (vSol)

**Current durability: 2 / 10** (currently parked — contributing 0 to system function)

| Dimension | Score | Evidence |
|-----------|-------|----------|
| Consequence | 2 | Missed near-migration signal = Future Bound panel empty. No WATCHTOWER detection depends on this. UX only. |
| Loss probability | 5 | `_portal_vsol` entirely in-memory. `portal_vsol.json` flush is disabled (`LISTENER_PORTAL_VSOL_FLUSH_ENABLED=0`). `tracked_trade_mints` resets on every PumpPortal WS reconnect. |
| Recovery | 1 | Trade re-seed on reconnect recovers subscriptions but not vSol values. New trade events rebuild state over time. |
| Effort | S | — |

**First durable write:** `portal_vsol.json` (when flush enabled). No DB table.

**Minimal durability fix:** Re-enable flush to `portal_vsol.json` AND load it on listener startup to restore `_portal_vsol` state. This is a 2-line fix once the vSol flush is re-enabled.

**Target durability: 6 / 10** (acceptable for UX-only stream)

---

### 8. WATCHTOWER TELEMETRY

**Current durability: 3 / 10** (by design — acceptable)

| Dimension | Score | Evidence |
|-----------|-------|----------|
| Consequence | 1 | Loss has zero impact on detection. Affects dashboard banners and audit trail only. |
| Loss probability | 4 | In-memory `_event_q` (maxsize=5000). `put_nowait` silently drops on full. 5-retry OPS DB write. |
| Recovery | 1 | None by design. |
| Effort | — | — |

**Assessment:** CAPTURE FRAGILE by design. Do not invest in making this durable. The events it records are derived from the system — if the system works, they're reconstructable from other tables.

**Target durability: 3 / 10** (no change warranted)

---

## Summary Table

| Stream | Current Score | Target Score | Loss Probability | Consequence | Recovery | Effort |
|--------|-------------|-------------|-----------------|-------------|----------|--------|
| Treasury Hits | **1** | 9 | 5 | 5 | 0 | S |
| Migrations | **3** | 8 | 4 | 5 | 2 | M |
| Births | **4** | 8 | 3 | 4 | 2 | S |
| Cascade Launches | **5** | 7 | 2 | 5 | 4 | S |
| Wrap-Close Detection | **6** | 8 | 2 | 4 | 4 | S (env change) |
| Subprov Discovery | **4** | 7 | 3 | 4 | 3 | S (via Stream 1) |
| Future Bound | **2** | 6 | 5 | 2 | 1 | S |
| Telemetry | **3** | 3 | 4 | 1 | 1 | — |

---

## Top 5 Highest ROI Durability Projects

Ranked by `risk_reduced ÷ implementation_effort`.

---

### Project 1 — Treasury Webhook Inbox

**ROI ratio: CRITICAL / S effort**

**Risk reduced:** Eliminates the #1 permanent loss vector. Treasury miss cascades into: no subprov session → no WS subscription → all future launches via that subprov missed. One inbox write fixes the entire downstream chain.

**What to build:**
Add a `wt_treasury_webhook_inbox` table to `wt_ops_v2.db`. In the Flask webhook handler (`main.py:33807`), replace the queue write with a direct synchronous write to this table (OPS DB — separate from hot DB, no contention) and return 200. The background drain reads from this table instead of the in-memory queue.

```python
# Before: _wt_infra_queue.put_nowait(payload)
# After:
with ops_db_conn() as conn:
    conn.execute(
        "INSERT INTO wt_treasury_webhook_inbox (received_at, payload, status) VALUES (?,?,?)",
        (int(time.time()), json.dumps(payload), 'PENDING')
    )
    conn.commit()
# return 200 — now safe
```

On startup, drain any `status='PENDING'` rows from the last 24h before starting live processing.

**Expected benefit:** Treasury hits: 1 → 9. Subprov discovery: 4 → 7 for free.  
**Estimate:** 4–6 hours.

---

### Project 2 — Fix `_seen()` Pre-Write Dedup in Cascade Launches

**ROI ratio: HIGH / XS effort (single line move)**

**Risk reduced:** Eliminates the session-level permanent loss for cascade launches. Currently `_seen(candidate, sig)` marks the sig as processed before the DB write succeeds — an RPC failure or DB write failure permanently blocks retry for the session lifetime.

**What to build:**  
Move the `self._processed.add(key)` call inside `_seen()` to AFTER `store.record_launch(conn, ...)` succeeds. The `INSERT OR IGNORE` on `wt_watchtower_launches` is already the idempotency guarantee — the in-memory `_processed` set is only needed for avoiding redundant RPC calls, not for correctness. If `record_launch` fails, don't add to `_processed` — let it retry.

Also raise `WS_CATCHUP_SIG_LIMIT` from 8 to 25 (one env var change in `supervisord.conf`). Covers the reconnect window for any high-velocity candidate wallet.

**Expected benefit:** Cascade launches: 5 → 7. Essentially eliminates the "stuck this session" failure.  
**Estimate:** 2 hours (code) + env var change.

---

### Project 3 — Migration Inbox (sig durability before RPC)

**ROI ratio: HIGH / M effort**

**Risk reduced:** Closes the gap where a migration sig arrives, `processing_migrations.add(sig)` fires, then the RPC fails — leaving the sig blocked in `processing_migrations` this session with no recovery for direct-to-pool migrations.

**What to build:**  
Add `wt_migration_inbox` to `wt_ops_v2.db`. When `logsNotification` arrives with a migration sig (both paths — `listen_pumpswap_websocket` and `listen_pumpportal_websocket`), write `status=PENDING` to the inbox BEFORE adding to `processing_migrations` and BEFORE calling `handle_migration`. On restart, read PENDING rows and replay through `handle_migration`.

This also fixes the `processing_migrations` mutex bug: if the RPC fails and the row stays PENDING, on next restart or reconciler sweep it gets replayed cleanly.

Also fix the specific bug: `processing_migrations.add(sig)` should be moved to AFTER the RPC succeeds (or guarded by a try/finally that removes it on RPC failure). The inbox makes this correctness fix safe.

**Expected benefit:** Migrations: 3 → 8. Also makes the `processing_migrations` cross-path block harmless (inbox replay doesn't go through `processing_migrations` guard).  
**Estimate:** 2–3 days (inbox schema, write points in 2 paths, startup replay, fix processing_migrations guard).

---

### Project 4 — Birth Inbox (or: fix the existing dead drainer)

**ROI ratio: MEDIUM / S effort**

**Risk reduced:** Closes the silent-drop gap on birth DB write failure. Births are the highest-frequency event (hundreds/hour) and the silent drop on serializer exhaustion is happening now (queue_depth=60/60).

**What to build (minimal option):**  
The `webhook_birth_queue` table and drainer already exist but are dead (nothing writes to it). The simplest fix: when `_insert_bonding_curve_token` fails (catches an exception), write a recovery row to `webhook_birth_queue` with the raw PumpPortal payload. The drainer already knows how to replay from there. This is a 1-function change.

```python
# In _insert_bonding_curve_token exception handler:
except Exception as e:
    log_print(f"[BIRTH] ⚠ Write failed, queuing for retry: {e}")
    _queue_birth_for_retry(mint, sig, creator, symbol, name, bonding_curve_pda)
```

`_queue_birth_for_retry` writes to `webhook_birth_queue` — a table that already exists, in the listener's own DB connection. No new infrastructure.

**Expected benefit:** Births: 4 → 7. The PumpPortal WS reconnect window still exists, but DB write failures become retryable instead of permanent.  
**Estimate:** 4–6 hours.

---

### Project 5 — Raise CATCHUP_SIG_LIMIT + Fix `processing_migrations` Mutex

**ROI ratio: MEDIUM / XS effort**

**Risk reduced:** Two small fixes that close narrow but real gaps:

1. `WS_CATCHUP_SIG_LIMIT = 8` → `25` in `supervisord.conf`. Wrap-close detection and cascade launch catch-up scans only look back 8 signatures. On a busy subprov wallet, a critical wrap-close from 10 txs ago is outside the window. Raising to 25 costs one extra RPC page at most.

2. `processing_migrations` cross-path mutex fix: the `processing_migrations.add(sig)` guard prevents the second path (e.g., PumpPortal) from processing a sig that the first path (Helius) started but failed on. Add a `finally: processing_migrations.discard(sig)` on RPC failure so the second path can retry. The `completed_migrations` guard still prevents double-processing on success.

**Expected benefit:** Wrap-close detection: 6 → 8. Migrations: 3 → 5 (partial, without the full inbox from Project 3).  
**Estimate:** 2 hours (env change + one `finally` block).

---

## Implementation Order

| Order | Project | Effort | Streams Fixed | Cumulative Gain |
|-------|---------|--------|--------------|-----------------|
| 1 | Treasury Webhook Inbox | S | Treasury + Subprov Discovery | +8+3 = +11 pts |
| 2 | Fix `_seen()` dedup + raise CATCHUP limit | XS | Cascade + Wrap-Close | +2+2 = +4 pts |
| 3 | Fix `processing_migrations` mutex | XS | Migrations (partial) | +2 pts |
| 4 | Birth retry queue (activate dead drainer) | S | Births | +3 pts |
| 5 | Migration Inbox | M | Migrations (complete) | +3 more pts |

Total engineering time for all 5: ~1 week.  
Total durability gain: every critical stream moves from CAPTURE FRAGILE to PARTIALLY or FULLY DURABLE.

---

## What Remains Out of Scope

- **vSol / Future Bound:** Re-enable flush + load on startup. Low consequence, trivial fix, do it last.
- **Telemetry:** Do not invest. Acceptable fragility by design.
- **PumpSwap-direct migration coverage:** Requires subscribing to `pAMMBay…` pool-creation events. This is a coverage gap (new events never arrive), not a durability gap (arriving events being lost). Separate project.
