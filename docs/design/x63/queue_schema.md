# x63 — wt_walkback_queue: Exact Current Schema

Source: `sqlite3 database/wt_ops_v2.db ".schema wt_walkback_queue"` run 2026-07-21,
cross-checked against `ensure_schema()` in `src/core/walkback_queue.py:78-149` and
the ALTER TABLE block in `src/ops/watchtower_candidates.py:52-59`.

The live table has more columns than `ensure_schema()`'s `CREATE TABLE` literal
because several columns were added later via `ALTER TABLE ... ADD COLUMN` from
three different modules. `CREATE TABLE IF NOT EXISTS` is a no-op on an existing
table, so the authoritative column list is the live schema, not the CREATE TABLE
literal in `walkback_queue.py`.

## Column-by-column (live schema)

| Column | Type | Origin | Written by | Read by |
|---|---|---|---|---|
| `mint` | TEXT NOT NULL, PK | `walkback_queue.py:107` | `enqueue_migration()` INSERT (`walkback_queue.py:368-380`) | every query in the pipeline |
| `creator` | TEXT | `walkback_queue.py:108` | insert; `_process_row` FULL_WALKBACK branch UPDATE when recovered from DB (`walkback_worker.py:921-923`) | `classify_creator`, `_process_row`, `attribution_outcome.py` |
| `subprov` | TEXT | `walkback_queue.py:109` | insert; `_mark_complete` `COALESCE(subprov,?)` (`walkback_worker.py:585`) | classification lookups, `_process_row` |
| `treasury` | TEXT | `walkback_queue.py:110` | insert; `_mark_complete` `COALESCE(treasury,?)` (`walkback_worker.py:585`) | dashboard, `attribution_outcome.py` |
| `walkback_class` | TEXT NOT NULL DEFAULT 'FULL_WALKBACK' | `walkback_queue.py:111` | insert only, never updated after enqueue | `drain_batch` row selection payload, `_process_row` dispatch (`walkback_worker.py:874,915,892`) |
| `attribution_source` | TEXT | `walkback_queue.py:112` | insert only (`src` from `classify_creator`) | not read elsewhere in-repo besides dashboards (not traced further) |
| `intelligence_outcome` | TEXT | `walkback_queue.py:113` (dup ALTER at `:81`) | insert (zero-RPC classes); `set_intelligence_outcome`; `_mark_complete`; `_mark_failed`/`_mark_exhausted` do NOT set this except `_mark_exhausted` sets `NO_ATTRIBUTION_FOUND` (`walkback_worker.py:652`) | `queue_stats`, `detection_reconciliation.py:206`, `funding_boundary_backfill.py` |
| `status` | TEXT NOT NULL DEFAULT 'pending' | `walkback_queue.py:114` | insert (`pending`/`complete`/`skipped`/`waiting`); `_mark_running`→`running`; `_mark_complete`→`complete`; `_mark_failed`/`_mark_exhausted`→`failed`; `recover_stalled_running_jobs`→`pending`/`failed` | `drain_batch` WHERE clause, `build_walkback_health`, `queue_stats` |
| `rpc_used` | INTEGER NOT NULL DEFAULT 0 | `walkback_queue.py:115` | insert (0); every `_mark_*` does `rpc_used=rpc_used+?` | `queue_stats.rpc_total`, `funding_boundary_backfill.py` |
| `attempts` | INTEGER NOT NULL DEFAULT 0 | `walkback_queue.py:116` | incremented inside `deep_walkback.claim_with_lease` (called from `_mark_running`, not shown in walkback_queue.py/walkback_worker.py directly — see `src/core/deep_walkback.py`) | `drain_batch` WHERE `attempts < MAX_ATTEMPTS`, `finalize_exhausted_pending`, exception handler in `_process_row` (`walkback_worker.py:1044`) |
| `last_error` | TEXT | `walkback_queue.py:117` | `_mark_failed`, exception handler, `recover_stalled_running_jobs` sentinel strings | `build_walkback_health` write-failure detection (LIKE match) |
| `enqueued_at` | INTEGER NOT NULL DEFAULT strftime | `walkback_queue.py:118` | insert only, immutable | `drain_batch` ORDER BY (FIFO tiebreak), `queue_stats` trend window, health oldest-pending |
| `started_at` | INTEGER | `walkback_queue.py:119` | set by `deep_walkback.claim_with_lease` on claim; cleared to NULL by `recover_stalled_running_jobs` on requeue | `build_walkback_health` latency + stalled detection |
| `completed_at` | INTEGER | `walkback_queue.py:120` | every `_mark_complete`/`_mark_failed`/`_mark_exhausted`/`set_intelligence_outcome`/`link_only` | `build_walkback_health` latency, `queue_stats` |
| `updated_at` | INTEGER NOT NULL DEFAULT strftime | `walkback_queue.py:121` | every write path | `build_walkback_health` stalled fallback, `write_failures` window |
| `funder_wallet` | TEXT | `walkback_queue.py:122` (dup ALTER `:83`) | `_store_funder` (`walkback_worker.py:528-539`) | `promote_recurring_funders`, `funding_boundary_backfill.py` |
| `funding_mechanism` | TEXT | `walkback_queue.py:123` (dup ALTER `:84`) | `_store_funder` | `promote_recurring_funders` (mechanism aggregation) |
| `funder_amount_sol` | REAL | `walkback_queue.py:124` (dup ALTER `:85`) | `_store_funder` | `funding_boundary_backfill.py` |
| `funder_sig` | TEXT | `walkback_queue.py:125` (dup ALTER `:86`) | `_store_funder` | `funding_boundary_backfill.py`, `_mark_complete` LINEAGE_GAP path |
| `funder_slot` | INTEGER | `walkback_queue.py:126` (dup ALTER `:87`) | `_store_funder` | not read further in-repo besides display |
| `funder_block_time` | INTEGER | `walkback_queue.py:127` (dup ALTER `:88`) | `_store_funder` | `promote_recurring_funders` (min/max), `funding_boundary_backfill.py` |
| `claimed_by` | TEXT | **not in `walkback_queue.py` at all** — added elsewhere (likely `deep_walkback.py`, not audited here) | `deep_walkback.claim_with_lease` (worker_id) | lease reclaim logic in `deep_walkback.py` |
| `claimed_at` | INTEGER | same as above | `claim_with_lease` | lease reclaim logic |
| `lease_expires_at` | INTEGER | same as above | `claim_with_lease` | `drain_batch` WHERE clause: `status='running' AND COALESCE(lease_expires_at,0) < now` (`walkback_worker.py:1163`) |
| `next_retry_at` | INTEGER | not in `walkback_queue.py` | not observed being SET in the files read (likely `deep_walkback.py`) | `drain_batch` WHERE `COALESCE(next_retry_at,0) <= now` (`walkback_worker.py:1164`) — gates backoff |
| `path_state` | TEXT | `walkback_queue.py:360-363` sets on insert (`CREATE_ANCHORED`/`WAITING_FOR_CREATE_ANCHOR`/`QUEUED`) | insert; `deep_walkback.set_path_state` during `_expand_unknown_upstream` (`walkback_worker.py:824,1028`) | not read by worker selection logic directly |
| `termination_reason_json` | TEXT | not in `walkback_queue.py` | not observed set in files read (likely `deep_walkback.py`) | not observed read |
| `create_anchor_signature` | TEXT | `walkback_queue.py:372-374` insert | insert only from `create_signature` param | `_recover_create_signature_from_db` (`walkback_worker.py:786-790`) |
| `create_anchor_slot` | INTEGER | insert | insert only | not read further here |
| `create_anchor_block_time` | INTEGER | insert | insert only | not read further here |
| `create_anchor_source` | TEXT | insert | insert only | not read further here |
| `create_anchor_audit_state` | TEXT | insert (`VALID`/`MISSING_OR_MALFORMED`) | insert only | not read further here |
| `priority` | INTEGER NOT NULL DEFAULT 0 | **added by `src/ops/watchtower_candidates.py:52-59`, not `walkback_queue.py`** | `evaluate_and_enqueue_candidate` sets to `HIGH_PRIORITY=100` when a candidate fires (`watchtower_candidates.py:170-174`) | `drain_batch` `ORDER BY COALESCE(priority,0) DESC, enqueued_at ASC` (`walkback_worker.py:1165`) |
| `priority_reason` | TEXT | `watchtower_candidates.py:52-59` | same call, set to `"QUICK_BIRTH_MIGRATION+EPHEMERAL_WSOL_CREATOR_HANDOFF"` | not read by worker logic, display only |

## Indexes (all confirmed live)
- `ix_wbq_status (status, enqueued_at)` — `walkback_queue.py:131-132`
- `ix_wbq_class (walkback_class, status)` — `walkback_queue.py:134-135`
- `ix_wbq_outcome (intelligence_outcome)` — `walkback_queue.py:137-138`
- `ix_wbq_funder (funder_wallet)` — `walkback_queue.py:140-141`
- `ix_wbq_priority (status, priority DESC, enqueued_at ASC)` — `watchtower_candidates.py:60-63`, matches exactly the ORDER BY used by `drain_batch`.

## Important correction vs. the task's assumed schema
The task's "known" column list omits `claimed_by`, `claimed_at`, `lease_expires_at`,
`next_retry_at`, `path_state`, `termination_reason_json`, `create_anchor_*` (5
columns), `priority`, and `priority_reason` — 13 columns not in the assumed list.
Several of these (`claimed_by`, `claimed_at`, `next_retry_at`,
`termination_reason_json`) are not created anywhere in `walkback_queue.py` or
`walkback_worker.py`; they must originate in `src/core/deep_walkback.py`, which
was referenced (`deep_walkback.claim_with_lease`, `deep_walkback.set_path_state`)
but not opened in this audit — flagged here as **not fully traced**, since its
`ensure_schema()` is called at `walkback_queue.py:145-146` and is presumably
where these columns are added.
