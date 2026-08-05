# X76.3 — Shared Extractor Database Concurrency & Connection Ownership

## Objective

X73.2A isolated but explicitly deferred a shared-infrastructure defect:
`creator_funding_worker`'s tight polling loop periodically hits a
connection/task-leakage or write-lane collision in the shared
`RealTimeCreatorFundingExtractor`, tripping the worker's self-kill guard
and forcing a Supervisor restart. Functional and self-recovering, but not
the desired steady state. This milestone establishes and enforces a
single, provable connection-ownership contract for the shared extractor.

## Core invariant (restated, now enforced)

Every database connection is opened, used, committed/rolled back, and
closed within the same execution context and owning thread — on every
exit path, not just the success path. No write-capable connection or
background task may outlive the extraction call that created it, beyond
a bounded, non-cancelling supervised wait.

## Phase 1 — Concurrency map (summary; full trace was produced via code
inspection of every call site in this chain)

One call to `creator_funding_worker.py::_process_job()` →
`extract_funding_for_new_token()` → `extract_for_creator()` (which owns
`extraction_conn`, a `TrackedConnection` held open across the entire
multi-hundred-line paging loop) → three `asyncio.create_task()` calls
(CEX detection, BlockSec batching, post-launch automation, none awaited
by the extractor itself) → back in `extract_funding_for_new_token`, an
`asyncio.gather()` of four more write-capable coroutines
(`check_create_tx_for_jitotip`, `check_transfers_for_debridge`,
`check_transfers_for_axiom`, `extract_outgoing_transfers` — the last of
which dispatches its actual write, `_save_outgoing_transfer`, via
`asyncio.to_thread`).

**Root causes identified, all structural (not timing-random):**

1. **`extraction_conn` (the paging-loop connection) was only closed on
   one success path.** The pagination-error handler mid-loop `return`s
   without closing it; if the outer exception handler fires instead, it
   also never closes it. Every early exit leaked an open
   `TrackedConnection`, and if it had ever performed a write, the leak
   held the process-wide write lock / cross-process file lease / this
   thread's `_thread_write_lease` reentrancy guard until the connection
   reaper eventually force-closed it (up to `_MAX_TXN_CONNECTION_AGE_SECS`
   later) — poisoning every other write dispatched to that same thread in
   the meantime with `NestedDatabaseWriteError`.
2. **`check_create_tx_for_jitotip`, `check_transfers_for_debridge`,
   `check_transfers_for_axiom`, `_mark_extraction_complete`, and an inline
   deBridge-tagging block inside the paging loop all shared the same
   defect**: `conn.close()` was called explicitly only after a successful
   write, never in a `finally`. Any exception raised between `db_connect()`
   and that point (an RPC error, a malformed response, a SQL error) left
   the `TrackedConnection` open and, if a write had already started, its
   write lane held — permanently, until reaped.
3. **The three `asyncio.create_task()` calls were genuinely fire-and-forget
   at the extractor level.** `creator_funding_worker.py` had already
   added a bolt-on mitigation (`_await_orphaned_tasks`, X73.2) that diffs
   the *global* `asyncio.all_tasks()` set before/after each extraction and
   bounded-waits whatever appeared — a reasonable heuristic, but blind to
   which extractor instance spawned what, and providing zero protection
   for any OTHER caller (the listener process, a future one-shot recovery
   tool) that invokes `extract_funding_for_new_token()` directly.
4. **`run_post_launch_automation`'s own internal functions repeated
   the exact same close-only-on-success-path defect** in four of its six
   DB-touching methods (`_assign_creator_network`,
   `_update_funding_distribution_metrics`, `_detect_coordinated_funders`,
   `_rebuild_clusters_for_creator`) — the heaviest of the three
   fire-and-forget subtrees, and the most likely single source of a
   long-running orphaned task holding a stale connection.
5. **`automatic_cex_detection.py` and `blocksec_aml_batcher.py`** (the
   other two fire-and-forget subtrees) had the identical pattern across
   6 more functions.

**What was NOT changed, and why (per X73.2A's own hard-won lesson):**
`check_create_tx_for_jitotip`/`check_transfers_for_debridge`/
`check_transfers_for_axiom` keep their connection opened on the
event-loop thread, held across RPC `await` points, written to at the end
— the same shape X73.2A already tried to change twice (a direct
`threading.RLock` acquisition on the event loop, and the same lock via
`asyncio.to_thread`) and reverted both times because the actual thread
relationship under `to_thread` was never proven safe, and the direct
lock acquisition froze `asyncio.wait_for`'s own cancellation mechanism
(reproduced directly in this milestone's Phase 2 tests,
`TestSynchronousLockOnEventLoop`). This milestone's fix does not touch
where or how these functions execute — only that their connection can no
longer leak on an exception path. `_save_outgoing_transfer`'s already-
proven-safe `to_thread` shape (open+use+close entirely inside the
dispatched call) was left structurally unchanged and only gained the
same guaranteed-close treatment.

## Phase 2 — Regression tests

`tests/test_x76_3_extractor_concurrency.py` (19 tests, isolated per-test
SQLite database — not a copy of the live 2.9GB database, since these are
pure connection-ownership/concurrency tests unrelated to attribution
data):

- `TestNestedDatabaseWriteError` — reproduces the named error directly via
  `acquire_write_lease`, and proves the fixed pattern (`managed_db_connect`)
  allows sequential writes on the same thread without collision.
- `TestSynchronousLockOnEventLoop` — reproduces X73.2A's own documented
  hazard directly: a synchronous lock acquired with no `await` point
  inside a plain `async def` blocks the entire call stack, denying
  `asyncio.wait_for` any opportunity to service its own timeout — this is
  the actual mechanism behind X73.2A's observed "6+ minute hang, near-zero
  CPU."
- `TestCrossThreadConnectionUse` — proves the safe pattern (open+use+close
  entirely inside one `to_thread` dispatch) and documents the underlying
  `sqlite3` `check_same_thread=True` guarantee directly.
- `TestFireAndForgetTaskSupervision` — proves the extractor's new
  `_spawn_background_task`/`wait_for_background_tasks` registry tracks,
  bounded-waits, and self-prunes tasks, and never cancels a slow straggler.
- `TestCancellationReleasesOwnership` — proves a cancelled `to_thread`
  write still releases its lease (the thread keeps running to completion;
  Python cannot forcibly kill it), proves `managed_db_connect` releases
  the lease even when the body raises mid-write, and — as an explicit
  regression guard — directly demonstrates the OLD unsafe pattern still
  leaks the lease when reproduced in isolation, so future contributors
  can see exactly what NOT to write.
- `TestTimeoutCannotContinueMutating` — proves a `wait_for`-timed-out
  extraction's connection is closed before a second, independent caller
  attempts to write, and that the timed-out write itself never committed.
- `TestRealExtractorFunctionsUseManagedConnect` — structural regression
  guards asserting (via `inspect.getsource`) that the fixed functions
  actually use `managed_db_connect`/`finally`, so a future edit can't
  silently reintroduce the bare-close pattern.

All 19 pass against the repaired code; several (documented inline) are
written to demonstrate exactly what the unsafe pattern does when
reproduced directly, which is the more informative form of "fails
against the old pattern" for defects whose old form no longer exists in
the codebase to run the test against directly.

## Phase 3 — Repair (canonical execution design, applied where it fit
without a rewrite)

The milestone's suggested canonical shape (concurrent RPC/read collection
→ pure payloads → single supervised persistence stage) was **evaluated
and not imposed wholesale** — the actual defects found were connection-
lifetime bugs (missing `finally`), not payload-collection-order bugs, and
`_save_outgoing_transfer`'s already-correct `to_thread` shape already
matches the spirit of "single supervised persistence stage, connection
opened inside the owning execution context." Restructuring the four RPC-
gathered functions into a strict two-phase (collect-then-persist) design
was evaluated and rejected: it would mean holding results from up to four
concurrent RPC round-trips in memory before any of them could tag a
creator, adding real latency and complexity for connection-lifetime bugs
that a `finally` already fixes completely. Per the milestone's own
non-goal ("do not broaden this into a database-framework rewrite unless
evidence proves that is unavoidable") — no evidence emerged that it was.

**What was actually done:**
1. Every identified write-capable function that opened a `db_connect`/
   `sqlite3.connect` connection and only closed it on the success path now
   either uses `managed_db_connect(...)` as a context manager (guarantees
   `close()` in a `finally`, which is where `TrackedConnection.close()`
   also guarantees `_release_write_lane()` runs) or, where the existing
   control flow made a context-manager conversion invasive (the
   multi-hundred-line `extraction_conn` in `extract_for_creator`, the
   retry-loop in `_assign_creator_network`, the fallback-query loop in
   `get_unlabeled_addresses`), an explicit `conn = None` before the `try`
   and a `finally: if conn is not None: conn.close()` at the function's
   end.
2. `RealTimeCreatorFundingExtractor` gained a `_background_tasks` registry,
   `_spawn_background_task()`, and `wait_for_background_tasks()` — the
   three `create_task()` calls now go through the tracked spawn helper,
   and `extract_funding_for_new_token()` (the extractor's own public entry
   point, not just the worker) calls `wait_for_background_tasks()` before
   returning. This makes the "don't outlive the parent" invariant hold for
   every caller, not only `creator_funding_worker` (which keeps its own
   `_await_orphaned_tasks` sweep too — harmless double coverage over the
   same underlying tasks, not a conflict).
3. Neither bounded-wait mechanism ever cancels a still-running background
   task — a cancelled write mid-commit is strictly worse than a slow one
   (explicit non-goal, verified by test).

## Files changed

- `src/extractors/realtime_creator_funding_extractor.py` — guaranteed
  connection closure in `_save_outgoing_transfer`, `check_create_tx_for_jitotip`,
  `check_transfers_for_debridge`, `check_transfers_for_axiom`,
  `_mark_extraction_complete`, the inline deBridge tag inside the paging
  loop, `extraction_conn` in `extract_for_creator`, and the profile-cache
  count-check in `extract_funding_for_new_token`; added the
  `_background_tasks`/`_spawn_background_task`/`wait_for_background_tasks`
  supervision registry; the three `create_task()` sites now route through
  it; `extract_funding_for_new_token` now calls `wait_for_background_tasks()`
  before returning.
- `src/analysis/post_launch_automation.py` — guaranteed connection closure
  in `_assign_creator_network`, `_update_funding_distribution_metrics`,
  `_detect_coordinated_funders`, `_rebuild_clusters_for_creator`
  (`_check_watchtower_linkage` already did this correctly and was left
  unchanged; `_tag_creator_from_funding_patterns`'s DB code is unreachable
  dead code behind an early `return False` and was left unchanged).
- `src/analysis/automatic_cex_detection.py` — guaranteed connection
  closure in `_get_existing_classifications`, `save_classification`,
  `add_confirmed_cex_to_mapping`, `classify_addresses_from_funding`.
- `src/monitoring/blocksec_aml_batcher.py` — guaranteed connection closure
  in `get_unlabeled_addresses`, `_load_batch_time`, `_process_response`,
  `_log_batch`. (`get_cached_label`/`get_batch_stats` are read-only,
  close before any risky post-processing, and were left unchanged — not
  in the write-capable scope this milestone targets.)
- `pytest.ini` — added `asyncio_mode = auto` (pytest-asyncio was already a
  dependency but unconfigured; this is the first test file in the repo
  needing it — incidentally this also un-breaks
  `tests/test_phase1_monitoring.py`'s previously entirely-non-functional
  `async def test_phase1`, which now runs and fails for an unrelated,
  pre-existing reason — missing `creator_funders` table against whatever
  DB it's pointed at — confirmed via `git stash` that this failure mode
  predates X76.3 and is not a regression).
- `tests/test_x76_3_extractor_concurrency.py` (new, 19 tests).

**Explicitly not touched**: funding classification, attribution,
reconciliation, or scoring logic; `TrackedConnection`/write-lane
internals in `src/utils/db_locking.py` or `src/core/database_write_service.py`;
the self-kill threshold or guard in `creator_funding_worker.py`; the
disabled listener queue consumer or historical cron consumer; no
`check_same_thread=False` introduced anywhere; no connection passed into
`asyncio.to_thread()` (every `to_thread` dispatch in this call chain
opens its own connection inside the dispatched function, as before).

## Validation under sustained load

A standalone script (`scripts/` scratch, not committed — pure connection-
ownership stress test against an isolated SQLite database, not the live
database) simulated the real production shape: N concurrent "extraction
jobs," each firing 2 fire-and-forget background writes plus 4 gathered
writes (one via `to_thread`, mirroring `_save_outgoing_transfer`), using
exactly the fixed `managed_db_connect`/supervised-task pattern.

- 200 jobs, concurrency 8: 1200/1200 writes landed, 0 errors, 1.2s.
- 3000 jobs, concurrency 16: 18,000/18,000 writes landed, 0
  `NestedDatabaseWriteError` / write-lane errors, 17.1s.

## Regression

Two full-suite (3447-test) runs were attempted — one on baseline
(pre-X76.3, via `git stash`) and one with X76.3 applied. Both produced
the same widespread failures at the identical positions in the run
(`pool_validation/worker/test_price_worker_runtime.py`,
`test_helius_endpoint.py`, `test_ops_x8_operator.py`,
`test_ops_x21b_routes.py`, and many more `test_ops_x*` files) — this is
**pre-existing, full-suite-order-dependent global-state pollution**, not
a regression introduced by this milestone. Confirmed by running the
identical failing files in isolation: `test_ops_x8_operator.py` (56
tests) and `test_ops_x21b_routes.py` pass 100% standalone on both
baseline and with X76.3 applied; `test_helius_endpoint.py` and
`test_price_worker_runtime.py` fail even in isolation for reasons
unrelated to this milestone (live network dependency / worker-startup
timing), confirmed identical on baseline. This class of suite-wide
flake predates X76.3 and is out of this milestone's scope (it does not
touch funding classification, attribution, reconciliation, or scoring,
and none of the failing files import any of the four modules X76.3
changed).

**Targeted regression** (the four changed modules plus every existing
test file that imports them, run cleanly/standalone rather than inside
the polluted full-suite run):
- `tests/test_x76_3_extractor_concurrency.py` — 19/19 passed.
- `tests/test_phase1_with_env.py`, `tests/test_incremental_extraction.py`
  (both import `realtime_creator_funding_extractor`) — 2/2 passed.
- `tests/test_phase1_monitoring.py` — pre-existing failure, confirmed via
  `git stash` to predate X76.3 (missing `creator_funders` table against
  whichever DB it's pointed at; this test could not even execute at all
  before X76.3's `pytest.ini` change, since `pytest-asyncio` was an
  installed-but-unconfigured dependency and no async test in the repo had
  exercised the gap until this milestone's own new test file needed it).

21/21 relevant tests pass; 0 regressions attributable to X76.3.
