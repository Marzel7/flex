# X78.13A — Database Critical Escalation Verification

Read-only investigation. No fixes, no commits, no production changes.

Investigation window: epoch ~1786234243–1786234391 (2026-08-09 ~01:10–01:13 local).

---

## Phase A — Process Inventory

| Process | PID | Uptime | State | CPU | Threads | RSS |
|---|---|---|---|---|---|---|
| creator_funding_worker | 70052 | 29:03 | **U** (uninterruptible I/O wait) | 30.9% (5:26 total, barely growing) | 8 | 59M |
| infra_sync_scheduler | 8407 | 9:03:50 | R | 58.5% | 1 | **930M** |

`creator_funding_worker` is the same process X78.14 was deployed to (commit
`92b5cc63`, restarted at epoch 1786232686). It has not crashed or restarted.
Its process state is `U` — genuinely blocked in an uninterruptible kernel
wait, not spinning. Its CPU time (5:26) has grown only slightly relative to
its 29-minute elapsed uptime, consistent with the thread being stuck
waiting rather than doing work. This is a **different signature from
X78.13's incident**, where the process was `R` (running/runnable) and
actively burning CPU throughout.

**Host-wide (not process-specific):**

```
PhysMem: 7454M used (1638M wired, 3485M compressor), 166M unused
vm.swapusage: total=8192M used=7239M free=953M (88.4% full)
Swapins: 73,175,698  Swapouts: 76,479,853 (cumulative, both climbing)
Load average: 12.39, 12.02, 12.77
Disk: 10,057-17,048 transfers/sec, 124-234 MB/s
```

The host is under severe, sustained memory pressure and is actively
swap-thrashing. This is a system-wide condition, not confined to one
process or one database connection.

---

## Phase B — Database State

- **Write lock file**: currently **HELD**. `flex_complete_database.db.write.lock.owner`:
  ```json
  {"acquired_at": 1786233338.398556, "command": "intelligence_refresh.py:55 in _db",
   "process_pid": 70052, "thread": "asyncio_0",
   "transaction_id": "05260221-f76e-407d-8627-cd5f616e872f", "writer_id": "70052:asyncio_0"}
  ```
  At time of last check (epoch 1786234391), this lease had been held
  continuously for **1052.6 seconds (17.5 minutes)**, with the identical
  `acquired_at` and `transaction_id` across multiple checks — a genuine
  single continuous hold, not rapid reacquisition.
- **Active write owner**: `creator_funding_worker` (pid 70052), thread `asyncio_0`.
- **Busy writers waiting**: the same process's own other threads
  (`asyncio_1`, `asyncio_2`) are timing out repeatedly (60.0-60.008s each)
  trying to acquire the lease their sibling thread already holds — this is
  the SAME process contending with itself, not cross-process contention.
- **WAL checkpoint status**: `PRAGMA wal_checkpoint(PASSIVE)` via a
  fresh read-only connection returns `(1, -1, -1)` — `busy=1`, confirming
  a writer genuinely holds the database busy right now. The checkpoint
  *call itself* completed in 0.038s (fast) — it is reporting busy quickly,
  not hanging itself.
- **Long-running SQL**: the exact SELECT tagged at this call site
  (`creator_funding_worker.py`'s `_post_extraction_intelligence_refresh`,
  the query joining `token_analysis`/`creator_funders`/`creator_self_funding`/
  `network_membership` filtered by a single creator address) was
  reproduced independently on a separate read-only connection, live,
  concurrently with the ongoing incident: **it completed in 0.051 seconds**
  with no matching row. This directly disproves query cost as the holding
  mechanism — the SELECT itself is fast and well-indexed
  (`idx_token_analysis_earliest_creator` exists and is used by the WHERE
  clause).

**Conclusion: the database engine itself is not slow. A single connection
(this process's own) is holding the cross-process write lease open far
longer than any of its own SQL statements cost, while sitting in an `U`
process state — i.e., blocked on something outside SQLite's own query
execution, most consistent with disk I/O stalls induced by the host's
swap-thrashing.**

---

## Phase C — Funding Worker

**Current call chain** (traced from log evidence + source):

```
_process_job (creator_funding_worker.py)
  -> asyncio.wait_for(asyncio.to_thread(_post_extraction_intelligence_refresh), timeout=30s)   [X78.14 addition]
     -> _post_extraction_intelligence_refresh (creator_funding_worker.py:525)
        -> irc_conn = irc_db(DB_PATH)   # intelligence_refresh.py:55 in _db -- THIS is the held lease
        -> SELECT ... (measured 0.051s in isolation, NOT the bottleneck)
        -> [stuck here or shortly after -- exact statement unconfirmed without a stack dump]
```

**Comparison against the X78.13 verified call chain**
(`build_networks_release.py:279` → `sync_infra_wallets` → `collect_infra_wallet_rows`,
a documented ~2min unindexed multi-table scan under an open write
transaction):

**DIFFERENT.** This is not the same call chain. X78.13's mechanism was
already removed from this exact code path by commit `92b5cc63` — confirmed
by the log line `[CFQ_WORKER] [INTEL_REFRESH] NetworksRelease error:
NestedDatabaseWriteError: ... build_networks_release.py:32` appearing
*before* this incident's window, meaning `build_networks_release` is now
failing fast (bounded, in milliseconds-to-seconds via the cross-process
timeout) rather than hanging for minutes — the X78.13 fix is working as
designed. The CURRENT hold's tag (`intelligence_refresh.py:55 in _db`) is
a different, structurally earlier statement in the same enrichment
function, one call site upstream of `build_networks_release` in
`_post_extraction_intelligence_refresh`'s own sequence (IRC watchlist
upsert happens before the NetworksRelease call).

This tag is the historical **PERIOD A / Issue 2** signature, previously
observed but never explained (X78.11/X78.12-era investigations; explicitly
left open, not fixed, "requires separate live instrumentation" per every
prior closure report). **This is very likely PERIOD A recurring live**,
though the exact blocking statement inside `_post_extraction_intelligence_refresh`
after the SELECT (INSERT/UPDATE/commit, or `irc_conn.close()` itself) could
not be pinpointed without a stack trace (`py-spy` requires `sudo`, not
available in this environment).

---

## Phase D — Database Latency Attribution

Dashboard reports p99 ≈ 56s. Live measurements taken during this
investigation:

| Component | Measured | Classification |
|---|---|---|
| `PRAGMA wal_checkpoint(PASSIVE)` call itself | 0.038s | Fast — not the source |
| The exact enrichment SELECT (isolated, concurrent) | 0.051s | Fast — not the source |
| Cross-process lease acquisition (other threads/processes) | 60.0-60.008s repeatedly | **Bounded timeout, working as designed** — this IS visible in logs as the direct consequence of one thread's 17.5-minute hold, not an independent latency source |
| Host disk I/O | 124-234 MB/s, 10k-17k transfers/sec | Elevated, consistent with swap paging |
| Host swap | 88.4% full, climbing | **Severe** |

**Classification: the ~56s p99 figure is consistent with (and most plausibly
IS) repeated 60s-bounded `CrossProcessDatabaseWriteTimeout` waits being
sampled into a latency metric** — every other writer in the system that
happens to contend with `creator_funding_worker`'s currently-held lease
will itself wait up to 60s before failing, and a p99 computed over a mix of
fast (ms-scale) and these bounded-slow (60s) waits would land somewhere in
the tens-of-seconds range depending on the ratio, which is consistent with
~56s. This is **not** a single 56-second query, and **not** unbounded —
it is the visible, expected shape of many callers each hitting the same
60s ceiling while one thread's underlying hold continues. The underlying
hold itself is attributable to **infrastructure synchronization is
explicitly ruled OUT** (X78.14 already fixed that path; confirmed failing
fast in current logs) — the current hold is IRC-refresh-related, layered
on top of a severely memory-pressured host.

---

## Phase E — Heartbeat

`wt_worker_heartbeat` for `creator-funding`: `last_seen=1786233002`.
At last check (epoch 1786234333), staleness = **1331 seconds (22.2
minutes)**.

Separating the possibilities:

- **Worker exited**: NO — supervisor reports RUNNING, same PID (70052),
  no restart.
- **Worker still executing (making progress)**: NO — heartbeat write
  itself is one of the things timing out (`heartbeat write failed:
  CrossProcessDatabaseWriteTimeout ... command=creator_funding_worker.py:136
  in _db_connect`), and cycle-level progress (claimed/completed counts) is
  not advancing during this window.
- **Worker waiting**: YES, but distinguish which thread — `asyncio_1`
  (heartbeat writer) and `asyncio_2` (main cycle's `_recover_stale_and_claim`)
  are both waiting on the cross-process lease. `asyncio_0` (the thread that
  originally acquired the `intelligence_refresh.py:55` lease) is the one
  in the OS-level `U` state.
- **Worker blocked**: YES — this is the most precise characterization.
  Not deadlocked (SQLite/the lease mechanism itself has no circular wait;
  `asyncio_0` is not waiting on `asyncio_1`/`asyncio_2`), but genuinely
  stuck, most likely on disk I/O given the process's `U` state and the
  host's swap-thrashing.
- **Heartbeat failure (mechanism itself broken)**: NO — the heartbeat
  write mechanism (`_write_heartbeat` → `_db_connect`) is functioning
  correctly; it is failing for the same underlying reason every other
  writer is failing (the lease is held), not due to a bug in the heartbeat
  code itself.

---

## Phase F — PumpPortal / Ingestion

"No births" and "No migrations" were not independently re-verified with
live evidence in this investigation (out of scope per the charter's
explicit focus on the database/funding-worker mechanism), but based on
structural knowledge already established in X78.13: `creator_funding_worker`
consumes from a queue populated by *other* processes (the listener/creation
pipeline), and does not itself gate births or migrations. A stalled funding
worker does not, by itself, stop new tokens from being created or migrating
— those are separate pipeline stages. However, given the CURRENT incident
is host-wide (severe memory pressure, swap-thrashing, elevated disk I/O
affecting the whole machine), "no births"/"no migrations" are more likely
**an independent symptom of the same host-level resource exhaustion**
affecting the listener/creation pipeline directly, rather than a downstream
effect of the funding worker's specific stall. This should be verified
directly against `watchtower_listener`'s own state before being asserted
as fact — it is a plausible inference from the host-wide evidence
gathered here, not a directly measured conclusion, and is flagged as such.

---

## Phase G — X78.13 Validation

**Is the verified X78.13 mechanism still the primary cause of the current incident?**

## NO

Supporting evidence:

1. The X78.13 mechanism (`build_networks_release.py:279`'s in-line
   `sync_infra_wallets` call under an open write transaction) was removed
   by commit `92b5cc63`, deployed to this exact process at epoch
   1786232686.
2. Live logs from AFTER that deployment show `build_networks_release`
   failing FAST (bounded `NestedDatabaseWriteError`/`CrossProcessDatabaseWriteTimeout`,
   milliseconds-to-60s) rather than hanging for hundreds/thousands of
   seconds as it did before the fix (historical pre-fix values directly
   observed in the same log: 420s, 492s, 1156s, 1183s, 2624s).
3. The current hold's tag is `intelligence_refresh.py:55 in _db` — a
   different call site, one step earlier in the same enrichment function,
   never touched by the X78.14 fix.
4. The exact SQL at that call site was independently measured at 0.051s —
   ruling out query cost as this incident's mechanism, unlike X78.13 where
   the cost was the documented ~2min scan itself.
5. The process is in state `U` (blocked on I/O) rather than `R` (CPU-bound
   scanning), a materially different observable signature from X78.13.
6. The host is independently confirmed to be under severe, worsening
   memory pressure (88.4% swap full and climbing) — a condition that did
   not feature in the X78.13 investigation.

X78.14 remains correctly targeted at, and has verifiably fixed, the X78.13
mechanism. This is a **separate, newly-manifesting incident** riding on
top of a now-additionally-present host resource exhaustion condition, and
implicating the previously-unresolved PERIOD A / Issue 2 tag
(`intelligence_refresh.py:55 in _db`) recurring live for the first time
with enough evidence to localize (though not fully explain) it.

---

## Phase H — Root Cause Classification

## B — Same mechanism class, with secondary escalation

Refined statement: X78.13's *specific* mechanism (infra-wallet sync under
an open write lease) is **not** implicated in this incident — it is fixed
and confirmed failing fast now. However, this incident is the **same
mechanism CLASS** as both X78.13 and the older PERIOD A: a
long-held write lease originating from `creator_funding_worker`'s
post-extraction enrichment chain, now at a different call site
(`intelligence_refresh.py:55`) within that same chain. It is compounded by
a genuinely new, independent factor — severe host-wide memory pressure and
swap-thrashing — which was not present or measured during X78.13 and is
the most likely reason a normally-fast (51ms query) code path is currently
stuck in an OS-level I/O wait for 17+ minutes rather than completing
quickly.

This is not (C) a new independent *database* failure — the database
engine itself is fast and healthy by direct measurement (checkpoint calls,
isolated query execution). It is not (D) a monitoring false positive — the
held lock, stale heartbeat, and stalled queue are all independently
corroborated by multiple direct, live measurements. It is not (E)
insufficient evidence — the mechanism is well enough localized (exact call
site, exact process, exact thread, exact duration, host-level corroboration)
to act on, even though the precise line inside
`_post_extraction_intelligence_refresh` that is blocking (after the
already-measured-fast SELECT) was not pinpointed without a stack trace.

---

## Recommendation

**X78.14 remains valid and should not be reverted or redesigned.** It
correctly fixed the X78.13 mechanism, and live evidence confirms that fix
is working (the `build_networks_release` call site now fails fast instead
of hanging for minutes).

**Do not implement a fix for this new incident under this charter** — per
the charter's explicit scope, this was a verification-only investigation.
The newly-localized `intelligence_refresh.py:55 in _db` hold, and its
interaction with host-level memory/swap exhaustion, is recommended as a
**separate, new milestone** (e.g. X78.15), scoped specifically to:
(a) determining whether the host's memory pressure is itself a production
concern independent of this codebase (e.g. another process, or overall
memory headroom, unrelated to any of the X78 fixes), and (b) instrumenting
`_post_extraction_intelligence_refresh`'s IRC-refresh code path (the
INSERT/UPDATE/commit/close sequence after the already-proven-fast SELECT)
to determine the exact blocking statement, ideally with a `py-spy` dump
captured with `sudo` access next time this recurs.

Do not conflate this new finding with X78.13's closure — X78.13's verdict
(mechanism B, new independent mechanism, now fixed by X78.14) stands
unchanged. This is a distinct, additional finding.
