# X26.5.1 — Attribution Health Window Integrity

Status: Implemented, tested, live-verified. No classification, materialisation,
grouping semantics, database schema, or historical data changed — this is a
metric-integrity and UI-honesty fix only, exactly as scoped.

The React-error note in the sprint brief was ignored per its own instruction
(this codebase has no React runtime, confirmed in an earlier sprint).

---

## Phase 1 — Trace confirmed against current working tree

Re-verified line-for-line against the current `templates/discovery.html`,
`src/core/operation_dashboard_routes.py`, and `src/ops/discovery_triage.py`
before changing anything. X26.5's findings still held exactly:

| Widget | Endpoint | Table | Time window | Row cap |
|---|---|---|---|---|
| Landing tile | `/api/ops-v2/attribution-outcomes?limit=500` | `wt_attribution_outcomes` | 24h, applied client-side via `summariseOutcomes()` | 500 rows shared across all outcome types |
| Per-type drill-down | `/api/ops-v2/attribution-outcomes?limit=500&outcome_type=X` | `wt_attribution_outcomes` | None | 500 rows, per type |
| Triage summary | `/api/ops-v2/discovery-triage/summary` → `build_triage_summary()` | `wt_attribution_outcomes` | None | None |
| Legacy "Lineage Gaps" | `/api/ops/walkback-queue` → `queue_stats()` | `wt_walkback_queue.intelligence_outcome` | None (for the displayed figure) | None |

Confirmed live: `KNOWN_RELAY_REACHED` = 12 in the last 24h vs. 71 all-time
(Axiom alone = 46), reproducing the exact reported symptom.

## Phase 2 — Backend aggregate endpoint

Added `GET /api/ops-v2/attribution-outcomes/summary`
(`src/core/operation_dashboard_routes.py`, immediately after the existing
list endpoint):

```python
@ops_dashboard_bp.route("/api/ops-v2/attribution-outcomes/summary")
def api_attribution_outcomes_summary():
    window = (request.args.get("window") or "24h").strip().lower()
    completed_after_param = request.args.get("completed_after")
    if completed_after_param is not None:
        completed_after = int(completed_after_param); window = "custom"
    elif window == "all":
        completed_after = None
    else:
        window = "24h"; completed_after = int(time.time()) - 86400

    if completed_after is None:
        rows = conn.execute(
            "SELECT outcome_type, COUNT(*) AS n FROM wt_attribution_outcomes GROUP BY outcome_type"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT outcome_type, COUNT(*) AS n FROM wt_attribution_outcomes "
            "WHERE completed_at >= ? GROUP BY outcome_type", (completed_after,)
        ).fetchall()
    counts = {row["outcome_type"]: row["n"] for row in rows}
    return jsonify({"ok": True, "window": window, "completed_after": completed_after, "counts": counts})
```

Response shape matches the brief's preferred shape exactly:

```json
{"ok": true, "window": "24h", "completed_after": 1784131187,
 "counts": {"INSUFFICIENT_EVIDENCE": 280, "LINEAGE_GAP": 93, ...}}
```

- No row fetch, no row cap — `COUNT(*)/GROUP BY` runs entirely in SQL, so it
  is structurally incapable of the shared-cap-then-client-filter bug
  regardless of how much volume exists in the window.
- `window=all` supported for the drill-down's own aggregate needs (Phase 4).
- `completed_after` (raw unix seconds) supported for custom windows, though
  not currently used by any caller.
- Added as a **new route**, not a change to the existing list endpoint's
  shape — per Phase 7's compatibility requirement.

## Phase 3 — Landing panel

`templates/discovery.html`:

- `summariseOutcomes()` no longer takes `rows`/`cutoff` and filters
  client-side; it now just reshapes the aggregate endpoint's `counts` object
  into the same `[{outcome_type, count}]` array the rest of the rendering
  code already expected — no other call site needed to change.
- The landing `Promise.all([...])` fetch replaced
  `/api/ops-v2/attribution-outcomes?limit=500` with
  `/api/ops-v2/attribution-outcomes/summary?window=24h`.
- `healthPanel()` header changed from "Attribution Health" to
  **"Attribution Health · Last 24h"**, sub-copy now states "Terminal
  outcomes completed in the last 24 hours... Click a row for the all-time
  count," and every row's count now reads `N · 24h` instead of a bare
  number.
- This also fixed the same latent bug for the "Canonical operators" and
  "Unknown infrastructure" today-metrics on the same landing page, since
  both derived from the same `summary` object.

## Phase 4 — Drill-down

`filteredCases()` now fetches both the row list (unchanged: `limit=500`,
type-filtered, no time bound — deliberately kept all-time) **and** the new
`window=all` aggregate in parallel, then:
- Header changed to **"Attribution Health · All time"**.
- If the true all-time count (from the aggregate) exceeds the number of
  rows actually fetched (the 500-row cap), the sub-label switches to
  `"{fetched} of {true_total} all-time terminal outcomes shown (fetch limit
  reached)"` instead of silently presenting a partial list as complete.
  Currently this branch does not fire for any outcome type routed through
  this code path (max is `KNOWN_CEX_REACHED` at 319, under the 500 cap),
  but it is now structurally impossible for growth to silently under-report
  without disclosure.
- Grouped relay/CEX/bridge cards (`renderRelayGrouped`) are unchanged —
  per-terminal-entity counts still sum exactly to the fetched row count,
  verified live (Axiom 46 + 7 other wallets = 71, matching the all-time
  aggregate for `KNOWN_RELAY_REACHED` exactly).

## Phase 5 — Triage scope

`triageSummaryCard()` relabelled from "Insufficient Evidence · Level 1" /
"N terminal outcomes" to **"Insufficient Evidence · All time"** / "N
terminal outcomes (all time)". Verified `build_triage_summary()` has no
status/resolved/active filter of any kind (`_load_terminal_rows()` is an
unfiltered `SELECT ... WHERE outcome_type IN (...)`, no LIMIT) — so this is
genuinely raw historical volume, not a filtered actionable backlog, and is
now labelled as such rather than left to look comparable to the 24h
landing tile.

## Phase 6 — Legacy Lineage Gap label

`templates/watchtower_operational_intelligence.html` (a separate dashboard
page, unrelated to Discovery) sourced a "Lineage Gaps" figure from
`wt_walkback_queue.intelligence_outcome` — a different table from the
canonical `wt_attribution_outcomes.outcome_type` Discovery uses, with a
genuinely different count (1,001 vs. 620 all-time for the identical English
label, confirmed live in X26.5). Relabelled both visible occurrences:
- Diagnostics inline stat: `"gaps"` → `"walkback queue gaps"` with an
  explanatory `title=` tooltip.
- Outcome-code display map: `LINEAGE_GAP: {label:'Lineage Gaps', ...}` →
  `{label:'Walkback Queue: Lineage Gap Rows', ...}`.

No underlying `wt_walkback_queue`/`queue_stats()` logic was touched, per
the brief's explicit constraint.

## Phase 7 — API/compatibility audit

Searched every consumer of `/api/ops-v2/attribution-outcomes` before
editing anything:
- `templates/discovery.html:770` (drill-down list fetch) — **preserved
  exactly**, same query string, same response shape.
- `templates/discovery.html:784` (landing fetch) — the only call site
  changed, switched to the new `/summary` route.
- `src/ops/watchtower_funnel.py:189` — only builds an href string
  (`f"/api/ops-v2/attribution-outcomes?outcome_type={key}"`), never calls
  the endpoint itself; unaffected.
- `tests/test_ops_x20_6_discovery_prioritisation.py` — asserted the old
  bare `?limit=500` URL string; updated to assert the new
  `/summary?window=24h` string instead (the list endpoint's own
  `?limit=500&outcome_type=` assertion, used by the drill-down, is
  untouched and still passes).
- The existing list endpoint (`/api/ops-v2/attribution-outcomes`) was not
  modified at all — same route, same SQL, same response fields
  (`mint, outcome_type, stop_reason, terminal_entity, terminal_entity_type,
  confidence, operator_id, should_seed_emerging_operator, should_retry,
  completed_at, evidence`). Per the brief's preference, a dedicated new
  aggregate route was added instead of changing this one.

## Phase 8 — Tests

New file `tests/test_x26_5_1_attribution_health_window_integrity.py` — 17
tests:
- `test_24h_aggregate_applies_time_filter_in_sql` — proves the SQL
  `WHERE completed_at >= ?` clause actually excludes an out-of-window row.
- `test_exact_counts_when_more_than_500_rows_exist_inside_window` — the
  required regression fixture: 650 rows (600 `INSUFFICIENT_EVIDENCE` + 50
  `KNOWN_RELAY_REACHED`) all within the last 24h. Asserts the new
  `COUNT(*)/GROUP BY` returns the true 600/50 split, **and** separately
  replays the exact old buggy client-side pattern against the same fixture
  to prove it really would have undercounted (old pattern caps at 500 total
  rows across both types combined) — so this test would have failed
  against the pre-fix code, not just passed vacuously.
- `test_counts_grouped_correctly_by_outcome_type`,
  `test_rows_older_than_24h_excluded`,
  `test_all_time_window_returns_full_uncapped_total`,
  `test_grouped_terminal_counts_sum_to_alltime_total` (the Axiom
  46-of-60 scenario, proving grouped drill-down counts sum exactly to the
  aggregate total), `test_no_rows_mutated_by_summary_query` (SHA-256
  before/after).
- Frontend wording assertions:
  `test_landing_panel_no_longer_client_side_filters`,
  `test_landing_panel_visibly_states_last_24h`,
  `test_drilldown_visibly_states_all_time`,
  `test_drilldown_discloses_partial_fetch_when_truncated`,
  `test_triage_scope_explicitly_labelled`,
  `test_legacy_lineage_gap_label_identifies_walkback_queue_source`,
  `test_aggregate_endpoint_route_registered`.

Updated `tests/test_ops_x20_6_discovery_prioritisation.py` (old bare-URL
assertion) and `tests/test_ops_x21c_routes.py`-adjacent
`test_aggregated_outcome_drills_into_filtered_cases` (old "Underlying
cases" header text → "All time").

**Full suite run**: 51/51 passing across
`test_x26_5_1_attribution_health_window_integrity.py`,
`test_ops_x20_6_discovery_prioritisation.py`,
`test_x26_3_subprov_infrastructure_exclusion.py`,
`test_x26_2_1_attribution_gate_fix.py`, `test_discovery_workspace.py`.

Broader sweep across 22 Discovery/attribution-adjacent test files: 218
passed, 4 pre-existing failures **confirmed unrelated** — reproduced
identically against the pre-change baseline via `git stash` (one is a
stale wording assertion from an earlier X26.2.1-era section-ordering
change; three are a Flask application-context issue in
`test_ops_x21c_routes.py` that only manifests when run in combination with
certain other test files, not caused by or related to this sprint's
changes).

## Phase 9 — Live validation

Restarted `watchtower_api` via supervisor to load the new route, then:
- `GET /api/ops-v2/attribution-outcomes/summary?window=24h` →
  `{"KNOWN_RELAY_REACHED": 12, "LINEAGE_GAP": 93, "INSUFFICIENT_EVIDENCE": 280,
  "KNOWN_CEX_REACHED": 57, "UNKNOWN_INFRASTRUCTURE": 67}` — matches direct
  SQL `COUNT(*) ... WHERE completed_at >= ? GROUP BY` exactly.
- `GET /api/ops-v2/attribution-outcomes/summary?window=all` →
  `KNOWN_RELAY_REACHED: 71` (all-time), matching the drill-down's row count
  exactly.
- `GET /api/ops-v2/attribution-outcomes?limit=500&outcome_type=KNOWN_RELAY_REACHED`
  grouped by `terminal_entity`: **Axiom = 46**, next highest 14, total 71 —
  reproduces the exact number from the original reported symptom and
  confirms it sums to the all-time aggregate.
- `/discovery` and `/api/discovery/entity/<mint>` both return HTTP 200.
- `git status --porcelain -- database/*.db` empty — no DB mutation from
  this sprint's work.

## Deliverables

**Before/after query map**:

| Metric | Before | After |
|---|---|---|
| Landing tile counts | fetch 500 mixed-type rows → filter client-side to 24h → group in JS | `SELECT outcome_type, COUNT(*) FROM wt_attribution_outcomes WHERE completed_at>=? GROUP BY outcome_type` |
| Drill-down counts | fetch ≤500 type-filtered rows, no time bound, group by `terminal_entity` in JS | unchanged (row list), now paired with a `window=all` aggregate call to detect/disclose truncation |
| Triage summary | unfiltered `SELECT ... WHERE outcome_type IN (...)`, no LIMIT | unchanged (still correct for what it computes), now labelled "all time" |
| Legacy Lineage Gaps | `wt_walkback_queue.intelligence_outcome` grouped count, unlabelled | same query, relabelled "Walkback Queue: Lineage Gap Rows" |

**New aggregate endpoint**: `GET /api/ops-v2/attribution-outcomes/summary`
— documented in Phase 2 above; params `window` (`24h` default, or `all`),
`completed_after` (custom unix-seconds cutoff, overrides `window`).

**Exact UI wording changes**:
- "Attribution Health" → "Attribution Health · Last 24h" (landing)
- Health row counts: bare number → "N · 24h"
- "Attribution Health · Underlying cases" → "Attribution Health · All time" (drill-down)
- Drill-down sub-label: "N typed terminal outcomes" → "N all-time terminal outcome(s)", or the partial-fetch-disclosure variant when truncated
- "Insufficient Evidence · Level 1" → "Insufficient Evidence · All time"; "N terminal outcomes" → "N terminal outcomes (all time)"
- "Lineage Gaps" (legacy panel) → "Walkback Queue: Lineage Gap Rows"

**Test summary**: 17 new tests, 2 existing tests updated for the intentional
wording change, 51/51 passing in the directly-relevant suite; 218/222
passing in the broader sweep, 4 failures pre-existing and confirmed
unrelated via baseline comparison.

**Live count comparison**: landing 24h `KNOWN_RELAY_REACHED=12` vs.
drill-down all-time `71` (Axiom alone `46`) — both now visibly labelled
with their window, eliminating the apparent contradiction while preserving
both real numbers.

**Confirmation of no classification or historical data changed**: no
`wt_attribution_outcomes` row was inserted, updated, or deleted by this
sprint; no `materialize_outcome`/`derive_outcome` logic was touched; no
schema change (`git status` on `database/*.db` empty throughout).

**Remaining pagination/legacy-panel limitations**: the drill-down's
500-row-per-type fetch cap is still a real cap — it is not currently
truncating anything (max observed type count is 319), but if any single
outcome type's all-time count ever exceeds 500, the new partial-fetch
disclosure will correctly say so rather than silently showing an
incomplete list; true pagination was not implemented in this sprint since
the brief allows either pagination or a labelled partial view, and the
labelled-partial approach is the smaller, lower-risk change for a page not
yet hitting that ceiling. The legacy `wt_walkback_queue`-based panel itself
was relabelled but not deprecated, per the brief's "prefer deprecation if
it no longer adds distinct value" being a judgment call left for a
follow-up decision rather than assumed in this sprint.
