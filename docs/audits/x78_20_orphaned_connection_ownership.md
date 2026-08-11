# X78.20 — Orphaned SQLite Connection Ownership

## Verdict

**H — OWNER STILL UNRESOLVED**

One concrete contributing defect is proven as
`FAILED_CROSS_THREAD_CLOSE_RETAINING_LOCK`: production listener diagnostics contain repeated reaper-thread
`sqlite3.ProgrammingError` failures for native connections created on other
threads. Several events record `write_lane_owned=true`, and several record an
open transaction. The pre-X78.20 close sequence released the application write
lane before calling native `sqlite3.Connection.close()`. Native close then
failed on thread affinity, leaving the connection registered and open after its
serializer/flock ownership had been cleared.

This is runtime evidence plus a directly traced code path and deterministic
reproduction. It justified the narrow lifecycle repair below. It does **not**,
however, identify the kernel flock holder for the historical null-owner episode,
so the primary Phase 46 classification remains H rather than promoting
correlation to proof.

## Baseline

- Branch: `classification-attribution-axis`
- Baseline HEAD: `cb1fc110e105436c4baa9fe15f956628f80db3ce`
- X78.17: `1ef0a5c5`
- X78.18: `cb1fc110`
- X78.19 lifecycle/metric changes were present but uncommitted and preserved.
- Production lock: `database/flex_complete_database.db.write.lock`
- Observed inode/device: `445573398 / 16777234`
- A live sample attributed the flock sidecar to Creator Resolution PID 39618;
  `lsof` showed open lock-file descriptors in multiple writers, which are
  candidates only and do not independently identify the kernel flock holder.

## Lock stack

1. Application opens a `TrackedConnection`.
2. The first write takes process-local `_DB_WRITE_LOCK`.
3. `acquire_write_lease()` takes the database-path flock.
4. It publishes the acquisition owner sidecar.
5. SQLite begins/commits/rolls back the transaction.
6. `TrackedConnection` releases the flock and process lock.
7. Native SQLite close must execute on the creation thread.

Before this repair, step 6 occurred before step 7 during `close()`, even when
step 7 ran on a foreign reaper thread and was guaranteed to fail. Owner metadata
was also unlinked before `LOCK_UN`, creating a diagnostic blind interval and
allowing a stale releaser to erase a successor sidecar.

## Repair

- Reject foreign-thread close before releasing any lane or touching the native
  connection. Retain the connection in the live registry as
  `CLOSE_FAILED_WRONG_THREAD`; owner-thread rollback/close remains authoritative.
- Track explicit native states: `OPEN`, `CLOSE_FAILED_WRONG_THREAD`,
  `CLOSE_FAILED`, `CLOSED`.
- Serialize owner-sidecar publication/removal with a guard flock.
- Publish `RELEASE_PENDING`, release the physical flock, and only then remove
  matching metadata. A physical release failure retains `RELEASE_FAILED` with
  PID/thread/caller/error.
- Match metadata by transaction ID, so stale/double release cannot remove a
  successor owner's diagnostics.
- Capture one bounded JSONL bundle per wait episode after one second when the
  flock is busy but application owner metadata is absent. The bundle includes
  waiter, process-local leases, open/failed connections, lock-file openers and
  WAL size. Default path:
  `logs/diagnostics/x78_20_null_owner_episodes.jsonl`.

No timeout, SQLite safety, serializer, second-hop, Evidence, acquisition,
Primitive, authority, discovery, motif or relationship semantics changed.

## Deployment

Only active Python services importing the shared lifecycle module were reloaded.
Stopped services and ngrok were untouched.

| Service | Before PID | After PID |
|---|---:|---:|
| watchtower_api | 30675 | 40655 |
| watchtower_listener | 40342 | 40666 |
| creator_funding_worker | 39601 | 40676 |
| creator_resolution_worker | 39618 | 40680 |
| walkback_worker | 3120 | 40683 |
| intelligence_snapshot_scheduler | 3119 | 40700 |
| ws_cascade | 3124 | 40704 |
| alert_evaluator | 3116 | 40717 |

Post-reload all eight reported `RUNNING`; `/` returned HTTP 302 in 4.9 ms,
the WAL was checkpointed to zero bytes at the first sample, and subsequent
owner metadata used the new `ACTIVE` state.

## Deterministic proof

- Foreign reaper close retains the lane, native connection and registry record;
  the owner thread subsequently rolls back and closes it.
- Stale/double release cannot erase a newly acquired owner's sidecar.
- A deliberately removed sidecar while flock remains held emits exactly one
  correlated null-owner episode.
- Terminating a process releases its kernel flock and permits a bounded parent
  acquisition.
- Same-thread close, double close, nested-write rejection and prior timeout
  attribution remain covered by existing regressions.

Validation completed:

- Focused X78.20/X78.19/lease suite: **23 passed**.
- Bounded X78.9–X78.19 regression: **146 passed**.
- `git diff --check`: clean.

## Remaining boundary

The first bounded episode bundle captured a 20.135-second waiter with no
application owner. It retained a price-service connection marked
`CLOSE_FAILED_WRONG_THREAD` and `write_lane_owned=true`, plus the candidate
lock-file-opening PIDs. macOS `lsof` did not identify which candidate held the
advisory flock in that historical sample. After reload, the sidecar again
reported an `ACTIVE` owner and no second null-owner episode appeared in the
initial bounded interval. A future episode now records `lsof` lock-status fields
as well as candidates, but until one correlates the exact kernel holder the
remaining null-owner root cause is unresolved.

The reaper does not have an owner-thread cooperative cleanup queue. It now
records the request/failure safely and leaves cleanup to the owning scope. This
is an explicit architecture gap, but adding a general cross-thread cleanup
executor would be a pool redesign and is outside the proven minimal repair.

Listener descriptor growth remains a separate question. Current classification:
`OWNER_UNRESOLVED`; the repair prevents unsafe foreign cleanup but does not claim
every idle listener FD is lock-retaining. Natural second-hop runtime metrics were
`NOT_OBSERVED` and second-hop was not forced or modified.

Evidence Platform remains disabled. Acquisition remains on hold.
