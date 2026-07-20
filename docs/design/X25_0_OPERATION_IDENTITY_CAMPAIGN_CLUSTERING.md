# X25.0 — Operation Identity & Campaign Clustering (Design Only)

Status: DESIGN ONLY. No code, schema, or UI changed by this document.

Data sources: `database/wt_ops_v2.db` — `wt_watchtower_launches` (43 rows,
41 with timing data), `wt_treasury_funders` (per-treasury upstream funder
ledger), `wt_confirmed_treasuries` (58 total, 7 represented in confirmed
launches), `wt_discovered_subprovs` (1,226). Continues directly from X24.9's
measured finding that treasury reuse (71%) is the only real discriminator
among creator/subprov/treasury reuse. All claims below are measured against
these tables; where the population is too small to support a claim, that
limitation is stated rather than papered over.

**Explicit response to the sprint's closing instruction — treasury was
challenged, not assumed:** the evidence below shows treasury is necessary
but **not sufficient** as the Operation anchor. A pure single-treasury model
is rejected in favor of a treasury-funding-mesh cluster (§1, Candidate B),
because two of the seven treasuries in this population are demonstrably
funded by a third, confirmed treasury before their own first launch —
exactly the topology that a single-treasury anchor would incorrectly split
into three separate "operations."

---

## 0. Population size caveat (read this before the rest)

The confirmed-launch population is **43 launches across 7 distinct
treasuries**, with per-treasury launch counts of 15, 13, 7, 4, 2, 1, 1. This
is enough to detect within-treasury mechanism rotation and one funding-mesh
relationship, but it is **not enough to statistically validate a lifecycle
model, a merge/split confidence calibration, or predictive claims** (Phases
5, 6, 8). Where this document proposes a lifecycle stage, confidence tier,
or predictive claim, it is explicitly marked as a **design proposal informed
by n=1–3 observed instances**, not a validated model. Treat Phases 5/6/8 as
the design's most falsifiable, least-tested parts, and prioritize re-running
this analysis once the population grows past roughly 100–150 launches or
15-20 treasuries before trusting the lifecycle/confidence thresholds
operationally.

---

## 1. Phase 1 — What is an Operation?

Tested directly against the five candidates:

**(A) Treasury alone — REJECTED.** A single confirmed treasury
(`G2CQewGxgMrriQ5dRq557neaCVFZzY3bDsvSCBnGewPZ`) was found funding **two
other confirmed treasuries** — `Cgwr5FAa6d39tqJXKgDkxhopgJuuJA6s8bZfZGY9hkTe`
(funded 2000 SOL across 4 transfers, 1781102222–1781109577) and
`43PKjr22AFXtCMmLtQ1wxYojnjqEB86iFKK5qUYo3y3D` (funded 10 SOL, one transfer,
1781129795) — **before either funded treasury's own first launch**
(1781490778 and 1781650252 respectively, both ~4-9 days after the funding).
Treating these as three separate operations would be wrong: it is one
capital source expanding into two additional treasury wallets, which then
independently ran their own launch campaigns. A treasury-alone anchor
mechanically produces this over-splitting error whenever a mesh exists.

**(B) Treasury cluster (funding mesh) — ADOPTED, with a precise
membership rule.** An Operation is the **transitive closure of confirmed
treasuries connected by treasury→treasury funding edges recorded in
`wt_treasury_funders`**, where the funding treasury's transfer(s) to the
funded treasury measurably **precede** the funded treasury's own first
launch. This is not "any treasury that ever moved SOL to another treasury"
(that would over-merge on coincidental/CEX-style transfers) — it is scoped
to the funding pattern actually observed: a bulk capital transfer (100s-2000
SOL, matching provisioning-scale amounts, not dust) landing in a wallet that
subsequently becomes a treasury and begins its own launch campaign. Using
this rule, the 7 confirmed treasuries collapse into **5 candidate
Operations**: `{G2CQew, Cgwr5FAa6d, 43PKjr22AFXtCMmL}` (one operation, 3
treasuries, 1+2+4=7 launches), `{DchJquEZzM}` (1 treasury, 15 launches),
`{9hGcxVHFaj}` (1 treasury, 13 launches), `{Dtwi1eLMTL}` (1 treasury, 7
launches), and `{43PKjr22AFXtSXqE}` (1 treasury, 1 launch — the vanity-
prefix collision with the unrelated `43PKjr22AFXtCMmL` correctly resolved
by full-address comparison, per existing `vanity-family-attribution`
guidance: never cluster on truncated/displayed prefixes).

**(C) Treasury + behaviour — PARTIALLY ADOPTED, as the fingerprint (§4), not
the identity.** Behavior (funding mechanism, cadence, capital range) is
essential for *distinguishing* and *fingerprinting* an operation once
identified, but it should not be the *identity* itself: `DchJquEZzM`
switches its own funding mechanism outright (WSOL_WRAP_CLOSE → 
SEEDED_ACCOUNT_CLOSE, §3) mid-campaign while remaining unambiguously the
same treasury/operation. If behaviour were part of the identity, this single
operation would incorrectly split into two.

**(D) Treasury + infrastructure fingerprint — REJECTED as identity, kept as
fingerprint input.** Per X24.9 §4.2/4.3, the only "infrastructure reuse"
signal found so far in this population (Axiom, Raydium Authority V4) was a
false-positive artifact of the walkback recurring-funder heuristic, not a
genuine shared operational asset — using it as part of the *identity* would
import noise directly into Operation membership. It remains valid as one
input to the Operation Fingerprint (§4) once scoped correctly (X24.9 §1.3).

**(E) Something else entirely — not supported by the data.** No signal
measured (timing, capital range, migration behaviour) discriminates better
than the treasury-funding-mesh; all of them are properties *of* a mesh-
identified operation, useful for the fingerprint, not substitutes for the
anchor itself.

**Canonical definition adopted:**

> An **Operation** is the transitive closure of confirmed treasuries
> connected by treasury→treasury bulk-capital funding edges that measurably
> precede the funded treasury's own first launch. A treasury with no such
> edge (incoming or outgoing) is a single-treasury Operation of size 1.

---

## 2. Phase 2 — Historical clustering

Clustering was attempted along every listed axis against the 43-launch,
7-treasury population:

| Axis | Result |
|---|---|
| Treasury | Forms 7 groups; correctly captures per-treasury cadence but over-splits the confirmed mesh (§1) into 3 |
| Treasury funding mesh | Forms 5 groups (see §1); the only axis that correctly captures the known mesh without also merging unrelated treasuries |
| Subprov | Forms 43 groups (1 per launch) — subprov is disposable per-launch infrastructure, confirmed by X24.9 (0% reuse) and by this investigation (subprov count == launch count for every treasury, exactly, §3). **Not usable for clustering at all.** |
| Funding graph (creator/subprov edges) | Degenerates to the same 43 singleton groups as subprov, for the same reason |
| Infrastructure (terminal-entity overlap) | Produces large false clusters (e.g. every launch that happens to route through Axiom) — this is the exact false-positive class X24.9 §4.2/4.3 already identified; rejected as a clustering axis on its own |
| Timing (inter-launch gaps) | Does not itself cluster launches across treasuries (no evidence two different treasuries' launches interleave in a shared cadence); useful only as a per-operation *fingerprint* dimension, not a grouping key |
| Migration (`create_to_migration_secs`) | Sparse (only 5/43 rows populated) — insufficient data to cluster on this axis at all in the current population |
| Capital movement (`subprov_funding_sol`) | Forms tight per-treasury bands (see §4) but does not by itself link separate treasuries — useful as fingerprint, not identity |
| Behaviour (funding_mechanism) | Same as capital movement: distinguishes phases *within* an operation (§3) rather than linking separate treasuries |

**Conclusion: only the treasury-funding-mesh axis produces stable
operational groups that agree with independently-known evidence** (the
`G2CQew→Cgwr5FAa6d`/`G2CQew→43PKjr22AFXtCMmL` chain matches the funding
chain already recorded in memory as `G2CQew→5JWii73→GPTWGW→creator→launch`).
Every other axis either degenerates to singleton launches (subprov, funding
graph) or produces obviously-wrong over-merges (raw infrastructure overlap).

---

## 3. Phase 3 — Stability

**Does a treasury remain stable?** Yes, as a wallet identity: every launch
under a given treasury address uses that exact address, with no observed
mid-campaign treasury address change within a single operation's launch
sequence.

**Does it rotate?** The *treasury wallet* does not rotate within an
operation in this data — but the treasury's **funding mechanism does**.
`DchJquEZzM…` (15 launches) ran 4 `WSOL_WRAP_CLOSE` launches
(735-770 SOL fundings, chronologically first), then switched entirely to
`SEEDED_ACCOUNT_CLOSE` for its next 10 launches (funding flagged at exactly
`1.0 SOL` — clearly a different capital-accounting convention, not a
capital-size change), then reverted to a large (620 SOL) funding on its
final observed launch. This is a genuine **within-operation technique
rotation**, not a treasury rotation — the operation survived the mechanism
change because the treasury wallet identity was the invariant, not the
funding mechanism.

**When it rotates, does the operation survive? How?** In this dataset, the
treasury *wallet* never rotates away entirely (no observed "old treasury
retires, brand new treasury inherits its launches" event in the confirmed
set) — only the *technique* rotates while the wallet persists. The Operation
survives because Operation identity (§1) is anchored on the wallet-level
funding-mesh graph, which is unaffected by the operation switching its
downstream funding mechanism.

**Can one operation have multiple treasuries?** Yes — confirmed directly:
the `{G2CQew, Cgwr5FAa6d, 43PKjr22AFXtCMmL}` cluster is one operation
spanning 3 treasury wallets.

**Can one operation have multiple subprovs?** Yes, and in fact **every**
operation does, trivially: subprov count equals launch count for every
single treasury measured (e.g. `DchJquEZzM…`: 15 launches, 15 distinct
subprovs). Subprov multiplicity is not a discovery about a specific
operation — it is a structural constant of how this platform's launches are
built (single-use wrap-close/seeded-close wallets).

**Can one operation have multiple infrastructure paths?** Not yet testable
precisely — genuine in-chain infrastructure reuse (X24.9's boundary-scoped
definition) has n=0 confirmed instances in this population. This should be
re-tested once the population grows.

**What actually persists:** the treasury-wallet identity (and, where a mesh
exists, the funding relationship between treasury wallets). What changes
freely: funding mechanism, per-launch capital amount, subprov identity
(always), timing cadence.

---

## 4. Phase 4 — Operation fingerprint

Components tested for actual discriminating power against the 5 candidate
operations:

| Component | Discriminates? | Evidence |
|---|---|---|
| **Funding mechanism mix** | Yes | `DchJquEZzM` mixes both mechanisms (4 wrap-close, 11 seeded-close); `9hGcxVHFaj` and `43PKjr22AFXtCMmL` are 100% wrap-close; `Dtwi1eLMTL` is 100% seeded-close. This alone separates 4 of the 5 clusters. |
| **Capital range (subprov_funding_sol)** | Yes, per-mechanism | Within `WSOL_WRAP_CLOSE` launches, ranges are tight and distinct per treasury: `9hGcxVHFaj` 597-860 SOL (mean 723.6), `43PKjr22AFXtCMmL` 600-800 SOL (mean 700.0), `DchJquEZzM`'s wrap-close phase 735-770 SOL. Within `SEEDED_ACCOUNT_CLOSE`, `DchJquEZzM` is flagged at a constant 1.0 SOL for 10/11 launches (a clear accounting/technique signature), while `Dtwi1eLMTL` genuinely varies 600-1100 SOL. |
| **Launch cadence (inter-launch gaps)** | Yes, but noisy | `DchJquEZzM` shows a 403-hour (16.8-day) dormancy gap mid-sequence — a real, measurable tempo change. `9hGcxVHFaj` and `Dtwi1eLMTL` show more continuously distributed gaps (5-78 hours) without an equivalent dormancy spike. Useful as a fingerprint dimension, but small-n means single outlier gaps could be noise, not signal — treat cadence as descriptive, not yet as a hard classifier threshold. |
| **Migration timing** | Not testable | Only 5/43 rows populated; insufficient data. |
| **Infrastructure** | Not yet testable | 0 confirmed in-chain infra-reuse instances (X24.9). |
| **Reuse behaviour (subprov reuse across operations)** | No | 0% subprov reuse everywhere; carries zero information in the current population. |
| **Operational tempo / capital recycling** | Partially, via cadence + mechanism together | The `DchJquEZzM` dormancy-then-resume-with-a-mechanism-revert pattern is the clearest composite signal found — a single scalar (cadence alone, or mechanism alone) would have missed it; the fingerprint should be a small **vector**, not one number. |

**Recommended fingerprint (design, not implementation):** a per-operation
vector of `{funding_mechanism_histogram, capital_range_per_mechanism,
launch_count, treasury_count, cadence_summary (median gap + max gap),
mesh_depth (0 for single-treasury, 1+ for funded-treasury chains)}`. This is
a small, fully-measured-today vector — every field above is already
directly computable from `wt_watchtower_launches` and `wt_treasury_funders`
with no new schema.

---

## 5. Phase 5 — Operation lifecycle

**Can WATCHTOWER measure lifecycle stages?** Partially, and only
qualitatively at this population size. The single clearest example
(`DchJquEZzM`, 15 launches) shows a pattern consistent with:

- **Birth/Expansion**: 4 rapid `WSOL_WRAP_CLOSE` launches at large,
  consistent funding (735-770 SOL) within a ~1.2-day span.
- **Peak/steady-state**: a long run of 10 `SEEDED_ACCOUNT_CLOSE` launches at
  a flagged 1.0 SOL funding convention, spanning ~10 days, interrupted once
  by the 403-hour dormancy gap.
- **Possible Extraction/Recycle signal**: the final observed launch reverts
  to a large (620 SOL) funding under the `SEEDED_ACCOUNT_CLOSE` mechanism —
  a break from the preceding 1.0-SOL-flagged pattern, which could indicate a
  final large extraction-style launch, or simply the start of a new phase
  not yet observed further (the data ends here).

**This is one instance, not a validated model.** The proposed stage names
(Birth, Expansion, Peak, Extraction, Dormancy, Recycle, Death) are
**plausible labels for the pattern actually observed in the single richest
example**, not a confirmed general lifecycle. Do not implement a lifecycle
classifier from this alone — the correct next step is to re-run this exact
gap/mechanism-change analysis once 3-5 more high-launch-count treasuries
exist, and check whether the same birth→expansion→dormancy→shift pattern
recurs. "Death" specifically has **zero observed instances** (every treasury
in this set has at least one very recent launch; none has been silent long
enough to confidently call it dead versus merely dormant) and should not be
defined without a chosen dormancy-length threshold backed by data on how
long confirmed-dead operations actually go silent before never resuming
(currently unmeasurable — no such case exists yet).

---

## 6. Phase 6 — Identity confidence, merge/split rules

Given the small-n caveat (§0), this section proposes **rules**, calibrated
against the one observed merge case, rather than statistically fit
thresholds.

**Merge rule (when two treasuries should be considered one Operation):**
1. A recorded `wt_treasury_funders` edge exists from treasury A to
   treasury B, with `total_sol` at provisioning scale (order of 10s-1000s
   of SOL, not dust/rent amounts), AND
2. The funding transfer(s) (`last_seen` in `wt_treasury_funders`) occur
   **before** treasury B's own first launch (`MIN(create_time)` in
   `wt_watchtower_launches`).

Both conditions held for both observed merges (`G2CQew→Cgwr5FAa6d`:
2000 SOL, funded ~4 days before B's first launch; `G2CQew→43PKjr22AFXtCMmL`:
10 SOL, funded ~6 days before B's first launch) — note the second case is
only 10 SOL, far below the first's 2000 SOL, showing the amount threshold
should be a soft signal (favoring merge) rather than a hard cutoff; the
precedence-in-time condition is the load-bearing one.

**Confidence tiers (proposed, not yet statistically calibrated):**
- **CONFIRMED merge**: both conditions above hold, funder is itself a
  confirmed treasury (not just any wallet), and the funded treasury has at
  least one of its own confirmed launches (rules out crediting a mesh
  relationship to a treasury that never actually operated).
- **LIKELY same operation**: a funding edge exists and precedes first
  launch, but the funder is only a discovered-subprov-tier address, not a
  confirmed treasury (lower-confidence source).
- **SPLIT trigger**: a treasury previously merged into an operation begins
  showing a funding mechanism, capital range, and cadence with **zero
  resemblance** to the rest of the operation's fingerprint (§4) for a
  sustained run of launches, AND no further funding edge from the rest of
  the operation is observed during that run. Not yet observed in this
  data — proposed defensively, not evidenced.

These tiers are explicitly **design proposals**, calibrated against a single
observed instance each for merge and (untested) split. They should be
treated as a starting hypothesis to validate, not a finished decision
procedure, until more merge/split cases are observed.

---

## 7. Phase 7 — Operation graph

Proposed first-class object shape (design only, no schema change):

```
Operation
├── operation_id (derived: stable hash of the treasury-mesh's earliest-seen treasury address)
├── treasuries[]            (all wallet addresses in the funding-mesh cluster, §1)
├── mesh_edges[]            (treasury→treasury funding edges that established the merge, §6)
├── subprovisioners[]       (union of all subprov wallets across every launch under every treasury — expected to equal total launch count, not a compressed set, since subprov is disposable)
├── infrastructure[]        (only genuinely in-chain infrastructure reuse, X24.9-scoped — expected empty/sparse today)
├── launches[]              (all wt_watchtower_launches rows under any treasury in this operation)
├── fingerprint             (the §4 vector: mechanism histogram, capital ranges, cadence summary, mesh depth)
├── lifecycle_observations[] (qualitative stage notes per §5, NOT a hard classifier output yet)
└── attribution             (rollup: does this operation's launches ever reach CANONICAL_OPERATOR_REACHED, and which operator_id)
```

This replaces the previous Launch-centric graph (`Creator → Subprov →
Treasury`) with an Operation-centric one where Launch becomes a **child
record of Operation**, not the root. Creator and Subprov remain attributes
of each individual Launch (they are structurally disposable, §3), not
independent nodes in the Operation graph itself.

---

## 8. Phase 8 — Predictive value

**Does identifying Operations improve prediction?** Directionally yes, but
**not yet measurable to a statistical standard** at n=5 operations. What can
honestly be said from the data in hand:

- **Future treasury usage**: an Operation with a demonstrated funding mesh
  (`G2CQew` cluster) has already shown it can expand into new treasury
  wallets — this is a genuine, observed precedent (2 expansions from 1 seed
  treasury) that a pure single-treasury model would have no way to predict
  at all (it would report each new treasury as an unrelated cold-start). This
  is the clearest, most defensible predictive claim this data supports.
- **Future launch cadence**: `DchJquEZzM`'s dormancy-then-resume pattern
  suggests operations can go quiet and come back, which — if the pattern
  recurs — would let a future model avoid prematurely writing off a
  dormant operation as inactive. Currently n=1 instance; not yet
  a validated predictor.
- **Future funding / infrastructure / campaigns**: **not supported by
  current data.** No repeated infrastructure reuse, no second observed
  mesh-expansion event, and no second dormancy-resume cycle exist yet to
  generalize from. Any claim of predictive power here would be
  overstating a single anecdote as a pattern.

**Honest bottom line:** Operation identity already demonstrably improves
*explanation* (it correctly unifies 3 treasuries that a treasury-alone model
would wrongly split, and explains a mechanism change that a behavior-based
identity would wrongly treat as two operations). It has not yet been shown
to improve *prediction* in a statistically meaningful way, because the
current population contains only one instance each of the two candidate
predictive patterns (mesh expansion, dormancy-resume). This should be
re-evaluated once more instances accumulate.

---

## 9. Recommendation — should WATCHTOWER evolve to Operation-centric?

**Yes, with a staged, evidence-gated rollout — not a wholesale replacement.**

The case for Operation-centric architecture is strong on the *structural*
side: subprov is proven disposable (100% single-use, exactly 1:1 with
launches) and cannot anchor anything; treasury alone measurably
over-splits a real mesh; and the Operation-as-funding-mesh model already
explains a within-operation mechanism rotation that a naive per-launch view
cannot express at all.

The case is **not yet strong on the predictive/lifecycle side** (§5, §8) —
those sections are honestly reported as single-instance evidence, not
validated models, and should not be sold internally as "WATCHTOWER now
predicts campaign lifecycle" until re-measured against a larger population.

**What future WATCHTOWER should store as a first-class Operation** (design
only, no schema written here): the object shape in §7, computed as a
**read-only derived view** over existing tables (`wt_watchtower_launches`,
`wt_treasury_funders`, `wt_confirmed_treasuries`) using the merge rule in
§6 — not a new independently-writable table that could drift from the
underlying evidence. Recompute the mesh clustering and fingerprint on each
new confirmed launch, the same way `wt_attribution_outcomes` is already
recomputed per-mint today, so Operation identity never becomes a second,
independently-editable source of truth that can diverge from the treasury/
launch ledger it's derived from.

---

## 10. Explicitly out of scope (confirmed unchanged)

No files under `src/`, `templates/`, or any DB schema were modified to
produce this document. All queries were read-only `SELECT`s against
`database/wt_ops_v2.db`. No renames, no migrations, no UI changes, no new
tables created.
