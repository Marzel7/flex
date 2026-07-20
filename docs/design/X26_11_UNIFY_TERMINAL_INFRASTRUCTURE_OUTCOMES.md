# X26.11 — Unify Terminal Infrastructure Outcomes in Attribution Health

Status: Implemented, tested, live-verified. No attribution classification,
walkback, detection, operation identity, or database schema changed — a
presentation-and-aggregation-only change confined to
`src/core/operation_dashboard_routes.py`'s summary endpoint and
`templates/discovery.html`'s Attribution Health panel.

---

## Phase 1 — Terminal outcome inventory

| Outcome type | SQL source | Canonical enum | Reviewed terminal boundary? | Live count (all-time) |
|---|---|---|---|---|
| `KNOWN_CEX_REACHED` | `_boundary()`, `attribution_outcome.py` | Yes (`OUTCOME_TYPES`) | Yes | 331 |
| `KNOWN_BRIDGE_REACHED` | `_boundary()` | Yes | Yes | 0 (none currently materialized) |
| `KNOWN_RELAY_REACHED` | `_boundary()` | Yes | Yes — carries `terminal_entity_type` in `{AUTOMATION, RELAY, CUSTODY, PLATFORM, PROTOCOL, SYSTEM}` (any non-bridge/CEX registry category collapses here) | 73 (71 AUTOMATION, 1 CUSTODY, 1 RELAY) |
| `KNOWN_INFRASTRUCTURE_REACHED` / `KNOWN_AUTOMATION_REACHED` / `KNOWN_CUSTODY_REACHED` | — | **Do not exist** — confirmed via direct inspection of `OUTCOME_TYPES`; these are not separate canonical types, they are `terminal_entity_type` values nested inside `KNOWN_RELAY_REACHED` | N/A | N/A |
| `LINEAGE_GAP` | `derive_outcome()` fallback | Yes | **No** — see Phase 2 | 643 |
| `UNKNOWN_INFRASTRUCTURE` | `_known_unknown_infrastructure()` | Yes | **No** — see Phase 2 | 204 |

Where each is used elsewhere (unchanged by this sprint): the Discovery
Result card's `ANALYST_WORDING` map (`discovery.html:360-380`) and the
per-mint drill-down (`filteredCases()`) already render each type with its
own specific wording — this sprint does not touch either.

## Phase 2 — Semantic equivalence audit

**Confirmed equivalent** (all answer "attribution legitimately terminated
at a reviewed boundary," differing only by infrastructure subtype):
`KNOWN_CEX_REACHED`, `KNOWN_BRIDGE_REACHED`, `KNOWN_RELAY_REACHED`. Traced
`_boundary()` (`attribution_outcome.py:265-318`) — all three are only ever
returned when the terminal address is found in `CEX_ACCOUNTS` or
`INFRASTRUCTURE_ACCOUNTS` (a reviewed, static registry), never as a
heuristic guess.

**Confirmed NOT equivalent, correctly excluded**:
- `LINEAGE_GAP` — `derive_outcome()`'s own `stop_reason`: *"Walkback
  stopped at a lineage gap. **Retry only when new evidence arrives.**"*
  Its `terminal_entity_type` can also read `"INFRASTRUCTURE"`
  (`attribution_outcome.py:453-459`, `"INFRASTRUCTURE" if terminal else
  "UNKNOWN"`), but this is a **generic fallback label for "some address was
  involved,"** not proof of a reviewed boundary — the wording explicitly
  says attribution did NOT legitimately conclude, it's retriable.
- `UNKNOWN_INFRASTRUCTURE` — its own stop_reason: *"Unknown infrastructure
  identified. **Eligible for emerging-operator monitoring.**"* — the
  opposite of "reviewed": this is explicitly a not-yet-reviewed candidate
  under active monitoring for potential future operator promotion.

Merging either of these into the reviewed-infrastructure group would have
been a genuine semantic error (the brief's "do not merge if materially
different" guardrail) — confirmed via direct code/wording inspection, not
assumed.

## Phase 3 — Presentation model

`REVIEWED_INFRASTRUCTURE_REACHED` is **not** added to `OUTCOME_TYPES` or
`wt_attribution_outcomes` — confirmed via test
(`test_canonical_enums_untouched`) that the enum tuple is byte-for-byte
identical to its pre-sprint value. It exists purely as a computed
aggregate in the summary API response and a grouped row in the landing
UI. Every underlying `wt_attribution_outcomes` row keeps its own exact
`outcome_type` and `terminal_entity_type` forever, unmodified.

## Phase 4 — Backend aggregation

`GET /api/ops-v2/attribution-outcomes/summary` (unchanged endpoint,
`src/core/operation_dashboard_routes.py`) now additionally computes, from
the exact same already-fetched `counts` dict (no new base query):

```python
_REVIEWED_TERMINAL_OUTCOME_TYPES = ("KNOWN_CEX_REACHED", "KNOWN_BRIDGE_REACHED", "KNOWN_RELAY_REACHED")
reviewed_total = sum(counts.get(t, 0) for t in _REVIEWED_TERMINAL_OUTCOME_TYPES)
# + one additional GROUP BY terminal_entity_type query, filtered to just
# those three outcome_types, for the subtype breakdown:
subtype_rows = conn.execute(
    "SELECT terminal_entity_type, COUNT(*) AS n FROM wt_attribution_outcomes "
    "WHERE outcome_type IN (...) [AND completed_at >= ?] GROUP BY terminal_entity_type"
)
```

Response shape (additive — the existing `counts` field is completely
unchanged, so no existing consumer of this endpoint breaks):
```json
{
  "ok": true, "window": "24h", "completed_after": 1784143616,
  "counts": {"KNOWN_CEX_REACHED": 56, "KNOWN_RELAY_REACHED": 13, "...": "..."},
  "reviewed_infrastructure": {
    "total": 69,
    "subtypes": [
      {"terminal_entity_type": "CEX", "label": "Exchange (CEX)", "count": 56},
      {"terminal_entity_type": "AUTOMATION", "label": "Automation", "count": 12},
      {"terminal_entity_type": "RELAY", "label": "Relay", "count": 1}
    ]
  }
}
```
No database writes, no schema change — pure `COUNT(*)`/`GROUP BY`
aggregation over already-persisted rows, verified live: `56 (CEX) + 12
(AUTOMATION) + 1 (RELAY) = 69 = 56 (KNOWN_CEX_REACHED) + 13
(KNOWN_RELAY_REACHED)`.

## Phase 5 — UI presentation

`templates/discovery.html`'s `healthPanel()` now renders one grouped row,
`"Reviewed Infrastructure Reached"`, as a `<details>` element — the
concept is visible first (collapsed), the subtype breakdown one click
away (expanded), per the brief's success criterion. `summariseOutcomes()`
excludes the three reviewed-terminal outcome types from its own flat
per-type row list (via a new `REVIEWED_TERMINAL_OUTCOME_TYPES` filter) so
they render exactly once, inside the group, never duplicated as a
separate flat row too. Only subtypes that currently have a nonzero count
are shown — confirmed via the live response (no `BRIDGE` row appears,
since 0 `KNOWN_BRIDGE_REACHED` rows currently exist) — no information is
lost, since a zero-count subtype has nothing to display anyway.

## Phase 6 — Drill-down behaviour verified unchanged

Each subtype row inside the group links to
`/discovery?outcome_type=KNOWN_{SUBTYPE}_REACHED` — the exact same URL
pattern, read by the exact same unmodified `FILTER_OUTCOME`/
`filteredCases()` code path every other health row has always used.
Verified live: clicking "Exchange (CEX)" → `outcome_type=KNOWN_CEX_REACHED`
→ `/api/ops-v2/attribution-outcomes?limit=500&outcome_type=KNOWN_CEX_REACHED`
returns the full canonical 331-row all-time set, completely unaffected by
the new grouping — the grouped row is purely a navigation convenience,
never a new filter concept.

## Phase 7 — Cross-platform consistency

Audited Discovery Result (`ANALYST_WORDING` map), Attribution Outcome
(per-mint card), and the drill-down's own per-type rendering
(`filteredCases`, `renderRelayGrouped`) — all already correctly show the
specific canonical outcome type and subtype for a single mint's own
attribution result; none of them aggregate across multiple mints the way
the landing Attribution Health panel does. **No genuine inconsistency
found requiring a terminology change elsewhere** — per the brief's "do not
alter terminology elsewhere unless a genuine inconsistency is found,"
nothing else was touched.

## Phase 8 — Future-proofing

Confirmed: adding a new reviewed infrastructure registry category (e.g. a
new bridge provider) requires only a registry entry in
`src/utils/infra_mapping.py` plus using one of the three existing
canonical outcome enums (`_boundary()` already routes any non-CEX/bridge
category to `KNOWN_RELAY_REACHED`) — no UI/backend code change. Verified
directly: `_SUBTYPE_LABELS.get(new_category, new_category)` gracefully
falls back to the raw category name for any not-yet-explicitly-labelled
subtype rather than erroring or hiding it (`test_new_infrastructure_type_
automatically_contributes`), and the `GROUP BY terminal_entity_type` SQL
naturally includes any new value with zero code change.

## Phase 9 — Tests

`tests/test_x26_11_unified_terminal_infrastructure_outcomes.py` — 11
tests, all passing:
- `test_group_total_equals_sum_of_subtypes`,
  `test_individual_subtype_counts_unchanged`,
  `test_sql_aggregation_matches_direct_database_counts`.
- `test_24h_and_alltime_windows_both_aggregate_correctly`.
- `test_new_infrastructure_type_automatically_contributes` — a synthetic
  `NOVEL_FUTURE_CATEGORY` subtype correctly contributes to the total with
  zero code change.
- `test_lineage_gap_and_insufficient_evidence_excluded_from_group` —
  confirms Phase 2's semantic-equivalence boundary is enforced in code,
  not just documented.
- `test_non_terminal_outcomes_unaffected_by_grouping`.
- `test_canonical_enums_untouched` — asserts `OUTCOME_TYPES` is exactly
  its pre-sprint value and `"REVIEWED_INFRASTRUCTURE_REACHED"` was never
  added to it.
- `test_no_database_mutation` (SHA-256 before/after).
- `test_aggregate_endpoint_computes_reviewed_infrastructure_field`,
  `test_drilldown_filter_unchanged_in_template`.

Also updated one pre-existing test
(`test_ops_x20_6_discovery_prioritisation.py::test_terminal_outcomes_are_
aggregated_not_added_to_primary_feed`) whose assertion on `healthPanel()`'s
call signature needed to reflect the new, additive
`typed.reviewed_infrastructure` argument.

**Full regression**: 145/145 passing across this new suite plus
`test_x26_10_1_remove_invalid_treasury_subprov_wording.py`,
`test_x26_10_unified_terminal_infrastructure.py`,
`test_x26_9_1_infrastructure_activity_metrics.py`,
`test_x26_8_reject_state_aware_operational_behaviour.py`,
`test_x26_7_evidence_presentation_refresh.py`,
`test_x26_6_1_reject_state_aware_provenance.py`,
`test_x26_5_1_attribution_health_window_integrity.py`,
`test_discovery_workspace.py`, `test_x26_2_1_attribution_gate_fix.py`,
`test_ops_x20_6_discovery_prioritisation.py`, and the pre-existing
`test_ops_x21e_operational_behaviour_rendering.py`.

## Live verification

Restarted `watchtower_api`, confirmed:
- `GET /api/ops-v2/attribution-outcomes/summary?window=24h` returns
  `reviewed_infrastructure.total=69`, matching `counts.KNOWN_CEX_REACHED
  (56) + counts.KNOWN_RELAY_REACHED (13) = 69` exactly, with subtypes
  summing correctly (`56 CEX + 12 AUTOMATION + 1 RELAY = 69`).
- `GET /discovery` returns HTTP 200 and the served HTML contains
  `"Reviewed Infrastructure Reached"`.
- `GET /api/ops-v2/attribution-outcomes?limit=500&outcome_type=KNOWN_CEX_REACHED`
  (the drill-down a subtype click would follow) still returns the full,
  correct, canonical 331-row all-time set — completely unaffected.
- `git status --porcelain -- database/*.db` empty — no DB mutation.

## Confirmation that canonical attribution outcomes were not changed

- `src/ops/attribution_outcome.py` (`OUTCOME_TYPES`, `derive_outcome()`,
  `_boundary()`) — **not modified at all**; only read by the new
  aggregation.
- `wt_attribution_outcomes` — no schema change, no new column, no row
  ever written, updated, or deleted by this sprint.
- Only `src/core/operation_dashboard_routes.py` (backend aggregation,
  additive) and `templates/discovery.html` (landing panel grouping,
  presentation-only) were modified.
