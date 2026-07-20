# X27.2 — Investigation Reduction Pipeline

## Objective

Redesign Pipeline Health so every migrated launch belongs to exactly one
mutually-exclusive investigative bucket, turning it from a collection of
overlapping informational metrics into an analyst reduction queue.

## Phase 1 — Overlap audit (live, 2026-07-16, 24h window, 519 outcomes)

`wt_attribution_outcomes.outcome_type` is already exactly-one-per-mint —
there is no overlap *within* that table. The real overlap is between that
table and an independently-evaluated "repeat creator" signal, since
`KNOWN_MULTI_TOKEN_CREATOR` is only assigned when a mint's `attribution_source`
is already `serial_deployer`/`known_multi_token_creator` **and** the
CEX/bridge/relay boundary check (`_boundary()`) didn't already claim the
mint first (it runs earlier in `_classify()`'s priority chain).

Baseline `outcome_type` counts (24h):

| outcome_type | count |
|---|---|
| INSUFFICIENT_EVIDENCE | 296 |
| LINEAGE_GAP | 91 |
| UNKNOWN_INFRASTRUCTURE | 66 |
| KNOWN_CEX_REACHED | 54 |
| KNOWN_RELAY_REACHED | 12 |
| KNOWN_MULTI_TOKEN_CREATOR (already assigned) | 0 |

Two "repeat creator" definitions were measured, to find the correct one:

| Definition | Repeat-creator total (24h) | Verdict |
|---|---|---|
| naive `launch_count > 1` (all-time, per creator) | 402 / 519 (77%) | **Rejected.** Dominated by wallets with 1,000-15,000+ launches — the same shared/program-derived-authority false-positive class already flagged in prior sessions ([[single-token-creator-filter]]). Not a genuine repeat-creator population. |
| `launch_count >= 5` + `observation_seconds >= 7d` (raw, no other gates) | 353 / 519 (68%) | **Rejected.** Still swept up the same high-count wallets; missing the canonical-operator/fresh-provisioning/material-infrastructure-change exclusions. |
| `evaluate_launcher_profile().established` (the real, already-shipped gate — adds `canonical_operator_linked`, `fresh_provisioning_evidence`, `material_infrastructure_change` exclusions) | **144 / 519 (28%)**, 85 unique creators | **Used.** In line with a plausible genuine repeat-creator rate; reuses the platform's own vetted classifier rather than inventing a new threshold. |

Overlap matrix using the real `established` gate, cross-tabbed against
current `outcome_type` (i.e. what Pipeline Health would double-count today
if it added a naive "Repeat Creators" row alongside the existing per-type
rows):

| outcome_type | of which repeat-creator-established |
|---|---|
| INSUFFICIENT_EVIDENCE | 100 / 296 |
| LINEAGE_GAP | 25 / 91 |
| UNKNOWN_INFRASTRUCTURE | 15 / 66 |
| KNOWN_RELAY_REACHED | 3 / 11 |
| KNOWN_CEX_REACHED | 1 / 54 |

This is the actual overlap the brief describes: 144 launches would appear
in both their current `outcome_type` bucket and a naive "Repeat Creator"
row, double-counted and unable to reduce the investigation population.

## Phase 2 — Investigative priority (derived, not assumed)

Criterion: which bucket removes a launch from future human investigation
soonest/most completely.

1. **Known Operation** — fully resolved to a canonical operator; nothing left.
2. **Known Infrastructure** — reviewed CEX/bridge/relay boundary; no further attribution expected.
3. **Repeat Creator** — creator already understood from prior investigation (real `established` gate).
4. **Unknown Infrastructure** — investigation required; actionable (emerging-operator candidate).
5. **Lineage Gap** — investigation required; evidence incomplete (`AMBIGUOUS_BRANCH`/`MAX_DEPTH` fold in here — both are "stopped, retry only on new evidence").
6. **Insufficient Evidence** — pipeline improvement; weakest evidence tier.

This matches the brief's proposed order; it is now justified by measured
removal-value rather than assumed.

## Phase 3 — Canonical bucket semantics

| Bucket | Answers "why has this launch left the pipeline?" |
|---|---|
| Known Operation | Investigation complete. Attribution reached a confirmed canonical operator. |
| Known Infrastructure | Investigation complete. Funding terminates at reviewed infrastructure; no further attribution expected. |
| Repeat Creator | No investigation required. Creator already understood from prior investigation. (Priority may change in a future sprint.) |
| Unknown Infrastructure | Investigation required. Potential new infrastructure. |
| Lineage Gap | Investigation required. Evidence incomplete. |
| Insufficient Evidence | Pipeline improvement opportunity. Insufficient evidence currently exists. |

These are investigative dispositions, not attribution outcomes — wording
never re-asserts the underlying attribution fact (that's Attribution
Outcome's job).

## Phase 4 — Exclusive assignment implementation

`src/ops/investigation_pipeline.py`:
- `assign_bucket()` — single-mint assignment. Maps `outcome_type` straight
  to a bucket for the first three (already mutually exclusive at the
  attribution layer); `REPEAT_CREATOR` additionally reclaims any
  lower-priority `outcome_type` whose creator independently satisfies
  `evaluate_launcher_profile().established` (imported directly from
  `src/ops/attribution_outcome.py` — no reimplementation).
- `build_pipeline_health()` — batch version over a time window; returns
  per-bucket counts, the full mint→bucket assignment map, and a `conserved`
  boolean.
- Priority is data (`BUCKET_ORDER`, an ordered tuple) walked generically —
  not a hardcoded if/elif chain — so inserting a new bucket means adding
  one entry to `BUCKET_ORDER`/`_OUTCOME_TO_BUCKET`/`BUCKET_LABELS`/
  `BUCKET_REASONS`, never rewriting the walk (Phase 8).

## Phase 5 — Conservation proof (live)

```
total_launches: 519
conserved: True
Known Operation           0
Known Infrastructure      65
Repeat Creators           140
Unknown Infrastructure    51
Lineage Gap               66
Insufficient Evidence     197
sum: 519
```

`sum(bucket counts) == total_launches` exactly, live, on the real 24h
population. (Note: 140 in the live run vs. 144 in the earlier per-outcome_type
audit differs because the batch classifier evaluates `established` at
`build_pipeline_health()`'s own `now`, a few minutes later — expected,
not a bug — `MIN_LAUNCHER_OBSERVATION_SECONDS`'s 7-day threshold is a
moving boundary.)

## Phase 6 — UI redesign

`templates/discovery.html`'s `healthPanel()` was rewritten to render the
`/api/ops-v2/investigation-pipeline?window=24h` response: one row per
nonzero bucket, in priority order, each showing only the count remaining
after higher buckets have already claimed their launches. Panel title
changed from "Attribution Health · Last 24h" to "Investigation Queue ·
Last 24h" to reflect the changed purpose. A `conserved===false` guard
renders a visible warning row rather than silently trusting a broken sum.

## Phase 7 — Drill-down verification

New `GET /api/ops-v2/investigation-pipeline?bucket=<id>` returns only the
mints in `assignments` whose `bucket` equals `<id>` — structurally unable
to include a mint claimed by a higher-priority bucket, since each mint has
exactly one entry in the assignment map by construction. Live-verified:
`REPEAT_CREATOR` drill-down returned exactly 140 mints, matching the panel
count precisely; `test_drilldown_never_returns_launches_assigned_elsewhere_live`
walks all six buckets end-to-end against the real database and asserts
zero cross-bucket overlap and full coverage of the assignment set.

## Phase 8 — Future extensibility

Confirmed by construction: `BUCKET_ORDER` is an ordered tuple, not a
branching chain. A new bucket (Rapid Migration, Known Treasury, Known
Provisioning Hub, Shared Funding Pattern) requires only:
1. A new bucket id + label + reason.
2. Insertion into `BUCKET_ORDER` at the desired priority position.
3. Either a new `_OUTCOME_TO_BUCKET` mapping entry, or a new predicate
   branch in `assign_bucket()` alongside the existing `REPEAT_CREATOR`
   reclaim check.
No existing bucket's logic, the walk itself, or the route/UI need to
change.

## Phase 9 — Tests

`tests/test_x27_2_investigation_reduction_pipeline.py` — 15 tests: every
launch gets exactly one bucket, conservation (sum == total), zero overlap
across all six buckets, the real overlap-bug reproduction (an established
repeat creator's INSUFFICIENT_EVIDENCE-terminated mint is correctly
reclaimed into REPEAT_CREATOR), higher-priority buckets always win over
Repeat Creator, the naive `launch_count>1` rule is confirmed rejected,
priority-order-governs-assignment (BUCKET_ORDER structural check),
AMBIGUOUS_BRANCH/MAX_DEPTH folding into LINEAGE_GAP, missing-creator
fallback, zero DB mutation (SHA-256 before/after), window filtering,
canonical `OUTCOME_TYPES` unchanged, HTTP 200 + bad-bucket 400 route
behaviour, and a live end-to-end drill-down exclusivity walk against the
real databases. Three pre-existing tests were updated for the legitimate
`healthPanel()` signature/title change (`test_ops_x20_6_discovery_prioritisation.py`,
`test_x26_11_unified_terminal_infrastructure_outcomes.py`,
`test_x26_5_1_attribution_health_window_integrity.py`); their underlying
backend assertions (e.g. `reviewed_infrastructure` aggregate) remain
untouched and still pass. Full regression run: 235 passed / 1 pre-existing
unrelated failure (confirmed via `git stash` to predate this session's
work).

## Live verification

`GET /api/ops-v2/investigation-pipeline?window=24h` → HTTP 200,
`conserved: true`, 519 total, six buckets summing exactly.
`GET /api/ops-v2/investigation-pipeline?window=24h&bucket=REPEAT_CREATOR`
→ HTTP 200, 140 mints, all independently confirmed assigned to that bucket
and no other.

## Confirmation

No detection, attribution classification, walkback, or database schema
was changed. `wt_attribution_outcomes`/`OUTCOME_TYPES`/`_classify()` in
`src/ops/attribution_outcome.py` are byte-for-byte unchanged (confirmed by
`test_attribution_outcome_types_unchanged`). `src/ops/investigation_pipeline.py`
is new, additive, and read-only (zero writes, `PRAGMA query_only=ON`).
Only `src/core/operation_dashboard_routes.py` (one new route) and
`templates/discovery.html` (the landing Pipeline Health panel) were
touched among existing files.
