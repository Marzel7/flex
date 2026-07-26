# X64.5 — CREATE Anchor Race Recovery: Master Audit & Implementation Record

Companion documents: [x64_5_backfill_dry_run.md](x64_5_backfill_dry_run.md)
(Phase 2/7 population audit), [x64_5_implementation.md](x64_5_implementation.md)
(Phases 3-8 implementation detail), [x64_5_regression_results.md](x64_5_regression_results.md)
(Phase 9 tests), [x64_5_recoverable_rows.csv](x64_5_recoverable_rows.csv),
[x64_5_conflicts.csv](x64_5_conflicts.csv).

## Phase 1 — Call-path audit

Exhaustive search (`grep -rn "enqueue_migration("`) found exactly **two**
call sites in the entire codebase, both in production code paths:

### Call site 1 — `src/core/watchtower_attribution.py:147`, inside `store_migration()`
```python
from src.core.walkback_queue import enqueue_migration
enqueue_migration(conn, mint=mint, creator=creator)
```
- **`live_conn` supplied?** **No.** Not passed at all — defaults to `None`.
- **Transaction state of `conn`**: `conn` is the ops DB connection
  (`wt_ops_v2.db`), already `commit()`-ed for the `migrated_tokens` insert
  a few lines earlier (line 142) before this call.
- **Does `creator_funding_queue` insertion occur before or after
  enqueue?** Independent — `creator_funding_queue` lives in the live DB
  (`flex_complete_database.db`) and is written by an entirely different
  subsystem (the funding-extraction pipeline), not by
  `watchtower_attribution.py` at all. There is no ordering guarantee
  between the two writers.
- **Do both writes share a transaction?** No — different databases,
  different connections, different subsystems.
- **Does either write occur on another thread/connection?** Yes — the
  `creator_funding_queue` writer is a separate pipeline entirely.
- **Has the funding-queue transaction committed before lookup?** **Moot
  for this call site** — the lookup is never even attempted, since
  `live_conn` is `None` and the lookup block (`if live_conn and not
  create_signature:`) is gated on it being truthy.
- **Can enqueue execute independently of CREATE processing?** Yes — this
  call fires on every migration event, regardless of whether
  `creator_funding_queue` has been populated for that mint yet.

### Call site 2 — `src/core/pumpfun_curve_listener.py:816-823`, creator-unknown fallback
```python
from src.core.walkback_queue import enqueue_migration as _enq
from src.core.database_write_service import database_write_service
_ops_selector = f"operations:{...}"
database_write_service.register_database(_ops_selector, _ops_path)
_cls = database_write_service.submit(
    _ops_selector, "listener-walkback-enqueue",
    lambda _ops_conn: _enq(_ops_conn, mint=mint, creator=_creator_for_wb),
)
```
- **`live_conn` supplied?** **No** — same as call site 1, not passed.
- **Transaction state**: routed through `database_write_service`'s
  serialized write lane (per project memory — see "DB write serializer"),
  operating on the ops DB only.
- **`creator_funding_queue` insertion before/after**: independent, same
  as call site 1 — this fallback fires specifically when
  `creator_wallet` could not be resolved from `wt_staged_wallets`/
  `token_analysis` at migration time, i.e. exactly the case where the
  funding pipeline may still be catching up.
- **Shared transaction?** No.
- **Other thread/connection?** Yes, same as above.
- **Committed before lookup?** Moot — lookup never attempted, same gating
  reason.
- **Independent of CREATE processing?** Yes.

### Failure mode determination

**The confirmed, dominant failure mode is A (`live_conn not supplied`) —
not intermittently, but on 100% of calls through either production
entry point.** This is stronger than a race: it is a deterministic,
unconditional skip. Every `FULL_WALKBACK` row enqueued via either live
call site will always have `create_signature=None` going into the
`valid_signature()` check, regardless of whether `creator_funding_queue`
already held a valid signature at that exact moment or not.

Modes **B** (INSERT not yet committed) and **C** (INSERT occurs after
enqueue) remain **latent, secondary possibilities** that the Phase-1
fix (opening a live connection inside `enqueue_migration()` itself)
does not fully eliminate — if `creator_funding_queue`'s own write from
the separate funding-extraction pipeline genuinely hasn't landed yet at
the instant `enqueue_migration()` now performs its (newly-opened) live
lookup, the row will still enqueue with `MISSING_OR_MALFORMED`, exactly
as before. This is why the worker-side reconciliation pass (Phase 4/6)
is the mechanism that actually closes the gap unconditionally, not the
enqueue-time fix alone — per the task's own Phase 6 "Option C" framing
("allow independent writers, but make reconciliation mandatory and
reliable").

Modes **D** (wrong database connection), **E** (mint formatting
mismatch), and **F** (transient SQLite lock/read visibility) were
checked and **ruled out**: `LIVE_DB_PATH` resolves correctly (confirmed
by the enqueue-time fix successfully finding and validating 310/352
real stuck rows' signatures once wired up), mint strings match exactly
(same primary-key join used throughout this and the earlier X64.x
audits), and no lock error was observed in either the dry-run or the
live reconciliation run. Mode **G** (CREATE processing permanently
absent) applies to the 42 `MINT_NOT_FOUND` rows specifically — for
those, `creator_funding_queue` genuinely has no row at all, meaning the
funding-extraction pipeline itself never processed that mint (a
separate, upstream gap outside this task's scope — flagged, not
investigated further here).

## Phase 2 — Population audit

Full detail in `x64_5_backfill_dry_run.md`. At time of dry-run: **352**
stuck rows (`status='waiting'`, `path_state='WAITING_FOR_CREATE_ANCHOR'`,
anchor NULL or `MISSING_OR_MALFORMED`) — the expected ~339 from the
earlier ad-hoc check, grown to 352 by continued live enqueueing in the
interim. Classification: **310 RECOVERABLE_VALID_ANCHOR, 0
ANCHOR_PRESENT_INVALID, 0 AMBIGUOUS_MULTIPLE_ROWS, 42 MINT_NOT_FOUND**.
All 352 rows: `attempts=0`, `rpc_used=0` — none has ever been touched by
the worker, confirming `drain_batch`'s SELECT (which excludes
`status='waiting'`) is the reason these rows were structurally
unreachable, not a processing failure.

## Phases 3-8 — Implementation

Full detail in `x64_5_implementation.md`. Summary: new module
`src/ops/anchor_reconciliation.py` implements zero-RPC classification,
dry-run reporting, idempotent batch reconciliation, and a single-row
worker self-healing hook. `walkback_queue.py`'s `enqueue_migration()`
now opens its own short-lived read-only live connection when the caller
doesn't supply one. `walkback_worker.py`'s `run_loop()` runs the
reconciliation pass every cycle, non-essential/lock-tolerant, matching
the existing startup-maintenance pattern. Live production run: **310 of
352 rows recovered, 0 conflicts, canonical case verified end-to-end
including a real (non-injected) walkback that correctly did NOT connect
to the named validation-target treasury** — an honest negative result,
not a failure.

## Phase 9 — Tests

Full detail in `x64_5_regression_results.md`. 14 new tests in
`tests/test_x64_5_anchor_reconciliation.py`, all passing; combined
walkback/x64/anchor regression suite (100 tests) passes clean, including
one pre-existing test (`test_walkback_worker_startup_resilience.py`)
updated to stub the new reconciliation call, consistent with that file's
own established stubbing pattern.

---

## Required summary

- **Total WAITING_FOR_CREATE_ANCHOR rows**: 352 (at dry-run time)
- **Recoverable with zero RPC**: 310
- **Still genuinely missing**: 42
- **Malformed**: 0
- **Conflicting**: 0
- **Successfully reconciled**: 310
- **Released for walkback**: 310 (now `status='pending'`, selectable by
  `drain_batch`'s existing SELECT)

**Which enqueue call path produced the canonical failure?** Both
production call paths equally — `watchtower_attribution.py:147`
(`store_migration()`) and `pumpfun_curve_listener.py:816`
(creator-unknown fallback) — since neither ever passes `live_conn`. The
canonical mint `H55qUAeK…` was traced to have gone through
`store_migration()`'s path (it has a `migrated_tokens` row consistent
with that flow), but the root cause is identical regardless of which of
the two call sites handled it.

**Was `live_conn` missing, or was the signature uncommitted/not yet
inserted?** **`live_conn` was missing** — this is the dominant, 100%-
reproducible cause. `creator_funding_queue`'s own row for the canonical
mint carries `updated_at=1784577442`, the same second as
`wt_walkback_queue.enqueued_at=1784577442` — consistent with the funding
pipeline writing at essentially the same moment as the enqueue, but the
lookup was never attempted at all regardless of timing, because
`live_conn` was never supplied.

**How many historical launches were blocked by the same condition?**
352 at time of audit (continuously growing before this fix; the true
historical count since this pattern began is larger than the current
snapshot, since some rows may have separately timed out or been cleaned
up by other maintenance — not separately measured here).

**How many can now be walked with zero additional anchor-discovery
RPC?** 310 — all released to `status='pending'` using only data already
present in `creator_funding_queue`, no RPC spent on anchor recovery
itself (RPC is spent only by the walkback itself once a row runs, exactly
as for any other normal row).

**Does `H55qUAeK…pump` recover a lineage consistent with treasury
`4231KLYi…`?** **No.** The real, non-injected walkback surfaced a
different disposable subprov (`9JXUiZhYVzaxY1…`), with zero stored
connection to `4231KLYi…` or any of its known associated wallets. Per
the task's own instruction, the treasury was a validation target, not a
value to inject — this negative result is reported honestly rather than
forced to match.

## Success criteria — met

A valid CREATE signature that appears after migration enqueue can no
longer leave a walkback row permanently stuck: the enqueue-time fix
closes the gap immediately in the common case, and the worker's
self-healing reconciliation pass closes it unconditionally on the next
cycle even for a genuine timing race, at zero additional RPC cost. Every
historically recoverable row (310 of 352) was released through a
deterministic, idempotent, zero-RPC database reconciliation, verified
live with a second no-op run confirming idempotency.
