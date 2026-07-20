# X27.4 — Behavioural Discovery Engine (reframe)

Supersedes the framing (not the implementation) of the earlier X27.4
deliverable. The classifier, API, tests, and UI built for "Behavioural
Investigation Queue" already satisfy this brief's Phases 1-4 as written;
this document adds the explicit independence guarantees, the Phase 5
demonstration function, and the Phase 6 extensibility confirmation this
reframed brief calls for by name.

## Phase 1 — Behaviour-first architecture (confirmed, not re-implemented)

`src/ops/behaviour_queue.py`'s three classification entry points
(`rapid_birth_launch_lookup`, `burst_launch_lookup`,
`build_behaviour_queue`) take only database paths and a time window as
parameters — never a treasury, operator, or infrastructure identity.
Verified two ways:
- **Structural**: `test_classification_functions_never_take_treasury_operator_or_infrastructure_params`
  inspects each function's signature and asserts none accept a forbidden
  parameter name.
- **Source-level**: `test_behaviour_queue_module_never_queries_treasury_operator_infrastructure_tables`
  parses the module's AST and asserts no `conn.execute(...)` SQL string or
  import statement references `wt_confirmed_treasuries`,
  `wt_treasury_review`, `operator_entities`, `wt_discovered_subprovs`,
  `infra_mapping`, `wt_active_subprov_sessions`, or
  `wt_wrap_close_candidates` — the tables/modules that carry
  treasury/operator/infrastructure identity elsewhere in the codebase.

The module docstring was updated to state this explicitly: "this is NOT a
WATCHTOWER detector... a launch exhibiting the same operational tempo as
the historical WATCHTOWER corpus is worth investigating regardless of
which treasury (if any) turns out to have funded it."

## Phase 2 — Rapid Birth → Launch (unchanged from prior sprint)

Already implemented exactly as specified: fires only when
`wt_watchtower_launches.create_time IS NOT NULL AND
birth_to_launch_seconds IS NOT NULL`, threshold ≤5 seconds, no fallback to
`token_analysis.created_at` or any other signal. Confirmed by
`test_rapid_birth_launch_only_fires_with_canonical_timing` and
`test_archetypes_never_infer_unavailable_timing`.

## Phase 3 — Behaviour Queue (unchanged from prior sprint)

Already live: `GET /api/ops-v2/behaviour-queue`, Discovery landing panel
(`behaviourQueuePanel()`), drill-down (`filteredArchetype()`). Rapid
Birth → Launch's metadata already states `confidence: HIGH` and its
`coverage_note`/`coverage_pct` fields make the partial-coverage nature
explicit in both the API response and the rendered UI.

## Phase 4 — WATCHTOWER replay (unchanged; re-confirmed live)

```
total with canonical timing: 41
matched (<=5s): 40
precision: 97.56%
```

Reproduced again in this session — unchanged from the prior sprint's
measurement. This replay's role is explicitly validation-only: it proves
the archetype is high-precision against a known-positive set; it is not
what the archetype is "for."

## Phase 5 — Future operation discovery (new: demonstration function)

Added `rapid_birth_launch_candidates_for_treasury_discovery()`
(`src/ops/behaviour_queue.py`), implementing exactly the boundary the
brief specifies:

```
Rapid Birth → Launch
  → Unknown treasury
  → Repeated treasury
  → Repeated provisioning
  → Emerging operation
```

This function performs **only** the first arrow: it calls
`build_behaviour_queue()`/`launches_in_archetype()` and returns a bare
list of mints — no funder, treasury, or operator field is attached or
looked up. The remaining arrows (unknown treasury → repeated treasury →
repeated provisioning → emerging operation) are the job of
**already-built, separate modules** (`src/core/walkback_worker.py`,
`src/ops/treasury_expansion_resolver.py`,
`src/ops/emerging_operator_service.py`) — this sprint does not modify or
re-implement any of them. `test_phase5_candidate_discovery_returns_bare_mint_list_only`
confirms the return type carries no funding-side data.

**Live verification**: called against the real databases, this function
currently returns `[]` — consistent with the separately-documented finding
(see `X27_4_ZERO_RAPID_BIRTH_LAUNCH_INVESTIGATION.md`) that
`wt_watchtower_launches` has not received a new row in 53+ hours. The
function correctly reflects that state rather than inferring or
backfilling a result.

## Phase 6 — Future behavioural archetypes (architecture only, confirmed)

`ARCHETYPE_ORDER` remains a plain ordered tuple; `assignments` already
stores every matched archetype per launch (`archetypes_matched`), not only
the summary-count `primary_archetype`. Adding Burst Launches' siblings
(Coordinated Migration, Shared Provisioning Tempo, Migration Waves,
Creator Burst Activity) requires only:
1. A new lookup function, built on an independently-verified-trustworthy
   signal, taking no treasury/operator/infrastructure parameter (enforced
   by the same structural test pattern above, which would need one new
   assertion line per added function — no other change).
2. One new entry in `ARCHETYPE_ORDER`/`ARCHETYPE_LABELS` and the
   `archetypes` list construction in `build_behaviour_queue()`.

No implementation of any of these was performed — architecture
confirmation only, per the brief.

## Tests

`tests/test_x27_4_behaviour_queue.py` — 19 tests total (15 from the prior
sprint + 4 new): the two independence guarantees above, a same-input
-same-output determinism check proving classification is a pure function
of lifecycle timing (never varies with unstated treasury context), and
the Phase 5 bare-mint-list contract check. Full regression: 259 passed / 1
pre-existing unrelated failure (confirmed independent of this work in an
earlier sprint via `git stash`).

## Confirmation

No attribution, walkback, detection, or schema logic was changed.
`OUTCOME_TYPES` and `investigation_pipeline.BUCKET_ORDER` remain
byte-for-byte unchanged (re-verified via the existing tests in this run).
Only `src/ops/behaviour_queue.py` (docstring + one new function) and
`tests/test_x27_4_behaviour_queue.py` (4 new tests) were touched in this
reframe.
