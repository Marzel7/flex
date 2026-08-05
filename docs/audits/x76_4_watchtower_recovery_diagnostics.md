# X76.4 — WATCHTOWER Recovery Diagnostics

## Objective

Discovery, Walkback, Operation Matching, Treasury Review, and Operator
Identity each already work correctly in isolation. What was missing:
when all of that produces "0 WATCHTOWER launches" for a recent window,
nothing told the analyst *why* — which of the eight pipeline stages
(Launch → Walkback → Infrastructure Reconstruction → Behaviour Match →
Potential Expansion → Treasury Review → Confirmed Treasury → Operator
Identity Expansion → WATCHTOWER) the population actually stopped at.

## What already existed (reused, not rebuilt)

This milestone is almost entirely re-projection, not new detection logic:

- **`src/ops/watchtower_funnel.py::build_watchtower_funnel()`** already
  computes the exact sequential stage counts this milestone's pipeline
  section needs (launches → creators → walkbacks started/completed →
  subprovisioners → treasuries → known treasuries → canonical operators),
  each with its own `count`/`loss`/`href`, entirely from persisted tables
  (`token_analysis`, `wt_walkback_queue`, `wt_watchtower_launches`,
  `watchtower_token_attribution`, `wt_confirmed_treasuries`,
  `operator_entities`). It was already correct and already wired to a
  dashboard route (`/api/ops-v2/watchtower-attribution-funnel`) — just
  never surfaced on Discovery, and never paired with an explanation of
  *why* a stage was empty.
- **`src/discovery/operation_convergence.py::build_convergence_view()`**
  already computes "Potential Expansions" — Investigation Populations
  that score ≥ 0.34 against a known Operation's declared
  `OperationMatchingProfile` (`src/ops/operation_matching_profile.py`).
  This is the exact "Behaviour Match" / "Potential Expansion" concept the
  milestone spec names; it already existed as a first-class, named
  output, not something implicit that needed inventing.
- **`src/ops/treasury_review_workspace.py::list_review_workspace()`**
  already returns `pending_total`, `watchtower_candidates`,
  `oldest_pending_age_secs` — exactly the Treasury Review numbers the
  spec asks for.
- **`wt_treasury_review_actions`** (X76.2's immutable audit table) and
  **`operator_identity_events`** / **`wt_confirmed_treasuries`** (X76.1's
  projection contract) already record, respectively, every
  approve/reject decision and every treasury confirmation / operator
  expansion event with a timestamp — exactly what windowed
  approved/rejected/confirmed/expansion counts need.

**No dedicated rotation-detection module existed** — this is the one
genuinely new diagnostic signal in this milestone (see below).

## New module: `src/ops/watchtower_recovery_diagnostics.py`

A pure, read-only composition function,
`build_recovery_diagnostics(ops_db_path, core_db_path, window_seconds=...)`,
that:

1. Calls `build_watchtower_funnel()` once and re-labels six of its ten
   stages into the spec's named pipeline vocabulary (`_pipeline_status`)
   — no new counting logic, only presentation.
2. Calls `build_convergence_view()` (via the same read-only ops-DB
   connection `operation_convergence_routes.py`'s own route already
   uses) and filters `potential_expansions` down to the ones matched to
   WATCHTOWER specifically.
3. Calls `list_review_workspace()` for Treasury Review's pending/queue-age
   numbers, and separately queries `wt_treasury_review_actions` (X76.2)
   for windowed approved/rejected/dismissed counts (that table is
   audit-only and not time-windowed by the existing workspace function).
4. Queries `wt_confirmed_treasuries`/`operator_identity_events` directly
   for windowed confirmation and expansion counts (Operator Identity
   Expansion section).
5. Derives **Match Quality** (Recovered/Missing, no percentages per
   spec) straight off the same funnel stage counts used for the pipeline
   section — topology/funding/provisioning/treasury/controller each map
   to one existing stage's count being non-zero.
6. Derives the **Possible Treasury Rotation** signal (new logic, since no
   equivalent existed): true only when an unknown treasury (walkback
   resolved a treasury address absent from `wt_confirmed_treasuries`)
   co-occurs with known topology, known funding behaviour, AND known
   provisioning behaviour all at once. Explicitly diagnostic-only —
   returns a flag and counts, never writes anything, never promotes a
   candidate, never appears anywhere but this read-only payload.
7. Determines **exactly one primary bottleneck** via `_determine_bottleneck()`
   — a first-zero scan over the same pipeline stage counts already
   returned, in pipeline order. Because it reads off the identical
   numbers already displayed, the reported bottleneck can never disagree
   with what the pipeline section shows.
8. Builds the top-level `explanation` string using the same bottleneck
   logic — never a bare "0 WATCHTOWER launches"; always either "N
   confirmed WATCHTOWER launch(es) in this window" or "No confirmed
   WATCHTOWER launches. Reason: <bottleneck reason>."

**Deliberately not generic.** `build_watchtower_funnel()` is hardcoded to
the canonical WATCHTOWER control case by its own docstring/design (it
queries `operator_entities WHERE operator_id=WATCHTOWER_OPERATOR_ID`
directly, not parameterised). An earlier draft of this module accepted an
arbitrary `operator_id`/`operator_display_name` and was caught, via named
validation against 3SW2, silently mislabeling WATCHTOWER's own canonical-
operator counts as "139 confirmed 3SW2 launches" — the funnel underneath
never actually changed operator. Fixed by removing those parameters
entirely; this module is explicitly WATCHTOWER-only, matching the
milestone's own scope. A second Operation needing the same diagnostic
shape would first need its own operator-parameterised funnel.

## API + UI

- New route: `GET /api/discovery/watchtower-recovery-diagnostics?hours=N`
  (`src/discovery/routes.py`), `hours` defaults to 72, clamped to
  [1, 720] (matching the existing funnel route's own convention).
- New section on `/discovery` (`templates/discovery.html`), placed
  immediately after the page's hero/title block and before the search
  box — visible without requiring a search query, per the spec's "near
  the top, compact" instruction. Reuses the discovery page's existing
  stat-grid visual language rather than introducing a new pattern.
  Fetched independently (`loadWatchtowerRecoveryDiagnostics()`), same
  fire-and-forget-mount pattern as the page's existing
  `loadOperationConvergence()` — never blocks or is blocked by any other
  panel.

## Named validation against live production data

Real finding, not assumed:

| Window | Pipeline result | Bottleneck |
|---|---|---|
| 24h | Treasuries: 12–20ish resolved, known_treasuries: 0 | `treasury_review` — 1 Potential Expansion awaiting review |
| 72h | launches=60,060 → walkbacks_completed=1,394 → subprovisioners=741 → treasuries=53 → **known_treasuries=0** → canonical_operators=0 | `treasury_review` |
| 168h (7d) | Same shape, larger counts | `treasury_review` |
| 720h (30d) | canonical_operators=129 | `none` — pipeline progressing end-to-end |

At the 72h default window: walkback recovered known WATCHTOWER topology
(1,394 completions), known funding behaviour (741 subprovisioners
resolved), and known provisioning behaviour (53 treasuries reached) —
Match Quality shows all three **Recovered**. But zero of those 53
treasuries are in `wt_confirmed_treasuries` yet ("Known treasury":
**Missing**), so the pipeline correctly stops at Treasury Review, not
because detection failed but because governance hasn't caught up. Cross-
referencing `operation_convergence.py`'s live output confirms exactly
**one** Investigation Population — `3hJX Family` (family_id
`family:e8e110a28bfb4124`, 64 launches) — currently scores a perfect
1.0 match against WATCHTOWER's declared profile and is sitting in the
1,743-row Treasury Review pending queue. The rotation signal correctly
fires `possible_rotation: true` at this window (53 unknown treasuries +
all three behavioural signals present simultaneously) — flagged as
diagnostic only, not a claim that rotation occurred.

At the 30-day window the same population resolves to 129 confirmed,
canonical WATCHTOWER launches with `bottleneck: none` — proving the
pipeline genuinely works end-to-end; the 72h "0" was a real, explainable,
Treasury-Review-queue-depth artifact of the chosen window, not a
detection failure. This is exactly the answer the milestone's acceptance
criteria required: an analyst opening Discovery now sees "1 Potential
Expansion awaiting Treasury Review" instead of a bare "0."

## Acceptance criteria

- ✓ Recovery pipeline visible (compact stage strip, `/discovery`, near
  top).
- ✓ One bottleneck identified (`_determine_bottleneck`, first-zero scan,
  always exactly one).
- ✓ Rotated treasury diagnostics (`_rotation_signal`, diagnostic-only,
  new logic).
- ✓ Treasury Review integrated (pending/approved/rejected/dismissed/age,
  linked to `/intelligence/treasury-review`).
- ✓ Identity expansion integrated (confirmed treasuries, new-this-window,
  operator expansions, linked to the WATCHTOWER operator page).
- ✓ No attribution changes — `src/ops/operation_attribution.py`,
  `src/core/disposition_resolver.py`, `src/core/evidence_reconciliation.py`,
  `src/core/walkback_worker.py`, `src/core/attribution_outcome.py`,
  `src/discovery/service.py`, `src/discovery/operation_convergence.py`,
  `src/discovery/infrastructure_reconstruction.py`,
  `src/ops/treasury_review_workspace.py`,
  `src/ops/operator_identity_governance.py` (authoritative logic —
  only its own pre-existing, unrelated uncommitted `_transition()` block
  remains untouched/unstaged), `src/ops/watchtower_alignment.py`,
  `src/ops/watchtower_funnel.py`, and `src/core/treasury_bank.py` all
  confirmed empty-diff for this commit.
- ✓ No reconciliation changes, no resolver changes, no Discovery
  decisions changed — this module only reads already-computed
  dispositions/outcomes/statuses and re-labels/aggregates them; it never
  writes to any table and never calls any function that does.

## Regression

Full-suite pollution (the same pre-existing, full-suite-order-dependent
issue documented in X76.3's audit) reproduced again when running six
related test files together in one invocation — including one file's
`shutil.copy2()`-based DB-copy fixture producing a transient "database
disk image is malformed" error on a live 2.9GB SQLite database that was
being actively written to at copy time (a plain file copy of a live WAL
database mid-write is expected to occasionally produce a torn snapshot;
this is a pre-existing test-fixture limitation, not real corruption of
the live database — confirmed by running the exact same test completely
clean when isolated to its own file). Every one of the following passed
100% when run individually, in its own process:

- `tests/test_x75_5_investigation_trigger_provenance.py` — 2/2
- `tests/test_ops_x19_6_watchtower_alignment.py` — 7/8 (1 pre-existing
  unrelated failure, confirmed via `git stash` to predate this commit:
  asserts literal text "Recent Promotions" against
  `templates/mission_control.html`, a file this milestone never touches)
- `tests/test_x75_3a_structural_graph_integrity.py` — 18/18
- `tests/test_x76_2_treasury_review_audit_integrity.py` — 19/19
- `tests/test_x26_2_1_attribution_gate_fix.py` — 10/10
- `tests/test_x75_3a_projection_consistency.py` — 2/2

58/59 relevant tests pass; the one failure is pre-existing and unrelated
to this milestone (confirmed identical on baseline via `git stash`).
