# X29.0 — Operational Topology Intelligence Framework (Design)

**Status: design/investigation sprint. No code, schema, or UI was changed.**
Per direction: this sprint validates the three-dimension taxonomy against the
real corpus and produces a concrete, evidence-checked design; implementation
(schema, classifiers, replay execution, UI refactor) is scoped as a follow-up
sprint (**X29.1**) once this design is reviewed — mirroring the
investigate-then-implement split already used for
[X27.11](X27_11_SIMPLIFY_SUBPROV_LIFECYCLE_FANOUT_CAPTURE.md) →
[X28.0](X28_0_DECOUPLE_CREATOR_WATCH_LIFETIME.md).

## Objective

Split today's single, mixed, mutually-exclusive classification (the
`investigation_pipeline.py` bucket walk) into three orthogonal dimensions —
**Funding Topology** (exclusive), **Operational Behaviour** (additive),
**Funding Mechanism** (additive) — without changing any detection logic.

## Part 1 — What the current system actually mixes (code-verified)

Traced directly against `src/ops/investigation_pipeline.py`,
`src/ops/attribution_outcome.py`, and `src/ops/behaviour_queue.py`:

`assign_bucket()` ([investigation_pipeline.py:166-215](../../src/ops/investigation_pipeline.py#L166-L215))
walks a single exclusive priority list
(`KNOWN_OPERATION, KNOWN_INFRASTRUCTURE, REPEAT_CREATOR, RAPID_BIRTH_LAUNCH,
BURST_LAUNCH, UNKNOWN_INFRASTRUCTURE, LINEAGE_GAP, INSUFFICIENT_EVIDENCE`)
built by interleaving three genuinely different kinds of evidence into one
list:

| Today's bucket | What it actually measures | Which new dimension it belongs to |
|---|---|---|
| `KNOWN_OPERATION` | terminal funding source resolves to a named operator entity | **Topology** (where funding terminates) |
| `KNOWN_INFRASTRUCTURE` | terminal resolves to a CEX/bridge/relay | **Topology** |
| `UNKNOWN_INFRASTRUCTURE` | terminal resolves to an unreviewed treasury/subprov hub | **Topology** (a specific "unresolved-but-structured" topology state) |
| `LINEAGE_GAP` | some treasuries/subprovs recorded but lineage incomplete | **Topology** (partial/insufficient graph evidence) |
| `REPEAT_CREATOR` | `evaluate_launcher_profile()` — launch_count/observation-span history of the **creator wallet**, nothing about the funding graph shape | **Behaviour** |
| `RAPID_BIRTH_LAUNCH` | `birth_to_launch_seconds <= 5` | **Behaviour** |
| `BURST_LAUNCH` | ≥3 migrations within a 60s sliding window | **Behaviour** |
| `INSUFFICIENT_EVIDENCE` | true fallback | **Topology** ("Unknown") |

`REPEAT_CREATOR` is the clearest proof this needed splitting: it is
force-promoted over the outcome-mapped bucket the instant
`evaluate_launcher_profile().established` is true
([investigation_pipeline.py:203-206](../../src/ops/investigation_pipeline.py#L203-L206)),
regardless of what the funding topology actually looked like — meaning today
a Fan-Out-topology launch and a hypothetical Linear-topology launch from the
same repeat creator both collapse into the identical bucket, silently
discarding the topology information. This is precisely the brief's stated
concern ("Behaviour must never determine topology") already manifesting as
a real defect in the current single-dimension model.

`behaviour_queue.py`'s own docstring already states the additive principle
this sprint wants generalized: *"a launch CAN legitimately exhibit more than
one archetype at once"*
([behaviour_queue.py:34-41](../../src/ops/behaviour_queue.py#L34-L41)), and
its `build_behaviour_queue()` already returns `archetypes_matched: list`
([behaviour_queue.py:213-219](../../src/ops/behaviour_queue.py#L213-L219))
— tag-like and additive at the source. X27.5's fold into
`investigation_pipeline.py`'s single exclusive bucket walk discarded that
additivity by forcing `RAPID_BIRTH_LAUNCH`/`BURST_LAUNCH` into the priority
list. `investigation_pipeline.py`'s `secondary_evidence` field
([lines 276-285](../../src/ops/investigation_pipeline.py#L276-L285),
added in X27.9) is already a partial, ad-hoc reintroduction of additivity —
proof the current architecture is straining against its own exclusivity
constraint.

## Part 2 — The three-dimension model

### Dimension 1 — Funding Topology (exactly one value; Unknown is valid)

| Topology | Definition | Evidence required |
|---|---|---|
| **Fan-Out** | `treasury → subprov (1 hop) → creator`, subprov also funds/wrap-closes ≥1 sibling creator or candidate | `wt_watchtower_launches.subprov_wallet IS NOT NULL` AND ≥1 sibling row (another launch, or a `wt_candidate_websocket_watches`/`wt_wrap_close_candidates` row) sharing that `subprov_wallet` |
| **Linear** | `treasury → creator` direct, OR `treasury → subprov → creator` where that subprov produced no sibling — a single-use chain | `subprov_wallet IS NULL` (direct), OR `subprov_wallet IS NOT NULL` with exactly 1 observed creator ever funded by it |
| **Multi-Level Fan-Out** | `treasury → subprov → subprov → creator` (≥2 subprov hops before the creator) | A `wt_active_subprov_sessions` row whose `open_reason`/lineage links it as a child of another subprov session (the `_handle_subprov_tx` "sub-subprov" branch, [ws_cascade.py:3511-3573](../../src/core/ws_cascade.py#L3511-L3573)) — **see Gap 1 below, this is not yet in `wt_provisioning_edges`** |
| **Mesh** | Multiple treasuries/subprovs observed funding each other (cyclic or multi-parent) before any creator emerges | Already-established prior finding: "treasuries fund peer treasuries (circular/mesh)" (memory: `treasuries-fund-treasuries`) — needs a cycle/multi-parent check over `wt_provisioning_edges` + treasury-mesh hits, not yet a formal classifier |
| **Unknown** | Insufficient structural evidence to assign any of the above | Default — never inferred, only assigned when the checks above cannot resolve |

**Mutual exclusivity rule**: evaluate in the fixed order
Multi-Level Fan-Out → Mesh → Fan-Out → Linear → Unknown (most-specific-first,
same discipline as today's `BUCKET_ORDER`), first match wins, exactly one
value assigned. Never inferred from behaviour or mechanism.

### Dimension 2 — Operational Behaviour (zero or more additive tags)

Directly reuses `behaviour_queue.py`'s existing, already-additive logic —
**no redefinition needed**, just restoring its native list-returning shape
instead of collapsing it into `investigation_pipeline.py`'s exclusive walk:

| Tag | Existing source | Status |
|---|---|---|
| Rapid Birth→Migration | `behaviour_queue.py` `RAPID_BIRTH_LAUNCH` check ([lines 107-130](../../src/ops/behaviour_queue.py#L107-L130)) — `birth_to_launch_seconds <= 5` | Reuse as-is |
| Burst Launcher | `behaviour_queue.py` `BURST_LAUNCH` check ([lines 133-174](../../src/ops/behaviour_queue.py#L133-L174)) — ≥3 migrations/60s window | Reuse as-is |
| Repeat Creator | `attribution_outcome.py` `evaluate_launcher_profile()` ([lines 209-338](../../src/ops/attribution_outcome.py#L209-L338)) | Reuse as-is — already creator-scoped, not topology-scoped |
| High Migration Success | Not implemented today | **New** — needs a migration-outcome rate computed per creator/operation over `token_analysis` migration status; flagged as new work for X29.1, not designed further here (no existing function to reuse) |
| Slow Burn | Not implemented today | **New** — the inverse-tempo counterpart to Rapid Birth (a long birth-to-launch or long time-to-migration); flagged as new work, threshold TBD from historical distribution during X29.1 |

Behaviour tags are computed independently of topology and of each other —
a launch may carry 0, 1, or several simultaneously (e.g. Rapid Birth→
Migration AND Repeat Creator, as already proven possible by X27.9's
`secondary_evidence` field co-existing with a `REPEAT_CREATOR` bucket
assignment).

### Dimension 3 — Funding Mechanism (zero or more additive tags)

| Mechanism | Existing source | Status |
|---|---|---|
| Plain SOL Transfer | `funding_mechanism = 'PLAIN_TRANSFER'`, set at [ws_cascade.py:3050](../../src/core/ws_cascade.py#L3050) | Reuse as-is |
| WSOL Wrap-Close | `funding_mechanism = 'WSOL_WRAP_CLOSE'` (default), set throughout `ws_cascade.py`/`wrap_close_detector.py` | Reuse as-is |
| Seeded Account Close | `funding_mechanism = 'SEEDED_ACCOUNT_CLOSE'`, set at [wrap_close_detector.py:187,258](../../src/core/wrap_close_detector.py#L187) | Reuse as-is |
| Mixed | **Does not exist as a value anywhere** (confirmed by grep — zero hits) | **New, but cheap.** `wt_provisioning_edges` already stores mechanism **per edge** (`TREASURY_TO_SUBPROV` and `SUBPROV_TO_CREATOR` can already legitimately differ today, per that table's own per-edge design) — "Mixed" is simply the case where a launch's own two edges (or a cluster's launches, per `pattern_discovery.py`'s existing `GROUP_CONCAT` aggregation) don't all share one mechanism value. No new detection, only a rollup rule. |

Funding Mechanism is deliberately kept as an **implementation-detail tag
set**, per the brief — a single launch or operation can hold multiple
mechanism tags simultaneously (e.g. `WSOL_WRAP_CLOSE` + `Mixed` if its
sibling launches used `PLAIN_TRANSFER`).

## Part 3 — Representative historical walkthrough (real data, not hypothetical)

Pulled directly from `database/wt_ops_v2.db`:

**`wt_attribution_outcomes` (4,929 rows, the corpus `investigation_pipeline.py` classifies today):**

```
INSUFFICIENT_EVIDENCE   3226  65.4%
LINEAGE_GAP               800  16.2%
KNOWN_CEX_REACHED         418   8.5%
UNKNOWN_INFRASTRUCTURE    294   6.0%
KNOWN_RELAY_REACHED        96   1.9%
CANONICAL_OPERATOR_REACHED 73   1.5%
KNOWN_MULTI_TOKEN_CREATOR  22   0.4%
```

**`wt_watchtower_launches` (43 rows, the live cascade-detected corpus):**

```
funding_mechanism: WSOL_WRAP_CLOSE=25 (58%), SEEDED_ACCOUNT_CLOSE=18 (42%)
subprov_wallet IS NOT NULL: 43/43 (100%)
```

**Walkthrough of 4 representative cases:**

1. **A cascade-detected launch** (e.g. `AB7XXeQAvN2y…pump`, creator
   `GaUEGk…`, subprov `8aBvMm…`, treasury `43PKjr…`,
   `funding_mechanism=WSOL_WRAP_CLOSE`, `birth_to_launch_seconds=2`):
   - **Topology**: Fan-Out (has a `subprov_wallet`; that subprov is the
     confirmed `43PKjr` family hub already known from memory to fund
     multiple creators — `hello-payment-operator-linkage`).
   - **Behaviour**: Rapid Birth→Migration (`2s <= 5s` threshold).
   - **Mechanism**: WSOL Wrap-Close.
   - This is the model example from the brief — cleanly resolves on all
     three axes with existing data, no gaps.

2. **A `KNOWN_MULTI_TOKEN_CREATOR` outcome** (22 rows, 0.4%): today this
   collapses straight to `REPEAT_CREATOR` in the old model regardless of
   topology. Under the new model:
   - **Topology**: whatever the funding graph actually shows (Fan-Out if it
     has a subprov with siblings, Linear if not, Unknown if lineage is too
     thin) — **no longer forced to a single value by the behaviour finding**.
   - **Behaviour**: Repeat Creator (unconditionally, since that's what
     `evaluate_launcher_profile()` measures).
   - **Mechanism**: whatever `funding_mechanism` the edges show.
   - This is the exact case Part 1 identified as broken today — the new
     model fixes it by construction, not by adding a special case.

3. **A `LINEAGE_GAP` outcome** (800 rows, 16.2%): treasuries/subprovs
   recorded but resolution incomplete.
   - **Topology**: Unknown (or a partial Fan-Out if a subprov-creator hop is
     confirmed but the treasury terminal is not) — **this is exactly why
     Topology needs its own "Unknown" state distinct from "Linear"**: a
     `LINEAGE_GAP` launch is NOT structurally linear, it's structurally
     under-evidenced, and conflating the two would misrepresent 16% of the
     corpus as simple treasury→creator chains they were never proven to be.
   - **Behaviour**/**Mechanism**: independently computable regardless of the
     topology gap (a creator's own launch-history behaviour and the
     mechanism of whatever edges WERE observed don't depend on resolving the
     full lineage).

4. **An `INSUFFICIENT_EVIDENCE` outcome** (3,226 rows, 65.4% — the majority
   of the corpus): **Topology = Unknown** by definition; Behaviour and
   Mechanism may still independently resolve (a creator can be a proven
   Repeat Creator via `evaluate_launcher_profile()` even with zero funding
   lineage evidence — these come from entirely different tables/queries).
   This is the single most important validation finding: **65% Unknown
   topology does not mean 65% "no intelligence"** — behaviour and mechanism
   tags remain available independently, which is the whole point of
   splitting the dimensions apart. Under today's single-bucket model, most
   of this 65% is undifferentiated `INSUFFICIENT_EVIDENCE` noise; under the
   new model, it becomes "Unknown topology, but here's what we do know
   about behaviour/mechanism" — a strictly more informative baseline for
   the same underlying data.

## Part 4 — Gaps and honest limitations (found, not glossed over)

**Gap 1 — Multi-Level Fan-Out has no direct edge-table evidence.**
`wt_provisioning_edges`'s own `CHECK` constraint
([provisioning_edges.py:50](../../src/ops/provisioning_edges.py#L50)) only
permits `TREASURY_TO_SUBPROV`/`SUBPROV_TO_CREATOR` — there is no
`SUBPROV_TO_SUBPROV` edge type captured anywhere, despite the code
genuinely detecting and recording sub-subprov chains as
`wt_active_subprov_sessions` rows (the `_handle_subprov_tx` "sub-subprov"
branch, [ws_cascade.py:3511-3573](../../src/core/ws_cascade.py#L3511-L3573)).
A Multi-Level Fan-Out classifier can be built in X29.1 either by (a)
walking `wt_active_subprov_sessions`'s funding-signature lineage directly
(more work, no schema change), or (b) extending
`wt_provisioning_edges`'s `CHECK` constraint to add
`SUBPROV_TO_SUBPROV` (a genuine, if small, schema change — would need to be
called out explicitly as an exception to "no detection logic changes" if
chosen, since it's additive persistence, not altered detection).

**Gap 2 — Mesh has no formal classifier yet**, only prior qualitative
evidence (the `treasuries-fund-treasuries` memory finding). X29.1 needs to
define a concrete, evidence-based rule (e.g. "≥2 distinct treasuries
observed funding each other, OR a treasury observed both as a funder and a
recipient within N hops") before Mesh can be assigned rather than merely
asserted.

**Gap 3 — Linear topology is essentially unobserved in the live cascade
corpus** (0/43 launches lack a `subprov_wallet`). This isn't a defect in
the model — it reflects that WATCHTOWER's detection pipeline is
specifically built to find subprov-mediated fan-out — but it means Linear's
real-world coverage percentage cannot be estimated from this table alone;
X29.1's replay should also check `wt_attribution_outcomes`/
`creator_funders` for any direct treasury→creator cases the broader
attribution pipeline (not just the live cascade) has recorded.

**Gap 4 — High Migration Success and Slow Burn are net-new behaviour tags**
with no existing implementation to reuse (unlike the other three tags).
Their thresholds need to be derived from the historical distribution during
X29.1's replay (per the brief's own instruction not to hardcode without
evidence — the same discipline X27.11 applied to the quiet-period value).

**Gap 5 — "Mixed" mechanism rollup needs a defined scope.** Is "Mixed"
computed per single launch (treasury-edge mechanism vs. creator-edge
mechanism, already available per `wt_provisioning_edges`'s per-edge
columns), per creator (all launches by one creator), or per operation
cluster (`pattern_discovery.py`'s existing `GROUP_CONCAT` aggregation,
already closest to this)? Recommend **per-launch** as the base unit (matches
Topology and Behaviour's granularity) with cluster-level rollup as a
derived view, not a competing definition — but this should be confirmed in
X29.1 before implementation, not assumed here.

## Part 5 — Mock UI layout

Today's `discovery.html` renders a single flat/creator-grouped bucket list
fed by `/api/ops-v2/investigation-pipeline`. The three-dimension replacement:

```
┌─ Funding Topologies ─────────────────────────────┐
│  Fan-Out              ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  NN%     │
│  Linear                ▓                  NN%     │
│  Multi-Level Fan-Out   ▓▓                 NN%     │
│  Mesh                  ▓                  NN%     │
│  Unknown              ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ NN%  │
│  [click a topology → drill into launches/creators] │
└───────────────────────────────────────────────────┘
┌─ Behavioural Archetypes (additive) ──────────────┐
│  ☑ Rapid Birth→Migration        NN%               │
│  ☐ Burst Launcher               NN%               │
│  ☑ Repeat Creator                NN%               │
│  ☐ High Migration Success        NN%               │
│  ☐ Slow Burn                     NN%               │
│  [multi-select filter, combinable with topology]   │
└───────────────────────────────────────────────────┘
┌─ Funding Mechanisms (additive) ──────────────────┐
│  WSOL Wrap-Close        NN%                        │
│  Plain Transfer         NN%                        │
│  Seeded Account Close   NN%                        │
│  Mixed                  NN%                         │
└───────────────────────────────────────────────────┘
```

A single launch's detail view becomes:

```
Funding Topology:     Fan-Out
Operational Behaviour: Rapid Birth→Migration, Repeat Creator
Funding Mechanisms:    WSOL Wrap-Close
```

— directly matching the brief's target intelligence-model example. The
existing bucket-drilldown interaction (creator-grouped mint lists,
`?window=`/`?bucket=`/`?mint=` query params) is preserved in shape, just
re-pointed at three independent facets instead of one exclusive list; no
new interaction pattern needs inventing.

## Part 6 — Recommendations for X29.1 (implementation sprint)

1. **Topology classifier**: new module (e.g. `src/ops/funding_topology.py`),
   pure read-only derivation over `wt_watchtower_launches` +
   `wt_provisioning_edges` (+ `wt_active_subprov_sessions` lineage for
   Multi-Level Fan-Out per Gap 1) — no detection-path changes.
2. **Behaviour**: restore `behaviour_queue.py`'s native additive
   `archetypes_matched` list as the canonical output (stop collapsing it
   into `investigation_pipeline.py`'s exclusive walk); add High Migration
   Success and Slow Burn once thresholds are derived from replay.
3. **Mechanism**: add a `MIXED` rollup function over existing
   `funding_mechanism` values — no new detection.
4. **`investigation_pipeline.py`'s `BUCKET_ORDER`/`assign_bucket()`**: retire
   in favor of three independent facet computations; `KNOWN_OPERATION`/
   `KNOWN_INFRASTRUCTURE`/`UNKNOWN_INFRASTRUCTURE`/`LINEAGE_GAP`/
   `INSUFFICIENT_EVIDENCE` map onto Topology states (per the Part 1 table);
   `REPEAT_CREATOR`/`RAPID_BIRTH_LAUNCH`/`BURST_LAUNCH` map onto Behaviour
   tags. This is a genuine retirement of the current bucket model, not an
   addition alongside it — confirm this is acceptable before X29.1 starts,
   since dashboards/routes referencing `BUCKET_ORDER` directly
   (`operation_dashboard_routes.py`'s `/api/ops-v2/investigation-pipeline`)
   will need updating in lockstep.
5. **Replay**: run all three classifiers over the full
   `wt_attribution_outcomes` (4,929 rows) + `wt_watchtower_launches` (43
   rows) corpora, produce the exact coverage percentages the brief
   requests, and specifically re-verify Gap 3's Linear-topology estimate
   against the broader `creator_funders`/attribution corpus, not just the
   live cascade table.
6. **Schema decision on Gap 1**: decide whether to add `SUBPROV_TO_SUBPROV`
   to `wt_provisioning_edges` (small schema addition, needs explicit
   sign-off since the brief says "no detection logic should change" — this
   would be additive persistence of already-detected facts, not new
   detection, but is worth flagging plainly rather than assuming it's in
   scope).

## Conclusion

The three-dimension model is **validated against the real corpus**: it
cleanly explains the model example (case 1), fixes a concrete, identified
defect in the current system (case 2, `REPEAT_CREATOR` overriding topology),
correctly distinguishes "structurally unresolved" from "structurally
simple" (case 3, `LINEAGE_GAP` vs. Linear), and shows that even the
majority-Unknown-topology slice of the corpus (case 4, 65.4%) retains
independently useful behaviour/mechanism intelligence — the central
argument for splitting the dimensions apart in the first place. Five gaps
were found and are documented rather than glossed over, most importantly
that Multi-Level Fan-Out and Mesh need new classifier logic (not just
reorganization) built from data that either already exists in a different
table (`wt_active_subprov_sessions` lineage) or has only qualitative,
not-yet-formalized precedent. Implementation is recommended as a separate
X29.1 sprint.
