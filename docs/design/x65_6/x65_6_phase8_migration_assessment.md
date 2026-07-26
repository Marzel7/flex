# X65.6 — Phase 8: Migration Assessment

No implementation was performed. This phase estimates population
impact using live production numbers (same measurement approach as
X65.5 Phase 8, restated under the Campaign naming and the now-exclusive
three-bucket model from Phase 5A/5B).

## How existing launches populate Campaign

Using the same 7-day, 4,199-launch baseline:

| Bucket | Count | Basis |
|---|---|---|
| `WATCHTOWER` | 1,447 | Every launch with a real `subprovisioners` entry in `wt_attribution_outcomes.evidence_json`, or a `wt_watchtower_launches` row — the mandatory criteria (Phase 3) are already satisfiable from existing fields for this exact set (X65.5 Phase 8 measured this identically) |
| `OTHER_CAMPAIGN` | not yet directly measurable without implementing the classifier — this bucket requires distinguishing "has some funding lineage, but not wrap-close-shaped" from "no lineage at all," which is not a query run in this investigation | — |
| `UNCLASSIFIED` | 2,752 (upper bound; some of this may actually belong to `OTHER_CAMPAIGN` once that split is computed) | Launches with zero `subprovisioners` evidence at all (X65.5 Phase 8's measured figure) |

The exact `OTHER_CAMPAIGN` vs. `UNCLASSIFIED` split within the 2,752
non-WATCHTOWER launches is the one number this task cannot report from
existing measurements alone — distinguishing "some lineage, not
WATCHTOWER-shaped" from "no lineage at all" requires running Phase
3's actual decision logic, which is design-only in this task, not
implemented.

## Whether any new data collection is required

**No.** Every mandatory and confidence-increasing signal in Phase 3's
decision model reads from tables and functions that already exist and
are already populated by live, unmodified detection code:
- `creator_identity` — `src/ops/creator_identity.py`, already runs on
  every Discovery load.
- Wrap-close provisioning evidence — `wt_watchtower_launches` (live
  cascade) and `wt_attribution_outcomes.evidence_json.subprovisioners`
  (walkback), both already written today.
- Fan-out/single-use/not-reused signals —
  `wt_candidate_websocket_watches`, already written by
  `_handle_subprov_tx()` (X65.4 Phase 1), unused only by the *display*
  layer today, not by detection.
- Treasury tier — `src/ops/treasury_resolution.py`, already a
  complete, tested, deployed module (X65.1).

## Whether existing Discovery queries already expose sufficient evidence

**Yes, for the mandatory-criteria and Baseline-confidence tier.**
1,447 of 4,199 launches (34.5%) are classifiable today with zero new
queries — this was independently confirmed in X65.5 Phase 8 by reading
only `evidence_json` and `wt_watchtower_launches`, both already part
of the existing `/api/ops-v2/operational-intelligence` response
pipeline. Confidence-tier refinement (Medium/High, via
`wt_candidate_websocket_watches`) requires one additional read per
launch against an existing table — not a new query pattern, just a new
field added to an existing response object (per Phase 5's non-goal:
additive only).

## Estimated UI impact

- One new stage in the existing numbered cascade (`renderX58Mounts()`)
  — no new mount points beyond one `<div>`, no new CSS framework, no
  new page.
- One new function (`x60CampaignRows()`) inserted into the existing
  filter chain — matches the shape of the five functions already
  there.
- One new breadcrumb row, one new `data-x56-dimension` value handled by
  the *existing*, generic click-binding — zero new JS event listeners.
- One new table column (Campaign) in the launch results table,
  alongside the existing Treasury/Topology/Confidence columns.
- Net new lines of template/JS code: small, bounded, and additive —
  comparable in scope to X65.1's Treasury Resolution panel addition
  (which shipped as a single new mount + two new render functions + one
  new API route).

## Backwards compatibility

- Every existing dimension, mount, filter function, and API field
  remains unchanged (Phase 5/5A/5B).
- URL query-string compatibility: `x56SyncUrl()` is already generic
  over whatever keys exist on `TOPO_SELECTION` — a bookmarked or shared
  Discovery URL from before this change (with no `campaign` param)
  continues to work exactly as today, simply with `campaign` defaulting
  to unset (showing all campaigns) until an analyst explicitly narrows
  by it.
- No existing API consumer (internal or external) breaks, since no
  field is removed or renamed on the response — only added.

## Risks and edge cases

- **`OTHER_CAMPAIGN` boundary ambiguity**: a launch with weak, partial
  funding lineage evidence (e.g. a single incoming transfer with no
  further resolvable structure) sits close to the `OTHER_CAMPAIGN` /
  `UNCLASSIFIED` boundary — Phase 3's rule ("any funding lineage exists
  at all" vs. none) is a reasonable first cut, but the exact threshold
  for "lineage exists" deserves a dedicated pass (not scoped to this
  design task) before implementation, to avoid an arbitrary or
  inconsistent line.
- **Coverage-limited confidence tiers**: per X65.4/X65.5 Phase 5's
  already-documented finding, most of the Discovery population
  (walkback-resolved rather than live-cascade-confirmed) has no
  `wt_candidate_websocket_watches` history at all — most `WATCHTOWER`-
  classified launches outside the live-cascade-confirmed set will land
  at Baseline confidence by default, not because they lack real
  fan-out, but because this specific evidence source has no coverage
  for them (same caveat carried forward from X65.4/X65.5, not
  introduced here).
- **The known Topology-classifier gap (X65.4) is unaffected by this
  task**: a `WATCHTOWER`-classified, high-confidence launch can still
  display an incorrect `Topology` field (e.g. `Linear` despite real
  fan-out) until X65.4's separately-scoped fix ships — this design
  deliberately does not fix that dependency (Phase 5's non-goal), so
  the discrepancy remains visible in the UI, annotated rather than
  hidden (per X65.5 Phase 7's mock-up precedent).
- **`campaign_conserved` must be checked on every load, not assumed**:
  per Phase 5B, the exclusivity guarantee is structural, but any future
  code change to the decision model that accidentally introduces a
  second matching branch (a real risk in any hand-written if/elif
  chain) would silently violate it — carrying forward the existing
  `canonical_behaviour_conserved`/`conserved` precedent's own mitigation
  (a returned boolean, checked, not just assumed) is the concrete
  safeguard against this risk.
