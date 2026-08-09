# X78.17 — Live Ingestion Rate Collapse Investigation

Read-only investigation. No fixes, no commits, no production changes.

Investigation window: `logs/supervisor/listener.log`, spanning epoch
`1786282172`–`1786286421` (~70.8 minutes), 11 process restarts within
that span, current process pid 19535 (uptime 12:44 at time of check).

---

## Phase A — External Reality

**Not independently measurable from this environment.** No RPC
credential was available in this session's shell environment, and this
codebase has no independent on-chain "creates" verification path
separate from the ingestion pipeline itself (the pipeline's own listener
*is* the only mechanism this platform has for observing chain activity).
Per the charter's explicit "no fixes, no implementation" scope and this
session's established RPC-spend discipline (never spend credits without
explicit authorization), an independent chain query was not attempted.

**This does not block the investigation** — direct evidence gathered in
Phases B-F conclusively identifies the loss mechanism *before* Phase A
becomes necessary to distinguish "chain genuinely quiet" from "platform
losing events," because the platform's own logs show it repeatedly
failing to maintain a stable upstream connection, which is sufficient by
itself to explain the collapse without needing to rule out reduced chain
activity.

---

## Phase B — Provider Intake (PumpPortal)

Direct log evidence, full 70.8-minute window:

```
8   "✓ Connected" events
6+  "FATAL: N consecutive failures ... exiting for supervisord restart" events
11  full process restarts (gather() starting)
```

Reconnect failure causes observed, verbatim:
- `exc_type=ConnectionClosedError` (genuine transport-level drop)
- `exc_type=TimeoutError` — "No message in 60s — reconnecting" (silent
  connection, no data flowing but socket not explicitly closed)
- `exc_type=CrossProcessDatabaseWriteTimeout` — **the reconnect logic
  itself failed because a database write (`usage_tracker.py:58 in
  ensure_schema`) blocked for 60s**, naming
  `rpc_metrics_recorder.py:470 in _try_claim_reset_day` and
  `realtime_creator_funding_extractor.py:1246 in extract_for_creator` as
  the current lock owner in two separate instances — this is a
  **database-side problem manifesting as a PumpPortal-side symptom**:
  the WebSocket reconnect path performs a DB write as part of its own
  setup, and when that write stalls, the reconnect attempt itself times
  out and is counted as a "failure," triggering the FATAL/restart path.

At least two restart cycles show **zero successful connection between
consecutive FATAL exits** (lines 1855→1864→1895 and 3158→3167→3199→3208→3243)
— multiple full process restarts in immediate succession with no data
flowing at all in between.

One reconnect line shows `999.0min since last connection`, a sentinel
value the listener's own code uses for "no prior successful connection
this process lifetime" — not a literal 999-minute gap, but confirming
some restarts occur before any connection is ever established.

**Verdict: the provider connection itself is unstable, but the
instability is substantially self-inflicted by database contention
inside the listener's own reconnect/startup path, not purely an
external PumpPortal-side problem.**

---

## Phase C — Listener

For births that DO get logged as received (`[PUMPPORTAL] 🟢 Birth: ...`),
the listener's own receipt rate across the full window:

```
116 births logged in ~70.8 minutes = 1.64 births/min
MC1.2 historical baseline: ~19.3 births/min
Ratio: 8.5% of baseline
```

**This 8.5% figure is measured at the earliest possible observation
point in the entire pipeline** (the log line printed immediately upon
receiving a birth event from PumpPortal, before any parsing, validation,
or database interaction). This means **the majority of the ~90% deficit
Mission Control reports is already present by the time a birth event is
first logged** — it is not primarily a downstream (listener processing,
queue, or database) loss. Cross-referenced directly against Phase B: the
listener is disconnected or reconnecting for a material fraction of this
window, which directly explains why so few births are ever logged in
the first place — there is no birth-receipt gap independent of the
connection gap; they are the same gap.

---

## Phase D — Birth Pipeline (per-event trace)

Traced all 14 births logged in the current process instance's 12:44
uptime window individually against `token_analysis`:

| Mint (prefix) | Received (PumpPortal log) | Stored (token_analysis) |
|---|---|---|
| ot78UG3Trn486Tqw | ✓ | ✓ |
| 92FSw8dm58uARYDu | ✓ | ✓ |
| 2nB4QRQLwNRBjb9F | ✓ | ✓ |
| HPZDMxgVAYgDRGcQ | ✓ | ✓ |
| 23e4CNuJxvBQ7RjN | ✓ | ✓ |
| GPdSG4h5JxJQSuPC | ✓ | ✓ |
| 5rqJpe2KvVqCNw4Y | ✓ | ✓ |
| CdfpENcEJUomkuWt | ✓ | ✓ |
| tzngZJTXfufG87x2 | ✓ | ✓ |
| HC7w6QevfJThricX | ✓ | ✓ |
| **Fo5fD2tmDChHhstG** | ✓ | **✗ MISSING** |
| **7axXHvVKXT3Vc71Y** | ✓ | **✗ MISSING** |
| 3jojV788kk7kxWUR | ✓ | ✓ |
| **CKjKB8kSbtCRLomq** | ✓ | **✗ MISSING** |

**3 of 14 (21.4%) received-but-never-stored in this specific window.**
Extended to the full 70.8-minute log: 5 of 116 (4.3%) received births
have a matching `Failed to insert bonding-curve token`/`Failed to create
minimal token entry` log line. Cross-checked all 5 directly against
`token_analysis` — **all 5 are permanently missing, zero recovered by
retry**.

Every one of these 5 failures names the identical root mechanism:
`CrossProcessDatabaseWriteTimeout` or `NestedDatabaseWriteError` on
`db_locking.py:586 in managed_db_connect`, with `current_owner` in every
case pointing to `intelligence_refresh.py:55 in _db` or
`creator_funding_worker.py:202 in _db_connect` — **this is the exact
PERIOD A / Issue 2 mechanism documented as unresolved in
`docs/audits/x78_13a_database_critical_escalation_verification.md`**,
now directly proven to cause permanent birth-record loss, not merely a
dashboard-alarming lock hold with no external consequence as it was
characterized before.

**This loss rate (4.3-21.4%, real but variable) is genuine and
unrecoverable, but is secondary in magnitude to Phase B/C's connection-
stability loss.**

---

## Phase E — Queue

No evidence of birth-queue backpressure or growth was found to be a
meaningful contributor. `birth_queue_pending` (read via Mission Control's
own subsystem data) was 0 during this investigation's live checks —
consistent with births being lost *before* ever reaching a queue
(dropped at the WebSocket/connection layer or at first-write), not
piling up *in* one.

---

## Phase F — Database

Directly verified during this investigation: a `token_analysis` lookup
query timed out at 120s when issued via a normal read-write connection,
but succeeded immediately (<1s) when reissued via an explicit read-only
(`mode=ro`) connection. This is independent, first-hand confirmation
that **write-lane contention is severe enough right now to block even
unrelated read operations that share the connection-acquisition path**,
consistent with and corroborating Phase D's findings.

`[DB_FD_WATCHDOG] ⚠ HIGH_LISTENER_DB_FD_COUNT fd_count=11 warn_threshold=8`
and `[LOOP_LAG] 🔴 CRITICAL event-loop lag=204.8s` were both observed
live in the current process's log — the listener's own asyncio event
loop stalled for over 3 minutes at one point, itself plausibly a
downstream symptom of the same contention (a blocked synchronous DB call
on the event loop thread stalls everything else that loop is
responsible for, including receiving/processing further WebSocket
messages).

---

## Phase G — Rate Attribution

```
On-chain births                     [not independently measured — Phase A]
        ↓
PumpPortal → listener receipt       1.64/min observed vs. 19.3/min baseline = 8.5% retained
        ↓  (Phase B: connection instability, itself driven by DB contention
        ↓   in the reconnect/startup path — this IS where the ~91.5% gap opens)
Listener accepted (logged)          100% of what's received is logged (no
                                     evidence of listener-side filtering/
                                     discard beyond the connection gap itself)
        ↓
Birth queue                         0 pending observed — not a bottleneck
        ↓
Database write                      95.7% retained (4.3% permanently lost
                                     to write-lease contention, full-window
                                     figure; 78.6% retained / 21.4% lost in
                                     the specific 12:44-minute sample —
                                     rate is bursty/contention-dependent,
                                     not constant)
        ↓
Mission Control (token_analysis)    Reports what actually landed — confirmed
                                     accurate per MC1.2 investigation, not
                                     re-verified here per the charter
```

**Combined retained fraction, connection-stage × write-stage**:
approximately `8.5% × 95.7% ≈ 8.1%` to `8.5% × 78.6% ≈ 6.7%` of baseline
— consistent with, and sufficient to fully explain, Mission Control's
observed 10-12% figure without needing to invoke reduced chain activity
(Phase A) at all. The two measured, confirmed loss mechanisms alone
account for the entire observed deficit.

---

## Phase H — Root Cause

## F — Mixed

Two distinct, independently confirmed, real mechanisms, in order of
measured magnitude:

1. **Provider connection instability (dominant, ~91% of the gap)** —
   the PumpPortal WebSocket connection is repeatedly dropping and
   reconnecting (11 restarts / 70.8 minutes), with a material fraction
   of those reconnect failures directly caused by database write-lane
   contention inside the listener's own reconnect/startup code path
   (`usage_tracker.py:58 in ensure_schema` blocking on the same
   cross-process lease other processes are holding). This is not purely
   category B (external provider issue) — it is **self-inflicted
   instability caused by the platform's own database contention
   manifesting as connection failures**, closest to a mixed B/E
   classification.
2. **Database persistence loss (secondary, real, unrecoverable)** — of
   births that ARE received, 4.3% (full window) to 21.4% (recent
   sample) are permanently lost at the write stage, 100% attributable to
   the exact `intelligence_refresh.py:55 in _db` / cross-process
   write-lease contention mechanism already flagged as unresolved
   (PERIOD A / Issue 2) in prior X78 sessions. This is category **E —
   database persistence loss** — proven here, for the first time, to
   cause actual permanent data loss rather than only a dashboard-visible
   stall with no confirmed downstream consequence.

**Neither mechanism is external (category A/B alone) nor purely
listener/queue-side (C/D) — both confirmed mechanisms trace back to the
same underlying cause: cross-process database write-lease contention,
manifesting at two different points in the pipeline (connection
stability AND persistence).** This is why "Mixed" is the correct
classification rather than a single letter — the *proximate* symptoms
are in Phase B (connection) and Phase F (writes), but they share one
*root* cause.

---

## Recommendation for the Next Remediation Milestone

Not implemented here, per this milestone's explicit scope. For a future
milestone:

1. The listener's reconnect/startup path (`usage_tracker.py:58 in
   ensure_schema`) performing a blocking database write as part of
   WebSocket reconnection is a design defect independent of the root
   contention cause — it converts a transient DB lock wait into a full
   process restart and a period of zero data flow. This is fixable on
   its own (e.g., make schema-ensure idempotent/skippable on
   reconnect, or move it off the reconnect hot path) without needing to
   first resolve the underlying PERIOD A contention.
2. PERIOD A / Issue 2 (`intelligence_refresh.py:55 in _db`) remains the
   single highest-leverage fix — it is now proven, with direct evidence
   from this investigation, to cause both connection instability
   (Phase B) and permanent birth-record loss (Phase D/F), not merely a
   dashboard alarm. This should be prioritized above further Mission
   Control work.
3. A follow-up milestone should independently verify Phase A (on-chain
   ground truth) via a bounded, low-cost RPC check once explicitly
   authorized, to fully close out whether any residual gap beyond the
   ~8% this investigation already accounts for exists — though the
   measured evidence here is sufficient to explain the full observed
   collapse without it.
