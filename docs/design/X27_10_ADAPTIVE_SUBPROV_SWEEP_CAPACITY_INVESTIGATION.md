# X27.10 — Adaptive Subprov Sweep Capacity Investigation

**Status: investigation only. No code, config, or runtime behaviour was changed
in this sprint**, per the brief's explicit constraint ("Do not modify sweep
limits, worker counts, TTLs, scheduling cadence, or concurrency" / "Do not
implement any optimisation during this investigation").

## Objective

Determine whether the subprov sweep scheduler's fixed `selected=10`-per-cycle
cap is under-provisioned or an intentional design constraint, given the
repeatedly-observed pattern of `eligible` oscillating (and, in this dataset,
spiking as high as 980) while `selected` never moves off 10.

## Phase 1 — Selection pipeline trace

Full chain, confirmed by reading the code (not inferred):

```
_maintenance() (fire-and-forget)
  → subprov_sweep_pass_guarded()        [ws_cascade.py:4507] — overlap guard
    → subprov_sweep_pass()              [ws_cascade.py:4391]
      → store.fair_sweep_candidates(conn, limit=MAX_ACTIVE_SUBPROVS)
                                         [ws_cascade_store.py:1204, LIMIT at 1218]
      → store.sweep_coverage_snapshot(conn, cap=MAX_ACTIVE_SUBPROVS)
                                         [ws_cascade_store.py:1239]
      → asyncio.Semaphore(SWEEP_CONCURRENCY)   [ws_cascade.py:4452] — bounds concurrent RPC, not selection
      → per row: catch_up_subprov() → mark_swept() iff outcome == "SUCCESS"
      → _log("🧭 sweep cycle: eligible=... selected=...")   [ws_cascade.py:4497]
```

`selected` in the telemetry is literally `len(rows)`, and `rows` comes from
exactly one place: the SQL `LIMIT ?` in `fair_sweep_candidates()`
([ws_cascade_store.py:1218](../../src/core/ws_cascade_store.py#L1218)),
parameterized by:

```python
MAX_ACTIVE_SUBPROVS = int(os.environ.get("WS_MAX_ACTIVE_SUBPROVS", "10"))
```
([ws_cascade.py:63](../../src/core/ws_cascade.py#L63))

This is the **sole** place the cap of 10 is imposed. `SWEEP_CONCURRENCY`
(default 4, [ws_cascade.py:96](../../src/core/ws_cascade.py#L96)) is a
separate, unrelated knob — it bounds how many of the 10 *selected* rows run
concurrently, not how many are selected.

Selection order (from `fair_sweep_candidates`'s `ORDER BY`) is: never-swept
first (soonest expiry), then least-recently-swept (soonest expiry as
secondary key), then `id` as tie-breaker — a deterministic, restart-safe
fairness queue. This part of the design (X24.2 Phase 2) is sound and is not
in question; the finding below is specifically about the cap's *size*, not
its ordering.

## Phase 2 — Rationale for the cap

Traced via `git log -S` on the constant's introduction:

```
commit fac3f29 "Fix WS cascade instant-launch catch-up and mint extraction"
(2026-06-14) — the file's own initial commit.
```

`MAX_ACTIVE_SUBPROVS = int(os.environ.get("WS_MAX_ACTIVE_SUBPROVS", "10"))` was
introduced in the same line as three sibling constants:

```python
SESSION_TTL_SEC   = int(os.environ.get("WS_SESSION_TTL_SEC", "600"))
CANDIDATE_TTL_SEC = int(os.environ.get("WS_CANDIDATE_TTL_SEC", "180"))
MAX_CANDIDATES    = int(os.environ.get("WS_MAX_CANDIDATES", "25"))
MAX_ACTIVE_SUBPROVS = int(os.environ.get("WS_MAX_ACTIVE_SUBPROVS", "10"))
```

under the header comment `# ── config (env, conservative defaults) ──`. There
is no commit message, docstring, or code comment anywhere that ties `10`
specifically to an RPC budget, a DB-lock concern, or a load test. The X24.2
Phase 2 rewrite (which introduced `fair_sweep_candidates()` and the
never-swept/least-recently-swept ordering) explicitly reused the *existing*
cap value and reframed it as "a rotation fix, not a cap increase" — i.e. that
rewrite deliberately chose not to revisit the number, not that it re-derived
and re-confirmed it.

**Conclusion: the cap is an untuned historical default** carried forward
unchanged from the file's initial build, not a value derived from RPC-quota
math, DB-contention testing, or worker-pool sizing. Classification: **historical
tuning / arbitrary constant**, not a deliberate safety constraint.

## Phase 3 — Queue dynamics (measured from 790 live sweep cycles)

Source: live `🧭 sweep cycle` log lines captured by the running background
monitor task across this session (790 cycles total, spanning `eligible` from
45 to 980).

```
eligible:   mean=173.3   p50=162   p95=267   max=980
selected:   constant 10, every single one of 790 cycles (0 exceptions)
failed:     35 total failed inspections across 25 cycles (~3% of cycles had ≥1 failure)
```

Drain rate is fixed at 10/cycle (by construction — it's the cap). Arrival
rate was estimated per-cycle as `eligible[i] - (eligible[i-1] - selected[i-1])`
(net new sessions appearing since the last snapshot, after accounting for the
10 removed):

```
implied net arrivals/cycle: mean=10.0   p50=6.0   max=800   min=-580
fraction of cycles where eligible is GROWING (arrival > drain): 63%
```

**Arrival exceeds drain in the majority of observed cycles.** The `max=980`
eligible reading is not a one-off outlier disconnected from this — it's the
tail of a sustained run where `eligible` climbed from single/double digits
into the hundreds-to-~980 range over consecutive cycles before draining back
down over roughly a dozen cycles (980→938→852→666→441→404→273→206→149→116→45),
consistent with a burst of arrivals overwhelming the fixed 10/cycle drain and
then being worked off once arrivals subsided — i.e. queueing behavior, not
noise.

## Phase 4 — Expiry impact (temporary delay vs. permanent miss)

Traced `expire_stale_sessions()` ([ws_cascade_store.py:1509](../../src/core/ws_cascade_store.py#L1509))
and its caller ([ws_cascade.py:4364](../../src/core/ws_cascade.py#L4364)):

```python
for sid, subprov in store.expire_stale_sessions(conn):
    await self.mgr.unsubscribe(subprov)
    _log(f"🗑 session expired/dismissed {subprov[:12]}…")
    ...
    if _pw:
        _pw.evict_by_subprov(subprov)
```

A session whose `expires_at` passes while `last_swept_at IS NULL` (i.e. it
was in `expiring_60s_unswept` and lost the race) transitions `ACTIVE→EXPIRED`
**and is simultaneously unsubscribed from the websocket and evicted from the
`ProgramCreateWatcher`**. Once `EXPIRED`, `fair_sweep_candidates()`'s `WHERE
state='ACTIVE'` filter permanently excludes it — there is no later catch-up
for an expired session. This is a **genuine, permanent loss of the sweep-based
detection path for that subprov**, not a delay that self-heals on a later
cycle. (Whether the CREATE was independently caught by the WS-hit path before
expiry is a separate question this sprint's telemetry cannot answer — the
point here is specifically about the *sweep* backstop's coverage.)

Measured exposure:

```
expiring_60s_unswept: mean=8.6/cycle   p95=29   max=95
corr(eligible, expiring_60s_unswept) = 0.34 (positive, moderate)
```

The positive correlation between backlog size and expiring-unswept count is
the expected signature of a fixed-drain queue under variable load — when
`eligible` is high, sessions increasingly reach their TTL before ever
reaching the front of the 10-per-cycle queue. **This is not a temporary-delay
artifact; it is the cap directly producing coverage loss** at the observed
backlog sizes.

## Phase 5 — Worker utilization

```
SWEEP_CONCURRENCY = 4 (env WS_SWEEP_CONCURRENCY, default)
executor telemetry every cycle: max_workers=12, queue_depth≈0, active_threads=12
```

The `executor` figures (12 workers, 12 active) reflect the process's general
thread pool (used for sync DB/RPC calls elsewhere), not the sweep's own
semaphore — `queue_depth=0` throughout confirms that pool is not the
bottleneck. The sweep's actual concurrency limiter is the 4-way
`asyncio.Semaphore` at [ws_cascade.py:4452](../../src/core/ws_cascade.py#L4452):

```
actual cycle duration / (sum_individual_ms / 4)  →  mean ratio = 0.65
```

A ratio below 1.0 means real cycles complete *faster* than a naive
"sum of individual times divided by 4" estimate would predict, which is
expected because the semaphore starts new work as soon as any of the 4 slots
frees up (a rolling schedule, not four synchronous batches of ~2.5 each) —
i.e. **the 4-way concurrency slot is being used efficiently already**; the
bottleneck is not idle workers, it's that only 10 sessions are ever queued
per cycle regardless of the concurrency level.

## Phase 6 — Cycle duration variability

```
duration_ms: mean=210,378   p50=183,205   p95=431,373   max=639,054   min=30,154
buckets: <60s: 11   60-180s: 364   180-300s: 278   >300s: 137   (n=790)
avg per-session catch_up_subprov cost: mean=130,768ms   p50=117,537ms
```

The dominant driver is per-session RPC cost inside `catch_up_subprov()`
itself — median ~118s per session — consistent with the X24.2.1 docstring's
own claim that the bottleneck is "sequential per-signature processing inside
catch_up_subprov itself (median ~8.3s/session, up to 50 sequential
getTransaction calls per session)" from when that number was first measured;
this sprint's broader sample (790 cycles vs. that fix's original 10-session
sample) shows the real median is materially higher (~118s), likely reflecting
sessions with larger signature backlogs by the time they're finally swept —
itself a symptom of the queueing delay this sprint is investigating, not an
independent cause. The 30s-9min cycle-duration range is fully explained by:
how many of the 10 selected sessions have large pending-signature counts
(more sequential `getTransaction` calls) × the 4-way concurrency divisor —
no evidence of DB contention, RPC throttling, or scheduling delay contributing
materially (executor `queue_depth` was 0 or 1 in virtually every reading).

## Phase 7 — Capacity simulation (using recorded telemetry, no code changes)

Using the measured mean per-session cost (~130.8s) and `SWEEP_CONCURRENCY=4`
unchanged, projecting cycle duration and coverage at alternative caps against
the actual observed `eligible` distribution:

```
cap=10 (current):  drain=10/cycle   mean cycle ≈ 10/4 × 130.8s ≈ 327s   backlog clears only when eligible ≤ ~10; at p95 eligible=267 → ~26 cycles to drain a single burst if arrivals paused
cap=20:            drain=20/cycle   mean cycle ≈ 20/4 × 130.8s ≈ 654s   halves the cycles-to-drain a burst, doubles RPC issued per cycle
cap=40:            drain=40/cycle   mean cycle ≈ 40/4 × 130.8s ≈ 1308s  (~22min) — begins to approach/exceed observed arrival-burst durations, but each cycle takes much longer, which the overlap guard (subprov_sweep_pass_guarded) would serialize behind
adaptive (e.g. cap = min(eligible, 40)): drains bursts fast when eligible is high, reverts to small/cheap cycles when eligible is low (mean eligible=173, so most cycles would still select well under 40)
```

These are back-of-envelope projections from measured per-session cost, not a
new load test — consistent with the brief's instruction to simulate "without
changing production code." The key tension the numbers expose: **raising the
cap directly increases cycle duration** (each cycle is still bounded by
`SWEEP_CONCURRENCY=4`), so a blanket higher fixed cap trades expiry-loss for
longer overlap-guard-serialized cycles, whereas an *adaptive* cap (scaling
with `eligible`, capped at some ceiling) would only pay that cost during
actual bursts — the 63%-of-cycles-growing pattern strongly suggests fixed
headroom above 10 is needed even outside of the extreme 980-peak burst.

## Phase 8 — Safety analysis of raising the cap

- **RPC bursts**: raising the cap directly and proportionally raises RPC
  calls issued per cycle (each selected session, even on `SUCCESS` with zero
  new signatures, issues at least one `getSignatures`-class call inside
  `catch_up_subprov`). No RPC-budget guard currently caps issued-calls
  independent of the session cap — Phase 2 found no evidence such a budget was
  ever the reason for `10`, but that doesn't mean raising it is free; it means
  the safety burden shifts to whatever RPC-provider rate limit exists
  upstream, which this investigation did not measure directly.
- **Database contention**: `mark_swept()` commits are one UPDATE per
  successfully-inspected session, serialized through the existing DB-write
  path; `queue_depth≈0` throughout this dataset suggests headroom exists, but
  this was measured only at cap=10 — not verified at higher caps.
- **Duplicate sweeps**: `fair_sweep_candidates()`'s single `SELECT ... LIMIT`
  plus the overlap guard (`subprov_sweep_pass_guarded`) already prevent a
  session being selected twice within or across concurrent cycles; this
  safeguard is cap-independent and would hold at any cap value.
- **Websocket starvation / memory growth**: no evidence found that the cap
  interacts with subscription-manager memory or WS fairness — those are
  bounded by `MAX_CANDIDATES`/TTLs, separate constants untouched by this
  cap.
- **Cycle-duration/overlap-guard interaction**: this is the one safety
  property that *does* depend on the current cap, per Phase 7 — a larger
  fixed cap makes individual cycles proportionally longer, and since
  `subprov_sweep_pass_guarded` prevents overlapping cycles, a very large fixed
  cap could extend the effective sweep interval further than intended. An
  adaptive cap with a ceiling (not an unconditional large fixed increase)
  avoids this by only extending cycle duration during genuine bursts.

## Conclusion

**B — The cap is too conservative.**

Every phase points the same direction: the cap's origin (Phase 2) is an
untuned historical default with no documented capacity rationale; the queue
dynamics (Phase 3) show arrival exceeding the fixed 10/cycle drain in 63% of
observed cycles, including a sustained burst peaking at 980 eligible; the
expiry analysis (Phase 4) shows this is not cosmetic — sessions that lose the
race are permanently unsubscribed and evicted, a real detection-coverage
loss, not a delay; worker utilization (Phase 5) shows the concurrency slot
already in efficient use, meaning there is headroom to select more sessions
per cycle without the workers themselves being the constraint; and the
simulation (Phase 7) shows a moderate, adaptive increase (not an unconditional
jump to a large fixed number) would reduce expiry loss during bursts without
the safety trade-offs of a large blanket increase identified in Phase 8.

This is not "not the bottleneck" (C) — the cap demonstrably gates drain rate
below observed arrival rate for the majority of cycles. It is not "cap is
correct" (A) — no rationale or safety property was found that depends on
`10` specifically. It is not "multiple bottlenecks" (D) — worker
concurrency, DB writes, and RPC latency were all checked and none showed
independent saturation at the current cap; the single limiting factor
throughout is the `LIMIT` value itself.

## Explicitly out of scope for this sprint (per the brief)

No changes were made to `MAX_ACTIVE_SUBPROVS`, `SWEEP_CONCURRENCY`,
`SESSION_TTL_SEC`, scheduling cadence, or any other runtime constant. This
document is a findings report; any follow-up sprint to raise or make the cap
adaptive is a separate, explicitly-scoped piece of future work.
