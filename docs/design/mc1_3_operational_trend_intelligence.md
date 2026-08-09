# MC1.3 — Operational Trend Intelligence

Adds trajectory ("is it getting better or worse") on top of MC1.2's
current-state health model. Frozen and unmodified: capability hierarchy,
capability engine's status/evidence/signal computation, incident engine,
evidence model, severity model, historical baseline engine (`get_expected_rate_per_min`),
API compatibility (only new, additive `trend`/`flow_metrics` fields).

---

## The trend-data gap (design decision, made explicitly before writing code)

Investigated before implementing: Live Ingestion has a genuine, queryable
event history (`token_analysis.analyzed_at`/`migrated_at`) — the same
data MC1.2's baseline engine already reads. Its trend can be computed
from real historical data with zero new persistence.

The other 5 capabilities (Creator Funding, Operational Intelligence,
WATCHTOWER, Infrastructure, Price Tracking) have **no equivalent history
table anywhere** — `wt_worker_heartbeat`, `funding_queue_pending`,
`database.p99_wait_ms`, etc. are all single current-value rows with
nothing recording what they were 15 minutes ago. Computing a real trend
for them without adding new persistence (out of scope — "No new backend
systems") requires either (a) new database tables (rejected — violates
the frozen stateless architecture), (b) an in-memory rolling sample
buffer that warms up as the process runs (chosen), or (c) fabricating a
trend from insufficient data (rejected outright — this would actively
mislead operators).

**Decision, confirmed with the user before implementation**: in-memory
rolling buffer, same convention as MC1.1's incident-start cache and
MC1.2's baseline cache — per-process, resets on restart, explicitly
reports `insufficient_history` (with a `samples_collected`/`samples_required`
count) rather than approximating a trend from too few samples. This is
an honest, bounded limitation, not a hidden one — the dashboard renders
"Collecting data…" during warm-up rather than a fabricated direction.

---

## Phase A — Trend Windows

Fixed windows: 5m / 15m / 60m / 24h (`TREND_WINDOWS_MIN`), used
identically by both the real-data engine (Live Ingestion) and the
buffer-based engine (the other 5 capabilities). No additional data
collection beyond what MC1.2 already established for Live Ingestion, and
the buffer for everything else (which piggybacks on the SAME
`compute_capabilities()` call the dashboard already makes every poll —
no new poll cadence, no new endpoint).

---

## Phase B — Live Ingestion Trend (real historical data)

`compute_live_ingestion_trend(event_type)`: computes rate over each of
the 4 fixed windows (`_rate_over_window`, a generalization of MC1.2's
`count_recent_events` to an arbitrary historical window instead of only
"now"), plus a direct current-5m-vs-prior-5m comparison to classify
`direction` as `improving`/`stable`/`degrading`. A `TREND_DIRECTION_EPSILON`
(default 5%) noise floor prevents a trivial fluctuation from being
reported as a real reversal.

This is genuinely measured, not predicted: `direction` is the sign of an
actual comparison between two real historical windows, nothing
extrapolated forward. `pct_of_baseline` is exposed directly (same
`get_expected_rate_per_min` MC1.2 established, frozen and unmodified).

Verified live against the real database while writing this: at the time
of testing, the ongoing outage documented in MC1.2's own report showed
`births: observed=2.4/min, prior_5m=3.2/min → degrading, 12.5% of
baseline` — real, current, matching what MC1.2's replay independently
found.

---

## Phase C — Capability Trend (in-memory rolling buffer)

`_record_capability_samples()`: appends one `(mono_ts, wall_ts,
status_rank)` sample per capability on every `compute_capabilities()`
call — capped at `TREND_BUFFER_MAX_SAMPLES` (default 2000, oldest
dropped first). This is `compute_capabilities()`'s one new, explicitly
documented side effect (its docstring was updated to say so directly —
the function is no longer purely side-effect-free, though its
status/evidence/signal *outputs* remain fully deterministic given the
same input).

`compute_capability_trend(name)`: for each fixed window, finds the
oldest buffered sample still within that window and compares its status
rank against the current sample. Reports `insufficient_history` (not a
guessed `stable`) whenever fewer than `TREND_BUFFER_MIN_SAMPLES` samples
exist, or when the buffer's total span doesn't yet reach even the
shortest (5m) window.

**A real bug was found and fixed during implementation**: the first
draft defaulted `overall_direction` to `"stable"` whenever no window had
enough span — silently claiming a measured non-change that was never
actually measured. Caught by
`test_capability_trend_reports_insufficient_history_when_no_window_spanned`,
which failed on the first draft. Fixed by defaulting to
`insufficient_history` instead, only overwritten once a window genuinely
has comparison data.

`duration_in_current_status_secs`: walks backward through the buffer
while the status rank stays unchanged, giving a real measured "how long
has it been in this state" figure from actual recorded samples.

Live Ingestion's own `trend` key (set inside `_compute_live_ingestion`,
Phase B's real-data version) is never overwritten by the generic
buffer-based trend — `compute_capabilities()` only attaches the
buffer-based trend to capabilities that don't already have one.

---

## Phase D — Incident Timeline

Incident cards (`mcRenderIncidents` in the dashboard template) now show
`Started` (formatted from the existing `first_detected_at`, unchanged
field) and `Trend` (reusing the exact same `trend` object already
rendered on the corresponding capability card — not recomputed, not a
second source of truth). For Live Ingestion's incidents, the more
significant of births/migrations trend (whichever isn't `stable`) is
shown, since a single incident can stem from either or both signals.

---

## Phase E — Visualisation (sparklines)

`mcSparklineSvg()`: a small (~90×22px) inline SVG polyline, no charting
library, built from Live Ingestion's 4 real rate data points
(24h→60m→15m→5m, oldest to newest). Genuine measured points, not
interpolated between — a straight line between 4 real numbers, nothing
fabricated in between them. Color reflects direction (red if the most
recent point is lower than the first, green if higher). Deliberately
scoped to Live Ingestion only — the other 5 capabilities' buffer only
tracks a status *rank* over time (0-3), not a meaningful numeric series
worth plotting as a line chart; their trend is shown as a badge
(direction + duration) instead, per Phase E's own "no large charts, only
immediate operational context" instruction.

---

## Phase F — Dashboard Card Order

Every capability card's layout is now: operational metrics → **trend
badge (+ sparkline for Live Ingestion)** → status/evidence row →
diagnostics — trend inserted directly after the metrics block MC1.1A/MC1.2
already promoted to the top, before status/evidence, matching the
charter's exact required order. No card structure, incident grouping, or
layout logic outside this insertion point was touched.

---

## Phase G — Replay Validation

`scripts/mc1_2_baseline_replay.py` (MC1.2's script, extended rather than
duplicated) now also computes and prints trend direction at each
replayed window, comparing against the immediately-prior window using
the exact same classification MC1.3's `compute_live_ingestion_trend`
uses internally.

Run against real data (`--hours 3`): trend direction tracked every
observed status transition with no lag, and correctly reported
`insufficient_history` for the very first window in the replay (no prior
window to compare against). One honest finding, not hidden: during a
partial-recovery period, births/migrations legitimately oscillate
between `improving`/`degrading` multiple times within a few hours at the
15-minute bucket granularity — this is real signal noise inherent to
short-window rate comparison during a bursty recovery, not an algorithm
defect, and matches the same noise characteristic MC1.2 already
documented for migrations' low baseline rate. Anyone tuning
`TREND_DIRECTION_EPSILON` or the trend window granularity in the future
should be aware of this.

---

## Regression

31 tests in `tests/test_mc1_1_capability_layer.py` (21 from MC1.1/MC1.2 +
10 new MC1.3), all passing:

- New autouse fixture `_reset_trend_buffer` clears the module-level
  sample buffer between tests (same convention as the existing incident-
  cache fixture).
- `test_capability_computation_is_pure_and_deterministic` updated: it now
  asserts determinism of `status`/`degraded_by`/`evidence`/`signals`
  specifically (still fully deterministic) rather than the whole
  capability dict, since `trend` legitimately grows with each call by
  design — that IS the entire point of the buffer, and asserting whole-dict
  equality would be asserting the feature doesn't work.
- New tests cover: insufficient-history reporting (both the min-samples
  case and the min-window-span case that caught the real bug above),
  measured degradation/recovery detection across a real window
  comparison, `compute_capabilities()` attaching `trend` to every
  capability without live_ingestion's own trend being overwritten, the
  epsilon noise floor, buffer capping, one-sample-per-call recording, and
  `/api/health/full`'s additive `trend` field.

`/api/health/full`'s response shape confirmed unchanged except the
purely additive `trend` key inside every `capabilities.*` entry
(`flow_metrics` for Live Ingestion was already added by MC1.2).
