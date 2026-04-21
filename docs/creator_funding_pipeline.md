# Creator Funding Pipeline

How the system establishes token creators, checks their funding history, and times that work against migration.

---

## 1. What "creator" means

Every Pump.fun token has a single creator — the wallet that signed the `create` instruction on-chain. We store this in two columns:

| Column | Source | When set |
|---|---|---|
| `pf_ws_creator` | Single `getTransaction` on `create_tx_signature` | As soon as we process the create tx or curve completes |
| `earliest_tx_creator` | Multi-RPC `getSignaturesForAddress` fallback | When `create_tx_signature` is unknown |

`pf_ws_creator` is preferred. `earliest_tx_creator` is a fallback and carries more RPC cost.

---

## 2. Creator resolution paths

### Path A — single getTransaction (fast, 1 RPC)
`_ensure_pf_ws_creator(mint, reason=...)` in `pumpfun_curve_listener.py:4244`

1. Read `create_tx_signature`, `pf_ws_creator`, `earliest_tx_creator` from `token_analysis`.
2. If `pf_ws_creator` already set → return immediately (no RPC).
3. If `create_tx_signature` is available → call `getTransaction`, validate it is a Pump.fun CREATE tx, infer creator from fee payer.
4. Write `pf_ws_creator` to DB → call `_enqueue_creator_funding_job()`.

This path costs **1 Helius credit**.

### Path B — getSignaturesForAddress fallback (slow, many RPC)
Used inside `_ensure_pf_ws_creator` when `create_tx_signature` is NULL, and also called directly from post-migration analysis.

1. `analyzer.get_creator_from_earliest_tx()` — queries all signatures for the mint, walks back to the earliest, infers creator.
2. Costs approximately **5–20 credits** depending on token age.
3. Result stored in `earliest_tx_creator` (not `pf_ws_creator`).

### Path C — DB only (zero RPC)
If the caller already has a creator from `pf_ws_creator` or `earliest_tx_creator` in DB, it is passed directly to `_enqueue_creator_funding_job()` without any RPC. Used in the `migration_already_known` branch at `pumpfun_curve_listener.py:6417`.

---

## 3. When creator resolution is triggered

Three distinct moments trigger `_ensure_pf_ws_creator` or a direct enqueue:

### 3a. Curve complete event (pre-migration)
**Trigger:** `accountSubscribe` push on the bonding curve PDA → `complete` bit flips `false → true`.  
**Handler:** `_handle_curve_complete_transition()` at `pumpfun_curve_listener.py:8364`  
**Timing:** fires before migration tx lands on-chain (typically seconds to minutes earlier).

Flow:
```
accountSubscribe push
  → complete=true detected
  → persist curve_complete=1, curve_completed_at, curve_completed_slot
  → premig_log [TIMING] curve_complete_event
  → _ensure_pf_ws_creator(mint, reason="curve_complete")   ← Path A
  → _enqueue_creator_funding_job(..., source="pf_ws_creator_curve_complete", delay_seconds=0)
  → premig_log [TIMING] enqueue_start
```

If Path A fails (create_tx unavailable), falls back to:
```
  → read pf_ws_creator or earliest_tx_creator from DB
  → _enqueue_creator_funding_job(..., source="curve_complete_fallback", delay_seconds=0)
  → premig_log [TIMING] enqueued_fallback
```

**Source tag:** `pf_ws_creator_curve_complete` or `curve_complete_fallback`

### 3b. Migration tx detected — new token
**Trigger:** PumpSwap migration tx parsed from logsSubscribe/transactionSubscribe.  
**Handler:** `_process_migration_with_mint()` at `pumpfun_curve_listener.py:6401` (new token branch at line 6495).

```
migration tx detected
  → _create_minimal_token_entry(mint)
  → _mark_token_migrated_in_db(mint, migrated_at=now)
  → asyncio.create_task(_ensure_pf_ws_creator(mint, reason="migration"))   ← Path A
    → _enqueue_creator_funding_job(..., source="pf_ws_creator_migration", delay_seconds=0)
```

**Source tag:** `pf_ws_creator_migration`

### 3c. Migration tx detected — token already tracked
**Trigger:** Same migration tx, but `_token_exists_in_db(mint)` returns True (token was pre-tracked from curve events).

```
migration tx detected
  → token already in DB
  → _get_resolved_creator_for_mint(mint)  ← Path C, reads existing DB column
  → _enqueue_creator_funding_job(..., source="migration_already_known", delay_seconds=0)
  → asyncio.create_task(_ensure_pf_ws_creator(mint, reason="migration:pre_tracked"))
    → returns immediately if pf_ws_creator already set
```

**Source tag:** `migration_already_known`

---

## 4. Which tokens get curve watcher subscriptions

`_get_hot_bonding_curves()` at `pumpfun_curve_listener.py:8435`:

```sql
SELECT bonding_curve_pda FROM token_analysis
WHERE bonding_curve_pda IS NOT NULL
  AND lifecycle_stage = 'bonding_curve'
  AND curve_complete = 0
  AND is_about_to_migrate = 1
```

`is_about_to_migrate = 1` is set when bonding curve progress reaches the threshold (near-graduation). This keeps the subscription count low (~100–150 at any time vs 1,800+ if `progress >= 50%` were used).

New PDAs are added dynamically via `watch_bonding_curve(pda)` → `_curve_watch_queue` as tokens are first discovered. Unsubscribed immediately after `complete=true` fires.

---

## 5. The creator_funding_queue

`creator_funding_queue` is a persistent job queue that survives restarts. Each row represents one pending, running, or completed extraction.

Key columns:
- `creator_address` — the creator to analyze
- `mint` — associated token
- `source` — which pipeline enqueued it (see tags above)
- `status` — `pending | running | complete | failed`
- `next_attempt_at` — unix timestamp when worker should pick it up (always `now+0` since delay was removed)
- `create_tx_signature` — passed to extractor to skip re-lookup
- `curve_completed_slot` / `enqueued_slot` — for timing analysis
- `created_at` — when job was enqueued (used as `creator_funding_enqueued_at` in UI)
- `funding_extracted_at` — stamped when extraction completes

Duplicate guard: `ON CONFLICT(creator_address, mint) DO NOTHING` + explicit pre-check at `pumpfun_curve_listener.py:3516`.

---

## 6. Funding extraction

`extract_funding_for_new_token(creator, migration_timestamp_str, create_tx_signature, mint)` in `realtime_creator_funding_extractor.py:2318`.

### Repeat creator cache check (early return)
At the top of extraction:

```python
SELECT COUNT(*) FROM creator_funders WHERE creator_address = ?
```

If `COUNT > 0` → skip full extraction, return `{"skipped": True, "cached_funders": N}`, record ~100 saved credits via `record_cache_event()`.

This avoids full Helius scans for creators who have launched multiple tokens. Savings logged to RPC dashboard under `section="creator_funding"`, `optimization_layer="creator_funders_cache"`.

### Full extraction (new creator)
For unknown creators, runs:
1. `process_new_token(creator, migration_timestamp_str)` — `getSignaturesForAddress` + `getTransaction` per sig to build funder list.
2. Concurrently:
   - `check_create_tx_for_jitotip()` — checks if creator was jito-tipped at launch
   - `extract_outgoing_transfers()` — SOL sent out of creator wallet
   - `check_transfers_for_debridge()` — deBridge cross-chain signals
   - `check_transfers_for_axiom()` — Axiom trading bot signals

Results saved to `creator_funders` table. `fully_analyzed` flag is set to `1` only when `cex_exchange IS NOT NULL OR is_classified = 1`, so it is NOT a reliable "done" indicator for all creators — always use `COUNT(*)` for cache checks.

### 90s timeout issue
Full extraction can timeout for active creators (many signatures). If `creator_funders` has any existing rows from a prior token by the same creator, the cache check fires first and extraction is skipped entirely — so the timeout only hits truly new high-volume creators.

---

## 7. Timing model

Three timing points are captured per migrated token and shown on the `/pumpfun` recent migrations cards:

| Point | Source | Meaning |
|---|---|---|
| `curve_completed_at` | `token_analysis.curve_completed_at` | When accountSubscribe push detected `complete=true` |
| `creator_funding_enqueued_at` | `creator_funding_queue.created_at` | When job was inserted into queue |
| `creator_funding_extracted_at` | `creator_funding_queue.funding_extracted_at` | When extraction finished |

All three are displayed as deltas relative to `migrated_at`:

```
timing | curve=3s before | enqueue=2s before | extract=96s after
```

Interpretation:
- **curve before migration**: the accountSubscribe event fired N seconds before the migration tx was processed. This is the pre-signal lead time.
- **enqueue ≈ curve**: enqueue happens immediately after creator resolution (delay=0), so typically within 1–2s of curve.
- **extract after migration**: full Helius scan takes time. For known creators (cache hit), this would show as near-zero. For new creators with many transactions, can exceed 60s.

`[TIMING]` lines in `logs/premigration.log` track these at millisecond granularity:
```
[TIMING] mint=... curve_complete_event t=+0.000s slot=...
[TIMING] mint=... creator_resolve_start t=+0.012s
[TIMING] mint=... creator_resolve_done t=+0.234s creator=yes
[TIMING] mint=... enqueue_start t=+0.235s
[TIMING] mint=... migration_arrived t=1714000000 curve_complete_was=yes delta_since_complete=8s
```

---

## 8. Multiple creators / edge cases

Pump.fun tokens have exactly one creator. However:

- **`pf_ws_creator` vs `earliest_tx_creator`**: Two different resolution methods can produce different results if the CREATE tx fee payer is a hot wallet or contract. `pf_ws_creator` (strict CREATE validation) is preferred.
- **Creator already resolved**: `_ensure_pf_ws_creator` returns early if `pf_ws_creator` is already set — no RPC, no re-enqueue.
- **Known repeat creators**: 50+ token creators hit the cache check and skip extraction. Their existing `creator_funders` rows are reused for classification.
- **NULL create_tx_signature**: Falls back to `get_creator_from_earliest_tx()` (Path B). More expensive but works for tokens seen only at migration without prior tracking.
- **Creator resolution failure**: If both Path A and Path B fail, `_handle_curve_complete_transition` falls back to whatever is already in `pf_ws_creator` or `earliest_tx_creator` columns. If those are also NULL, no enqueue happens for that token at curve complete time. The migration path will retry at arrival.

---

## 9. Source tags reference

| Source tag | Enqueue path | Creator resolution |
|---|---|---|
| `pf_ws_creator_curve_complete` | Curve complete event | Path A (`_ensure_pf_ws_creator`, `create_tx_signature` available) |
| `curve_complete_fallback` | Curve complete event | Path C (existing DB column, after Path A failed) |
| `pf_ws_creator_migration` | Migration tx — new token | Path A (called at migration) |
| `migration_already_known` | Migration tx — pre-tracked token | Path C (existing DB column) |
| `creator_discovery` | Background discovery sweep | Various |

---

## 10. Cross-process state

`logs/curve_watch_state.json` is written by the listener after every subscribe/unsubscribe. Flask reads it to show live subscription counts on `/funding-queue`. Format:

```json
{
  "subscriptions": [{"pda": "...", "mint": "..."}],
  "updated_at": 1714000000
}
```

Stale if listener is not running — Flask endpoint returns `{"error": "..."}` which the UI surfaces as "Unavailable".
