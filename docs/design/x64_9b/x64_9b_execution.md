# X64.9B — DONE-row Deduplication Finding — Aborted Per Task's Own Criteria

**This task was reclassified from execution to validation/design mid-flight.
No rows were deleted. No data was modified in any way.**

The originally-requested task ("X64.9B — Controlled Purge of Completed
`wt_subprov_sig_retry` Records") explicitly stated: **"Abort if: ...
DONE rows are actively referenced."** Phase 1's final validation found
exactly that condition. This report documents the finding and proposes
a revised, evidence-based path forward — it does not execute a purge.

## Phase 1 — Final Validation (executed; this is where the abort triggered)

| Check | Result |
|---|---|
| Current row count | 2,312,171 total |
| Current status distribution | `DONE`=2,312,107, `PENDING`=56, `FAILED`=5, `RUNNING`=3 |
| Number of DONE rows | 2,312,107 (99.997%) |
| Number of non-DONE rows | 64 |
| Schema changes since X64.9 | None — schema byte-for-byte identical to X64.9's recorded snapshot |
| **Production code still does not read completed rows** | **FALSE — this is the abort trigger.** See below. |

### The dependency X64.9 missed

`src/core/ws_cascade.py:2196-2199`, inside
`_process_subprov_sig_durable()` (the function that durably processes
every incoming subprov signature, WS-delivered or replayed), runs:

```python
row = conn.execute(
    "SELECT status FROM wt_subprov_sig_retry "
    "WHERE subprov_wallet=? AND signature=?",
    (subprov, sig)).fetchone()
if row and row[0] == "DONE":
    self._metric("subprov_sig_already_done_skipped")
    return []
```

This is a **durable, DB-backed deduplication boundary**: for every
signature the system observes (live or replayed), it checks whether
that exact `(subprov_wallet, signature)` pair was already marked DONE,
and if so, skips reprocessing entirely — avoiding a redundant
`getTransaction` RPC call and downstream fanout/attribution work.

This directly contradicts the task's stated background assumption
("production never re-reads completed rows"), which appears to trace
back to X64.9's dependency analysis of the *narrower*
`due_subprov_sig_retries()` reader (which does correctly exclude DONE
rows) — X64.9 did not separately check this second, distinct reader in
`ws_cascade.py`.

### Additional context found: an in-memory layer exists, but does not make the DB check redundant

`ws_cascade.py` also has `_subprov_sig_seen()`, an in-process,
memory-bounded (capped at 5,000 keys, evicted oldest-first) dedupe
cache checked separately. This does **not** make the DB-level check
redundant — its small bound means it cannot cover anything beyond the
most recent ~5,000 signatures, and it is fully reset on every process
restart (which happens routinely — see this project's own documented
FD-watchdog self-restart pattern). The DB-level `DONE` check is the
*durable, long-tail* backstop specifically for cases the in-memory
cache has already evicted or lost to a restart.

### Empirical redelivery rate

The code's own comment documents an offline sampling result: **"0/48
across 8 live subprovs"** — i.e., in that sample, this dedupe check
never actually fired (no signature was ever seen twice). This suggests
the check is a rare-case safety net, not a frequently-exercised hot
path. However:
- 0/48 is a small, offline sample, not a live production measurement
  over time — `subprov_sig_already_done_skipped` is tracked as an
  in-memory metric (`self._metric(...)`) but this audit did not find
  it surfaced to any persisted log or dashboard, so there is no way to
  confirm the true redelivery rate over the system's full operating
  history.
- "Rare" is not "zero" — WS delivery systems commonly have
  at-least-once semantics under reconnect/replay scenarios, which is
  presumably exactly why this check was written in the first place.

**Given this, per the task's own explicit instruction, this triggers a
hard abort: "DONE rows are actively referenced."** No purge was
performed.

## Additional evidence gathered (read-only, in support of a revised proposal)

- `last_attempt_at` is indexed (`ix_subprov_sig_retry_status ON
  (status, last_attempt_at)`), directly supporting a time-bounded
  retention rule without a new index.
- DONE rows currently span `last_attempt_at` from **2026-07-04
  14:02:30 to 2026-07-21 15:45:35** — roughly 17 days of accumulated
  history at present.
- No corruption or unexpected schema drift found — `PRAGMA quick_check`
  was not re-run in this pass since no write was attempted, but the
  prior X64.9 integrity check (`ok`) stands unmodified given nothing
  was written to the database.

## Phases 2-9: not executed

Per the task's own abort instruction, Phases 2 (Recovery Planning)
through 9 (Report, in its original "execution" framing) were not
carried out as originally scoped, since Phase 4 (the purge) never
should — and did not — happen. This document itself fulfills the
reporting intent of the task, but as a validation/design finding
rather than an execution record.

## Revised proposal: X64.9B (Design) — Time-Bounded DONE-Row Retention

Rather than a blanket "purge all DONE rows," the evidence above
supports a **bounded-retention purge** that preserves the
deduplication safety net for a defined recent window while still
reclaiming the overwhelming majority of the table's storage:

- **Proposed rule**: `DELETE FROM wt_subprov_sig_retry WHERE
  status='DONE' AND last_attempt_at < ?` — where `?` is a cutoff
  chosen to comfortably exceed any realistic WS redelivery/replay
  window.
- **Cutoff needs a measured basis, not a guess.** This audit did not
  find data pinning the actual maximum redelivery/replay lag this
  system could see (e.g. the Helius WS provider's own redelivery
  window, or how long a backlog replay after an extended outage might
  take to catch up). **Recommend instrumenting
  `subprov_sig_already_done_skipped` to a persisted counter/log
  (currently in-memory only) for a measurement period before fixing a
  cutoff value**, rather than picking an arbitrary 24-48h window without
  evidence.
- As a provisional, conservative starting point pending that
  measurement: **7 days** would retain the check's protection across
  the entire observed DONE-row history's most recent third while still
  reclaiming roughly two-thirds of the table's current 2.31M DONE rows
  (a rough estimate — exact row counts by age band were not queried in
  this pass, since no destructive action follows from this report).
  This should be treated as a starting hypothesis for discussion, not a
  final recommendation.
- Non-DONE rows (`PENDING`/`FAILED`/`RUNNING`, 64 rows total) remain
  untouched under this design, exactly as in the original task's scope.

## Recommended next step

Reclassify this work as **"X64.9B — DONE-row Deduplication Retention
Audit"**: first instrument and measure the actual redelivery/replay
rate and lag (via a persisted counter, observed over a representative
period — days to weeks), establish an evidence-based safe cutoff, then
bring a **separately-scoped, separately-approved** execution task
(e.g. "X64.9B-exec") implementing the bounded purge — following the
same audit-then-execute discipline already established across
X64.8/X64.9/X64.9A. No purge of any kind should proceed against this
table until that measurement exists.

## Success criteria (revised for this reclassified task)

- ✅ Only read-only validation was performed — zero rows modified,
  zero rows deleted.
- ✅ The abort condition explicitly named in the original task's own
  instructions was correctly detected and honored.
- ✅ The dependency that invalidated the original premise is fully
  documented with exact file/line evidence.
- ✅ A revised, evidence-based path to a safe future purge is proposed,
  requiring a measurement step before any execution is authorized.
