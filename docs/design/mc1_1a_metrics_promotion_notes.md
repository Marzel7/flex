# MC1.1A — Operational Metrics Promotion

UI-only follow-on to MC1.1. Zero backend diff — `git diff --stat`
confirms only `templates/system_health_dashboard.html` changed; the
capability engine, incident engine, evidence engine, and rate engine
(`src/ops/mission_control_capabilities.py`) and the API route
(`src/core/main.py`) are byte-for-byte unchanged from the MC1.1 commit.

## What changed

Each capability tile in the capability grid now renders in this order
(previously: head → evidence/status → diagnostics):

1. **Head** (name + status dot) — unchanged.
2. **Operational metrics** (NEW, promoted to the top) — per capability:
   - `live_ingestion`: Births block (rate-signal detail string, last
     birth age, birth queue depth) and Migrations block (same shape).
   - `creator_funding`: queue depth, oldest eligible age, worker
     heartbeat age, worker status.
   - `operational_intelligence`: creator queue depth, resolution
     efficiency %, last pipeline activity age.
   - `watchtower`: subscription count, heartbeat freshness, latest
     launch age.
   - `infrastructure`: DB write p99, serializer queue depth.
   - `price_tracking`: active tokens, last peak update age, last
     snapshot age.
3. **Status row** (NEW styling — a colored badge instead of plain text,
   still showing the exact same `cap.status` value) + evidence count —
   now the click target for expanding diagnostics (was the whole tile
   before).
4. **Degraded-by annotation** — unchanged content, same position
   relative to status.
5. **Diagnostics** (contributing signals list, expandable) — unchanged
   content, now visually last.

Incident cards gained a "Jump to {capability}" link (Phase E) that
smooth-scrolls to and briefly highlights the corresponding capability
tile via its existing `id="mc-cap-{name}"` anchor (that id already
existed in MC1.1; MC1.1A only added the link and a CSS highlight
transition, no new anchor/id scheme).

## Why the Live Ingestion metrics show a "Detail" string, not a raw rate number

The charter's worked example shows `Births: 0/min, Expected: 18.2/min`.
MC1.1's rate engine (`get_expected_rate_per_min()`) is deliberately
mechanism-only per its own charter ("Do NOT hardcode production rates.
Implement only the mechanism") and currently returns `None` — there is
no historical baseline wired in yet, so the backend has never computed
an actual `observed_rate_per_min`/`expected_rate_per_min` pair to
display. Rather than inventing a rate number the API doesn't produce
(which MC1.1A's own "no new calculations" gate forbids), the Births/
Migrations metric blocks surface the exact detail string the frozen
`birth_rate_collapse`/`migration_rate_collapse` signals already compute
today — which, in the current mechanism-only state, is the silence-based
fallback detail (e.g. `"no baseline available; silence=153s (fallback
threshold 5400s)"`), plus the separately-available `last_birth_age_secs`/
`birth_queue_pending` fields for the "Last birth"/"Birth queue" rows.
Once a future milestone wires `get_expected_rate_per_min()` to a real
baseline (out of scope for both MC1.1 and MC1.1A), the same signal detail
string will automatically start reading `"observed X/min vs expected
Y/min baseline"` and this card requires no further change to display it
correctly — the promotion/layout work done here is forward-compatible
with that future data without needing to be revisited.

## Validation performed

- `git diff --stat` confirms zero changes to
  `src/ops/mission_control_capabilities.py` and `src/core/main.py`.
- Full MC1.1 backend test suite (`tests/test_mc1_1_capability_layer.py`,
  13 tests) re-run unchanged and still passing — proves the capability/
  incident/evidence/severity computation is untouched.
- `/system-health` route renders successfully via Flask test client
  (200 status), all new template elements present in the output.
- Embedded JavaScript passes `node --check` syntax validation.
- No new `fetch()` call added — `mcCapabilityMetricsHtml()` reads only
  from `fullHealth.subsystems` and `cap.signals`, both already present
  in the single `/api/health/full` response the dashboard was already
  fetching before MC1.1A.
