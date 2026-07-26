# X64.7C — Phase 1: Controlled Backup Deletion — Final Report

Executed 2026-07-21. Scope: delete exactly the two named June 4 manual
pre-migration backups, per explicit authorization, after full
pre-deletion verification.

## Phase 1 — Pre-deletion verification (all passed)

| Check | Result |
|---|---|
| Both files exist | ✅ confirmed (8.1G each) |
| `PRAGMA quick_check` | ✅ `ok` on both |
| Open by any process (`lsof`) | ✅ neither file held open |
| Code/config/script references | ✅ zero (see [backup_dependency_audit.md](../x64_7b_preflight/backup_dependency_audit.md)) |
| Not the live databases | ✅ confirmed distinct paths from `database/flex_complete_database.db` and `database/wt_ops_v2.db` |

No abort condition was triggered.

## Phase 2 — Deletion

| File | Size |
|---|---|
| `database/flex_complete_database.backup_pre_lineage_20260604_162419.db` | 8,187,183,104 bytes |
| `database/flex_complete_database.backup_pre_opsbridge_20260604_170906.db` | 8,171,587,072 bytes |

- Deletion timestamp: 2026-07-21 13:41:56 local
- Bytes reclaimed: **17,358,770,176 bytes (16.17 GiB)**
- Free space before: 795Mi (0.3% capacity, disk effectively full)
- Free space after (immediately post-delete): 17Gi (91% capacity)

## Phase 3 — Post-deletion verification

| Check | Result |
|---|---|
| Both files no longer exist | ✅ confirmed (`ls` → No such file or directory) |
| ~16GB reclaimed | ✅ 16.17 GiB, matches expected sum |
| Free space increased | ✅ 795Mi → 17Gi, holding steady at 17Gi as of this report |
| `database/flex_complete_database.db` exists, openable | ✅ 10,600,730,624 bytes, live, actively written |
| `database/wt_ops_v2.db` exists, openable | ✅ 2,522,779,648 bytes, live, actively written |
| `PRAGMA quick_check` on `flex_complete_database.db` | ✅ **ok** (read-only, no modification) |
| `PRAGMA quick_check` on `wt_ops_v2.db` | ✅ **ok** (completed after brief contention with the live write-serializer lane under active load) |

No live database was modified by any check in this phase.

## Phase 4 — Process health

| Process | Status |
|---|---|
| `watchtower_listener` | RUNNING, pid 64512, uptime steady since 14:32:36 |
| `walkback_worker` | RUNNING, pid 55843, uptime unbroken since the one deliberate restart at 14:03:16 — **zero restarts since**, deletion or otherwise |

**Investigated: repeated `watchtower_listener` restarts after the deliberate one.**

Five additional restart events were observed after my one deliberate
restart (14:03:16 → pid 55792): SIGTERM-restarts at 14:09:51 and
14:12:01 (operator/supervisor-initiated, cause not tied to this task),
and two supervisord-logged **"exited: watchtower_listener (exit status
1; not expected)"** events at 14:13:47 and 14:32:35.

Root-caused both "unexpected" exits by locating the `[LISTENER]
Starting listener` boundary lines in `listener.log` and reading
backward. Both are immediately preceded by the same log line:

```
[DB_FD_WATCHDOG] 🚨 CRITICAL_LISTENER_DB_HANDLE_LEAK fd_count=12 threshold=12 db=.../flex_complete_database.db — exiting for clean restart
```

This is the **pre-existing `_db_fd_watchdog` self-protection
mechanism** (documented in this project's own operating history — see
"Listener FD leak → WAL pinning → p99 spike": `DB_WRITE_SERIALIZE=1` +
`_db_fd_watchdog` with warn-threshold 8, exit-threshold 12). The
watchdog deliberately calls `exit(1)` when the listener's own open
file-descriptor count against `flex_complete_database.db` reaches 12,
forcing a clean supervisord respawn before accumulated FDs can pin the
WAL checkpoint. supervisord logs this as "not expected" only because
it doesn't distinguish an intentional self-exit code from a genuine
crash — there is no Python traceback at either boundary, and dozens of
lower-severity `HIGH_LISTENER_DB_FD_COUNT` warnings (fd_count 8-10)
appear before and after both events, including several **after** the
16.17GiB deletion (e.g. lines 282677-282988), confirming this is
ongoing baseline behavior unrelated to the deletion.

**Conclusion: no restart occurred because of the deletion.** All
restarts are explained either by my own deliberate restart or by this
pre-existing, intentional FD-watchdog exit mechanism, which was firing
before the deletion and continues to fire afterward at the same
cadence — it is not a regression introduced by this task or by
X64.5-X64.7A's code changes.

- No "disk full" / "No space left on device" errors since the
  deletion — the one occurrence found in `listener.log` (line 177675,
  `[BIRTH] Failed to insert bonding-curve token ... No space left on
  device`) predates the deletion by a wide margin (well before the
  270000-line region corresponding to the 14:03+ window).
- No new sqlite write failures found in `listener_err.log` in the
  window following the deletion; only routine `DB_COMMIT_SLOW` /
  `DB_CONNECT_SLOW` / `DB_LOCK_ERROR`-and-retry noise, consistent with
  pre-existing, already-documented lock-contention behavior on the hot
  DB, not new failures.

## Phase 5 — Summary

| Item | Value |
|---|---|
| Files deleted | 2 (`backup_pre_lineage_20260604_162419.db`, `backup_pre_opsbridge_20260604_170906.db`) |
| Total bytes reclaimed | 17,358,770,176 bytes (16.17 GiB) |
| Free space before | 795Mi (~0.3% free) |
| Free space after | 17Gi (9% free, 91% capacity) |
| Integrity checks | `flex_complete_database.db`: **ok**. `wt_ops_v2.db`: **ok** |
| Listener status | RUNNING, stable; explained the FD-watchdog self-restarts as pre-existing, non-regression, non-deletion-related behavior |
| Worker status | RUNNING, fully stable, zero restarts since the one deliberate restart |
| Unexpected observations | The listener's FD-watchdog exit-and-respawn cycle (~every 10-20 min under load) is a pre-existing operational pattern, not new — flagged here only because it was surfaced during this task's process-health check; no action taken, no file outside the two approved backups was touched |

No verification failed. No file outside the two explicitly approved
backups was deleted, moved, or modified. Task complete.
