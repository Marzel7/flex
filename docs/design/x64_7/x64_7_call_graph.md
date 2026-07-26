# X64.7 — Phase 2: End-to-End CREATE Call Graph

Traced by a dedicated read-only Explore pass across
`src/core/pumpfun_curve_listener.py` (11,932 lines),
`src/core/creator_resolution_queue.py`, `main.py`, and
`src/analysis/pump_fun_post_migration_analyzer.py`. Every load-bearing
claim below was independently spot-checked by direct file read against
the cited line numbers before being accepted into this document.

## Detection functions

| Function | file:line | Signal source | Method |
|---|---|---|---|
| `_is_pumpfun_create_candidate` | `pumpfun_curve_listener.py:5519` | WS `logsSubscribe` on `PUMPFUN_PROGRAM` | Cheap log-string prefilter; excludes migrate/buy/sell, matches `instruction: create`/`initializemint`(`2`). Does not fetch tx. |
| `handle_birth` | `pumpfun_curve_listener.py:6017` | WS routing, webhook drain, birth reconciler | Fetches tx, derives mint, validates via `PostMigrationAnalyzer._validate_pumpfun_create_tx` — the authoritative CREATE validator. |
| Webhook prefilter | `main.py` route `/api/webhook/pumpfun-birth` (~8967) | Helius enhanced-transactions webhook | Same log-string prefilter; writes `INSERT OR IGNORE INTO webhook_birth_queue`; no mint/creator extraction in the handler itself, deferred to drain loop. |
| `create_interceptor.py` | `src/core/watchtower/create_interceptor.py` (gated `ENABLE_CREATE_INTERCEPTOR`) | Helius webhook on treasury/signaller wallets | Predictive pre-CREATE trading-signal subsystem — **not confirmed to write `token_analysis`/`creator_funding_queue`**; orthogonal to the durable-persistence pipeline, flagged for a separate audit if in scope. |

Mint derivation: `_extract_mint_from_tx` (authoritative), `_extract_mint_from_logs`
(fallback), `_pick_confident_pumpfun_mint`/`_infer_indirect_pumpfun_mint`
(ambiguous cases, lines 2477-2530+).

Creator derivation: `PostMigrationAnalyzer._infer_creator_from_tx` (birth
path, `handle_birth:6036` — verified, this line is `creator =
analyzer._infer_creator_from_tx(tx_data)` in the current file),
`analyzer.get_creator_from_earliest_tx()` (migration path, gated by
`CREATOR_BACKFILL_ENABLED`, default `"0"` — **verified**:
`run_listener.sh:7` sets `export CREATOR_BACKFILL_ENABLED=0` in
production), `_ensure_pf_ws_creator` (curve-complete path, `5743`),
PumpPortal `traderPublicKey` payload field (webhook fast-path, `10812`).

## Writes to `token_analysis`

| Function | file:line | Trigger | Failure handling | Commit |
|---|---|---|---|---|
| `_create_minimal_token_entry` | `7740-7783` | migration first-seen | 6x retry on lock; broad `except Exception` on final failure — logs `[DB_ERROR]`, returns silently, no re-raise, no dead-letter | Synchronous |
| `_update_token_entry_with_creator` | `7788-7850` | called ONLY from line 9012+ (post-instrumentation: now the ledger-write block precedes it, see `x64_7_implementation.md`), gated on `earliest_creator` being truthy | Same retry/except shape | Synchronous, single `UPDATE` sets `earliest_tx_creator`, `created_at`, `bonding_curve_pda`, `create_tx_signature` together (line 7818) |
| `_insert_bonding_curve_token` | `5637-5720` | called from `handle_birth` and webhook fast-path replay | `try/except Exception` → logs `[BIRTH] ⚠ Failed to insert...`, returns — no retry, no dead-letter | Synchronous |
| Portal fast-path `UPDATE ... pf_ws_creator` | `5809-5824` | migration/curve-complete with `_portal_vsol` cache hit | `try/except Exception as e: log_print(...)` — failure logged but swallowed | Synchronous, inside `self.db_lock` |

**Confirmed, load-bearing finding**: `_update_token_entry_with_creator`
is the **only** function that writes `bonding_curve_pda`/
`create_tx_signature` at migration time, and per line 8996 (`if
earliest_creator:`), it is entirely skipped when no creator was resolved
by that point — which, per the `CREATOR_BACKFILL_ENABLED=0` production
default, is the common case whenever no fast-path/portal creator was
already cached.

## `creator_funding_queue` write paths

Single primary writer `_enqueue_creator_funding_job`
(`pumpfun_curve_listener.py:4468-4620`), hard-gated
`if not creator or not mint: return False` (verified at line ~4483). 10
call sites (lines 3206, 3228, 5397, 5789, 5815, 5924, 8187, 9132, 11140,
plus the periodic sweep below) all funnel through it. **Confirmed
universal property**: every path requires a non-null creator; **none**
require a non-null `create_tx_signature` — several sites explicitly pass
`create_tx_signature=None`.

Second, independent writer: `_enqueue_funding_handoff` in
`src/core/creator_resolution_queue.py:250-296`, called from
`enqueue_missing_funding_jobs` (periodic sweep, line 302). **Verified via
direct read**: this sweep's own SQL WHERE clause requires
`COALESCE(pf_ws_creator, earliest_tx_creator) IS NOT NULL` — structurally
excludes every creator-NULL mint from ever being backfilled by this path.

## Reconciliation / backfill mechanisms

| Mechanism | file:line | Gate | Completeness property |
|---|---|---|---|
| `_birth_reconciler_loop` | `11460-11562` | Runs every 60s, but (per `PUMPFUN_BIRTH_RECONCILE_ONLY_WHEN_PUMPPORTAL_DOWN=1`, the production default, confirmed in `run_listener.sh`) **only executes its sweep body when PumpPortal is fully disconnected** | Availability-based, not completeness-based — protects against full outages, not partial/intermittent message loss while nominally "up" |
| `webhook_birth_queue` drain | `10758-10845` | 5s poll loop, always running | Independent redundant channel; payload-replay avoids RPC, Helius-sourced rows without payload fall back to `handle_birth` (same silent-exit risk) |
| `enqueue_missing_funding_jobs` | `creator_resolution_queue.py:298-341` | `lifecycle_stage='migrated'` AND creator non-null | Cannot help creator-NULL rows (structural exclusion, see above) |
| `enqueue_missing_migrated_tokens`/`enqueue_missing_creator` | `creator_resolution_queue.py:139-230` | `lifecycle_stage='migrated'` | Bonding-curve-stage-only tokens (never migrated) are invisible to this backfill entirely |
| `anchor_reconciliation.py` (X64.5/X64.6) | `src/ops/anchor_reconciliation.py` | zero-RPC, ops-side | Only re-reads `creator_funding_queue`/`token_analysis` — **cannot recover a signature that was never captured by any upstream writer in the first place**, confirmed by `classify_stuck_row` returning `ANCHOR_STILL_MISSING`/`MINT_NOT_FOUND` when both are empty |

## Silent failure paths (Phase 4 finding, load-bearing)

**Verified by direct read** (`handle_birth`, lines 6024-6038 pre-
instrumentation): three early returns with **zero logging of any kind**:
```python
tx_data = await self._get_transaction_cached(signature)
if not tx_data:
    return                      # no log line
mint = await self._extract_mint_from_tx(tx_data)
if not mint:
    return                      # no log line
...
if not validation.get("is_pumpfun_create"):
    return                      # no log line
```
A CREATE candidate that reaches `handle_birth` but fails tx-fetch,
mint-extraction, or CREATE-validation vanished with **zero trace in
logs** prior to this task's Phase 4 instrumentation (see
`x64_7_implementation.md`). This made it structurally impossible to
distinguish "CREATE never received" from "CREATE observed but silently
rejected" from logs alone — now fixed via the required
`CREATE_TX_RECEIVED`/`CREATE_PARSE_STARTED`/`CREATE_PARSE_REJECTED`
event names.

## Ordering / race between migration and birth processing

**Confirmed structurally**: `handle_migration` and `handle_birth` are
independent, concurrently-scheduled `asyncio.create_task` calls from the
same WS message loop — no ordering guarantee for the same mint. The
`ON CONFLICT(mint) DO UPDATE SET ... COALESCE(...)` pattern
(`_insert_bonding_curve_token`, line 5669 area) means whichever writer
arrives first wins for `create_tx_signature`/creator fields — this
prevents *clobbering* but does **not** guarantee eventual completeness:
if migration-time processing runs first and finds no creator
(`CREATOR_BACKFILL_ENABLED=0`), and no birth event for that mint is ever
separately observed/queued, `create_tx_signature` stays NULL
indefinitely. **This is the presumptive direct mechanism producing the
19 CREATOR_UNKNOWN stuck rows.**

No function was found that, given only a mint (no signature), re-derives
the CREATE transaction via a mint-scoped backward search — the only
signature-agnostic recovery mechanism is the *global*
`_birth_reconciler_loop`, availability-gated as above.

## Call-graph summary with explicit exit branches

```
WS logsSubscribe(PUMPFUN_PROGRAM)                Helius webhook (enhanced_transactions)
        │                                                   │
        ▼                                                   ▼
_is_pumpfun_create_candidate (5519)         main.py /api/webhook/pumpfun-birth
  ├─ False → [EXIT, unclassified]                    prefilter, same logic
  ▼                                                          │
asyncio.create_task(handle_birth) (11043)          INSERT OR IGNORE webhook_birth_queue
        │                                                    │
        ▼                                          drain_webhook_birth_queue (5s poll)
handle_birth (6017)  ◄─────────────────────────────────────  │
  ├─ tx_data fetch fails → [EXIT — now logged: CREATE_PARSE_REJECTED]
  ├─ mint extraction fails → [EXIT — now logged: CREATE_PARSE_REJECTED]
  ├─ is_pumpfun_create=False → [EXIT — now logged: CREATE_PARSE_REJECTED]
  ▼
[X64.7: CREATE_LEDGER_WRITE_ATTEMPT → wt_create_event_ledger, creator-independent]
  ├─ ledger write fails → [LOGGED: CREATE_LEDGER_WRITE_FAILED, birth processing continues]
  ▼
_insert_bonding_curve_token (5637) → token_analysis (birth fields)
  ├─ DB write exception → [LOGGED EXIT, no retry, no dead-letter]
        │
        ▼ (separately, NOT from handle_birth — independent, racing task)
handle_migration (10174) → _process_migration_with_mint
  ▼
creator extraction (8955-9016)
  ├─ no fast-path creator AND CREATOR_BACKFILL_ENABLED=0 (production default)
  │     → earliest_creator stays None → _update_token_entry_with_creator NEVER CALLED
  │     → [EXIT: token_analysis.create_tx_signature/bonding_curve_pda stay NULL]
  ▼ (if earliest_creator resolved)
_update_token_entry_with_creator (7788) → UPDATE token_analysis (7818)
  ▼
_enqueue_creator_funding_job (4468) [10 call sites]
  ├─ creator is None → return False [HARD GATE EXIT]
  ▼
creator_funding_queue: INSERT (create_tx_signature may still be NULL)
        │
        ▼
[Backfill sweeps — periodic, NOT event-driven, both creator-gated]
enqueue_missing_funding_jobs — creator IS NULL → [EXIT: row invisible]
_birth_reconciler_loop — PumpPortal connected → [EXIT every cycle: sweep body skipped]
        │
        ▼
wt_walkback_queue (ops DB) — enqueue_migration
  ├─ [X64.7]: resolve_anchor_with_priority now checks wt_create_event_ledger FIRST
  │     → if the ledger write above succeeded, anchor recovered here even with
  │       creator=NULL — the fix
  ├─ neither ledger nor legacy sources populated → still [TERMINAL STUCK STATE]
```
