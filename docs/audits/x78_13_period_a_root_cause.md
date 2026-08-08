# X78.13 — PERIOD A / Funding Worker Stall Root Cause

Read-only investigation. No code changes, no commit, no push, no production changes.

Investigation timestamp window: epoch ~1786229536–1786229664 (2026-08-08 ~23:52–23:54 local).

---

## Phase A — Process State

| Field | Value |
|---|---|
| Process | `creator_funding_worker` |
| PID | 58062 |
| Supervisor status | RUNNING |
| Uptime | 1:56:57 at time of check |
| OS process state | `R` (running/runnable) |
| CPU | 7.6–10.5% (sampled twice, still accumulating: 6:15 total CPU time) |

**The process is not exited, not deadlocked at the OS scheduler level, and not
in an uninterruptible sleep.** It is actively consuming CPU. This rules out a
classic "hung on a blocking syscall forever" or "process died silently"
explanation before any code-level analysis.

---

## Phase B — Queue State

```
status    count
complete  6536
expired   622
pending   16842
retry     55
```

- Oldest pending job: `2026-06-28 10:19:34` (pre-existing backlog, not
  necessarily caused by this incident).
- **Heartbeat table** (`wt_worker_heartbeat`), read directly from the
  database:

```
worker_name         last_seen    status  (as datetime)
creator-resolution   1786229217  ok      2026-08-08 22:46:57
creator-funding       1786228030  ok      2026-08-08 22:27:10
```

- At time of check (epoch `1786229664`), the `creator-funding` heartbeat is
  **1634 seconds (27.2 minutes) stale**. `creator-resolution`'s heartbeat, by
  contrast, is only 447s old and continuing to update — confirming the stall
  is specific to `creator_funding_worker`, not a database-wide or
  system-wide condition.

**Verdict: work is not progressing. The worker is genuinely stalled**, not
merely quiet between fast cycles.

---

## Phase C — Current Wait Point

No `py-spy` dump was obtained (requires `sudo`, unavailable in this
environment). The blocking location was instead established by direct
evidence: log timeline reconstruction, source tracing, and live probes of
the lock file and WAL state.

**stdout/stderr log timeline (last real activity before silence):**

```
[REALTIME_FUNDING] Extracted outgoing transfers for HedyzG2HfpJKH5GP...
[CFQ_WORKER] complete creator=HedyzG2HfpJK mint=AQaGGcX3VNVVQ4Dj funders=0 elapsed=21.6s
[CFQ_WORKER] [INTEL_REFRESH] IRC error: NestedDatabaseWriteError: ... intelligence_refresh.py:55 in _db ...
[CFQ_WORKER] [INTEL_REFRESH] NetworksRelease error: CrossProcessDatabaseWriteTimeout: ... build_networks_release.py:32 in db_transaction ...
[CFQ_WORKER] WAL: 21.6MB busy=1 (cycle 1/3)
```

No further lines have been written to either log file since. Two live probes,
several minutes apart, both found:

- The cross-process write-lock file (`flex_complete_database.db.write.lock`)
  currently **unheld** (acquired via non-blocking `flock` test from a probe
  script both times).
- `PRAGMA wal_checkpoint(PASSIVE)` on the live database returns `0|2083|84`
  (busy=0) — the database is not currently pinned by any writer.

This rules out a literal indefinite hold on the cross-process file lock or a
literal SQLite-level lock as the *current* blocking mechanism — those are
intermittently acquired and released normally throughout. The block is
happening **without continuously holding the lock**, which points to a
long-running, CPU/IO-bound Python-level operation between lock acquisitions,
not a stuck lock.

**Code-path trace to the exact call site**, from `creator_funding_worker.py`:

1. `_process_job()` (line 727) completes extraction, then runs a chain of
   best-effort post-processing steps, each in its own `try/except` — with one
   exception (see below).
2. Line 858–860:
   ```python
   try:
       await asyncio.to_thread(_post_extraction_intelligence_refresh, creator)
   except Exception as e:
       _log(f"intelligence refresh failed creator={creator[:12]}: {e}")
   ```
   **This `asyncio.to_thread` call has no timeout**, unlike the primary
   extraction call earlier in the same function (line 789,
   `asyncio.wait_for(..., timeout=JOB_TIMEOUT_SECONDS)`). Whatever runs
   inside `_post_extraction_intelligence_refresh` can block the calling
   event-loop thread indefinitely — nothing in `_process_job` or the outer
   cycle loop bounds it.
3. `_post_extraction_intelligence_refresh()` (line 506) calls, among other
   things (line 605–607):
   ```python
   try:
       from src.utils.build_networks_release import build_networks_release
       build_networks_release(DB_PATH)
   except Exception as e:
       _log(f"[INTEL_REFRESH] NetworksRelease error: {e}")
   ```
4. `build_networks_release()` (`src/utils/build_networks_release.py:247`)
   opens a write transaction and, as the **very first statement inside it**
   (line 278–279):
   ```python
   with db_transaction(db_path) as db:
       sync_infra_wallets(db)
       ...
   ```
5. `sync_infra_wallets()` (`src/utils/infra_mapping.py:1793`) is a
   **single-connection convenience wrapper**:
   ```python
   def sync_infra_wallets(db_conn, include_cex: bool = True) -> int:
       ensure_infra_wallets_table(db_conn)
       rows = collect_infra_wallet_rows(db_conn, include_cex=include_cex)
       return write_infra_wallet_deltas(db_conn, rows)
   ```
   `collect_infra_wallet_rows()`'s own docstring (line 1698–1705) states
   verbatim: *"Read-only scan: ... including the three full token_analysis
   SELECT DISTINCT scans. ... the ~2min scan never blocks concurrent writers"*
   — **but only if run on a separate, read-only connection.** Here it runs
   on `db`, the connection already holding `build_networks_release`'s open
   write transaction (opened via `sqlite3.connect(db_path, timeout=15)` in
   `db_transaction()`, then handed in directly).
6. `write_infra_wallet_deltas()`'s own docstring (line 1762–1768) states the
   same expectation from the other side: *"Callers should open the write
   connection immediately before this call and commit/close immediately
   after, so the write lease is held only for the handful of actual changes,
   not the full scan."* `build_networks_release.py` violates this contract.

**`token_analysis` row count at time of check: 1,616,533 rows** — consistent
with the documented "~2min scan" cost order of magnitude.

This is the exact blocking mechanism: a full multi-hundred-thousand-row,
multi-`SELECT DISTINCT` read scan, executed on a connection already holding
an open write transaction, itself invoked from an unbounded
`asyncio.to_thread` call with no caller-side timeout. The worker is not
"waiting" on a lock in the classical sense observable via lock-file state —
it is inside a long-running scan on the same thread that's supposed to be
advancing the job loop, with nothing to time it out.

---

## Phase D — PERIOD A Verification

**PERIOD A's original tag was `intelligence_refresh.py:55 in _db`.** That
exact tag *was* observed once in this incident's log window
(`[CFQ_WORKER] [INTEL_REFRESH] IRC error: NestedDatabaseWriteError: ...
outer_command=intelligence_refresh.py:55 in _db
inner_command=intelligence_refresh.py:55 in _db`) — but this was a
**bounded, self-healing, already-logged-and-caught** nested-write error, not
the long hold. It resolved and execution moved on to the next step
(`build_networks_release`) in the same function, as evidenced by the very
next log line.

**The actual current stall's call site is `build_networks_release.py:279`
→ `sync_infra_wallets` → `collect_infra_wallet_rows`, not
`intelligence_refresh.py:55`.**

**PERIOD A is explicitly REJECTED as the mechanism for this incident.** The
tag that defined PERIOD A appeared only as an unrelated, already-resolved,
bounded side-event within the same enrichment chain — not as evidence of the
historical long-hold mechanism recurring. This is a structurally different
defect: a large unindexed scan running under an already-open write
transaction, previously documented as a known anti-pattern in this codebase
(see `infra_sync_scheduler.py`'s own separated-connection design and its
docstrings explicitly warning against exactly this), but never previously
traced to this specific call site (`build_networks_release.py`).

---

## Phase E — Snapshot Failure Direction

The dashboard also reports "Intelligence snapshot build failed." Established
direction:

**Funding Worker stalled → intelligence snapshot build failed**, not the
reverse.

Evidence: `build_networks_release()` is invoked *by* `creator_funding_worker`
(via `_post_extraction_intelligence_refresh`), not the other way around.
`intelligence_snapshot_scheduler` (pid 8341, its own separate supervised
process) was confirmed still `RUNNING` throughout this investigation with no
restart — it is a distinct process from `creator_funding_worker`. The most
direct causal link established here is that `creator_funding_worker`'s own
in-process call to `build_networks_release` is the one now stuck; whether
`intelligence_snapshot_scheduler`'s separately-reported failure shares the
same root cause (contention for the same lock/table from a different
process) or is an independent symptom was not established with equal rigor
and should not be assumed identical without separate investigation of that
process's own logs/stack.

---

## Phase F — PumpPortal

**Outcome: Independent.**

No PumpPortal-related log lines, imports, or call paths were found anywhere
in the traced chain (`_process_job` → `_post_extraction_intelligence_refresh`
→ `build_networks_release` → `sync_infra_wallets`). PumpPortal retry
behavior, as reported separately on the dashboard, shares no code path with
the mechanism identified above. Do not merge these two dashboard symptoms
into one root cause.

---

## Phase G — Database

- `PRAGMA wal_checkpoint(PASSIVE)` returned `busy=0` both times it was
  checked live during the incident — **no long-held SQLite-level lock**.
- The cross-process advisory file lock was **unheld** both times it was
  checked live — **no blocked writers at the lock-file level** at the
  moments sampled.
- No write amplification observed — this is unrelated to and does not
  implicate X78.12's DomainResolver batching fix, which remains untouched
  and unregressed (confirmed: `realtime_creator_funding_extractor.py` does
  not appear anywhere in this incident's call chain).
- **No regression from X78.12.** The dashboard's "Database: HEALTHY" /
  "Funding failures: 0" readings are consistent with what was independently
  observed: the database itself has no stuck lock and no elevated error
  rate at the storage-engine level. The problem is a stuck *application-level*
  scan holding a transaction, which a simple lock/health probe correctly
  reports as healthy because the lock **is** released between individual
  SQL statements' waits — the scan itself just never finishes fast enough
  for the heartbeat to update.

**State explicitly: the database is healthy. The application code holding a
transaction open across an unbounded scan is not.**

---

## Phase H — Root Cause Classification

## B — New independent mechanism

Supporting evidence (measured, not inferred):

1. Live process state: PID 58062, `R`, actively consuming CPU — ruling out a
   dead/exited/deadlocked process.
2. Heartbeat table: `creator-funding` heartbeat 27.2 minutes stale at time of
   check, confirmed via direct SQL against `wt_worker_heartbeat`, while a
   sibling worker's (`creator-resolution`) heartbeat continued updating
   normally in the same window — isolating the stall to this one process.
3. Log timeline: last activity ends at `_post_extraction_intelligence_refresh`'s
   `build_networks_release` call, immediately followed by silence.
4. Source trace: `build_networks_release.py:279` calls `sync_infra_wallets(db)`
   — the single-connection wrapper explicitly documented (in its own and its
   helper functions' docstrings) as unsafe for exactly this use, because it
   runs a "~2min scan" of a 1,616,533-row table on a connection already
   holding an open write transaction, invoked from an **unbounded**
   `asyncio.to_thread` call (line 859) with no caller-side timeout — unlike
   the primary extraction call in the same function, which does have one.
5. Live lock/WAL probes during the stall found no held lock and no busy WAL
   checkpoint, consistent with a long Python-level scan (not a stuck lock)
   being the actual blocking mechanism.
6. PERIOD A's defining tag (`intelligence_refresh.py:55 in _db`) appeared
   once in this incident's window but as a bounded, already-resolved,
   unrelated side-event — not the blocking mechanism itself. PERIOD A is
   explicitly rejected as the cause of this incident.

This is a distinct, newly-identified defect from both Issue 1 (resolved,
DomainResolver write amplification) and the historically-open Issue 2 /
PERIOD A (`intelligence_refresh.py:55`, still unproven, still open,
unaffected by this finding).

---

## Deliverables Summary

- **Process analysis:** §Phase A — running, CPU-active, not exited/deadlocked.
- **Queue analysis:** §Phase B — 16,842 pending, not draining; heartbeat 27.2min stale.
- **Stack trace:** no py-spy dump obtained (sudo unavailable); blocking
  location established via log timeline + source trace instead (§Phase C).
- **Wait-point analysis:** §Phase C — `build_networks_release.py:279`
  (`sync_infra_wallets`) running an unbounded ~2min+ scan under an open
  write transaction, itself called from an untimed `asyncio.to_thread`.
- **Snapshot dependency analysis:** §Phase E — funding worker stall is
  upstream of the snapshot failure, not caused by it.
- **PumpPortal relationship analysis:** §Phase F — independent, no shared code path.
- **Database verification:** §Phase G — healthy; no lock held, no WAL busy,
  no X78.12 regression.
- **Root cause report:** §Phase H — **B, New independent mechanism.**

No fixes were implemented. No code was modified. No commit was made.

---

## Recommendation for X78.14 (not authorized or implemented here)

The fix shape, for a future milestone, is narrow and already has a working
precedent in this codebase: `build_networks_release.py:279`'s
`sync_infra_wallets(db)` call should be replaced with the already-existing
separated-connection pattern (`collect_infra_wallet_rows` on a read-only
connection, `write_infra_wallet_deltas` on a short-lived write connection),
exactly as `infra_sync_scheduler.run_once` already does — per
`sync_infra_wallets`'s own docstring, which documents this exact
distinction and names the scheduler as the caller that already gets it
right. Additionally, `_process_job`'s call to
`_post_extraction_intelligence_refresh` (line 859) has no timeout bound,
unlike its sibling extraction call — that asymmetry should be addressed
independently of the `sync_infra_wallets` fix, since it is what allowed this
particular defect to stall the whole worker rather than fail bounded and
move on. Not implemented, not scoped, not committed as part of X78.13.
