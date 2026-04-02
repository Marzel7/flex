# Why "Snaps" Shows 0 in /token-behaviour — Root Cause Analysis

## What the UI Shows

The `/token-behaviour` page shows a `snapshot_count` column. For many tokens it reads **0** even when
price data has been collected. Two separate bugs cause this — one affecting display, one affecting
actual data collection.

---

## Bug 1 — Display reads stale count from `token_behavior`, not live `token_price_snapshots`

### Where it happens
`src/core/flex_dashboard_routes.py` — the vault/behaviour SQL query (around line 615):

```sql
tb.snapshot_count AS snapshot_count   -- reads from token_behavior table
```

`templates/flex_dashboard.html` (lines 3384, 3473):

```javascript
token.snapshot_count || 0
```

### Why it's wrong

`snapshot_count` in `token_behavior` is written by the classifier (`src/core/token_behaviour_monitor.py`)
when it last ran. If a token was classified at T=0 with 50 snaps, and the listener has since collected
200 more, the UI still shows 50 — or 0 if the token has never been classified.

The **live** count is always `SELECT COUNT(*) FROM token_price_snapshots WHERE mint = ?`.
The stored `token_behavior.snapshot_count` is a frozen snapshot from classification time.

### Tokens most affected
- Any token registered after the last classification run
- Tokens classified early (8-snap tier) before they had meaningful history
- Tokens with `token_behavior` row absent entirely (NULL → displayed as 0)

### Fix needed
In `src/core/flex_dashboard_routes.py`, replace the `tb.snapshot_count` column alias with a live
subquery for pages where accuracy matters:

```sql
(SELECT COUNT(*) FROM token_price_snapshots WHERE mint = tpa.mint) AS snapshot_count
```

> ⚠️ This is a correlated subquery against a 2.8M-row table. Add a covering index first or batch
> the counts in Python with a single GROUP BY query (same pattern used for `base_account_counts`
> to fix the Vaults page performance in a prior session).

---

## Bug 2 — Price fallback poll never wrote snapshots, so count was genuinely 0

### Where it happens
`src/core/price_worker.py` — `_fetch_pool_prices_async()` (async fallback, runs every 60s):

```python
# ❌ BEFORE FIX: computed price but never persisted snapshot
new_cache[mint] = aggregated
# ... only updated token_analysis, no _store_snapshot() call
```

`_recompute_prices_from_ws_state()` (WebSocket path, runs every 10s):

```python
# ✅ Did store snapshots — but only works once WS has received events for subscribed vaults
self.price_service._store_snapshot(token_price)
```

### Why it mattered for fresh tokens

Newly registered tokens subscribe to WebSocket vault account events. Until the first WS event
arrives (can take 10–60s or more depending on trading activity), `_recompute_prices_from_ws_state`
has no data for them.

`_fetch_pool_prices_async` is the fallback: it does a full `getMultipleAccounts` batch poll every
60s and **can** compute prices for all registered pools regardless of WS state. But before the fix
it stored results only in `token_analysis` (for UI display) — it never called `_store_snapshot()`,
so `token_price_snapshots` stayed empty.

Result: a token could be tracked for hours, show a price in the dashboard, and still have 0 rows
in `token_price_snapshots` — which means 0 snaps, and never gets classified.

### Fix applied
Added `_store_snapshot()` calls to `_fetch_pool_prices_async` immediately after computing prices
(same pattern as `_recompute_prices_from_ws_state`):

```python
for mint, token_price in new_cache.items():
    try:
        self.price_service._store_snapshot(token_price)
    except Exception as e:
        logger.debug(f"Failed to store snapshot for {mint}: {e}")
```

**File:** `src/core/price_worker.py` — `_fetch_pool_prices_async()`, after line ~1057.

---

## Bug 3 — `token_analysis.pool_address` not written on pool registration

### Where it happens
`src/core/pumpfun_curve_listener.py` — `_register_pool_inner()`:

```python
# Registers pool in token_pool_accounts ✅
registered = await discovery.discover_and_register_pool(pool_address, mint, ...)

# ❌ BEFORE FIX: never wrote pool_address to token_analysis
```

`_get_pool_address()` (used by on-chain price extraction):

```python
cursor.execute("SELECT pool_address FROM token_analysis WHERE mint = ?", (token_mint,))
```

### Why it mattered

The listener's on-chain price path (`_get_price_from_pool_account`) looks up the pool address from
`token_analysis.pool_address`. `pool_discovery.py` writes to `token_pool_accounts` but never touches
`token_analysis`. So `_get_pool_address` returned `None` for every newly registered token → skipped
on-chain fetch → fell back to DexScreener → DexScreener also failed (token too new) → no price
returned → no snapshot written → the price worker's WS path also had no seed data.

This affected 162 tokens at time of discovery.

### Fix applied

1. `src/core/pumpfun_curve_listener.py` — `_register_pool_inner()`: after successful registration,
   immediately writes `pool_address` to `token_analysis`:

```python
with DB_WRITE_LOCK:
    _conn = sqlite3.connect(DB_PATH, timeout=10)
    _conn.execute(
        "UPDATE token_analysis SET pool_address = ? WHERE mint = ?",
        (pool_address, mint),
    )
    _conn.commit()
    _conn.close()
```

2. DB backfill: ran once to fix the 162 existing affected tokens:

```sql
UPDATE token_analysis
SET pool_address = (
    SELECT tpa.pool_address FROM token_pool_accounts tpa
    WHERE tpa.mint = token_analysis.mint
      AND tpa.pool_address IS NOT NULL AND tpa.pool_address != ''
    LIMIT 1
)
WHERE (pool_address IS NULL OR pool_address = '')
  AND EXISTS (SELECT 1 FROM token_pool_accounts tpa WHERE tpa.mint = token_analysis.mint ...);
-- 162 rows updated
```

---

## Summary Table

| Bug | Symptom | Root cause | File(s) affected | Fixed? |
|-----|---------|------------|-----------------|--------|
| 1 — Stale display count | UI shows old/0 snaps even when real snaps exist | `snapshot_count` read from `token_behavior` (classification-time snapshot), not live table | `flex_dashboard_routes.py`, `flex_dashboard.html` | ❌ Still needs fix |
| 2 — Fallback poll skipped snapshot write | Tokens tracked for hours with 0 rows in `token_price_snapshots` | `_fetch_pool_prices_async` computed prices but never called `_store_snapshot()` | `price_worker.py` | ✅ Fixed |
| 3 — Pool address not propagated | On-chain price extraction returned None, 100% DexScreener fallback (also failing) | `token_analysis.pool_address` never written by `_register_pool_inner` | `pumpfun_curve_listener.py` | ✅ Fixed + DB backfill |

---

## Remaining Work (Bug 1)

The display-layer fix for Bug 1 requires care because a naive correlated subquery will be slow
(2.8M rows). Recommended approach in `flex_dashboard_routes.py`:

```python
# After fetching rows, bulk-count in one query (same pattern as base_account_counts)
mints = [r['mint'] for r in rows]
placeholders = ','.join('?' * len(mints))
snap_rows = conn.execute(
    f"SELECT mint, COUNT(*) as cnt FROM token_price_snapshots WHERE mint IN ({placeholders}) GROUP BY mint",
    mints,
).fetchall()
snap_counts = {r['mint']: r['cnt'] for r in snap_rows}
```

Then in `_vault_row_to_dict`, replace `row['snapshot_count']` with `snap_counts.get(row['mint'], 0)`.
