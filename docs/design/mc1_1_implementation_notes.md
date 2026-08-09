# MC1.1 — Stateless Capability Layer Implementation Notes

Implements the frozen MC1.0 design
(`docs/design/mc1_0_capability_severity_model.md`) exactly, per MC1.1's
charter: stateless option only, no persistence, no backend/ingestion
changes, purely additive to `/api/health/full`.

## What was built

- **`src/ops/mission_control_capabilities.py`** — the capability engine
  (Phase A), evidence engine (Phase B), incident engine (Phase C), rate
  engine (Phase D), and platform status derivation (Phase E). Pure
  functions of the existing 7 `/api/health/full` subsystem dicts — no new
  measurement, no new DB query anywhere in this module.
- **`src/core/main.py`** (`api_health_full()`) — wired the new module in
  as an additive layer (Phase F). The legacy `top` status derivation is
  left completely unchanged and used as a fail-soft fallback if the
  capability layer raises for any reason; on success, `top` is
  overwritten with `compute_platform_status()`'s result, satisfying
  MC1.0 Phase E ("platform severity now becomes maximum capability
  severity") while never risking the endpoint's availability.
- **`templates/system_health_dashboard.html`** — incident cards +
  capability grid (Phase G), inserted between the existing Phase-9
  banner/summary-strip and the existing Phase-2 subsystem groups. Driven
  entirely by `fullHealth.capabilities`/`fullHealth.incidents`, which is
  data the dashboard was already fetching (`/api/health/full` at line
  ~1588) — no new network request added. Renders nothing if those keys
  are absent, so it can never break on an unexpected response shape.
- **`tests/test_mc1_1_capability_layer.py`** — 13 tests covering
  determinism, the charter's exact worked example, propagation
  floor-vs-suppress semantics, the "propagated-only WARNING doesn't open
  its own incident" rule, independent-concurrent-incidents-stay-separate,
  PEAK-ONLY non-alarming behavior, incident timeline persistence-across-polls
  (in-memory), evidence-count/signal-list consistency, backward
  compatibility of the API response shape, and the rate-engine's
  primary/fallback evaluation order.

## A bug found and fixed during implementation

The first draft of `_apply_propagation()` only set a downstream
capability's `degraded_by` field when the upstream's severity was
*strictly greater than* the downstream's own independently-computed
severity. This meant a capability that was independently CRITICAL for
its own reasons (e.g. `creator_funding` with its own stopped worker)
while ALSO sitting downstream of a CRITICAL `live_ingestion` never got
its `degraded_by` annotation set — because CRITICAL == CRITICAL, no rank
increase occurred. MC1.0 Section 13 (operator playbook, step 4)
explicitly expects an operator to see the propagation annotation
*alongside* an independent problem so they know both are in play. Fixed
by decoupling the annotation (set whenever upstream is abnormal, always)
from the status floor (only raises the downstream's displayed severity
when the upstream is more severe than its own). Caught by
`test_downstream_propagation_floors_but_does_not_suppress_independent_severity`,
which failed on the first draft and passes after the fix — this is
exactly the kind of thing a "no revisiting the design" implementation
still needs a test to catch, since it's a coding bug in the
implementation, not a disagreement with the frozen design.

## Live verification against production data

Ran the endpoint (in-process test client, read-only, no deploy) against
the actual running database mid-implementation. It correctly identified
real, concurrent, independent conditions already known from this
session's own investigations: `live_ingestion` CRITICAL (birth/migration
rate collapse, PumpPortal retrying), `creator_funding` WARNING with its
own 3 independent abnormal signals (worker not running, heartbeat stale,
oldest eligible work stalled) *and* correctly annotated
`degraded_by: live_ingestion`, `operational_intelligence` WARNING with 2
own abnormal signals, and `watchtower` WARNING purely via propagation
(zero own signals) — correctly producing **3** incident cards (one per
capability with independent evidence), not 6, and specifically NOT
opening a 4th card for `watchtower`'s purely-propagated state, matching
MC1.0 Section 13's rule exactly.

## Phase D note: rate engine is mechanism-only, as instructed

`get_expected_rate_per_min()` currently returns `None` unconditionally —
per MC1.1's explicit "Do NOT hardcode production rates. Implement only
the mechanism" instruction. This means every rate evaluation currently
falls through to the silence-based fallback (using the real,
already-computed `last_birth_age_secs`/`last_migration_age_secs`), which
is the exact behavior MC1.0 Section 5 specifies for "insufficient
history." Live Ingestion's severity in the current deployment is
therefore driven by the fallback thresholds (5400s/90min for both births
and migrations, matching the charter's own worked example), not yet by a
real historical baseline — wiring `get_expected_rate_per_min()` to an
actual rolling-baseline query is explicitly out of scope for MC1.1 and
deferred to a future milestone, consistent with MC1.0's Amendment 2
resolution.

## Regression

- 13/13 new tests pass (`tests/test_mc1_1_capability_layer.py`).
- `/api/health/full`'s existing response shape verified unchanged: all 7
  subsystem blocks present with identical keys; `capabilities` and
  `incidents` are the only new top-level additions.
- Diff scope confirmed via `git diff --stat`: only `src/core/main.py`'s
  `api_health_full()` route function, plus new files. No ingestion
  worker, listener, `creator_funding_worker.py`, or any X78-series file
  touched.
- Full X78 regression suite (X78.14, X78.16) re-run alongside the new
  MC1.1 tests: 26/26 passing, confirming no interaction/regression.
- Template renders successfully via Flask test client (200 status,
  all new elements present in output HTML); embedded JavaScript passes
  `node --check` syntax validation.

## What was intentionally NOT built (per the frozen design + charter)

- No new database table — stateless option only, per MC1.0 Section 11 /
  MC1.1's explicit instruction.
- No new background worker or process.
- No change to how any of the 7 existing subsystem blocks compute their
  own fields.
- No real historical rate baseline (`get_expected_rate_per_min` is a
  stub returning `None`) — mechanism only, per Phase D's explicit scope.
- The existing Phase-9 banner (`mc-banner`/`mc-summary-strip`) and
  Phase-2 subsystem groups are unchanged and still fully functional —
  the new capability layer sits above them, does not replace or remove
  them, per Phase G's "reorganize beneath, don't remove" instruction.
