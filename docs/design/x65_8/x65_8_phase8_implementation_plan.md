# X65.8 — Phase 8: Implementation Plan

No implementation performed in this task. This is the plan for a
future, separately-authorized implementation.

## Backend files touched

| File | Change |
|---|---|
| `src/ops/funding_topology.py` | Add `_subprov_candidate_watch_counts()` (new function, Phase 5); modify `classify_topology_for_launch()` to consult it before the existing `_subprov_sibling_counts()` check at step 5; modify `build_topology_classification()` to compute and pass the new lookup alongside the existing ones |
| `src/ops/campaign_classification.py` | **No changes** — Campaign already reads `wt_candidate_websocket_watches` independently; this task does not modify Campaign in any way |
| `src/core/operation_dashboard_routes.py` | **No changes** — the `/api/ops-v2/operational-intelligence` response shape is unchanged; `topology`/`topology_derived_from` fields keep their existing meaning, only the underlying evidence changes |
| `templates/discovery.html` | **No changes required** — Topology's existing display/filter logic (`renderTopologyDistribution()`, `x60TopologyRows()`) is unaffected; the terminology rename from X65.5/X65.6 (Fan-Out → "SubProv Fan-Out") already covers this, if desired, but is not required by this task |

## New functions

```python
# src/ops/funding_topology.py

def _subprov_candidate_watch_counts(ops_conn: sqlite3.Connection) -> dict[str, int]:
    """{subprov_wallet: distinct candidate_wallet count} from
    wt_candidate_websocket_watches. Independent of Campaign -- reads
    the same table Campaign reads, via its own query, no cross-module
    call."""
    if not _table_exists(ops_conn, "wt_candidate_websocket_watches"):
        return {}
    rows = ops_conn.execute(
        "SELECT subprov_wallet, COUNT(DISTINCT candidate_wallet) AS n "
        "FROM wt_candidate_websocket_watches GROUP BY subprov_wallet"
    ).fetchall()
    return {r[0]: r[1] for r in rows}
```

## Modified functions

- `classify_topology_for_launch()`: insert a new check at the start of
  step 5 (per Phase 5's decision order), reading a new
  `candidate_watch_counts` parameter (batched, computed once per
  `build_topology_classification()` call — mirroring the existing
  `sibling_counts`/`multi_level_subprovs`/`mesh_treasuries` batching
  pattern already used in this function's signature).
- `build_topology_classification()`: call
  `_subprov_candidate_watch_counts(ops_conn)` once (alongside the
  existing `_subprov_sibling_counts(ops_conn)` call) and pass the
  result through to `classify_topology_for_launch()` for every launch.

## SQL changes

**None to any schema.** One new `SELECT ... GROUP BY` query added to
the existing per-load classification pass (batched once, not per-launch
— matching the existing pattern for `_subprov_sibling_counts()`). No
`CREATE TABLE`, `ALTER TABLE`, or write statement of any kind.

## Expected performance impact

**Minimal.** `wt_candidate_websocket_watches` has 3,053,025 rows
(Phase 2) — a single `GROUP BY subprov_wallet` aggregate query over
this table, with the existing `ix_wc_subprov_time`/`ix_cand_watch_subprov`
indexes already present on `subprov_wallet` (confirmed via schema
inspection in prior X65.4 work), should complete in low single-digit
seconds at most, comparable to the existing `_subprov_sibling_counts()`
query's own cost over the much smaller `wt_provisioning_edges` table.
This runs once per Discovery load (cached via the existing SWR layer,
X29.1.2), not once per launch — no per-launch RPC or query fan-out is
introduced, consistent with every other classifier in this module.

**Recommended before implementation**: a live `EXPLAIN QUERY PLAN`
check and a wall-clock timing measurement against the real 3M-row
table, to confirm the existing index is actually used and the
aggregate completes within the same order of magnitude as the
classifier's other queries — not assumed, verified, per this project's
own standing performance-measurement discipline (X64.8's own
documented lesson about assumed-vs-measured query cost).

## Regression risks

| Risk | Mitigation |
|---|---|
| A launch's classification changes unexpectedly outside the intended 49-launch set (Phase 7) | The revised rule only fires when `wt_candidate_websocket_watches` has data AND that data differs from what `wt_provisioning_edges` would have produced — bounded and testable directly against Phase 7's measured simulation set |
| Performance regression from the new aggregate query | Measure before shipping (see above); the query is read-only, indexed, and runs once per load, not per launch |
| Conservation invariant breaks (`sum(topology counts) != total_launches`) | `classify_topology_for_launch()` remains a single exhaustive if/elif/else chain (Phase 5/7) — the existing `conserved` boolean (already returned by `build_topology_classification()`) continues to validate this on every call, unchanged |
| Accidental coupling to Campaign | Code review checklist: confirm `funding_topology.py` has zero imports from or references to `campaign_classification.py` anywhere, before merging |
| Breaking the walkback-fallback path for non-cascade-confirmed launches | Preserved exactly as-is at steps 5c/5d/5e (Phase 5/6) — no changes to that code path at all |

## Testing strategy

1. **Unit tests** (new, mirroring `tests/test_x29_1_operational_topology_intelligence.py`'s existing fixture conventions): a minimal in-memory schema with `wt_candidate_websocket_watches` populated for a subprov with >1 distinct candidates, confirming `FAN_OUT` is now reached via the new evidence path (`derived_from` should reflect the new source name).
2. **Regression tests**: re-run the existing `test_x29_1_operational_topology_intelligence.py` suite unmodified — every existing test (Fan-Out via `wt_provisioning_edges`, Linear, Mesh, Unknown, Multi-Level) must continue to pass exactly as today, proving the fallback paths are untouched.
3. **Independence test** (new, explicit): assert `funding_topology.py`'s source contains no `import` of `campaign_classification` and no reference to a `campaign` field — a structural guard against the exact anti-pattern the task's architecture diagram forbids.
4. **Population-impact regression test**: assert the simulated 49-launch change set (Phase 7) matches exactly, using a snapshot of the real subprov/candidate-watch data at implementation time, to catch any unintended drift between design and implementation.
5. **Conservation test**: assert `build_topology_classification()`'s `conserved` boolean remains `True` against the live population, before and after the change.
6. **Live verification**: after deployment, re-run this task's own Phase 3 replay against the 43 confirmed WATCHTOWER launches and confirm the mismatch count drops from 21 to (ideally) 0, using the same methodology already established in this document.
