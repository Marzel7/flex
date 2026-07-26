# X64.9B1 — Phase 1: Audit of the Existing DONE-row Dedupe Path

Read-only code audit of `src/core/ws_cascade.py`, focused on
`_process_subprov_sig_durable()`, `_subprov_sig_seen()`, and the
`subprov_sig_already_done_skipped` metric, ahead of instrumenting the
dedupe check for durable redelivery measurement (X64.9B1).

## `_process_subprov_sig_durable()` — all callers

Four call sites, each passing a distinct `source` value already:

| Call site | Line | `source` | Context |
|---|---|---|---|
| `catch_up_subprov` batch/replay loop | `ws_cascade.py:4117` | `"CATCHUP"` | Out-of-order batch reprocessing after a gap is detected (X24.7's alternating-priority policy); `advance_cursor=False` |
| `subprov_retry_pass()` | `ws_cascade.py:4642` | `"RETRY"` | The retry-queue consumer itself — rows returned by `due_subprov_sig_retries()` (PENDING/RUNNING only) are re-run through this same function |
| `_hot_subprov_burst()` | `ws_cascade.py:4715` | `"HOT_BURST"` | RPC-polling fallback for newly-armed subprovs, used when WS subscription hasn't confirmed yet — can race with the WS path and observe the same signature twice |
| Live WS message handler | `ws_cascade.py:5325` | `"WS"` | The primary live path — one signature at a time, in arrival order |

All four sources funnel through the exact same dedupe check at the top
of `_process_subprov_sig_durable()` — no source bypasses it.

## Delivery sources and replay paths

- **WS (live)**: Helius WebSocket push notifications, one at a time.
- **HOT_BURST**: an RPC-polling fallback that runs *concurrently* with
  WS subscription attempts for newly-armed subprovs — by design, this
  can observe and attempt to process the same signature the WS path
  also sees, making it the most likely source of a genuine same-process
  duplicate.
- **CATCHUP**: explicit gap-recovery replay — re-fetches and reprocesses
  a batch of signatures for a subprov after a detected discontinuity
  (e.g. a missed WS window). This is a deliberate, expected replay path
  and is exactly the scenario the durable DONE check exists to protect
  against (re-observing an already-processed signature after a gap).
- **RETRY**: technically also a "replay" in the sense that it re-invokes
  the same function on rows explicitly selected because they are
  PENDING or RUNNING (never DONE, per `due_subprov_sig_retries()`'s own
  filter) — so RETRY-sourced calls should almost never hit the
  `row[0] == "DONE"` branch under normal operation, since a DONE row
  wouldn't be selected for retry in the first place. (An edge case
  where this branch *could* still fire from a RETRY-sourced call: if
  the same `(subprov, signature)` was independently marked DONE by a
  concurrent WS/HOT_BURST call between the retry-queue read and this
  function's dedupe check — a narrow race, not the common case.)

## Timestamp fields available

- `wt_subprov_sig_retry.last_attempt_at` (INTEGER, unix epoch seconds) —
  updated on every write to the row (enqueue/running/done/failed),
  meaning at the moment the dedupe check reads a DONE row, the current
  `last_attempt_at` value **is** the original completion timestamp
  (the row is never written again after reaching DONE, since a later
  duplicate is skipped before any further write — this is exactly the
  behavior being measured, i.e. it's self-consistent: as long as the
  dedupe check keeps firing correctly, `last_attempt_at` on a DONE row
  is frozen at its original completion time).
- The existing `SELECT status FROM wt_subprov_sig_retry WHERE
  subprov_wallet=? AND signature=?` query does **not** currently select
  `last_attempt_at` — this must be added to compute duplicate age
  (Phase 4).
- No separate "first marked DONE at" column exists distinct from
  `last_attempt_at` — since a DONE row is never rewritten (per the
  dedupe check preventing exactly that), `last_attempt_at` doubles
  correctly as "the time this row was marked DONE," with no ambiguity.

## Current in-memory metric behaviour

- `self._subprov_sig_metrics: dict[str, int]` is initialized once per
  `Cascade` instance (in `__init__`, line ~2082) and incremented via
  `self._metric(name)` (line 2154-2155) — a plain in-memory dict, no
  persistence of any kind.
- `subprov_sig_already_done_skipped` is incremented at line 2205, the
  exact branch this task instruments.
- The dict is surfaced at two points: `casc._subprov_sig_metrics.get(...)`
  at line 4890 (a specific named-key readout) and `**dict(casc._subprov_sig_metrics)`
  at line 4912 (a full dump) — both appear to feed some kind of
  status/health payload (not traced further in this pass since it's
  out of scope for the instrumentation itself, but confirms the dict is
  read elsewhere in-process, so any change to its shape/keys should not
  break those readers — the new instrumentation will add new keys
  additively, not rename/remove existing ones).

## Does the metric survive process restarts?

**No.** `_subprov_sig_metrics` is a plain Python `dict` attribute on
the `Cascade` object, created fresh in `__init__` — it is fully lost on
every process restart. Given this project's own documented FD-watchdog
self-restart pattern (firing roughly every 10-20 minutes under load,
per X64.7C's investigation), this in-memory counter alone cannot
provide a meaningful cumulative measurement over any period longer than
the time between restarts. **This is the core reason durable
persistence is required for this task's objective** (a multi-day/week
measurement window, per the eventual retention-cutoff decision this
work is building toward).

## Can duplicate events be emitted more than once for the same underlying signature?

In principle, yes, in a narrow race: if the *same* signature arrives
near-simultaneously via two different sources (most plausibly WS and
HOT_BURST, which are explicitly designed to run concurrently for the
same newly-armed subprov), both could independently reach the dedupe
check before either has written a DONE status — in that race, neither
sees the other's in-flight state as DONE yet (the row would be RUNNING
at most, not DONE), so **this specific race does not cause a double
"already_done_skipped" count** — it would instead cause both to proceed
past the dedupe check and both attempt the full processing path,
which is a *separate*, pre-existing behavior unrelated to this
instrumentation task (and not something this task is scoped to fix).

The dedupe-skip event this task measures only fires when a **prior**
call has already fully completed and committed DONE status — for two
skip-events to double-count the *same original completion*, two
separate later signatures would each need to independently observe the
already-DONE row, which is a legitimate scenario (e.g. WS redelivers
the same signature twice on different occasions) and should indeed be
counted as two separate duplicate observations, not deduplicated
against each other — each occurrence represents independent evidence
about redelivery frequency, which is exactly what this measurement
needs to capture.

## Constraints this design must respect (carried forward from the task)

- The dedupe check's existing behavior (return `[]` immediately) must
  not change in timing or outcome — instrumentation is strictly
  additive, added around the existing branch, never inside a path that
  could alter its return value or add blocking work before the
  `return []`.
- No existing retry row may be updated, re-timestamped, or otherwise
  modified by this instrumentation.
- The metrics write must not risk a nested-write-lane error — see
  Phase 3 for the concrete mitigation (a separate, short-lived
  connection, opened and closed independently of the dedupe check's own
  connection, with best-effort error handling that never propagates a
  failure back into the dedupe path).
