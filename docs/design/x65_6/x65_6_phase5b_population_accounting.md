# X65.6 — Phase 5B: Population Accounting & Reclassification

Extends Phase 5A's exclusivity invariant across time — as new launches
arrive and existing launches' Campaign classification is
re-evaluated with better evidence, the population must remain
mathematically consistent at every point in time, not just as a
single-snapshot check.

## The two distinct events, and how each affects the invariant

| Event | Effect on Discovery Total | Effect on Campaign bucket counts |
|---|---|---|
| **New launch detected** | Increases by exactly 1 | The new launch is assigned to exactly one bucket (Phase 3's decision model, evaluated fresh) — that bucket's count increases by 1; the other two buckets are unaffected |
| **Reclassification** (a previously-classified launch gets new evidence — e.g. a treasury resolves, or the X65.4 Topology-wiring fix ships and produces new fan-out evidence) | **Unchanged** — reclassification never adds or removes a launch from the Discovery population itself | The launch's *previous* bucket count decreases by 1, its *new* bucket count increases by 1 — the two movements are paired, never independent |

## Why this holds by construction, not by a reconciliation step

Phase 3's classifier is a **pure function of a launch's current
evidence state** — `campaign_for(launch_evidence) → {WATCHTOWER,
OTHER_CAMPAIGN, UNCLASSIFIED}` — with no memory of a launch's prior
classification. Re-running it against updated evidence for the same
launch simply produces a (possibly different) single value; it is
mathematically impossible for this kind of function to assign a launch
to two buckets at once, or to zero buckets, at any single evaluation.
This is the same reasoning already relied upon in X65.0
(`canonical_behaviour_for()`) and X65.4/X29.1
(`classify_topology_for_launch()`) — both are re-run in full on every
Discovery page load / API call, and both already tolerate a launch's
classification changing between one load and the next without ever
needing a migration step, an update-in-place counter, or any explicit
"move this launch from bucket A to bucket B" operation. Campaign
inherits this same stateless-recomputation design, not a new one.

## Worked example, following the task's own numbers

**State 1** (before a new launch arrives):
```
Discovery Total     4,199
WATCHTOWER          1,447
Other Campaigns     [[whatever Phase 5A's real split resolves to]]
Unclassified        2,752
```

**State 2** (one new launch detected, immediately recognized as
WATCHTOWER — i.e. it already has FRESH_CREATOR + wrap-close evidence
at first evaluation):
```
Discovery Total     4,200   (+1: a genuinely new launch entered the system)
WATCHTOWER          1,448   (+1: the new launch's own classification)
Other Campaigns     [[unchanged]]
Unclassified        2,752   (unchanged — no existing launch's bucket moved)
```
Both totals increase together, because a new launch increases the
denominator and is then assigned to a bucket — this is not a
reclassification, it is the classifier's very first evaluation of a
launch that didn't previously exist in the population at all.

**State 3** (later, no new launches arrive, but one previously
`Unclassified` launch gets a treasury resolved / topology-wiring fix
applied, and is now recognized as WATCHTOWER on re-evaluation):
```
Discovery Total     4,200   (unchanged — same population, only its
                              members' evidence changed)
WATCHTOWER          1,449   (+1: the reclassified launch)
Other Campaigns     [[unchanged]]
Unclassified        2,751   (-1: the same launch's PREVIOUS bucket)
```
The total is unchanged; exactly one bucket gains what exactly one
other bucket loses — a paired movement, never an independent increment
on one side alone.

## Validation rule (to be enforced, once implemented, as a returned boolean — not a new mechanism)

Exactly as recommended in Phase 5A, a `campaign_conserved` field
(mirroring `canonical_behaviour_conserved`/`conserved` in the existing
codebase) is checked on **every** computation of the Campaign
classification — at every Discovery page load, not just once at
"launch time" — so that both event types above (new-launch growth and
reclassification churn) are validated identically, by the same single
assertion: `count(WATCHTOWER) + count(OTHER_CAMPAIGN) +
count(UNCLASSIFIED) == total_launches`, recomputed fresh every time.
There is no separate "migration correctness" check needed beyond this,
because the classifier never mutates a stored bucket assignment in
place — it recomputes the whole partition from current evidence on
every call, so the invariant either holds trivially (a partition of a
set always sums to the set's size) or a genuine bug exists in the
decision model itself (e.g. a launch matching two terminal branches,
or matching none) — the same class of bug X65.0's own
`canonical_behaviour_conserved` check exists to catch, reused here
rather than re-invented.

## No duplicate counting or population loss, by design

- **No duplicate counting**: impossible by construction, since the
  decision model (Phase 3/5A) is a strict single-exit branch — a
  launch is never evaluated against more than one terminal condition
  simultaneously.
- **No population loss**: impossible by construction, since the
  decision model's final `else` (Phase 3's `UNCLASSIFIED` fallback)
  catches every launch that doesn't match `WATCHTOWER` or
  `OTHER_CAMPAIGN` — there is no evidence state a launch could be in
  that falls through all three branches unassigned.
