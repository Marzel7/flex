# X65.8 — Align Topology Classification with Validated Operational Evidence (Full Report)

Consolidated report combining all 8 phases. Design only — no code was
changed in this task. No changes to Campaign classification, treasury
resolution, attribution logic, or detection logic.

## Contents

1. [Review Current Topology Classifier](#phase-1--review-current-topology-classifier)
2. [Compare Evidence Sources](#phase-2--compare-evidence-sources)
3. [Replay Validated WATCHTOWER Launches](#phase-3--replay-validated-watchtower-launches)
4. [Root Cause](#phase-4--root-cause)
5. [Design Updated Topology Logic](#phase-5--design-updated-topology-logic)
6. [Preserve Existing Behaviour](#phase-6--preserve-existing-behaviour)
7. [Population Impact](#phase-7--population-impact)
8. [Implementation Plan](#phase-8--implementation-plan)

---

## Phase 1 — Review Current Topology Classifier

Read-only review of `src/ops/funding_topology.py` (unchanged since
X65.4's own audit — re-confirmed current as of this task).

### Where classification is performed

`classify_topology_for_launch()` (per-launch, pure function) called
from `build_topology_classification()` (batch entry point, run once
per Discovery load). This is the sole topology classifier — nothing
else in the codebase computes a `topology` value.

### Full decision tree

```
For a given launch (mint):

1. Is there a selected walkback edge chain with depth ≥ 2 AND a parent
   wallet that fans out to >1 child (among SELECTED walkback edges only)?
   → MULTI_LEVEL_FAN_OUT

2. Else, is there no subprov AND no treasury evidence at all?
   → UNKNOWN

3. Else, is the involved subprov itself a recorded child of another
   subprov session (SUBPROV_SESSION_OPENED_WS, via=subprov_plain_xfer)?
   → MULTI_LEVEL_FAN_OUT

4. Else, is the involved treasury also recorded as a subprov elsewhere
   (wt_active_subprov_sessions treasury∩subprov overlap)?
   → MESH

5. Else, if a subprov is known:
   a. Does wt_provisioning_edges record >1 DISTINCT CREATOR for this
      subprov (SUBPROV_TO_CREATOR edges, across ALL mints)?
      → FAN_OUT (n_siblings > 1)
   b. Does it record exactly 1 creator?
      → LINEAR (n_siblings == 1)
   c. No SUBPROV_TO_CREATOR edge recorded for this subprov at all —
      fall back to selected-walkback parent fan-out at depth ≥ 1:
      does the immediate walkback parent fan out to >1 child?
      → FAN_OUT (walkback fallback)
      else → LINEAR (walkback fallback, no observed branch)
      else (no walkback evidence either) → UNKNOWN

6. Else, if only a treasury is known (no subprov at all):
   → LINEAR ("treasury_direct_no_subprov")

7. Else → UNKNOWN
```

### Evidence sources consulted, per classification value

| Value | Table(s) read | What is actually counted |
|---|---|---|
| `MULTI_LEVEL_FAN_OUT` (walkback variant) | `wt_walkback_edge_candidates`, `wt_walkback_queue.termination_reason_json` | Selected walkback hop chains for THIS mint's own resolution, not general fan-out |
| `MULTI_LEVEL_FAN_OUT` (session-lineage variant) | `watchtower_events` (`SUBPROV_SESSION_OPENED_WS`) | Whether the subprov is itself a recorded child of another subprov |
| `MESH` | `wt_active_subprov_sessions` (treasury∩subprov overlap) | Structural mesh signal, currently matches 0 launches live |
| `FAN_OUT`/`LINEAR` (primary) | `wt_provisioning_edges` (`SUBPROV_TO_CREATOR` only) | **Distinct creators**, not distinct recipients |
| `FAN_OUT`/`LINEAR` (walkback fallback) | `wt_walkback_edge_candidates`/`wt_walkback_queue` | Same creator-ancestry counting, one hop shallower |
| `UNKNOWN` | (absence of the above) | No lineage evidence at all |

### Traversal depth, thresholds, confidence model

| Parameter | Current value |
|---|---|
| Graph traversal depth | 1 hop (subprov→creator) for primary Fan-Out/Linear; up to 2 hops for Multi-Level walkback variant |
| Branching criteria | `COUNT(DISTINCT to_wallet)` on `SUBPROV_TO_CREATOR` edges — creators only |
| Fan-out threshold | `> 1` distinct creator |
| Temporal window | None |
| Amount similarity | None |
| Confidence model | **None at all** — a material contrast with Campaign (X65.7), which has a three-tier confidence model built in from the start |
| RPC/DB sources | Exclusively local SQLite reads against `wt_ops_v2.db`; zero RPC |

### Exact assignment locations (`src/ops/funding_topology.py`)

- `LINEAR`: lines 276-280 (`n_siblings == 1`), line 296-298 (walkback fallback, no branch), line 303-304 (`treasury_direct_no_subprov`)
- `FAN_OUT`: lines 270-275 (`n_siblings > 1`), lines 290-294 (walkback fallback, branch observed)
- `UNKNOWN`: line 246 (no lineage evidence at all), line 299 (subprov present, no sibling evidence, no walkback), line 306 (final fallback)

---

## Phase 2 — Compare Evidence Sources

Live measurements against `database/wt_ops_v2.db`, 2026-07-22.

### Raw table sizes

| Table | Row count |
|---|---|
| `wt_provisioning_edges` | 1,550 |
| `wt_candidate_websocket_watches` | 3,053,025 |
| `wt_active_subprov_sessions` | 161,818 |
| `wt_watchtower_launches` | 43 |
| `wt_walkback_edge_candidates` | 1,244 |
| `wt_walkback_queue` | 7,335 |

### Distinct-subprov coverage (raw, whole-table)

| Table | Distinct subprovs covered |
|---|---|
| `wt_provisioning_edges` (`SUBPROV_TO_CREATOR.from_wallet`) | 620 |
| `wt_candidate_websocket_watches` (`subprov_wallet`) | 442 |
| `wt_active_subprov_sessions` (`subprov_wallet`) | 75,623 |

Raw counts alone are misleading: `wt_provisioning_edges` covers more
*distinct subprovs* than `wt_candidate_websocket_watches` despite far
fewer total rows (one deduplicated row per subprov→creator pair vs.
one row per individual wrap-close event). Raw coverage breadth is not
the right comparison — coverage of the specific population Topology
needs to classify correctly is.

### Coverage of the confirmed-WATCHTOWER population specifically

| Table | Launches covered (of 43 confirmed) | % |
|---|---|---|
| `wt_provisioning_edges` | 1 | 2.3% |
| `wt_candidate_websocket_watches` | 39 | 90.7% |
| Both | 1 | 2.3% |
| Neither | 4 | 9.3% |

For the exact launches Campaign already correctly identifies as
WATCHTOWER, `wt_candidate_websocket_watches` covers 39x more of them
than `wt_provisioning_edges` does.

### Timing / freshness

Both tables are actively maintained and current — neither is stale or
abandoned (`wt_provisioning_edges`: 2026-07-14 to 2026-07-22;
`wt_candidate_websocket_watches`: 2026-06-14 to 2026-07-22, still
being written live). The difference is entirely about which write
path populates them.

### Completeness comparison matrix

| Dimension | `wt_provisioning_edges` | `wt_candidate_websocket_watches` |
|---|---|---|
| Writer | `capture_provisioning_relationship()`, called **only** from the walkback success path, **only** once a creator is already known | `open_candidate_watch()`, called from `_handle_subprov_tx()` for **every** wrap-close/candidate destination observed live |
| What it records | One deduplicated edge per (subprov, creator) pair | Every individual wrap-close destination event, including non-creator siblings |
| Can it represent sibling (non-creator) recipients? | **No** — schema CHECK constraint restricts `edge_type` to `TREASURY_TO_SUBPROV`/`SUBPROV_TO_CREATOR` only | **Yes** — every candidate wallet a subprov's wrap-close ever targeted is recorded, creator or not |
| Coverage of cascade-confirmed launches | 2.3% (1/43) | 90.7% (39/43) |
| Coverage of walkback-only-resolved launches | Higher (this is its designed population) | Near-zero |
| Reliability of a positive signal | High | High |
| Reliability of a negative/absent signal | Low — absence often means "walkback never ran," not "no fan-out exists" | Low for walkback-only launches — absence often means "never passed through the live cascade" |

### Overlap

Only 1 of 43 confirmed WATCHTOWER launches has coverage in *both*
tables — the two sources are almost entirely non-overlapping
populations, not two redundant views of the same data.

### Conclusion for Phase 5's design

Topology should consume `wt_candidate_websocket_watches` as its
primary fan-out evidence source for launches it covers, retaining
`wt_provisioning_edges`/walkback evidence as a fallback — the reverse
of today's priority order.

---

## Phase 3 — Replay Validated WATCHTOWER Launches

Live replay of all 43 confirmed WATCHTOWER launches against the live
`/api/ops-v2/operational-intelligence?window=all` response's
`campaign` field (X65.7, unmodified), an "Observed" topology computed
directly from `wt_candidate_websocket_watches`, and the same
response's existing `topology` field.

### Summary

| Category | Count |
|---|---|
| **Campaign=WATCHTOWER, Current Topology correctly = FAN_OUT-equivalent** | **0** |
| Campaign=WATCHTOWER, Current Topology wrongly = UNKNOWN | 20 |
| Campaign=WATCHTOWER, Current Topology wrongly = LINEAR (direct contradiction — 25 observed recipients) | 1 |
| Campaign=WATCHTOWER total | 21 |
| Not in the 365-day window's population at all (pre-existing coverage boundary, not a Topology defect) | 21 |
| Campaign=OTHER_CAMPAIGN (correctly not WATCHTOWER) | 1 |

Of the 21 launches Campaign correctly and independently identifies as
WATCHTOWER, **0 are correctly classified by the current Topology
classifier**. 20 are `UNKNOWN` and 1 (`EGB4sv9ddN...`, 25 observed
recipients) is `LINEAR` — the same contradiction case found in X65.4,
reconfirmed here against Campaign's own live output.

### Explaining every mismatch

Every mismatch traces to the identical mechanism: the current
Fan-Out/Linear rule counts `SUBPROV_TO_CREATOR` edges in
`wt_provisioning_edges` (2.3% coverage of this population) rather than
`wt_candidate_websocket_watches` (90.7% coverage), which shows real,
substantial fan-out (2 to 481 recipients) for every mismatched launch.

### Note on "NOT_IN_WINDOW"

21 of the 43 confirmed launches are older than the 365-day window
queried and have no row in `wt_attribution_outcomes` at all — a
separate, pre-existing population boundary, not a defect in either
classifier evaluated here.

---

## Phase 4 — Root Cause

All 21 mismatches trace to a single mechanism, not 21 separate
incidents.

### The mechanism

`_subprov_sibling_counts()` (`src/ops/funding_topology.py:58-69`)
counts **distinct creators** from `wt_provisioning_edges`, whose sole
writer (`capture_provisioning_relationship()`,
`src/ops/provisioning_edges.py:150-205`) only inserts an edge when a
creator is **already known** (`if subprov and creator:`, line 195) —
called exclusively from the walkback success path, a structurally
separate path from the live cascade that confirmed these 43 launches.

### Why this produces exactly the two mismatch patterns

- **`UNKNOWN` (20 of 21)**: zero `SUBPROV_TO_CREATOR` rows for the
  subprov → no sibling-count entry → correct fallthrough to `UNKNOWN`
  given the (absent) inputs.
- **`LINEAR` (1 of 21, `EGB4sv9ddN...`)**: exactly one recorded edge
  from an unrelated walkback resolution → `n_siblings == 1` →
  confidently wrong `LINEAR`, contradicted by 25 independently-observed
  recipients.

### Root cause classification

| Candidate cause | Applies? | Evidence |
|---|---|---|
| **Creator-only traversal** | **Yes — primary cause** | Writer-side gate at `provisioning_edges.py:195` |
| **Incomplete provisioning graph** | **Yes — underlying condition** | 2.3% coverage of the confirmed-WATCHTOWER population |
| **Missing sibling expansion** | **Yes — structural gap** | Schema CHECK constraint has no edge type for non-creator siblings at all |
| **Evidence ignored** | **Yes — the fixable gap** | `wt_candidate_websocket_watches` has 90.7% coverage and is never read in `funding_topology.py` |
| **Outdated graph construction** | Partial | Not stale, but its design predates the richer candidate-watch data |
| **Threshold issue** | No | Even threshold `>0` would fail identically — the count is zero regardless |

### Conclusion

A single, well-understood mechanism fully explains all 21 mismatches —
matching X65.4's original finding, now reconfirmed against Campaign's
live output.

---

## Phase 5 — Design Updated Topology Logic

Design only. Revises the Fan-Out/Linear rule to consume
`wt_candidate_websocket_watches` while remaining fully independent of
Campaign.

### Architecture constraint (honored exactly)

```
Observed Evidence
        │
        ├── Campaign
        └── Topology
```

**Never** Campaign → Topology. Topology reads
`wt_candidate_websocket_watches` directly, via its own SQL — no call
into `campaign_classification.py`, no read of `records["campaign"]`,
no dependency on Campaign having run. This mirrors the existing
relationship between `funding_topology.py` and
`operational_behaviour_tags.py` (X29.1) — not a new pattern.

### New, independent evidence function

```python
def _subprov_candidate_watch_counts(ops_conn):
    """{subprov_wallet: distinct candidate_wallet count} from
    wt_candidate_websocket_watches -- queried independently, no
    cross-module call, no dependency on Campaign."""
    if not _table_exists(ops_conn, "wt_candidate_websocket_watches"):
        return {}
    rows = ops_conn.execute(
        "SELECT subprov_wallet, COUNT(DISTINCT candidate_wallet) AS n "
        "FROM wt_candidate_websocket_watches GROUP BY subprov_wallet"
    ).fetchall()
    return {r[0]: r[1] for r in rows}
```

### Revised decision order (additive priority insertion, not a rewrite)

```
1-4. [UNCHANGED — MULTI_LEVEL_FAN_OUT variants, MESH]
5. If a subprov is known:
   a. NEW: wt_candidate_websocket_watches records >1 distinct
      candidate_wallet? → FAN_OUT
   b. NEW: exactly 1? → LINEAR
   c-f. [EXISTING, now a FALLBACK] wt_provisioning_edges /
        walkback-based checks, unchanged
6-7. [UNCHANGED]
```

Only the evidence-source *priority* changes at step 5; nothing is
replaced — `wt_provisioning_edges` remains reachable as a fallback for
launches `wt_candidate_websocket_watches` has no data for.

### Confidence model

Not required by this task; a `derived_from`-style provenance string
addition is optional and left for Phase 8's implementation plan, not
designed further here.

### No new evidence source, no new detection

Zero new tables, zero new detection logic, zero new RPC — this adds a
second, independent reader of an already-existing, already-populated
table.

---

## Phase 6 — Preserve Existing Behaviour

- **Non-WATCHTOWER launches**: unaffected — launches with no
  `wt_candidate_websocket_watches` coverage fall through to the
  unchanged `wt_provisioning_edges`-based logic, exactly as today.
- **Linear detection**: preserved via the same three paths
  (candidate-watch count=1, provisioning-edge sibling count=1,
  walkback fallback), only reordered in priority.
- **Mesh detection**: entirely unchanged — runs before the revised
  logic and never reads `wt_candidate_websocket_watches`.
- **Unknown where evidence genuinely doesn't exist**: preserved — the
  final fallback is untouched.
- **Only WATCHTOWER launches with validated fan-out migrate**: the 21
  launches that migrate are precisely those Campaign independently
  confirms as WATCHTOWER *and* that show real
  `wt_candidate_websocket_watches` fan-out — this follows from the
  evidence source's own coverage boundary (near-zero outside the
  cascade-confirmed population), not from any Topology-side reference
  to Campaign's output.

---

## Phase 7 — Population Impact

Measured live against the full 365-day-window population
(`window=all`, 7,269 launches).

### Current vs. projected distribution

| Topology | Current | Projected | Change |
|---|---|---|---|
| `UNKNOWN` | 5,353 | 5,304 | −49 |
| `LINEAR` | 916 | 916 | 0 |
| `MULTI_LEVEL_FAN_OUT` | 431 | 431 | 0 |
| `FAN_OUT` | 569 | 618 | **+49** |
| **Total** | **7,269** | **7,269** | **0** |

### Simulation results

| Outcome | Count |
|---|---|
| Would change `UNKNOWN`/`LINEAR` → `FAN_OUT` | **49** |
| Would change `UNKNOWN` → `LINEAR` | 0 |
| No subprov resolvable at all (correctly stays `UNKNOWN`) | 5,042 |
| Subprov resolved but no candidate-watch coverage (unchanged existing logic) | 1,177 |
| Already correct under both old and new evidence | 1 |

### Improvement and conservation

**49 launches (0.67% of total, 0.92% of the current `UNKNOWN` pool)**
would move to `FAN_OUT`; **7,220 launches (99.33%)** remain unchanged —
a precisely-bounded correction, not a broad reclassification. This
modest size is the honest, measured extent of the exact gap this task
targets, not a shortfall.

**Conservation**: `5,304 + 916 + 431 + 618 = 7,269` — exactly matches
the total population before and after. This holds by construction
(the revised rule remains a single exhaustive if/elif/else chain), not
by a separate reconciliation step.

---

## Phase 8 — Implementation Plan

No implementation performed in this task.

### Backend files touched

| File | Change |
|---|---|
| `src/ops/funding_topology.py` | Add `_subprov_candidate_watch_counts()`; modify `classify_topology_for_launch()` and `build_topology_classification()` |
| `src/ops/campaign_classification.py` | **No changes** |
| `src/core/operation_dashboard_routes.py` | **No changes** |
| `templates/discovery.html` | **No changes required** |

### SQL changes

None to any schema. One new batched `SELECT ... GROUP BY` query per
Discovery load (not per-launch) — no writes.

### Expected performance impact

Minimal — a single aggregate query over `wt_candidate_websocket_watches`
(3,053,025 rows), using existing indexes on `subprov_wallet`, run once
per load (cached via the existing SWR layer). Recommended: measure via
`EXPLAIN QUERY PLAN` and wall-clock timing before shipping, not assumed.

### Regression risks and mitigations

| Risk | Mitigation |
|---|---|
| Unexpected classification changes outside the 49-launch set | Bounded and testable directly against Phase 7's measured simulation |
| Performance regression | Measure before shipping; read-only, indexed, once per load |
| Conservation invariant breaks | Existing `conserved` boolean continues to validate on every call |
| Accidental coupling to Campaign | Code review: zero imports of `campaign_classification` in `funding_topology.py` |
| Breaking the walkback-fallback path | Preserved exactly as-is, untouched |

### Testing strategy

1. New unit tests for the candidate-watch-based Fan-Out/Linear path.
2. Full existing `test_x29_1_operational_topology_intelligence.py` suite must pass unmodified.
3. New independence test: assert no import of `campaign_classification` anywhere in `funding_topology.py`.
4. Population-impact regression test against the measured 49-launch change set.
5. Conservation test: `conserved == True` before and after, against live data.
6. Live verification: re-run this report's Phase 3 replay post-deployment and confirm the mismatch count drops from 21 to 0.
