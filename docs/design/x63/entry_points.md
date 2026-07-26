# x63 — All Entry Points That Enqueue Into wt_walkback_queue

Search performed: `grep -rn "enqueue_migration\|INSERT INTO wt_walkback_queue\|INSERT OR IGNORE INTO wt_walkback_queue" src/ scripts/`

Four distinct call sites found, plus the single row-insert implementation.

## 1. `src/core/walkback_queue.py:307-392` — `enqueue_migration()` (the only INSERT)
This is the sole function that ever inserts a row into `wt_walkback_queue`
(`INSERT OR IGNORE`, `walkback_queue.py:368-380`). All other entry points call
into this function; none insert directly except the test/shadow script noted
in §4.

## 2. `src/core/watchtower_attribution.py:146-147` — inside `store_migration()`
- **Trigger:** every call to `store_migration()`, i.e. every observed
  pump.fun migration event stored via Layer 1 of the attribution pipeline
  (`watchtower_attribution.py:122-147`).
- **Condition:** none beyond `store_migration` itself being called; wrapped
  in a bare `try/except: pass` (`watchtower_attribution.py:145-148`), so a
  failure here is silent.
- **Payload:** `mint=mint, creator=creator` — no `force`, no `live_conn`, no
  create-signature params passed explicitly (defaults apply).

## 3. `src/core/pumpfun_curve_listener.py:816` — creator-unknown fallback
- **Trigger:** inside the migration-processing path, when `creator_wallet`
  could not be resolved from `wt_staged_wallets`/`token_analysis` at
  migration time (`pumpfun_curve_listener.py:~808-826`).
- **Condition:** gated on `if not creator_wallet:` — i.e. only fires when the
  creator lookup chain (staged wallets, `wt_creator_launches` backfill) came
  up empty.
- **Payload:** `mint=mint, creator=_creator_for_wb` where `_creator_for_wb`
  is `token_analysis.earliest_tx_creator` (possibly `None`). Routed through
  `database_write_service.submit()` rather than calling `enqueue_migration`
  directly on a raw connection — uses a serialized write-service wrapper
  (`pumpfun_curve_listener.py:817-826`) instead of a bare connection, unlike
  entry point #2.
- Note: this is a **retrospective/backfill-style enqueue** triggered by a
  live migration event where creator attribution was incomplete, not a pure
  "new migration" trigger like #2.

## 4. `scripts/x54_shadow_validation.py:66` — direct INSERT, bypasses `enqueue_migration`
- **Trigger:** manual script run, not a pipeline trigger.
- **Condition:** whatever the script's own logic gates on (not audited in
  depth here — flagged as out of scope of the production pipeline; this is a
  validation/shadow script, not a triggered entry point).
- **Payload:** `INSERT OR IGNORE INTO wt_walkback_queue(mint,creator,walkback_class,status,attempts,enqueued_at,updated_at) VALUES (?,?,?,'running',1,?,?)`
  — inserts directly as `status='running', attempts=1`, **skipping**
  `classify_creator()`'s zero-RPC classification and **skipping**
  `evaluate_and_enqueue_candidate()`. This is the only enqueue path in the
  codebase that does not go through `enqueue_migration()`.

## What does NOT enqueue
- `src/ops/funding_boundary_backfill.py` — reads `wt_walkback_queue` and
  `wt_attribution_outcomes` to populate `wt_funding_boundary`; does not
  enqueue anything (`funding_boundary_backfill.py:44-51`, LEFT JOIN read-only).
- `src/ops/detection_reconciliation.py` — fully read-only against
  `wt_walkback_queue.intelligence_outcome`; confirmed no writes anywhere in
  the file (`detection_reconciliation.py:9-13` docstring, and no
  `conn.execute("INSERT`/`UPDATE` calls present).
- `src/ops/watchtower_candidates.py:evaluate_and_enqueue_candidate()` does
  **not** insert into `wt_walkback_queue` — it only UPDATEs `priority` on an
  existing row (`watchtower_candidates.py:170-174`) and inserts into
  `wt_watchtower_candidates`, a separate table. It is always called from
  inside `enqueue_migration()` itself (both the "already exists" branch,
  `walkback_queue.py:326-333`, and the post-insert branch,
  `walkback_queue.py:382-385`), so it can never run before a
  `wt_walkback_queue` row exists for that mint.
- No script under `scripts/` with "backfill" in the name was found calling
  `enqueue_migration` (search confirmed only `x54_shadow_validation.py`
  touches the table directly, via raw INSERT, not via backfill logic).

## Duplicate-prevention mechanism
`enqueue_migration()` is idempotent on `mint` via `INSERT OR IGNORE`
(`walkback_queue.py:368`, PK is `mint`). Before that, if `force=False`
(the default) and a row already exists, the function short-circuits: it
still calls `evaluate_and_enqueue_candidate()` (so a later-arriving X63
signal can still raise priority on an existing row) but returns the
existing `walkback_class` without touching `status`/`attempts`
(`walkback_queue.py:321-333`). `force=True` deletes the existing row first
(`walkback_queue.py:365-366`) — no caller in the codebase passes
`force=True` (not found in the grep results above).
