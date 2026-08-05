# Follow-on Engineering Milestone: Shared Extractor Database Concurrency

Status: **not started — documentation only, per X73.2A scope boundary.**

## Origin

Discovered while building the standalone `creator_funding_worker` (X73.2).
`src/extractors/realtime_creator_funding_extractor.py`'s `extract_funding_for_new_token()`
fires four SQLite-writing operations concurrently via `asyncio.gather()`
(`check_create_tx_for_jitotip`, `check_transfers_for_debridge`,
`check_transfers_for_axiom`, `extract_outgoing_transfers`), plus three
further fire-and-forget `asyncio.create_task()` calls for background
enrichment. Running this code from a tight polling worker (rather than the
long-lived listener process it was originally written for) surfaced a real,
reproducible `NestedDatabaseWriteError` — see X73.2's own investigation.

## What was proven

- **Root cause of the original crash**: the gathered functions each open a
  `TrackedConnection` early (before any RPC calls), then write to it late
  (after RPC round-trips). When two of them finish their RPC waits close
  together, their writes can collide with each other or with a completely
  unrelated caller's write on the same OS thread, tripping
  `TrackedConnection`'s thread-local reentrancy guard
  (`_thread_write_lease` in `src/core/database_write_service.py`).
- **`_save_outgoing_transfer`** (the highest-frequency write of the four,
  called once per outgoing transfer inside a loop) was fixed successfully:
  dispatching the *entire* synchronous function — including its own
  internal `db_connect()` call — via `asyncio.to_thread()` means the
  connection is opened and used on the *same* thread throughout. This ran
  clean through a 5-minute sustained test with zero
  `NestedDatabaseWriteError` and confirmed real `creator_funders` writes.
  **This fix is committed and kept** (X73.2A).

## What was NOT proven (and reverted)

Three sibling functions (`check_create_tx_for_jitotip`,
`check_transfers_for_debridge`, `check_transfers_for_axiom`) share a
different, riskier shape: the `TrackedConnection` is opened on the
**coroutine's own thread** (the event loop thread, since these are plain
`async def` methods with no `to_thread` of their own), held open across
RPC calls, and only written to at the very end. Two attempted fixes were
tried against `check_create_tx_for_jitotip` specifically and both failed
or were unproven:

1. **`with DB_WRITE_LOCK:` directly around the write, no dispatch.**
   `DB_WRITE_LOCK` is a synchronous `threading.RLock`
   (`src/utils/db_locking.py`). Acquiring it directly on the event loop
   thread blocks the *entire* event loop for as long as another thread
   holds it — including freezing `asyncio.wait_for`'s own timeout
   mechanism in whatever awaited the extraction. Observed effect: a job
   that should have failed at a 90s timeout instead hung for 6+ minutes
   with the worker process alive but doing no work (confirmed via `ps`
   showing near-zero CPU accumulation over that window).

2. **Same lock, but the write closure dispatched via `asyncio.to_thread()`.**
   This should, in principle, move the blocking acquisition off the event
   loop thread. In sustained testing it reduced but did **not** eliminate
   `NestedDatabaseWriteError` — `check_create_tx_for_jitotip` raised it
   repeatedly (7+ times in a 5-minute window) even with this dispatch in
   place. The suspected reason, **not confirmed**: the
   `TrackedConnection` object itself was created on the event loop thread
   (`conn = db_connect(DB_PATH, timeout=60)`, called directly inside the
   `async def`, before the `to_thread` dispatch existed). Python's
   `sqlite3` module defaults to `check_same_thread=True`, which normally
   raises `ProgrammingError` — not `NestedDatabaseWriteError` — if a
   connection is used from a different thread than the one that created
   it. Since we observed `NestedDatabaseWriteError` (a
   `database_write_service.py`-level exception) rather than a
   `sqlite3.ProgrammingError`, the actual thread relationship between
   "the thread that opened `conn`" and "the thread `asyncio.to_thread`
   happened to run the closure on" was never definitively established.
   This is exactly the kind of assumption X73.2A's scope explicitly
   excludes from the funding-worker milestone.

Because the underlying mechanism isn't understood with confidence, both
attempted fixes were reverted. `check_create_tx_for_jitotip`,
`check_transfers_for_debridge`, and `check_transfers_for_axiom` are back to
their pre-X73.2 form: unlocked, synchronous writes on a
`TrackedConnection` opened on the event loop thread. This is the same
behavior these functions have had for as long as they've existed, running
inside the listener process (where the "long-lived process, many
concurrent creators over time" pattern apparently didn't surface this
often enough to be noticed/fixed before now) — not a regression, a
known, pre-existing limitation now made visible by a new caller.

## What this follow-on milestone should own

1. **`TrackedConnection` + `asyncio.to_thread` composition rules.**
   Establish, with actual verification (not inference from stack traces),
   whether a `TrackedConnection` opened on one thread can safely be used
   from another thread via `to_thread`, and under what conditions. If not
   safe, define the correct pattern (e.g.: never share a connection object
   across a `to_thread` boundary; always open+use+close within the same
   dispatched call, as the proven `_save_outgoing_transfer` fix already
   does).
2. **Retrofit `check_create_tx_for_jitotip` / `check_transfers_for_debridge`
   / `check_transfers_for_axiom`** using whatever pattern (1) establishes
   as correct — most likely restructuring each to open its connection
   *inside* a `to_thread`-dispatched closure (mirroring
   `_save_outgoing_transfer`'s now-proven shape) rather than opening it on
   the coroutine's own thread and writing to it later.
3. **Audit the three fire-and-forget `asyncio.create_task()` calls**
   (`_run_automatic_cex_detection`, `_try_blocksec_batch`,
   `run_post_launch_automation`) for the same class of issue — X73.2 added
   a bounded `asyncio.wait()` around them in `creator_funding_worker.py`
   (`_await_orphaned_tasks`) as a worker-side mitigation, but the
   extractor itself still fires them unsupervised; a caller other than
   this worker (e.g. the listener, if `LISTENER_CREATOR_FUNDING_QUEUE_ENABLED`
   is ever re-enabled) has no equivalent protection.
4. **Decide whether `extract_for_creator`'s long-lived connection pattern**
   (one `TrackedConnection` held open across the entire multi-hundred-line
   extraction body, multiple commits interleaved with RPC `await` points)
   is itself a source of lease-related surprises — a single, unexplained
   collision between this function and an unrelated heartbeat write was
   observed once during X73.2 testing and has not recurred or been
   explained.

## Non-goals for this future milestone

- Do not change what any of these functions compute or write (attribution
  logic, CEX/Jitotip/deBridge/Axiom detection rules) — this is purely a
  concurrency/connection-lifetime correctness problem layered on top of
  already-correct business logic.
- Do not redesign `TrackedConnection` or the write-lease mechanism itself
  unless the investigation in (1) proves it's structurally incompatible
  with `asyncio.to_thread` — start from "how do callers need to use it
  correctly," not "how should the primitive be redesigned."
