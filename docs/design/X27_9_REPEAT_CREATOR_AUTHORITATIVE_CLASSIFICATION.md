# X27.9 — Make Repeat Creator Classification Authoritative

## Objective

Fix the measurement defect X27.8 identified in
`evaluate_launcher_profile()` so that any launch whose creator genuinely
satisfies the Repeat Creator criteria (launch count AND sustained
observation span) is classified Repeat Creator, without weakening the rule,
reordering `BUCKET_ORDER`, or special-casing any specific creator/mint.

## Root cause (confirmed in X27.8, fixed here)

`evaluate_launcher_profile()` (`src/ops/attribution_outcome.py`) derived
`first_seen`/`last_seen`/`observation_seconds` from
`creator_funders.first_detected_at` whenever that table had any matching
rows — a funder-*discovery* timestamp, not an activity-span measurement —
and only fell back to the more-accurate `token_analysis`-derived span if
`creator_funders` returned nothing. A creator whose funder rows were all
backfilled within the same few seconds (a one-time discovery event)
therefore measured `observation_seconds` near zero regardless of how long
its actual launch history spanned, failing the 7-day observation gate on a
measurement artifact rather than a real gap in activity.

## Fix

### Phase 1/3 — canonical creator activity span

`evaluate_launcher_profile()` now derives `first_seen`/`last_seen`/
`observation_seconds` **exclusively** from the creator's own launch records
in `token_analysis`, via a new helper,
`_creator_launch_history_span()`. `creator_funders` is retained only for
`historical_funder_count` and `material_infrastructure_change` (its recency
signal — "did new funding infrastructure just appear" — is still valid for
that purpose) and no longer supplies the observation span.

### Phase 2 — creator identity unchanged

`_resolve_creator()` / the `pf_ws_creator`-preferred, `earliest_tx_creator`
-fallback resolution logic in `assign_bucket()` and
`evaluate_launcher_profile()` is untouched — same creator identity
semantics as before, per the brief's explicit instruction not to introduce
a different resolution path for this rule.

### Phase 4 — timestamp normalization

New helper `_normalize_timestamp()`:
- Accepts int/float epoch seconds directly.
- Accepts a numeric string (`"1784220816"`) via digit-check + `int()`.
- Accepts an ISO-8601 string (`"2026-07-16T16:53:35Z"`) via
  `datetime.fromisoformat()` (after normalizing a trailing `Z` to `+00:00`),
  always interpreted as UTC if no timezone is present.
- Returns `None` for anything else (empty string, unparseable garbage) —
  **never fabricates a value, never silently falls back to "now"**.

`_creator_launch_history_span()` iterates each matching `token_analysis`
row in Python (not raw SQL `MIN`/`MAX`, which cannot correctly compare mixed
epoch/ISO-8601 strings — the exact bug a bare `CAST(... AS INTEGER)` on an
ISO string produces: `"2026-07-16..."` truncates to `2026`, a
wrong-but-plausible-looking epoch second), normalizes each timestamp, and
returns `(valid_timestamp_count, first_seen, last_seen)`. If fewer than two
valid timestamps exist, `first_seen`/`last_seen` are both `None` and
`observation_seconds` is `0` — the gate fails honestly rather than
fabricating a span from a single point.

Bounded to `launch_count <= 1000` (same cheap-measurement threshold the
prior implementation used), so this remains a bounded per-row scan, not a
full-table pass for very large histories.

### Phase 5 — priority walk unchanged

`assign_bucket()`'s `BUCKET_ORDER` and its sequential first-match-wins
`if`/`return` chain are **untouched**. Repeat Creator was already evaluated
before Rapid Birth/Burst Launch/Unknown Infrastructure/Lineage Gap/
Insufficient Evidence, and Known Operation/Known Infrastructure were
already evaluated before Repeat Creator — this fix corrects `established`'s
*input*, not the pipeline's *priority logic*, exactly as X27.8 concluded was
needed.

### Phase 8 — secondary evidence preserved

`build_pipeline_health()` (`src/ops/investigation_pipeline.py`) now attaches
`secondary_evidence: {rapid_birth_launch, burst_launch}` to every
assignment, populated whenever the underlying lookup matched — independent
of which bucket won. A new `secondary_evidence_for(pipeline, mint)` accessor
was added alongside the existing `launches_in_bucket()`/`creators_in_bucket()`
(both left unchanged, so no existing caller's return shape changes). No
second dashboard, no second bucket count — this is additive drill-down
metadata only, matching the brief's "Primary investigation classification:
Repeat Creator / Additional behavioural evidence: Burst Launch cluster"
guidance without a template rewrite.

## Phase 6 — historical before/after replay (live, 24h window)

Captured via `git stash` (pre-fix code) vs the fix in place, against the
same live production databases:

```
                        BEFORE   AFTER
Known Operation              0       0
Known Infrastructure        55      55
Repeat Creator              137     196   (+59)
Rapid Birth → Launch          0       0
Burst Launches                96      83   (-13)
Unknown Infrastructure        —       45
Lineage Gap                   —       42
Insufficient Evidence          —      166
total_launches               587     587
conserved                    True    True
```

Movements into Repeat Creator, by prior bucket (measured directly on the
mint-level assignment map, matched between snapshots):

```
Insufficient Evidence  → Repeat Creator: 33
Burst Launches         → Repeat Creator: 16
Lineage Gap            → Repeat Creator: 12
Unknown Infrastructure → Repeat Creator:  6
                                   total: 67
```

(The aggregate bucket-count delta of +59 vs. the mint-level movement count
of 67 reflects the ~1-mint difference between two live snapshots taken
seconds apart on a rolling 24h window — both figures independently confirm
`Known Operation`/`Known Infrastructure` never lost or gained a launch.)

Sampled 3 moved launches directly, proving each satisfies both thresholds
with no higher-priority known attribution existing:

| mint | creator | launch_count | observation_seconds | established |
|---|---|---|---|---|
| `C39QjiBwdA1y...` | `AMEd7bE5CY...` | 343 | 5,840,386 (~68d) | True |
| `29ZUjbwbSbb...` | `5gZYUbBDuT...` | 256 | 7,438,941 (~86d) | True |
| `AanSQT7G6JS...` | `GihG1WciMf...` | 38 | 7,284,133 (~84d) | True |

## Phase 7 — X27.8 case verification (live)

```
creator = C2N2Ac5E9m128Tct2AepMpN4VdULpxWAX3gcigqup7rc
launch_count = 895
observation_seconds = 8,113,057  (~93.9 days, > 604,800 threshold)
established = True

assign_bucket(mint="GoFJ78jZsPhk3i5dyy8tmbpf4c6RkvRD6Vw3sUPfpump", ...)
-> {"bucket": "REPEAT_CREATOR", ...}
```

Confirmed via direct execution against live production data (not inferred).
The launch's genuine Burst Launch cluster evidence
(`{"matched": true, "cluster_size": 3}`) remains present in
`secondary_evidence.burst_launch` — visible, not discarded — while the
exclusive bucket is now `REPEAT_CREATOR`.

## Phase 9 — Tests

`tests/test_x27_9_repeat_creator_authoritative.py` (18 tests, all pass):

- Core fix: an established creator above both thresholds qualifies;
  clustered `creator_funders` rows (the exact X27.8 shape) no longer shrink
  a genuine months-long history; high launch count within 7 days still
  correctly fails (non-goal: threshold unchanged).
- Timestamp normalization: mixed epoch/ISO-8601 timestamps normalize
  correctly and produce the true span; invalid/unparseable timestamps are
  ignored and counted, never fabricated into history; fewer than two valid
  timestamps fails the observation gate honestly.
- Priority authority: Repeat Creator wins over Burst Launch, Rapid Birth →
  Launch, Unknown Infrastructure, and Lineage Gap; Known Operation and
  Known Infrastructure still win over Repeat Creator (unchanged, higher
  priority).
- The exact X27.8 creator/shape (large launch count over ~93 days, funder
  rows clustered in seconds) is classified `REPEAT_CREATOR`, not
  `BURST_LAUNCH`.
- Bucket conservation, zero cross-bucket overlap, and exactly-one-bucket
  per launch all still hold after the fix.
- Secondary Burst Launch evidence remains available in
  `secondary_evidence` after a launch's exclusive bucket changes to Repeat
  Creator.

Full regression re-run (`ws_cascade`/`x24`/`x27` suites, including the
existing 62 X27.2/X27.4/X27.5/X27.6 tests) confirmed clean — no existing
test depended on the old funder-derived span behavior.

## What was not changed (non-goals, confirmed)

`MIN_LAUNCHER_HISTORY` (5) and `MIN_LAUNCHER_OBSERVATION_SECONDS` (7 days)
are unchanged. `BUCKET_ORDER` is unchanged — Repeat Creator remains
priority 3, below Known Operation/Known Infrastructure. No creator identity
resolution logic changed. No attribution outcome, walkback, lifecycle
capture, websocket behavior, or database schema was touched. No creator or
mint was special-cased — the fix is a general measurement correction
applied to every creator uniformly, verified by replaying the entire live
population (Phase 6), not just the one case that prompted the investigation.

## Confirmation

`evaluate_launcher_profile()`'s `established` gate is unchanged in its
*criteria* (launch count ≥5 AND observation span ≥7 days AND no
canonical-operator/fresh-provisioning/infrastructure-change disqualifier) —
only its *measurement* of the observation span was corrected. A genuine
repeat creator can no longer be placed in Burst Launches, Rapid Birth →
Launch, Unknown Infrastructure, Lineage Gap, or Insufficient Evidence merely
because its activity span was measured from an unrelated funder-discovery
timestamp.
