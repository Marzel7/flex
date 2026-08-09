# MC1.2 — Live Ingestion Flow Health

Makes observed event flow the primary, authoritative signal for the
Live Ingestion capability, replacing the previous mechanism-only rate
engine stub. Backend change (unlike MC1.1A-E, which were UI-only) — this
milestone's own scope explicitly requires it (Phase A: implement
`get_expected_rate_per_min()` using production history).

Frozen and unmodified per the charter: capability hierarchy, incident
engine, evidence engine, dashboard layout, capability cards, incident
grouping. Only `_compute_live_ingestion()`'s internal signal computation
and the Live Ingestion card's metrics block changed.

---

## Phase A — Historical Baseline

### Data investigation (before writing any algorithm)

Queried the real database directly before choosing a baseline approach.
Found `token_analysis.analyzed_at` (births, filtered
`source_platform='pumpfun'`) spans ~180 days, 1.6M rows;
`migrated_at` (migrations) spans 31,540 rows. A naive "median rate over
every window in the lookback period, including zero-event windows"
approach was tested first and found unsafe on this platform's real data:

- The full 180-day history contains a single contiguous **594-hour
  (24.75-day) gap** with zero recorded births.
- Even a recent, focused **7-day** lookback had **101/557 (18%)**
  completely empty 15-minute windows.
- A **1-day** lookback was measured to be unstable (2.27/min) relative
  to 7-day and 14-day lookbacks, which converge closely (19.33/min vs.
  19.53/min) — a very short window can be dominated entirely by
  whatever is happening right now, which is exactly the instability a
  baseline exists to avoid.

**Chosen approach**: median rate computed over only the **nonzero**
windows within the lookback period (default 7 days, configurable via
`MC_BASELINE_LOOKBACK_DAYS`). This is a purely statistical exclusion —
it requires no persisted incident history (staying consistent with
MC1.0/MC1.1's stateless architecture) and is inherently robust to a
minority of the lookback period being an outage (real, or in this dev
environment's case, simply the ingestion process not running), directly
satisfying Phase A's "exclude known outage windows" requirement without
needing a ground-truth list of exactly which windows were outages.

A minimum-sample guard (`MC_BASELINE_MIN_NONZERO_BUCKETS`, default 8)
ensures `get_expected_rate_per_min()` returns `None` — correctly
triggering the silence-based fallback per MC1.0 Section 5's contract —
rather than trusting a baseline computed from too little real data.

### Implementation

`_compute_historical_baseline_per_min(event_type)`: one indexed,
read-only SQL query (`GROUP BY` bucket, using the existing
`idx_ta_analyzed_at`/`idx_ta_migrated_at` indexes) against
`token_analysis`, bucketed server-side in SQLite rather than pulling
individual rows into Python. Results cached in-memory
(`_BASELINE_CACHE`) with a 5-minute TTL (`MC_BASELINE_CACHE_TTL_SEC`) so
this doesn't add a query to every single dashboard poll — same
in-memory, resets-on-restart convention already used for MC1.1's
incident-timeline tracking.

`get_expected_rate_per_min()` — previously a stub always returning
`None` — now delegates to this real baseline (via the cache).

`count_recent_events(event_type, window_min)` — new companion function
computing the observed count in the rolling window (the numerator
`evaluate_rate_signal()` always needed but never had a real source for;
it was hardcoded to `0`). Same table/columns/indexes as the baseline
query.

No fixed production rate constant is shipped — `RATE_CRITICAL_RATIO`
(0.1) and `RATE_WARNING_RATIO` (0.5) remain the only literal defaults,
and those are dimensionless ratios, not absolute rates, matching MC1.0
Amendment 2's explicit constraint.

---

## Phase B/C — Flow Health & Health Rules

`_compute_live_ingestion()` now calls `count_recent_events()` for both
event types (previously hardcoded to `0`) and passes the real counts
into `evaluate_rate_signal()`, which was already correctly structured
(from MC1.1) to evaluate rate as primary and silence as fallback — MC1.2
completes that mechanism rather than redesigning it.

Connection status (`pumpportal`/`pumpswap`) and listener freshness
remain their own signal entries (contributing evidence, individually
visible) but their contribution to the aggregate `status` is combined
via `_max_status()`, which can only **raise** severity, never lower it —
structurally guaranteeing "a connected socket never overrides a
collapsed event rate" (Phase C's explicit rule): a fully healthy
connection contributes `HEALTHY` to the max, which can never outrank an
already-`CRITICAL` rate signal.

The frozen `ingestion` subsystem (unmodified, out of scope) only ever
produces `UNKNOWN`/`RETRYING`/`CONNECTED`/`STALE` for `pumpportal`/
`pumpswap` — there is no `DISCONNECTED` value in the real code. `STALE`
and `UNKNOWN` are treated as the closest real equivalents to the
charter's generic "DISCONNECTED" example (Phase D Case 2), independently
capable of raising severity to `CRITICAL` even if the rate signal
momentarily looks fine.

---

## Phase D — Worked Examples (Verified, Not Assumed)

All three of the charter's worked cases were reproduced as regression
tests directly against `_compute_live_ingestion()`:

- **Case 1** (`test_charter_case1_...`): PumpPortal `CONNECTED`, birth
  rate 0 vs. expected 18/min → `CRITICAL`. Connection signal itself
  reads `abnormal: false` (supporting evidence, not health) while the
  aggregate status is `CRITICAL` from the rate signal alone.
- **Case 2** (`test_charter_case2_...`): connection `STALE` (this
  codebase's real equivalent of "disconnected"), rate signal
  independently healthy → still `CRITICAL`, immediately, from the
  connection signal alone.
- **Case 3** (`test_charter_case3_...`): birth rate at 40% of expected
  (4.0/min observed vs. 10.0/min expected) → `WARNING`, not `CRITICAL`,
  proving the rate engine catches a slowdown well before total silence.

---

## Phase E/F — Operational Metrics & Messaging

`_compute_live_ingestion()`'s result now includes a `flow_metrics` field
(births/migrations, each with `observed_per_min`, `expected_per_min`,
`mode`, and the underlying age) — the same numbers already computed by
`evaluate_rate_signal()`, surfaced at the capability level so the
dashboard doesn't need to re-derive or re-parse the signal detail
string.

`templates/system_health_dashboard.html`'s `mcCapabilityMetricsHtml()`
(the `live_ingestion` branch only — no other capability's metrics block,
no layout, no card structure, no incident logic touched) now reads
`cap.flow_metrics` directly and renders Observed / Expected / "N% below
baseline" rows as the headline metrics, falling back to "baseline
unavailable" + last-event-age only if `flow_metrics` is absent or still
in silence-fallback mode (e.g. immediately after a fresh deployment
before 8 nonzero historical windows exist).

---

## Phase G — Replay Validation

`scripts/mc1_2_baseline_replay.py` (new, read-only, no writes/ingestion
interaction): walks forward through real historical timestamps,
computing the trailing baseline **at each point in time** (never
looking ahead — the same walk-forward constraint the live system
operates under) and classifying each 15-minute window exactly as
`evaluate_rate_signal()` would.

Run against the real database (`--hours 6`, default 7-day lookback):
the replay caught a **real, sustained ingestion outage** already present
in this dev environment's data — a transition from a healthy ~20-33/min
period into sustained near-zero observed rate, correctly classified
`CRITICAL` within a single 15-minute window of the drop, remaining
`CRITICAL`/`WARNING` (oscillating with brief partial-recovery blips)
for the rest of the replayed window, never falsely reading `HEALTHY`
during the outage. This is real validation against real data, not a
synthetic fixture — Phase G's "confirm incidents occur at the expected
time, no false positives" requirement is demonstrated directly.

Migration classification showed more oscillation than births in the
same replay, which is an honest, expected characteristic (not a defect)
of the algorithm given migrations' much lower baseline rate (0.6/min vs.
19.33/min for births) — a single migration event within one 15-minute
window can swing the observed rate substantially at that low a
baseline, worth noting for anyone tuning `RATE_WINDOW_MIN` or the ratio
thresholds in the future.

---

## Regression

21 tests in `tests/test_mc1_1_capability_layer.py` (13 original + 8 new
MC1.2-specific), all passing:

- New autouse fixture `_isolate_from_live_baseline` stubs
  `get_expected_rate_per_min`/`count_recent_events` back to their
  pre-MC1.2 `None`/`0` values by default, restoring the pre-existing
  tests' documented isolation from the live database (MC1.2 made these
  functions genuinely query the real DB by default, which would have
  silently broken that isolation without this fixture — caught by
  running the existing suite and finding 4 failures, all correctly
  traced to this cause, not to any actual regression in behavior).
- New tests reproduce the charter's Cases 1-3 directly, verify the
  median-of-nonzero-windows baseline algorithm, verify the minimum-
  sample guard, verify the in-memory cache, and include one deliberate
  integration test that does NOT use the isolation stub — calling the
  real baseline query against the real database directly, to prove the
  SQL itself runs correctly end-to-end, not just against mocks.

`/api/health/full`'s response shape confirmed unchanged except for the
purely additive `flow_metrics` key inside `capabilities.live_ingestion`
— all 7 existing subsystem blocks, `capabilities`, and `incidents`
keys/shapes otherwise untouched.
