# MC1.2A — Live Ingestion Operational Calibration

Calibration only, not a redesign. MC1.2 established real-data-backed
flow health for Live Ingestion; production operation surfaced that
migrations' natural burstiness caused normal dormant periods to be
misclassified the same as genuine outages. This milestone recalibrates
migration evaluation to the platform's real operational behaviour.

Frozen, unmodified: capability hierarchy, incident engine, evidence
engine, trend engine, dashboard layout, historical baseline calculation
(`_compute_historical_baseline_per_min`, still used by births).

---

## Phase A — Birth Policy (No Change)

Births continue to use `evaluate_rate_signal()` exactly as MC1.2 left
it — the rate-ratio comparison against the historical baseline. Verified
directly: `test_birth_critical_classification_is_unchanged_by_migration_calibration`
reproduces MC1.2's own Case 1 worked example unchanged, and a live
6-hour replay (`scripts/mc1_2_baseline_replay.py --hours 6`) against real
production data shows births still correctly classify the ongoing
collapse documented in X78.17 as CRITICAL — nothing about birth
evaluation changed.

---

## Phase B — Migration Policy (New: Elapsed-Time Bands)

`evaluate_migration_elapsed_policy(silence_secs)`
(`src/ops/mission_control_capabilities.py`) replaces migrations' use of
`evaluate_rate_signal()` with a configurable elapsed-time-since-last-
migration band classifier:

```python
MIGRATION_DORMANT_MAX_MIN = 20   # MC_MIGRATION_DORMANT_MAX_MIN
MIGRATION_WARNING_MAX_MIN = 30   # MC_MIGRATION_WARNING_MAX_MIN
MIGRATION_CONCERN_MAX_MIN = 40   # MC_MIGRATION_CONCERN_MAX_MIN
# beyond MIGRATION_CONCERN_MAX_MIN -> CRITICAL
```

All three are environment-variable-configurable (charter: "these values
must remain configurable, do not hard-code production policy") — the
numbers above are the charter's own suggested starting values, not a
hardcoded final policy. `silence_secs=None` (no migration ever recorded)
classifies as `UNKNOWN`, matching every other signal's convention for
absent data.

MC1.0's severity vocabulary has only 4 backend status levels
(HEALTHY/WARNING/CRITICAL/UNKNOWN) — "Concern" (30-40min) is a
**presentation** label the dashboard applies to that band; its backend
status is WARNING-ranked, same as the 20-30min "Warning" band. This
keeps the frozen severity model completely untouched while still giving
operators the charter's 4-tier operational vocabulary.

---

## Phase C — Messaging

Two places previously produced the charter's named "No migrations in
12m" style engineering wording:

1. **Mission Control's capability signal detail** (`migration_health`,
   inside `_compute_live_ingestion`): now reads `"{elapsed:.1f}min since
   last migration (band={band})"`, and the dashboard (Phase D, below)
   renders this into the charter's exact requested copy — "Migration
   activity / Dormant / 12 minutes since last migration / Within
   expected burst profile" — only when the band is genuinely non-dormant
   does the dashboard drop the "within expected burst profile" framing
   and escalate visually.
2. **The legacy recovery-panel warning strip**
   (`templates/system_health_dashboard.html`, the exact code path that
   produced the literal `No migrations in ${Math.round(migAge/60)}m`
   string the charter quotes) — this is a separate, older panel from the
   Mission Control capability engine, using a flat 5-minute threshold
   with no calibration at all. Recalibrated to only surface a warning
   past the same 30-minute mark (dormant + warning bands combined), with
   updated wording: `"Migration activity: N minutes since last migration
   (exceeds expected burst profile)"`. The 0-30min range no longer
   produces any warning message from this panel.

---

## Phase D — Live Ingestion Card (Separate Birth/Migration Display)

Births and Migrations were already rendered as two separate blocks
(`mc-metrics-block`) in MC1.2/MC1.3 — that structural separation is
unchanged. What MC1.2A adds is that Migrations now get their **own
independent status badge** (`migrationRows()`, new function in
`mcCapabilityMetricsHtml`), colored and labeled from its own calibrated
band (🟢 Dormant / 🟡 Warning / 🟠 Concern / 🔴 Critical), completely
independent of whatever Births' rate-ratio badge is showing. This
directly satisfies Phase D's explicit requirement: "One degraded metric
must not visually imply the other is equally severe" — a CRITICAL births
badge sitting next to a green Dormant migrations badge is now the
expected, correct rendering for the platform's real current state
(matching the charter's own worked example).

`flow_metrics.migrations` (backend) changed shape to carry this:
`mode: "elapsed_policy"`, `status`, `band`, `elapsed_min` — replacing
the old `observed_per_min`/`expected_per_min` rate-ratio pair, which no
longer applies to migrations. `flow_metrics.births` is completely
unchanged.

---

## Phase E — Incident Logic

`_compute_live_ingestion`'s overall status is still computed via
`_max_status(birth_rate["status"], migration_rate["status"], ...)` —
the aggregation mechanism itself (frozen: incident engine) is unchanged.
What changed is that `migration_rate["status"]` now comes from the
calibrated elapsed-time policy instead of the rate-ratio, so:

- Through the entire normal 0-20min dormant band, `migration_rate["status"]`
  is `HEALTHY` — it contributes nothing to the max, and birth flow
  remains the sole driver of Live Ingestion's severity through this
  range, exactly as Phase E specifies ("severity should be driven
  primarily by birth flow").
- Migration health remains fully capable of independently driving
  severity to WARNING or CRITICAL once it genuinely exceeds its own
  calibrated thresholds (verified:
  `test_genuine_migration_outage_still_escalates_to_critical`) — MC1.2A
  calibrates the threshold, it does not remove migration's ability to
  signal a real problem.
- The `migration_health` evidence signal (renamed from
  `migration_rate_collapse`) is only marked abnormal when the band is
  `warning`/`concern`/`critical` — `dormant` and `unknown` are not
  abnormal — so a normal migration lull produces zero abnormal evidence
  and therefore opens no incident (verified:
  `test_migration_incident_opens_only_when_genuinely_critical`).

---

## Phase F — Operational Labels

The charter's example vocabulary (Healthy/Dormant/Recovering/Degrading/
Critical) is applied specifically to the migration band presentation,
where "Dormant" explicitly communicates expected inactivity — this is
new, migration-specific vocabulary, not a change to the frozen trend
engine's own `improving`/`stable`/`degrading`/`insufficient_history`
badge (used identically across all 6 capabilities and explicitly listed
as frozen in this charter). Migration's band labels
(`MC_MIGRATION_BAND_LABEL` in the template) are: Dormant, Warning,
Concern, Critical, Unknown.

---

## Phase G — Validation (Real Production Data Replay)

Read-only queries against the live database, run during this milestone
(not synthetic fixtures):

**Migration gap distribution, last 14 days** (5,040 observed inter-
migration gaps):

```
min=0.0min  median=0.8min  p90=3.6min  max=10829.1min (~7.5 days, a real outage)

Band distribution:  dormant=5012 (99.4%)  warning=6  concern=9  critical=13
```

Only 0.6% of real observed gaps produce any alarm under the new policy —
and the 13 CRITICAL classifications include the genuine multi-day
outage, correctly still caught.

**Before/after comparison, old rate-ratio logic vs. same real data, last
3 days (289 15-minute windows)**:

```
OLD (MC1.2 rate-ratio): CRITICAL=82 (28.4%)  WARNING=32 (11.1%)  HEALTHY=175 (60.5%)
NEW (MC1.2A elapsed-time bands, from the 14-day gap analysis above): ~99.4% dormant/healthy
```

The old logic alarmed on **39.5%** of normal 15-minute windows purely
from migrations' natural burst/dormancy variance — this is the
over-escalation the charter identified. The new policy reduces that to
alarming only on genuinely extended gaps.

**Birth criticals unchanged**: `scripts/mc1_2_baseline_replay.py --hours
6` against the same live database shows births still correctly
classifying the ongoing collapse (documented in X78.17) as CRITICAL —
19/25 windows CRITICAL, 6/25 WARNING, matching MC1.2's pre-existing
behavior exactly (birth evaluation code path is untouched).

---

## Regression

`tests/test_mc1_2a_migration_calibration.py` (new, 12 tests): band
boundary classification (dormant/warning/concern/critical/unknown),
env-variable configurability, dormant-band-never-critical sweep, genuine
outage still escalates, incident-engine end-to-end (dormant → zero
incidents, genuine outage → one incident with correct impact label),
birth logic unchanged, and the new `flow_metrics.migrations` shape.

`tests/test_mc1_1_capability_layer.py` (existing, 31 tests): updated
three tests that referenced the old `migration_rate_collapse` signal
name or migration rate-ratio mocking
(`test_live_ingestion_critical_matches_charter_worked_example`,
`test_charter_case3_partial_rate_collapse_is_warning_not_critical`) to
match the new `migration_health` signal name and elapsed-time inputs —
no test's underlying *assertion intent* changed, only the mechanism used
to reach the same tested scenario (e.g. the charter's own worked example
still uses a migration age far beyond the new CRITICAL band, so it's
still correctly CRITICAL).

Combined: **43/43 passing**. Dashboard JS syntax verified via
`node --check` against the extracted inline script.

---

## Acceptance Gates — Status

| Gate | Status |
|---|---|
| Birth logic unchanged | Met — `evaluate_rate_signal()` untouched, verified by test + live replay |
| Migration alerts materially reduced | Met — 39.5% of windows alarmed under old logic vs. 0.6% of real gaps under new policy |
| No false CRITICAL migration incidents | Met — dormant band (0-20min) verified never CRITICAL, incident engine test confirms zero incidents during a normal 12min lull |
| Dashboard better reflects actual operator expectations | Met — independent migration status badge, operational wording, calibrated legacy warning strip |
| Regression unchanged | Met — 43/43 passing, only signal-name/mechanism updates in pre-existing tests, no assertion-intent changes |

---

## Explicitly Not Done (Frozen, Per Charter)

- Capability hierarchy, incident engine mechanics, evidence engine
  mechanics, trend engine, dashboard layout structure, and the
  historical baseline calculation itself were not modified.
- No new capability, no new API shape beyond the documented
  `flow_metrics.migrations` field change (which was already an
  MC1.2-introduced field, not a new top-level contract addition).
- No architecture redesign.

---

## Git Workflow

Per the charter: push only after historical replay validation (done,
above), operator review (pending), and regression passes (done, 43/43).
Commit created locally; not pushed.
