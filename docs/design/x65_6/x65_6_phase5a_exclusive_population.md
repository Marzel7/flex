# X65.6 — Phase 5A: Maintain Exclusive Discovery Population

## Renaming adopted

Per direction received during this task, the dimension introduced in
Phase 2 is renamed from "Operational Pattern" to **"Campaign"**
throughout this design (all prior X65.6 phase documents updated
accordingly). Values are renamed to match:

| Old value (Phase 2/3 draft) | Final value |
|---|---|
| `WATCHTOWER_PROVISIONING` | `WATCHTOWER` |
| `OTHER_CAMPAIGN` | `OTHER_CAMPAIGN` (unchanged) |
| `UNKNOWN_CAMPAIGN` | `UNCLASSIFIED` |

This rename makes the hierarchy self-explanatory: `Creator Identity`,
`Topology`, `Funding Origin`, `Treasury` remain **descriptive
attributes** of a launch, while `Campaign` is the **single, exclusive
bucket** every launch belongs to — the naming itself now communicates
the exclusivity guarantee this phase verifies.

## Exclusivity was already structural in Phase 3's decision model — restated explicitly here

Phase 3's decision model is a strict `if / elif(implicit) / else`
chain with exactly one terminal branch reached per launch:

```
1. FRESH_CREATOR?          NO  → (OTHER_CAMPAIGN or UNCLASSIFIED) — terminal
                           YES → continue
2. Wrap-close evidence?    NO  → (OTHER_CAMPAIGN or UNCLASSIFIED) — terminal
                           YES → WATCHTOWER — terminal
```

Every path through this decision tree terminates in exactly one of the
three values — there is no branch that assigns a launch to more than
one bucket, and no branch that assigns none (the two `OTHER_CAMPAIGN`
vs. `UNCLASSIFIED` sub-checks at each `NO` are themselves also
mutually exclusive: "any funding lineage exists at all" vs. its
negation). This mirrors X65.0's `canonical_behaviour_for()` design
precedent exactly (first-match-wins, exhaustive, single exit value)
— Campaign is designed the same way from Phase 2/3 onward, not
retrofitted here.

## Population Integrity check (design-time verification, not yet implemented)

The invariant to hold, once implemented:

```
count(WATCHTOWER) + count(OTHER_CAMPAIGN) + count(UNCLASSIFIED)
  == total Discovery population
```

This is the same **conservation check** pattern already used
elsewhere in this codebase for exactly this purpose —
`build_operational_intelligence()`'s existing
`canonical_behaviour_conserved` boolean (X65.0) and
`build_topology_classification()`'s existing `conserved` boolean
(X29.1/X65.4) both already assert `sum(per-value counts) ==
total_launches` as a returned, checkable field. The recommended
implementation (not performed in this task) is a `campaign_conserved`
boolean returned alongside the new `campaign` field, computed the
identical way — reusing an established pattern in this codebase rather
than inventing a new verification mechanism.

## Illustrative population check (using live 7-day totals from X65.5 Phase 8's measurements)

Using the same 7-day, 4,199-launch population already measured in
X65.5 Phase 8 (real numbers) combined with the exclusivity rule above
(a hypothetical run, since Campaign is not yet implemented):

```
Discovery Population
4,199 launches

WATCHTOWER Provisioning     1,447   (matches X65.5 Phase 8's measured
                                     "immediately classifiable" count —
                                     these launches all have real
                                     subprovisioners evidence)
Other Campaigns              [not yet measurable — requires the
                               classifier to exist to distinguish
                               "has some funding lineage but not
                               wrap-close-shaped" from "no lineage at
                               all"]
Unclassified                 2,752   (X65.5 Phase 8's measured "no
                                     subprov evidence at all" count)

Total                        4,199 ✓ (1,447 + [Other] + 2,752, by
                                       construction, once Other is
                                       computed as the remainder)
```

Because `OTHER_CAMPAIGN` and `UNCLASSIFIED` are defined (Phase 3) as a
strict partition of "not WATCHTOWER" (any lineage at all → Other;
none → Unclassified), the sum is guaranteed to equal the total by
construction, not by a separate reconciliation step — this is the same
guarantee X65.0's `canonical_behaviour_conserved` provides for
Behaviour Cohort, extended to Campaign.

## Existing evidence dimensions remain independent and descriptive

Per the task's own framing, a WATCHTOWER-classified launch continues
to display, unaffected by its Campaign membership:
- Creator Identity: `FRESH_CREATOR`
- Topology: `SubProv Fan-Out` (or whatever the existing classifier
  computes — unmodified, X65.5 Phase 6 terminology only)
- Treasury: `Unknown Treasury` (or Confirmed/Probable/New — Phase 4,
  unmodified)
- Confidence: `High` (the Campaign-specific confidence tier, Phase 3)

None of these four fields are made exclusive by this phase — only
Campaign itself is exclusive. A launch can be `WATCHTOWER` +
`Unknown Treasury` + `High Confidence` simultaneously, because
Treasury and Confidence are orthogonal, descriptive attributes of a
Campaign-member launch, not competing exclusive buckets themselves —
exactly the same relationship Behaviour Cohort already has with
Creator Identity today (exclusive within its own dimension, freely
combinable across dimensions).

## No double-counting across the UI

Because Campaign is computed once per launch as a single-valued field
(not a list, not an additive tag-set — unlike, e.g., the pre-X65.0
`behaviours` array this project deliberately moved away from), any
UI surface that sums Campaign-bucket counts (the top-level "Discovery
Population" summary, the per-bucket card counts) is summing over a
partition, not an overlapping set — the same structural guarantee that
makes X65.0's Behaviour Cohort summary counts safe to sum today.
