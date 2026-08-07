# X78.3 — Creator Funding RPC Cache Nested-Write Repair

## Summary

X78.2's live sanity window surfaced a second, distinct
`NestedDatabaseWriteError` source, structurally different from the
detached-descendant mechanism X78.2 fixed: `extraction_conn` (opened once
per `extract_for_creator` call) colliding with `RPCCache._get_conn()` —
same job, same thread, different connection object.

X78.3 reproduced this deterministically against the real
`_flush_page_batch` and `RPCCache` code (Phase 1), established that
`RPCCache.get()`/`.set()` are themselves innocent — correctly write-lease-
respecting (Phase 2) — and traced the actual defect to a single unhandled
exception path inside `_flush_page_batch` (Phase 3-9). Fixed with a 19-line
addition (one `rollback()` call) in one file.

## Root cause (confirmed, Phase 5 verdict: C)

`RealTimeCreatorFundingExtractor._flush_page_batch()`
(`realtime_creator_funding_extractor.py`) is called once per extraction
page, passed `extraction_conn` (opened at `extract_for_creator`'s
line ~1305 and held open across the whole paging loop). Its
`transfer_index_rows` block:

```python
if transfer_index_rows:
    try:
        cursor.executemany("""INSERT OR IGNORE INTO transfer_index ...""",
                            transfer_index_rows)
        conn.commit()
    except Exception as ti_err:
        print(f"[TRANSFER_INDEX] ⚠ Insert error: {ti_err}", flush=True)
```

`cursor.executemany()` is write-shaped SQL, so `TrackedCursor.execute()`
(`db_locking.py:213`) calls `self.connection._acquire_write_lane()`
**before** the statement runs — the write lease is already held the
instant `executemany()` is invoked, regardless of whether it later
succeeds. If any row in `transfer_index_rows` is malformed (wrong tuple
arity/types — which does happen: the two call sites building these rows,
lines ~1470 and inside the main paging loop, construct tuples by hand
with no schema validation), `executemany()` raises before reaching
`conn.commit()`. The `except` above caught and logged this — but, unlike
the function's OUTER `except` (fixed in X78.0, which does call
`conn.rollback()`), this INNER `except` did neither `rollback()` nor
re-raise. Execution fell through to the function's normal return with
`extraction_conn`'s write lease still held.

Because `_thread_write_lease` (`database_write_service.py:85`) is a
`threading.local()` reentrancy guard keyed on the OS thread, the very
next write attempt on that same thread — by ANY connection, not just
`extraction_conn` — collided:

- **The pattern actually observed live** (100 occurrences in ~4 minutes):
  `RPCCache._get_conn()` → `db_connect()` → `_ensure_table()`'s
  `CREATE TABLE IF NOT EXISTS rpc_response_cache` (write-shaped), called
  from `get_transaction()`/`get_signatures_until_time()` during the same
  paging loop, on the same thread.
- **The rarer self-collision** (5 occurrences,
  `extract_for_creator` → `extract_for_creator`): the *next page's* own
  `_flush_page_batch` call on the same `extraction_conn`/thread.
- **The rarest variant** (1 occurrence,
  `extract_for_creator` → `db_locking.py:_patched_connect`): any other
  code on that thread calling bare `sqlite3.connect()` on the flex DB,
  transparently redirected by the global monkeypatch
  (`db_locking.py:724`) into `db_connect()`.

All three share the identical root cause — confirmed by reproducing each
shape directly against the real primitives (see Validation below) — they
are not three separate defects.

### Why the connection-identity/caller-tag ambiguity existed

`db_connect()`'s caller tag (`_db_caller`, used as `outer_command`/
`inner_command` in every `NestedDatabaseWriteError`) is computed via
`inspect.stack()[1]` **at connection-open time**
(`db_locking.py:490-491`), not at write-lease-acquisition time. For
`extraction_conn`, opened once at `extract_for_creator`'s line ~1305,
every later write on that same connection carries the identical
`outer_command=realtime_creator_funding_extractor.py:1305 in
extract_for_creator` tag regardless of which statement, hundreds of lines
later, actually triggered the lease. This is why X78.1's static analysis
could not distinguish "the extraction's own write is still in flight" from
"a leaked lease from an earlier, unrelated write on the same connection is
still held" — both produce the same label. X78.3 resolved the ambiguity
empirically, by reproducing each candidate mechanism and matching the
resulting `outer_command`/`inner_command` pair against the live log.

## GET vs SET audit (Phase 2)

- `RPCCache._get_conn()` (`rpc_cache.py:65-73`): calls `db_connect()` then
  a `PRAGMA busy_timeout` (read/config-only, not write-shaped). No lease
  acquisition here.
- `RPCCache._ensure_table()` (`:75-102`): `CREATE TABLE IF NOT EXISTS` —
  write-shaped, correctly wrapped in `conn = None` / `try` / `finally:
  conn.close()`, with `conn.commit()` on success. Called once per
  `RPCCache.__init__`. **This is the exact statement whose lease
  acquisition attempt raised in the live signature** — not because
  `_ensure_table` is defective, but because a lease was already held by
  something else on the same thread.
- `RPCCache.get()` (`:104-172`): `SELECT` (no lease) on hit/miss;
  `DELETE`/`UPDATE` (write-shaped) only on the lazy-expiry and
  hit-count-increment paths — both correctly wrapped with `conn.commit()`/
  `conn.close()` on every path, including the `except`.
- `RPCCache.set()` (`:174-216`): `INSERT OR REPLACE` (write-shaped),
  correctly wrapped, `conn.commit()`/`conn.close()` on every path.

**Verdict: RPCCache's own connection/transaction handling is correct.**
Neither GET nor SET is the defect; both are innocent bystanders that
simply attempted a legitimate write while another connection's lease
(from an unrelated, earlier failure) was still held on the same thread.

## extraction_conn lifetime (Phase 6-7)

`extraction_conn` is opened once (`db_connect(DB_PATH, timeout=90)`,
~line 1305) and held open across the entire paging loop (hundreds of
lines, potentially many RPC round-trips) until `extraction_conn.close()`
near the end of `extract_for_creator` (~line 1885/1953). This single-
connection-for-the-whole-extraction design is intentional (comment at
~1275: "opened below and held open across the ... extraction run") and
was not changed by this fix — the actual defect is not that the
connection is held open too long, but that ONE exception path along the
way failed to release its **write lease** (a much shorter-lived state
than the connection itself) when a statement failed. The write lease is
correctly acquired-and-released many times across the connection's
lifetime (each `_flush_page_batch` call, each `conn.commit()`) — the
defect was specific to the one un-rolled-back failure path.

Given that, X78.3 did not restructure `extraction_conn`'s lifetime
(Phase 7's "shorten the transaction" option was considered but rejected
as unnecessary — see Repair Design below); the minimal, evidence-matched
fix closes the actual gap instead.

## Database identity (Phase 9)

`RPCCache` and `extraction_conn` both operate on the same file
(`DB_PATH`, the main flex database) via `TrackedConnection`, sharing the
same process-wide `_DB_WRITE_LOCK` (`threading.Lock()`,
`db_locking.py:77`) and the same thread-local `_thread_write_lease`
reentrancy guard. This is expected and correct — the guard's job is
exactly to prevent two write-capable connections on the same database
from committing concurrently on the same thread; it was correctly
exposing the leaked-lease defect, not malfunctioning.

## Repair (Phase 10)

Single change, `realtime_creator_funding_extractor.py`, inside
`_flush_page_batch`'s `transfer_index_rows` except block: added
`conn.rollback()` (wrapped in its own `try/except: pass`, matching the
pattern already used by the function's outer except), before the existing
log line. 19 lines added (mostly comment), no deletions, no other files
touched.

Rejected alternatives (Phase 10's stated preferred order, evaluated and
not chosen):
1. *Shorten extraction_conn's write transaction* — not needed; the
   transaction boundaries (per-`_flush_page_batch`-call, `commit()` after
   each) were already correctly scoped. The defect was a missing
   rollback on failure, not an over-broad transaction.
2. *Give RPCCache a "correctly defined read path"* — RPCCache's own
   semantics are already correct (see GET/SET audit above); changing it
   would be treating the symptom, not the cause, and was explicitly
   disallowed by the task ("Do NOT simply catch NestedDatabaseWriteError.
   It is already being swallowed.").
3. *Defer RPCCache writes* — same objection; RPCCache did nothing wrong.
4. *Separate cache DB execution into its own context* — unnecessary
   architectural change for a one-line missing-rollback bug.

## Validation

- `tests/test_x78_3_rpc_cache_nested_write_reproduction.py` (4 tests) —
  Phase 1 deterministic reproduction against the real `_flush_page_batch`
  (malformed transfer_index row → lease leaked pre-fix / released
  post-fix), the core RPCCache-collision regression, the rarer
  self-collision-across-pages regression, and a happy-path non-regression
  check.
- `tests/test_x78_3_sequential_stress.py` (1 test) — Phase 15: 100
  simulated pages (30% malformed) with interleaved real `RPCCache`
  activity every iteration. Result: 0 `NestedDatabaseWriteError`, no
  leaked lease, cache remains functional.
- Confirmed the reproduction tests genuinely fail (hang, in fact — the
  pre-fix code deadlocks a later `db_connect()` call for the full 60s
  `_DB_WRITE_LOCK.acquire(timeout=60)` window, itself further evidence of
  the defect's severity) against the pre-fix code via `git stash`.
- Phase 16: re-ran all X78.2 tests (8) alongside the new X78.3 tests (5)
  in one combined run — 13/13 pass, no regression to the detached-
  descendant fix's `_STRAGGLER_TASKS`/`_await_stragglers_before_next_write`
  invariants.
- `git diff` scoped to a single file, 19 lines, purely additive; no
  changes to `TrackedConnection`, `_thread_write_lease`,
  `NestedDatabaseWriteError`, RPCCache's own code, or any attribution/
  reconciliation/resolver/walkback semantics.

## Live deployment / soak status

Not yet executed in this turn — see Production Readiness verdict below.

## Root-cause ledger (cumulative, X78.0-X78.3)

| Mechanism | Status |
|---|---|
| Individual connection/transaction leaks (25 fixes, X78.0, 9 commits) | FIXED / historical |
| `asyncio.to_thread` executor-thread-pool reuse amplification | HISTORICAL — real mechanism, not required to explain X78.2 or X78.3's causes |
| Primary extraction timeout/cancellation ordering (`b779689`, X78.0) | FIXED |
| Detached background descendants outliving `_process_job` (X78.2) | FIXED |
| RPCCache same-job nested ownership (X78.3) | **FIXED** — actual cause was `_flush_page_batch`'s unrolled-back transfer_index failure, not RPCCache itself |
| Other nested-write source | NONE currently identified; live soak (pending) is the next opportunity to surface one |

## Production readiness verdict (Phase 22)

**NOT READY** — pending live deployment and soak (Phase 18-21), not yet
executed in this turn. All local validation (deterministic reproduction,
regression against both X78.2 and X78.3 fixes together, 100-iteration
stress test) passes cleanly. Remaining blocker: a real supervised restart
plus the 15-minute sanity window and 60-minute/2-hour soak, confirming
zero recurrence of ALL THREE now-fixed collision shapes under real
production contention, per the task's Phase 20-21.

## Commit

Local commit only, not pushed, per task instruction.
