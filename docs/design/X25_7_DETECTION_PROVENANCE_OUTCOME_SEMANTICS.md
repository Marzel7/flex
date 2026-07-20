# X25.7 — Detection Provenance Outcome Semantics

Status: Implemented. Wording-only change to `templates/discovery.html`'s
`detectionReconciliation()` and `analystSummary()` functions. No backend
file was touched in this sprint — `src/ops/detection_reconciliation.py`
(classification logic), `src/ops/operation_identity.py`, and
`src/ops/attribution_outcome.py` are unchanged from the state X25.5.1 left
them in.

---

## Phase 1 — Purpose of Detection Provenance (confirmed)

The section answers exactly one question:

> **What did the detection pipeline successfully establish for this launch?**

Explicitly confirmed it does **not** answer:
- what operation owns the launch (Operation Identity's job);
- what infrastructure was reached (Infrastructure Attribution's job, `outcome()`);
- what the funding relationship chain looked like in detail (Funding Walkback's job, `walkback()`);
- whether a specific internal mechanism ("walkback") executed — since every
  migrated launch undergoes the same retrospective process, this is not a
  discriminating fact and was the actual defect this sprint fixes.

## Phase 2 — Semantics of every provenance state (defined before any wording was written)

| State | Known fact | Missing evidence | Evidence completeness |
|---|---|---|---|
| `LIVE_DETECTED` | Live cascade caught the launch at CREATE time (`detection_source` in `LIVE_STREAM`/`ACTIVE_CATCHUP`) | None | Complete |
| `RECONCILED` | A launch record exists via backfill/replay, not the live cascade | Live-timing evidence | Complete, but not live |
| `WALKBACK_RECOVERED` | Full funding lineage established after the fact (gated on `WATCHTOWER_CONFIRMED` outcome, per X25.5.1); no live-armed session ever covered the window | Live-timing evidence | Complete |
| `PIPELINE_INCONSISTENCY` | Full funding lineage established after the fact; a live-armed session *did* cover the window and still missed it | Live-timing evidence (should have existed, doesn't) | Complete (a detection gap, not an evidence gap) |
| `WALKBACK_OBSERVED` | A funding fragment exists but the outcome is not confirmed (`LINEAGE_GAP`/`NON_WATCHTOWER`/etc.) | The rest of the funding chain | Partial |
| `WALKBACK_INCONCLUSIVE` | A funding fragment exists; no walkback-queue record at all | Everything — can't even judge partial vs. complete | Inconclusive |

This table was derived directly from `detection_reconciliation.py`'s actual
branching logic (re-read in full before writing any wording), not assumed.

## Phase 3 — Before / after wording

| State | Before (process-centric) | After (outcome-centric) |
|---|---|---|
| Section label, `WALKBACK_RECOVERED` | "Walkback Recovered" | "Lineage Established" |
| Section label, `PIPELINE_INCONSISTENCY` | "Pipeline Inconsistency" | "Detection Gap" |
| Section label, `WALKBACK_OBSERVED` | "Walkback Observed" | "Partial Evidence" |
| Section label, `WALKBACK_INCONCLUSIVE` | "Walkback Inconclusive" | "Evidence Inconclusive" |
| `WALKBACK_RECOVERED` explain | "This launch belongs to a confirmed operation lineage, established by retrospective walkback rather than live detection. No live-armed session covered this launch at the time..." | "A complete funding lineage was established for this launch after the fact. No live detection covered this launch at the time." |
| `PIPELINE_INCONSISTENCY` explain | "...established by retrospective walkback rather than live detection. A live-armed session covered this launch at CREATE time and still missed it. This is a detection-pipeline gap..." | "A complete funding lineage was established for this launch after the fact, even though live detection was actively watching at CREATE time and still missed it. This is a detection gap, not an evidence gap." |
| `WALKBACK_OBSERVED` explain | "A partial funding relationship was reconstructed during walkback, but the available evidence was insufficient to establish confirmed operation lineage..." | "Partial funding lineage was established for this launch, but the available evidence is insufficient to confirm the complete lineage." |
| `WALKBACK_INCONCLUSIVE` explain | "A funding relationship fragment was reconstructed for this launch, but no walkback record exists to determine whether operation lineage was confirmed..." | "Available evidence is insufficient to establish funding lineage for this launch, and no record exists to judge how complete that evidence is." |
| Analyst Summary (`WALKBACK_RECOVERED`/`PIPELINE_INCONSISTENCY`) | "Recovered retrospectively by walkback, not live detection." | "Complete funding lineage established after the fact; not live detected." |
| Analyst Summary (`WALKBACK_OBSERVED`) | "A funding relationship was reconstructed by walkback, but confirmed operation lineage was not established." | "Partial funding lineage established; complete lineage not confirmed." |
| Analyst Summary (`WALKBACK_INCONCLUSIVE`) | "A funding fragment was observed; membership could not be determined." | "Available evidence is insufficient to establish funding lineage." |

**Justification for every change**: every "before" string named the
mechanism ("walkback", "reconstructed during walkback", "walkback record")
as the subject of the sentence. Since every migrated launch undergoes this
same retrospective process, naming it carries zero discriminating
information for the analyst — two launches with identical evidence
completeness read identically regardless of whether one calls it "walkback
recovered" and the other "walkback observed." The "after" wording instead
names the *evidential result* (complete / partial / inconclusive funding
lineage, live vs. not-live detection), which is the actual variable fact
across launches.

One deliberate exception, judged case-by-case rather than blanket-removed:
the `plain_transfer_associated` footnote still says "...which is why
walkback (a retrospective chain-history scan) could resolve it when live
detection could not." This single remaining mention names the mechanism
only to explain, inline and self-contained, why live detection specifically
failed for this launch (a plain-transfer funding hop doesn't emit the
program logs the live subscription watches for) — this is evidential
content (why evidence exists in the retrospective form it does), not
process narration, and a reader never needs prior knowledge of the
platform's architecture to understand it, satisfying Phase 6's readability
bar.

## Phase 4 — Evidence completeness levels

Confirmed the three levels (Complete / Partial / Inconclusive) already
exist as a natural partition of the six classification values (see the
Phase 2 table) — no new backend state was invented. `LIVE_DETECTED`,
`RECONCILED`, `WALKBACK_RECOVERED`, and `PIPELINE_INCONSISTENCY` all
represent *complete* evidence (the difference between them is live-vs-not
and, for the pipeline case, whether a detection gap occurred);
`WALKBACK_OBSERVED` is *partial*; `WALKBACK_INCONCLUSIVE` is *inconclusive*.
The wording now expresses these three levels with textually distinct
headline phrases ("complete funding lineage" / "partial funding lineage" /
"insufficient to establish funding lineage... judge how complete") so an
analyst can tell them apart without cross-referencing.

## Phase 5 — Independence audit

Grepped the full rewritten `explain`/label object for every term owned by
another Discovery section: no mention of "operator," "WATCHTOWER,"
"operation identity," "treasury mesh," "relay," "bridge," "exchange,"
"infrastructure boundary," "provisioned," "observed_only," "birth," or
"creator history" appears anywhere in the six rewritten states — confirmed
by automated test (`test_no_operator_assumptions_in_any_state` and three
sibling tests in `test_x25_7_provenance_outcome_semantics.py`).

## Phase 6 — Analyst readability

Each rewritten message was read in isolation as an analyst would encounter
it, with no assumed prior knowledge of platform internals:
- *"A complete funding lineage was established for this launch after the
  fact. No live detection covered this launch at the time."* — clearly
  states what is known (lineage, complete) and what didn't happen (no live
  catch), with no jargon.
- *"Partial funding lineage was established for this launch, but the
  available evidence is insufficient to confirm the complete lineage."* —
  clearly distinguishes partial from complete without naming a mechanism.
- *"Available evidence is insufficient to establish funding lineage for
  this launch, and no record exists to judge how complete that evidence
  is."* — an honest, self-contained statement of not-knowing.

None of the three requires the reader to know "walkback" is part of the
platform's architecture.

## Phase 7 — Regression tests

`tests/test_x25_7_provenance_outcome_semantics.py` — 18 new tests covering:
classification values unchanged, process language removed from primary
explain text, the one legitimate mechanism footnote still gated correctly,
the three evidence-completeness levels textually distinct, full
independence audit (no operator/operation/infrastructure/launch-profile
assumptions), no "unusual/rare/exception" framing, section-label rewrite
verification, and a direct backend-source-inspection test confirming
`classify_walkback_confirmed_launches()`'s gating logic (the
`_CONFIRMED_WALKBACK_OUTCOMES` constant, the `WALKBACK_OBSERVED`/
`WALKBACK_INCONCLUSIVE` states) is unchanged from X25.5.1.

Additionally updated 8 pre-existing tests across
`test_x25_6_operator_neutral_semantics.py`,
`test_x25_5_1_membership_gating_fix.py`,
`test_x24_1_discovery_reconciliation_rendering.py`,
`test_x24_8_attribution_semantics.py`, `test_x25_2_launch_profile.py`, and
`test_x25_3_semantic_clarity.py` — each had hard-asserted process-centric
wording strings this sprint intentionally superseded; each was updated to
assert the new outcome-centric wording while preserving the test's
original intent (e.g. "membership fact and entry-path fact stated
separately" still holds, just with different exact phrasing).

Full related regression suite: **155/155 passing.**

## Explicit confirmation

- **No detection logic changed** — `src/ops/detection_reconciliation.py` was not modified in this sprint; `git diff` shows zero changes to this file beyond what X25.5.1 already delivered.
- **No walkback behaviour changed** — `src/core/walkback_worker.py`, `src/ops/provisioning_edges.py` untouched.
- **No operation identity logic changed** — `src/ops/operation_identity.py` untouched.
- **No attribution logic changed** — `src/ops/attribution_outcome.py` untouched.

Only `templates/discovery.html` was edited: the `cfg`/`explain` objects
inside `detectionReconciliation()`, its preceding comment block, and five
`lines.push(...)` string literals inside `analystSummary()`.
