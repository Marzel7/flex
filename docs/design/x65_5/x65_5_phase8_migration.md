# X65.5 — Phase 8: Migration Strategy

No implementation was performed. This phase estimates, using live
production numbers, how the existing launch population would populate
the new bucket if it were implemented exactly as designed in Phases
2-4.

## Population baseline (live, 7-day window)

- **4,199** total launches with a `wt_attribution_outcomes` row (the
  same population base used throughout this and prior investigations).
- **43** confirmed WATCHTOWER launches (`wt_watchtower_launches`,
  all-time — the live cascade's own confirmed set).
- **442** distinct subprov wallets with at least some
  `wt_candidate_websocket_watches` fan-out history recorded (all-time).

## Launches immediately classifiable (mandatory criteria only, zero new work)

Mandatory criteria (Phase 3) are `creator_identity == FRESH_CREATOR`
plus direct evidence of wrap-close provisioning reaching the creator.
Every launch that already has a `wt_watchtower_launches` row, or a
`wt_attribution_outcomes.evidence_json.subprovisioners` entry backed by
a real `wt_active_subprov_sessions`/wrap-close session, already
satisfies this — no new detection or classifier work is required to
determine membership, only reading fields that already exist.

- **43 of 43** confirmed WATCHTOWER launches are immediately
  classifiable as bucket members at, at minimum, Baseline confidence
  (Phase 3) — this is the entire live-cascade-confirmed population,
  with zero new detection needed.
- **1,447 of 4,199 (34.5%)** of the broader 7-day `wt_attribution_outcomes`
  population have at least one `subprovisioners` entry in their
  evidence — these are also immediately classifiable at Baseline
  confidence with zero new detection, using only the field that
  already exists in `evidence_json`.

## Launches requiring no new detection (only reading already-captured evidence, still zero new code paths)

- **39 of 43 (90.7%)** confirmed WATCHTOWER launches already have real
  `wt_candidate_websocket_watches` fan-out history for their subprov —
  these can be classified at **Medium or High** confidence (Phase 3:
  observable fan-out is a confidence-increasing, not mandatory,
  signal) purely by reading this already-populated table. No new
  detection logic is required — this table is already written by the
  live `_handle_subprov_tx()` detector today (X65.4 Phase 1).
- **7 of 1,447 (0.5%)** of the broader `wt_attribution_outcomes`
  population (those resolved via walkback rather than the live
  cascade) have a subprov with any `wt_candidate_websocket_watches`
  history at all — consistent with X65.4 Phase 5's finding that this
  evidence source overwhelmingly only covers cascade-confirmed
  launches, not the broader walkback-resolved population.

## Launches needing only topology wiring (the X65.4 fix, not yet implemented)

This category is distinct from the above: these are launches where the
bucket's own membership test (Phase 3, independent of `Topology`) would
already work today using existing data, but the **separate**,
previously-identified `funding_topology.py` gap (X65.4) means the
*Topology* field displayed alongside the bucket (Phase 5's "preserve
existing evidence" requirement) would continue to under-report Fan-Out
for these same launches until that fix ships. This does not block the
bucket's own launch — it only means one of the several fields shown
*inside* a bucket member's row (Topology) would remain visibly
incomplete/wrong in the way X65.4 documented, independent of this
task.

- Applies to the same population as X65.4's own finding: of the 43
  confirmed launches, **21 received any Topology classification at
  all**, and **0 of those 21 were correctly labeled `FAN_OUT`** (X65.4
  Phase 4) — these 21 launches would appear correctly inside the new
  WATCHTOWER Provisioning bucket (since bucket membership doesn't
  depend on Topology), but their displayed Topology field would remain
  wrong until X65.4's separately-scoped fix is implemented.

## Launches remaining outside the bucket

- **2,752 of 4,199 (65.5%)** of the 7-day population have **no**
  subprov evidence at all (`evidence_json.subprovisioners` empty) —
  these fail the bucket's mandatory wrap-close-provisioning criterion
  outright and correctly remain outside the bucket. This is not a
  classifier gap; per X65.1/X65.2, a real subset of this population
  has no persisted funding-lineage evidence at all (a separate,
  already-investigated gap, not addressed by this task).
- Launches with a `creator_identity` other than `FRESH_CREATOR` (e.g.
  serial deployers, `UNKNOWN_CREATOR_IDENTITY` under the `HISTORY_ROW_CAP`
  guard) are excluded by the first mandatory criterion, by design —
  this is intentional per Phase 3, not a gap.

## Summary table

| Category | Count | % of 7d population (4,199) |
|---|---|---|
| Immediately classifiable, zero new work (confirmed cascade launches) | 43 | 1.0% |
| Immediately classifiable, zero new work (broader `subprovisioners` evidence) | 1,447 | 34.5% |
| — of which, additionally have fan-out confidence data (no new detection) | 46 (39 cascade + 7 walkback) | 1.1% |
| Would need the separate X65.4 Topology fix for the *Topology field specifically* to display correctly (bucket membership itself unaffected) | 21 (subset of the 43 confirmed) | 0.5% |
| Remain outside the bucket (no subprov evidence at all, or not a fresh creator) | 2,752+ | 65.5%+ |

## Overall conclusion

The canonical bucket, exactly as designed in Phases 2-4, could be
populated for **over a third of the current live population (34.5%)**
using only fields that already exist in `wt_attribution_outcomes` and
`wt_watchtower_launches` — zero new detection, zero new RPC, zero new
walkback logic. Confidence-tier refinement (Medium/High via observed
fan-out) is available today for a much smaller slice (~46 launches)
due to `wt_candidate_websocket_watches`'s current coverage limits
(X65.4 Phase 5) — this is the same, already-documented coverage gap,
not a new one introduced by this design.
