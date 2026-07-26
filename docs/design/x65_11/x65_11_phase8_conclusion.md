# X65.11 — Phase 8: Conclusion

## Do the last 24 hours of WATCHTOWER launches follow the same operational topology as previously confirmed WATCHTOWER launches?

**Yes, with a specific, well-evidenced qualification.** Every launch's
recorded funding path (Phase 2) follows the canonical
Treasury→SubProvider→Creator shape at the two hops evidence actually
covers. 13 of 19 launches (68%) show real, independently-measured
sub-provider fan-out (Phase 3: 5 sub-providers funding 8-68 distinct
creators all-time), directly consistent with the canonical model's
characteristic fan-out signature already established from historical
confirmed launches (X65.4/X65.8/X65.10's 43-launch replay). No launch
in this cohort contradicts the canonical model outright — the
qualification is that the model's specific *Provisioning Wallet* layer
(the middle tier between SubProvider and Creator) is not separately
observable for any of these 19 launches, a coverage gap rather than a
structural contradiction (Phase 2).

## If not, which launches differ?

**6 of 19 launches (32%)** are classified `Diverges` in Phase 5:
`GtpUa2zbVc…`, `5KtNnnPt7x…`, `2bFc6R3Wr8…`, `EnEgmM4Eb6…`,
`3zUqCv6rsq…`, `Ar3vVpZt2x…`. All 6 share the same divergence: their
recorded funding mechanism is `PLAIN_TRANSFER`, not the wrap-close
mechanism the canonical model's provisioning-wallet concept was built
around, and their sub-providers show no recorded creator-fan-out
evidence (0 or 1 creator each). 2 of these 6 additionally have an
unconfirmed treasury.

## Are the differences caused by genuine operational change, incomplete evidence, topology classification behaviour, or another identifiable cause?

Each of the three named causes is evaluated directly against this
cohort's evidence, not assumed:

- **Genuine operational change**: **not supported by this audit's
  evidence.** PLAIN_TRANSFER is not a newly-observed mechanism — this
  project's own standing memory
  (`distribution-funding-two-mode.md`, dated well before this 24-hour
  window) already documents PLAIN_TRANSFER as a recognized, legitimate
  alternative provisioning mechanism used alongside wrap-close. This
  audit found no evidence that today's 6 PLAIN_TRANSFER launches
  represent a *new* pattern rather than a continuation of an
  already-known, already-documented alternative mode.
- **Incomplete evidence**: **confirmed as the dominant cause of the
  Provisioning-Wallet-layer gap** (all 19 launches, not just the 6
  divergent ones) — `wt_candidate_websocket_watches` has zero coverage
  for every sub-provider in this cohort (Phase 2/3), because this
  cohort was resolved entirely via the walkback path
  (`walkback_class=FULL_WALKBACK` for all 19), not the live cascade
  that populates that table. This is the same, already-documented
  coverage boundary established in X65.4/X65.8's own evidence-source
  comparison — not a new finding, but directly re-confirmed here
  against live, current data.
- **Topology classification behaviour**: **partially contributory, but
  correctly so, not a bug.** The observed 1:1 correlation between
  funding mechanism and Topology label (13/13 WSOL_WRAP_CLOSE →
  MULTI_LEVEL_FAN_OUT, 6/6 PLAIN_TRANSFER → LINEAR, Phase 6) reflects
  the walkback evidence genuinely resolving deeper, more-branching
  chains for wrap-close-mediated funding in this specific cohort — the
  classifier is behaving as designed (X65.8/X65.10), reporting exactly
  what the walkback evidence shows, not fabricating a pattern.
- **Another identifiable cause**: **2 of the 6 divergent launches
  additionally share an unconfirmed treasury** (`FkccGTEh6tJe…`) — a
  separate, additional factor for those two specifically, not a cause
  of the other 4 launches' divergence.

## Does the evidence support retaining the current canonical WATCHTOWER topology, or should the model be revised?

**Retain the current canonical model.** The evidence gathered in this
audit:

- Directly confirms the canonical Treasury→SubProvider→Creator shape
  for 100% of this 24-hour cohort at the two hops with available
  evidence.
- Directly confirms real, substantial sub-provider fan-out for 68% of
  the cohort, using the same evidence sources and methodology already
  validated against 43 historical confirmed launches (X65.4/X65.10,
  22/22 matched).
- Finds the 32% "divergence" is fully attributable to a single,
  already-documented alternative funding mechanism (PLAIN_TRANSFER)
  the canonical model's own supporting documentation already
  acknowledges as legitimate — not evidence the model itself is wrong.
- Finds the Provisioning-Wallet-layer evidence gap affects the *entire*
  cohort uniformly (not selectively the divergent launches), confirming
  it is a data-coverage boundary (walkback-resolved vs.
  cascade-confirmed populations), not a model-accuracy problem.

No evidence in this audit supports revising the canonical model. The
identified gap (Provisioning-Wallet-layer coverage for
walkback-resolved launches) is a **data-coverage** finding, not a
**model-correctness** finding — consistent with, and not contradicting,
this project's established audit discipline of distinguishing
"the model is wrong" from "the evidence for testing the model is
incomplete for this specific population."
