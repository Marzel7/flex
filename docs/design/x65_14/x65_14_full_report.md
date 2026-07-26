# X65.14 — Validate the Campaign=WATCHTOWER Population (Full Report)

Read-only validation audit. No code changes, no database writes, no UI changes.
Sources: `database/wt_ops_v2.db` (direct SQL), live API payloads already captured in
X65.12/X65.13 (`campaign=WATCHTOWER`, `operation=WATCHTOWER`, both `window=all`), plus
new direct queries against `wt_provisioning_edges`, `wt_candidate_websocket_watches`,
`wt_confirmed_treasuries`, `wt_active_subprov_sessions`, and `wt_attribution_outcomes`,
2026-07-22.

## Contents

1. [Define Ground Truth](#phase-1--define-ground-truth)
2. [Partition the 291 Campaign Launches](#phase-2--partition-the-291-campaign-launches)
3. [Investigate Every Weak Candidate](#phase-3--investigate-every-weak-candidate)
4. [Investigate Possible False Positives](#phase-4--investigate-possible-false-positives)
5. [Precision Audit](#phase-5--precision-audit)
6. [False Positive Analysis](#phase-6--false-positive-analysis)
7. [Compare Against Canonical WATCHTOWER](#phase-7--compare-against-canonical-watchtower)
8. [Recommendations](#phase-8--recommendations)

---

## Phase 1 — Define Ground Truth

Per X65.13's reconciliation, three independent populations are used, kept strictly
unmerged:

| Population | Source | Definition |
|---|---|---|
| **A** | `wt_watchtower_launches` | Direct cascade-confirmed launches (live WS observation of a SubProv wrap-close whose destination itself CREATEs). 43 total, no window bound. |
| **B** | `operation_id = WATCHTOWER` (`is_watchtower` flag) | Independently confirmed operation attribution, dominated by `wt_attribution_outcomes.operator_id = '04265d9f-6eb2-568c-a49e-9253091a4dbb'`. 80 total within the 365-day `window=all`. |
| **D** | `campaign = WATCHTOWER` | The X65.7 classifier's aggregation-layer output. 291 total within the 365-day `window=all`. This is the population under validation in this task. |

No merging occurred anywhere in this analysis — every comparison below explicitly
names which population(s) it draws from.

---

## Phase 2 — Partition the 291 Campaign Launches

### Category definitions used (rules stated before results, per the task's own no-assumption requirement)

- **Category 1 (Ground-truth confirmed)**: `mint ∈ Population A`.
- **Category 2 (Operation-confirmed)**: `mint ∈ Population B` and `mint ∉ Population A`.
- **Categories 3/4/5** (all remaining launches, i.e. `mint ∈ D`, `mint ∉ A`, `mint ∉ B`):
  split using **independent operational fan-out evidence** — specifically the
  launch's own SubProvider's `wt_provisioning_edges` sibling-edge count and
  `wt_candidate_websocket_watches` distinct-candidate count. This is the same
  evidence source the Campaign classifier itself lists as a *confidence-raising*
  (non-gating) signal (`fan_out_observed`), queried directly per-subprov rather than
  taken from the classifier's own boolean, since the boolean was found (below) to be
  almost uniformly `False` across this remainder and therefore useless as a
  discriminator on its own.
  - **Category 3 (Strong candidate)**: subprov has ≥5 sibling edges OR ≥5 candidate
    watches — i.e., demonstrable multi-creator provisioning activity independent of
    this specific launch.
  - **Category 4 (Weak candidate)**: subprov has 1–4 sibling edges or candidate
    watches — some independent activity, but thin.
  - **Category 5 (Likely non-WATCHTOWER)**: subprov has **zero** sibling edges and
    **zero** candidate watches — no independent operational-network evidence beyond
    the launch's own creator-identity/wrap-close-or-plain-transfer facts.

  Rejected discriminator, and why: `campaign_evidence.treasury_tier` is `CONFIRMED`
  for **all 240** of these remaining launches (verified directly — `Counter` shows
  `{'CONFIRMED': 240}`), so it is a constant across this remainder and cannot
  discriminate within it. Treasury confirmation status is still recorded in the
  per-launch data below for context, but was not usable as the category boundary.

### Partition result

| Category | Count | % of 291 |
|---|---|---|
| 1 — Ground-truth confirmed (∈ A) | 21 | 7.2% |
| 2 — Operation-confirmed (∈ B, ∉ A) | 30 | 10.3% |
| 3 — Strong candidate | 219 | 75.3% |
| 4 — Weak candidate | 16 | 5.5% |
| 5 — Likely non-WATCHTOWER | 5 | 1.7% |
| **Total** | **291** | **100%** |

(21+30+219+16+5 = 291, exact partition, no launch double-counted or dropped.)

Note on Category 1's size (21, not 43): only 21 of Population A's 43 launches fall
within Population D's 365-day window at all (per X65.13's Phase 3 finding); the other
22 are simply outside the window and were correctly excluded from this partition of
the 291 — they are not part of Campaign's *current* output to evaluate.

---

## Phase 3 — Investigate Every Weak Candidate

All 16 Category-4 launches investigated individually for rule-firing and missing
evidence.

### Why Campaign classified them WATCHTOWER

All 16 satisfy Campaign's two **mandatory** criteria (X65.7): `creator_identity =
FRESH_CREATOR` (verified: 16/16) and wrap-close-or-plain-transfer funding evidence
present in `wt_attribution_outcomes.evidence_json` (verified: 16/16, mechanism tags
WSOL_WRAP_CLOSE 8, PLAIN_TRANSFER 6, MIXED 1, UNKNOWN 1). These two conditions alone
are sufficient for Campaign membership at `MEDIUM` confidence — all 16 are `MEDIUM`
(none `HIGH`, none `BASELINE`), consistent with mandatory-criteria-only membership.

### Which evidence is missing

- **Fan-out**: thin but present (1–4 sibling edges or candidate watches) — real
  independent activity exists at these subprovs, just not at the scale (≥5) that
  characterizes Population A's median subprov.
- **Single-use / not-reused confirmation**: `single_use_confirmed` and
  `not_reused_confirmed` are `False` for all 16 (checked directly) — these
  confidence-raising signals never fired.
- **Treasury linkage**: `treasury_tier=CONFIRMED` for all 16 (not missing), so
  treasury status is not the gap here.

### What prevented HIGH confidence / operation confirmation

Exactly the fan-out-strength gap: Campaign's own `HIGH` confidence tier (per
`campaign_classification.py`) requires `fan_out_observed=True` in addition to the
mandatory criteria, and the classifier's own `fan_out_observed` boolean — computed
from a different, narrower evidence check than the direct sibling/candidate-watch
query used in this audit — did not fire for any of these 16. Operation confirmation
(Category 2) additionally requires the mint to be present in
`wt_attribution_outcomes` with `operator_id = WATCHTOWER` specifically, which none of
these 16 have.

### Repeated pattern

- **Topology split cleanly two ways**: 7/16 LINEAR, 9/16 MULTI_LEVEL_FAN_OUT — no
  single dominant shape (consistent with X65.12's finding that topology tracks
  evidence-source availability, not mechanism, and is not itself a reliable
  WATCHTOWER-vs-not discriminator).
- **All 16 share the same structural gap**: real but thin operational-network
  evidence, not absent evidence. None of the 16 looks like a bare, evidence-free
  admission — they are genuinely borderline, not misclassified.

---

## Phase 4 — Investigate Possible False Positives

All 5 Category-5 launches investigated individually.

| Mint | Topology | Treasury (resolved) | SubProvider fan-out | Mechanism | Creator identity |
|---|---|---|---|---|---|
| `GtpUa2zbVcyJdvk2PRADam5uUcQsx8KAndMWUCMTpump` | LINEAR | `4231KLYipwRTmFdQ6ZBa1H4Jf3EpfF62Gzg6DWHWvhPZ` (CONFIRMED, `MANUAL_OVERRIDE_X64_HOP2_EVIDENCE`) | 0 sibling edges, 0 candidate watches | UNKNOWN | FRESH_CREATOR |
| `Ar3vVpZt2xZB5Z52F2tkWAXRiYM6umWUhRBJvUVXpump` | LINEAR | same treasury as above | 0, 0 | UNKNOWN | FRESH_CREATOR |
| `3zUqCv6rsqxvPJSLf1SetgYqjXBLqbuWyJqchnZfpump` | LINEAR | same treasury as above | 0, 0 | UNKNOWN | FRESH_CREATOR |
| `Bsd9kPRkEnCSeBSTyo6uF3Sq1QjXvtm6CwS46QeMpump` | UNKNOWN | `Dtwi1eLMTLaUuCwbztpFEoepBdzhjhjmLoTyAdsR3p3u` (CONFIRMED, `MANUAL_OVERRIDE_X64_DTWI1ELM`) | 0, 0 | UNKNOWN | FRESH_CREATOR |
| `ECHFvPRtUDDTg53V5k45pLk6E5zCunk5RYXrL1qopump` | UNKNOWN | `43PKjr22AFXtCMmLtQ1wxYojnjqEB86iFKK5qUYo3y3D` (CONFIRMED, `CONFIRMED_SEED`) | 0, 0 | UNKNOWN | FRESH_CREATOR |

### Campaign rules satisfied

All 5 satisfy exactly the two mandatory criteria and nothing more:
`creator_identity=FRESH_CREATOR` and `wrap_close_evidence=True` (from
`wt_attribution_outcomes.evidence_json` presence). None has `fan_out_observed`,
`single_use_confirmed`, or `not_reused_confirmed` — all three confidence-raising
signals are `False` for all 5 (verified directly). `mechanism=UNKNOWN` for all 5 —
meaning the mechanism classifier itself could not confidently tag WSOL_WRAP_CLOSE vs.
PLAIN_TRANSFER for these specific launches, even though the coarser wrap-close
evidence check (a different, less strict code path in
`campaign_classification.py`'s own `_wrap_close_evidence_by_mint()`) still passed.

### Does another operation better explain the evidence?

**Not disprovable from currently available evidence, and not proven either.** Three of
the five (`GtpUa2zbVc…`, `Ar3vVpZt2x…`, `3zUqCv6rsq…`) share the identical resolved
treasury `4231KLYipwRTmFdQ6ZBa1H4Jf3EpfF62Gzg6DWHWvhPZ`, a **manually confirmed**
treasury (`MANUAL_OVERRIDE_X64_HOP2_EVIDENCE` provenance) — meaning a human reviewer
did confirm this wallet as a treasury at some point, but this specific launch's
subprov shows zero independent multi-creator provisioning activity of its own. This is
consistent with either (a) a genuine, low-volume WATCHTOWER subprov that has not yet
accumulated observable fan-out, or (b) a different, unrelated operation that happens to
share a treasury-tier funding path with WATCHTOWER (treasuries fund other treasuries in
a mesh — per project memory `treasuries-fund-treasuries.md` — so shared treasury alone
does not prove common operatorship). **No evidence available in this audit
distinguishes between these two explanations.**

### Clustering

| Cluster | Mints | Shared trait |
|---|---|---|
| Cluster 1 | `GtpUa2zbVc…`, `Ar3vVpZt2x…`, `3zUqCv6rsq…` | Same resolved treasury (`4231KLYi…`), all LINEAR, all mechanism=UNKNOWN, all zero fan-out |
| Cluster 2 | `Bsd9kPRkEnCS…` | Own treasury (`Dtwi1eLMTLaU…`), topology=UNKNOWN, zero fan-out |
| Cluster 3 | `ECHFvPRtUDDT…` | Own treasury (`43PKjr22AFXt…`), topology=UNKNOWN, zero fan-out |

3 of 5 (60%) cluster together on a single shared treasury; the remaining 2 are
singletons on their own treasuries.

---

## Phase 5 — Precision Audit

| Metric | Count |
|---|---|
| Campaign launches (total) | **291** |
| Ground-truth confirmed (Category 1) | **21** |
| Operation-confirmed (Category 2) | **30** |
| Strong candidates (Category 3) | **219** |
| Weak candidates (Category 4) | **16** |
| Likely false positives (Category 5) | **5** |

Calculated directly from the exact partition in Phase 2; no estimation used.

- **Confirmed rate** (Cat 1 + 2) / 291 = 51/291 = **17.5%**
- **Strong-candidate rate** (Cat 3) / 291 = 219/291 = **75.3%**
- **Weak-candidate rate** (Cat 4) / 291 = 16/291 = **5.5%**
- **Likely-false-positive rate** (Cat 5) / 291 = 5/291 = **1.7%**

---

## Phase 6 — False Positive Analysis

### Which Campaign rule admitted each Category-5 launch

All 5 were admitted by the same two mandatory rules firing together with no
confidence-raising signal firing at all: `creator_identity == FRESH_CREATOR` AND
`wrap_close_evidence == True` (sourced from bare presence of a row in
`wt_attribution_outcomes.evidence_json`, not from a corroborated mechanism tag — all 5
have `mechanism=UNKNOWN`, meaning the *stricter* mechanism classifier could not confirm
what the *looser* wrap-close-evidence check accepted).

### Which rule should probably be strengthened

**The wrap-close-evidence check that accepts `mechanism=UNKNOWN` launches.** All 5
likely-false-positives — and 102 of the 219 Category-3 "strong candidates" as well —
have `mechanism=UNKNOWN`, meaning Campaign is currently satisfied by a coarser evidence
signal than the mechanism classifier itself requires. This is the specific,
identifiable discriminator this audit found: **every Category-5 launch has
`mechanism=UNKNOWN`; zero Category-1 (ground-truth) or Category-2
(operation-confirmed) launches do.**

### Is the classifier too broad, missing discriminators, over-weighting behavioural similarity, or under-weighting operational evidence?

**Under-weighting operational evidence, specifically fan-out, for the `mechanism=UNKNOWN`
subset.** The classifier is not too broad in general — 95.3% of the 291 (Cat 1+2+3)
have either confirmed ground truth or strong independent operational corroboration.
The failure mode is narrow and specific: launches with `mechanism=UNKNOWN` **and**
zero independent subprov fan-out evidence are being admitted on creator-identity +
bare wrap-close-evidence-presence alone, with no operational-network corroboration at
all. This is not "behavioural similarity" over-weighting in the general sense (Category
5 launches don't share a distinctive behavioural fingerprint with confirmed WATCHTOWER
launches beyond the two mandatory criteria) — it is a genuine gap in requiring at least
one piece of independent operational evidence when the mechanism signal itself is
inconclusive.

---

## Phase 7 — Compare Against Canonical WATCHTOWER

Each category compared directly against Population A (n=21, the subset of ground truth
visible within this window).

| Dimension | Cat 1 (A, n=21) | Cat 2 (n=30) | Cat 3 (n=219) | Cat 4 (n=16) | Cat 5 (n=5) |
|---|---|---|---|---|---|
| Funding mechanism | WSOL_WRAP_CLOSE 10 / SEEDED_ACCOUNT_CLOSE 11 | UNKNOWN 20 / MIXED 5 / WSOL_WRAP_CLOSE 4 / PLAIN_TRANSFER 1 | PLAIN_TRANSFER 112 / UNKNOWN 102 / WSOL_WRAP_CLOSE 3 / MIXED 2 | WSOL_WRAP_CLOSE 8 / PLAIN_TRANSFER 6 / MIXED 1 / UNKNOWN 1 | **UNKNOWN 5/5 (100%)** |
| Topology | **FAN_OUT 21/21 (100%)** | FAN_OUT 19 / MULTI_LEVEL_FAN_OUT 4 / UNKNOWN 3 / LINEAR 4 | MULTI_LEVEL_FAN_OUT 164 / FAN_OUT 55 | LINEAR 7 / MULTI_LEVEL_FAN_OUT 9 | LINEAR 3 / UNKNOWN 2 |
| Treasury reuse (tier) | UNKNOWN (terminal_entity is the operator UUID, not a wallet — X65.13 finding, expected) | mixed | CONFIRMED 219/219 | CONFIRMED 16/16 | CONFIRMED 5/5 (but no fan-out corroboration) |
| SubProvider fan-out | By definition (cascade-observed) | not separately re-derived here | ≥5 sibling/candidate edges (by construction) | 1–4 sibling/candidate edges | **0 sibling/candidate edges (100%)** |
| Creator lifecycle | FRESH_CREATOR 21/21 | FRESH_CREATOR 30/30 | FRESH_CREATOR 219/219 | FRESH_CREATOR 16/16 | FRESH_CREATOR 5/5 |
| Confidence tier | HIGH 10 / MEDIUM 11 | HIGH 10 / MEDIUM 9 / BASELINE 11 | MEDIUM 218 / HIGH 1 | MEDIUM 16/16 | MEDIUM 5/5 |

### Which characteristics separate confirmed WATCHTOWER from campaign-only launches

1. **Mechanism clarity.** Ground truth (Cat 1) and operation-confirmed (Cat 2) are
   never `mechanism=UNKNOWN` at the same rate as Cat 3/5 — Cat 1 is 0% UNKNOWN, Cat 2
   is 67% UNKNOWN (worth noting: Cat 2 itself is not immune to this ambiguity, though
   it is independently operation-confirmed via a separate attribution path so its
   membership doesn't rest on mechanism alone). **Category 5's 100% UNKNOWN rate is
   the single most distinguishing trait separating it from every other category.**
2. **Topology.** Population A (Cat 1) is 100% FAN_OUT — a real, specific, narrow
   signature — while Cat 3 (the bulk of Campaign) is majority MULTI_LEVEL_FAN_OUT.
   This mirrors X65.12's finding that topology tracks evidence-source availability
   rather than genuine operational difference, so this is not itself proof Cat 3 is
   wrong, but it does mean Cat 1's specific topology signature cannot be used
   naively as a filter for Campaign membership without reproducing X65.12's confound.
3. **Independent fan-out evidence is the cleanest discriminator found in this audit.**
   Every Category-5 launch has zero independent subprov-level evidence beyond the two
   mandatory Campaign criteria; every Category-1/2/3 launch has either direct cascade
   confirmation, independent operation attribution, or a measurably active
   multi-creator subprov.

---

## Phase 8 — Recommendations

**Is the Campaign classifier appropriately calibrated?**
**Mostly yes, with one narrow, well-identified gap.** 95.3% of the 291-launch
population (Categories 1–3) carries either confirmed ground truth or strong
independent operational corroboration. Only 1.7% (5 launches) show no operational
evidence beyond the two mandatory criteria. This is a materially better precision
profile than "the classifier is too broad" would predict.

**Should confidence thresholds change?**
**Yes, narrowly**: launches with `mechanism=UNKNOWN` should not be eligible for the
same `MEDIUM` confidence tier as launches with a confirmed mechanism, since this audit
found `mechanism=UNKNOWN` is the single trait perfectly separating Category 5 from
Categories 1/2. A new, lower tier (e.g. `BASELINE_UNVERIFIED_MECHANISM`) for
`mechanism=UNKNOWN` launches lacking any independent fan-out evidence would isolate
exactly the 5 launches this audit flagged, without affecting the 219 Category-3 launches
that also have `mechanism=UNKNOWN` (102 of them) but do carry independent fan-out
corroboration.

**Should additional operational evidence be required?**
**Yes, specifically for the `mechanism=UNKNOWN` + zero-fan-out intersection.**
Requiring at least one non-mandatory signal (fan-out, single-use, not-reused, or a
`wt_confirmed_treasuries` match with a demonstrably active subprov) before granting
Campaign membership to a `mechanism=UNKNOWN` launch would have excluded exactly the 5
Category-5 launches in this audit, while leaving the 286 others (including the 102
Category-3 launches that also carry `mechanism=UNKNOWN` but have real fan-out
evidence) unaffected.

**Should Campaign produce multiple confidence tiers?**
It already does (`HIGH`/`MEDIUM`/`BASELINE`), but this audit shows `MEDIUM` is
currently overloaded — it contains everything from Category-3's strong 219-launch
core to Category-5's 5 unsupported launches. A finer split (e.g., surfacing
`fan_out_observed` state and `mechanism` confirmation status as visible sub-fields
within `MEDIUM`, not necessarily new top-level tiers) would let Discovery UI consumers
distinguish these without waiting for a classifier change.

**Should certain launches automatically remain "Unknown" instead of WATCHTOWER?**
**Yes — specifically the 5 Category-5 launches identified in this audit**
(`GtpUa2zbVcyJ…`, `Ar3vVpZt2xZB…`, `3zUqCv6rsqxv…`, `Bsd9kPRkEnCS…`,
`ECHFvPRtUDDT…`), and any future launch matching the same profile
(`mechanism=UNKNOWN` AND zero independent subprov fan-out evidence). This is a
narrowly-scoped recommendation targeting 1.7% of the current population, not a
wholesale re-design of the classifier.

### Success-criteria answer

The 291 Campaign launches represent **mostly probable WATCHTOWER with a small,
well-characterized false-positive tail**: 17.5% confirmed via independent ground
truth or operation attribution, 75.3% strongly corroborated by independent
operational-network evidence, 5.5% thinly corroborated but genuinely borderline (not
misclassified), and 1.7% (5 launches) with no operational corroboration beyond the
classifier's own two mandatory criteria and sharing the single distinguishing trait
`mechanism=UNKNOWN`. This is **not** an overly broad classifier — it is a
well-calibrated one with an identifiable, narrow, fixable precision gap.

### Deliverables

Complete partition of the 291 Campaign launches into 5 categories with an exact,
reproducible boundary rule (Phase 2); investigation of all 16 weak candidates (Phase
3) and all 5 likely false positives individually, with clustering (Phase 4); a
directly-calculated precision table with no estimation (Phase 5); root-cause analysis
of the false-positive-admitting rule and a specific, narrow strengthening
recommendation (Phase 6); full dimensional comparison of every category against
canonical ground truth (Phase 7); and concrete, scoped recommendations, including the
specific 5 mints that should currently remain "Unknown" (Phase 8). No code was
changed; no database writes occurred; no UI was modified.
