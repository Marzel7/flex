# X26.0 — Treasury Hierarchy & Operation Root Discovery

Status: Investigation and design complete. No walkback logic, attribution
logic, Operation Identity resolver, database schema, or UI was changed.
Confirmed by `git diff --stat` showing only this new document.

**Headline finding, stated up front because it governs every phase below:**
the current dataset does **not** support the multi-layer hierarchy
(Root → Intermediate → Launch Treasury → Sub-provisioner → Creator, 5
levels) the background section hypothesizes. **Measured maximum depth is
exactly 1 hop** (Root Treasury → Member Treasury). This is a real,
measured result, not a failure to find the pattern — but it comes from an
extremely small sample (24 total funder-edge rows across 58 confirmed
treasuries; only 4 distinct treasuries have any recorded funder at all).
Every phase below is measured honestly against this sample, with the
sample-size caveat repeated wherever a conclusion depends on it.

---

## Phase 1 — Treasury hierarchy depth (measured, not inferred)

Method: for every confirmed treasury (`wt_confirmed_treasuries`, n=58),
walked the `wt_treasury_funders` graph restricted to funder→treasury edges
where **both** ends are themselves confirmed treasuries (the only edges
that can possibly form a multi-hop chain), with no timing restriction
applied (i.e., broader than X25.4's "funded before first launch" merge
rule, specifically to check whether a deeper chain exists that the merge
rule might exclude).

**Result: only 3 such edges exist in the entire dataset, and they are
identical to the set X25.4 already found under its stricter timing rule.**
Both root treasuries in this 3-edge set (`G2CQewGxgMrriQ5dRq557neaCVFZzY3bDsvSCBnGewPZ`
and `3sStXWrDYHSnHhY1cbjRNR23pF24W9jK6T8LnaP85TMm`) were checked for their
own incoming funder edges — both have **zero** recorded funder rows at all
(not even from unconfirmed wallets), confirming they are genuine dead-ends,
not truncated chains.

| Metric | Value |
|---|---|
| Maximum depth | 1 hop (Root → Member) |
| Minimum depth | 0 hops (58 - 4 = 54 confirmed treasuries have no treasury-to-treasury edge at all — they are isolated) |
| Median depth (among the 4 treasuries with any edge) | 1 hop |
| Average depth (among the 4 treasuries with any edge) | 1 hop |
| Confirmed treasuries with depth ≥2 (any 3-hop-or-deeper chain) | **0** |

**Sample-size caveat (repeated because it is load-bearing for every later
phase):** only 24 total rows exist in `wt_treasury_funders` across all 58
confirmed treasuries, and only 4 distinct confirmed treasuries appear as
the *funded* side of any edge at all. This is not a large enough sample to
rule out deeper hierarchies existing in general — it is a sample too small
to have observed one yet, if one exists. The correct statement is:
**no evidence of depth beyond 1 hop currently exists in the persisted
data**, not "deeper hierarchies do not exist."

## Phase 2 — Treasury role classification (validated against real data)

Tested the four hypothesized roles directly:

| Role | Definition tested | Found in data? |
|---|---|---|
| **Root Treasury** | Never funded by another confirmed treasury; funds other treasuries | **Yes — 2 instances**: `G2CQewGxgMrriQ5dRq557neaCVFZzY3bDsvSCBnGewPZ` and `3sStXWrDYHSnHhY1cbjRNR23pF24W9jK6T8LnaP85TMm` |
| **Intermediate Treasury** | Funded by a confirmed treasury AND also funds other confirmed treasuries | **No instances found.** Every treasury in the 4-node subgraph is either purely a root (funds only) or purely a leaf (funded only) — none does both. |
| **Launch Treasury** | Directly associated with launches (has its own `wt_watchtower_launches` rows) | **Yes**, but not exclusive to non-roots — see below |
| **Pure Funding Treasury** | Funds treasuries only; never launches tokens itself | **Yes — 1 clean instance**: `3sStXWrDYHSnHhY1cbjRNR23pF24W9jK6T8LnaP85TMm` (0 launches, funds 1 treasury) |

**Critical finding: Root and Launch Treasury are not mutually exclusive.**
`G2CQewGxgMrriQ5dRq557neaCVFZzY3bDsvSCBnGewPZ` is simultaneously a Root
Treasury (funds 2 other confirmed treasuries, has no incoming treasury
funder) **and** a Launch Treasury (has 1 launch of its own, directly). A
strict, mutually-exclusive role taxonomy (Root XOR Intermediate XOR Launch
XOR Pure-Funding) does not fit the measured data — the real structure
observed is: some treasuries fund only, some launch only, and at least one
does both simultaneously.

| Treasury | Launches | Funds (treasury count) | Funded by (treasury count) | Measured role |
|---|---|---|---|---|
| `3sStXWrDYHSnHhY1cbjRNR23pF24W9jK6T8LnaP85TMm` | 0 | 1 | 0 | Pure Funding Treasury (Root) |
| `G2CQewGxgMrriQ5dRq557neaCVFZzY3bDsvSCBnGewPZ` | 1 | 2 | 0 | Root Treasury **and** Launch Treasury (both, simultaneously) |
| `Cgwr5FAa6d39tqJXKgDkxhopgJuuJA6s8bZfZGY9hkTe` | 2 | 0 | 2 | Launch Treasury (leaf) |
| `43PKjr22AFXtCMmLtQ1wxYojnjqEB86iFKK5qUYo3y3D` | 4 | 0 | 1 | Launch Treasury (leaf) |

## Phase 3 — Operation hierarchy (built from X25.4's operations)

Of the 4 operations X25.4 already resolved, only 1 has any hierarchy at
all — the other 3 are single-treasury operations (depth 0, trivially "one
root, no children"). For the one multi-treasury operation (`Operation
BB9BB5`):

```
Operation BB9BB5
├── 3sStXWrDYHSnHhY1cbjRNR23pF24W9jK6T8LnaP85TMm  (ROOT, pure-funding, 0 launches)
│     └── Cgwr5FAa6d39tqJXKgDkxhopgJuuJA6s8bZfZGY9hkTe  (2 launches)
└── G2CQewGxgMrriQ5dRq557neaCVFZzY3bDsvSCBnGewPZ  (ROOT, 1 launch)
      ├── Cgwr5FAa6d39tqJXKgDkxhopgJuuJA6s8bZfZGY9hkTe  (2 launches) [same node, multi-parent]
      └── 43PKjr22AFXtCMmLtQ1wxYojnjqEB86iFKK5qUYo3y3D  (4 launches)
```

**This operation has two roots, not one**, both of which independently fund
the same downstream treasury (`Cgwr5FAa6d...`) — a genuine multi-parent
structure, not a strict tree. No cycles were found (confirmed directly: the
3-edge graph has no path returning to any node already visited). No
disconnected sub-trees exist within this operation (all 4 treasuries are
reachable from each other via the undirected mesh, which is definitionally
true since X25.4's connected-components algorithm is what grouped them).

**Answer to Phase 3's question**: among the operations that have any
internal structure at all (1 of 4), the observed pattern is **multiple
roots**, not one. The other 3 operations trivially have exactly one root
(themselves), since they have no internal edges at all.

## Phase 4 — Root stability

Both roots in the one multi-treasury operation are themselves also
launch-bearing (partially, for `G2CQew`) or pure-funding (`3sStXWr`) — there
is no case in this dataset of a root treasury *rotating* to a different
wallet while the operation continued, because no operation in the sample
has been observed to change its root at all. The relevant historical
mechanism-rotation evidence (from X25.0) — `DchJquEZzM` switching from
`WSOL_WRAP_CLOSE` to `SEEDED_ACCOUNT_CLOSE` mid-campaign, including a
16.8-day dormancy gap — occurred entirely **within a single-treasury
operation** (no mesh, no root/member distinction at all), so it measures
"does the treasury wallet stay constant while mechanism/tempo change"
(**yes**, confirmed in X25.0), but it does **not** measure "does the
*root* of a multi-treasury hierarchy stay constant while a lower-level
launch treasury rotates," because no multi-treasury operation in this
dataset has been observed long enough, or with enough internal churn, to
test that specific question. **This is an honest data gap, not a negative
finding** — the question Phase 4 asks cannot be answered from the current
sample; only the narrower single-treasury-wallet-stability question
(already answered in X25.0) can be.

## Phase 5 — Operation Identity model comparison (Model A vs. Model B)

**With only 1 of 4 operations having more than one treasury at all, Model
A (nearest launch treasury) and Model B (highest confirmed root) produce
**identical** operation groupings for 3 of the 4 operations** — a
single-treasury operation has no "nearest" vs. "highest" distinction to
make. The only operation where the models could differ is `Operation
BB9BB5`, and even there, X25.4's existing resolver **already implements
Model B's spirit** — it does not anchor on "nearest launch treasury," it
computes the full connected component and reports the true funding roots
(`3sStXWr`, `G2CQew`) as `ROOT` and the launch-bearing treasuries as
`MEMBER`. **Model A vs. Model B is not actually a live disagreement in
today's implementation** — `operation_identity.py` (X25.4) already behaves
like Model B by construction (transitive closure via connected components),
not like a naive nearest-treasury anchor.

Requested measurements (merge accuracy, false merges, split rate, campaign
stability, predictive usefulness) **cannot be meaningfully computed** at
n=1 multi-treasury operation and 3 single-treasury operations — any
percentage computed from this sample would be a spurious precision claim
(e.g., "100% merge accuracy" from one example proves nothing about the
general case). This is stated honestly rather than manufacturing false
confidence numbers from the sample.

## Phase 6 — Infrastructure relationship to treasury hierarchy

Checked every launch under `Operation BB9BB5`'s 4 treasuries against
`wt_attribution_outcomes` for infrastructure boundary data (relay/bridge/CEX
terminal entities). **Result: no infrastructure-boundary attribution
outcomes exist for this operation's launches at all** — of the 7 launches,
only 1 has any `wt_attribution_outcomes` row, and it is
`CANONICAL_OPERATOR_REACHED` (operator identity, not an infrastructure
boundary). **This phase cannot be answered from the current dataset** —
there is no observed instance of infrastructure appearing above, below, or
alongside a treasury hierarchy to document a pattern from. This is reported
as a data gap, not force-fit into either of the two example patterns the
brief offered.

## Phase 7 — Recommendation

**Evidence-based recommendation: keep the nearest-confirmed-treasury /
transitive-mesh model exactly as X25.4 already built it. Do not introduce
a formal Root/Intermediate/Launch-Treasury role taxonomy as a new
first-class distinction, because:**

1. **Depth doesn't support it.** Measured maximum hierarchy depth is 1 hop.
   A taxonomy designed for a 5-layer chain (per the brief's own background
   example) would have 4 of its 5 proposed layers (Root, the deeper
   Intermediate layers) completely unpopulated in every operation observed
   to date.
2. **The roles aren't mutually exclusive in the one case where they'd
   matter.** `G2CQewGxgMrriQ5dRq557neaCVFZzY3bDsvSCBnGewPZ` is simultaneously
   Root and Launch Treasury — a strict role taxonomy would need to handle
   dual-role treasuries as a documented exception on its very first real
   example, which is a sign the categories don't cleanly carve the actual
   structure.
3. **X25.4's existing resolver already captures what matters.** The
   `ROOT`/`MEMBER` distinction already present in `operation_identity.py`'s
   output (X25.4 Phase 4's object shape) already surfaces exactly the
   structural fact this sprint was asked to investigate — which treasury
   has no incoming qualifying edge. Adding a formal multi-level taxonomy on
   top would add complexity with zero currently-measurable analytical
   benefit, since no operation has depth beyond what `ROOT`/`MEMBER`
   already expresses.
4. **The sample is too small to justify a structural change.** 1
   multi-treasury operation, 3 funder-edge instances, 4 distinct treasuries
   with any recorded funder at all. Any recommendation to add permanent
   schema/model complexity on this sample would be over-fitting to a single
   example.

**This does not mean the underlying architectural question is resolved
forever** — if the confirmed-treasury population grows and multi-hop
chains start appearing, this investigation should be re-run. The
recommendation is scoped to *today's measured data*, not a permanent
architectural verdict.

---

## Deliverables checklist

- **Treasury-depth distribution**: Phase 1 (max 1, median/mean 1 among the
  4 treasuries with any edge, 0 among the other 54).
- **Treasury-role taxonomy**: Phase 2 — validated 3 of 4 hypothesized
  roles exist, found Root/Launch-Treasury are not mutually exclusive, found
  zero Intermediate-Treasury instances.
- **Operation hierarchy diagrams**: Phase 3 — one operation with real
  structure (two roots, multi-parent, no cycles), three trivial
  single-node operations.
- **Root stability analysis**: Phase 4 — the narrower single-treasury-wallet
  question is already answered (stable) by X25.0; the specific
  multi-treasury root-vs-rotating-launch-treasury question cannot be
  answered from this sample (explicit data gap, not a negative finding).
- **Identity model comparison**: Phase 5 — Model A/B are not actually in
  conflict in the current implementation; requested accuracy/false-merge/
  split-rate metrics cannot be honestly computed at this sample size.
- **Infrastructure placement analysis**: Phase 6 — no infrastructure-
  boundary data exists for the one operation with any internal structure;
  explicit data gap.
- **Single evidence-based recommendation**: Phase 7 — retain the current
  X25.4 mesh/`ROOT`-`MEMBER` model unchanged; do not introduce a deeper
  role taxonomy given current measured depth and sample size.

## Explicit confirmation

No walkback logic, attribution logic, Operation Identity resolver code,
database schema, or UI was changed. All measurements were read-only
queries against `database/wt_ops_v2.db` and one invocation of
`src.ops.operation_identity.build_operations()` (already-existing,
unmodified code). `git diff --stat` for this sprint shows only this new
document under `docs/design/`.
