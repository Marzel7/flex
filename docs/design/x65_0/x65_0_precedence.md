# X65.0 — Phase 3: Specificity Ordering

Precedence is derived from **how many independent conditions a rule
requires**, and how narrow/data-scarce its evidence source is — not
from which behaviour currently has the highest launch count (per the
task's explicit instruction: "based on specificity rather than
popularity"). By that measure, RAPID_MIGRATION (93.5% coverage, the
single most "popular" tag today) is actually the LEAST specific rule in
the whole set — it requires only one condition (migration speed) and
matches almost the entire population. Popularity and specificity are
inversely related here, which is exactly why popularity would have been
the wrong ordering basis.

## Specificity scoring, per behaviour

| Behaviour | Conditions required | Evidence source scarcity | Specificity rank rationale |
|---|---|---|---|
| RAPID_BIRTH_LAUNCH | 1 condition (`birth_to_launch_seconds <= 5`) | **Scarcest** — only 43 rows exist system-wide in `wt_watchtower_launches`, on-chain-sourced, "live-cascade-scoped" | Most specific by evidence scarcity: when this evidence exists at all, it is the highest-trust, narrowest-population signal in the entire behaviour set |
| QUICK_BIRTH_MIGRATION | 2 conditions (`creator_age <= 5s` AND `migration_delay <= 900s`) | Narrow — 67 of 4,128 launches (1.6%) in the measured window | Second most specific: two independent conditions must both hold, and it is a STRICT SUBSET of RAPID_MIGRATION (Phase 2: 100% overlap) — by definition, anything this specific must outrank the broader rule it is always a subset of |
| BURST_LAUNCH | 1 condition, but a RELATIONAL one (`cluster_size >= 3`, i.e. depends on OTHER launches' timing, not just this launch's own facts) | Narrow — 918 of 4,126 (22.2%) | More specific than a single-launch threshold check: it requires a pattern across multiple launches, not just this one launch's own timestamps |
| CREATOR_RECYCLING | 1 condition, but requires CROSS-REFERENCING the whole population (`creator appears on >1 mint`) | Broad — 2,462 of 4,126 (59.7%) | Less specific than BURST_LAUNCH: while it's also a population-level fact, it fires on any repeat launch by any wallet, a much weaker filter than "at least 3 launches within 60 seconds of each other" |
| RAPID_MIGRATION | 1 condition (`migration_delay < 300s`) | Broadest — 3,859 of 4,126 (93.5%) | Least specific of the timing-based rules; matches nearly the entire population |
| MIGRATION_5_TO_15M | 1 condition (`300s <= migration_delay < 900s`) | Narrow by population (22 of 4,126, 0.5%) but this is a residual bucket (mutually exclusive with RAPID_MIGRATION/DELAYED_MIGRATION by construction, not extra-specific by rule design) | A residual timing bucket, not "more specific" in the conditions-required sense — ranked alongside RAPID_MIGRATION/DELAYED_MIGRATION as a group |
| DELAYED_MIGRATION | 1 condition (`migration_delay >= 900s`) | Same reasoning as MIGRATION_5_TO_15M | Same tier as the other two migration-timing buckets |

## Canonical precedence tree

```
1. RAPID_BIRTH_LAUNCH
   (scarcest evidence source; on-chain-verified; when present, the
   single highest-trust signal — never overridden by anything broader)
        │
        ▼ (if not matched, or evidence unavailable)
2. QUICK_BIRTH_MIGRATION
   (two independent conditions; a strict subset of RAPID_MIGRATION per
   measured overlap — must win over the broader migration-speed-only
   rule it is always nested inside)
        │
        ▼
3. BURST_LAUNCH
   (a relational, population-level pattern — more specific than a
   single-launch timing threshold)
        │
        ▼
4. CREATOR_RECYCLING
   (a population-level fact, but a weaker filter than BURST_LAUNCH's
   3-within-60s requirement)
        │
        ▼
5. RAPID_MIGRATION / MIGRATION_5_TO_15M / DELAYED_MIGRATION
   (already mutually exclusive as a trio; the broadest, single
   -condition, single-launch timing facts — the residual bucket for
   anything with migration timing but no more specific signal)
        │
        ▼
6. UNKNOWN / UNCLASSIFIED_BEHAVIOUR
   (no rule's required evidence was available or satisfied)
```

## Why this ordering, not the task's own example ordering

The task's illustrative example tree places Rapid Migration ABOVE
Migration 5-15m ABOVE Delayed Migration ABOVE Creator Recycling, and
puts Burst Launcher between Quick Birth and Rapid Migration. This
audit's measured evidence supports a different arrangement in one
respect: **CREATOR_RECYCLING is ranked below BURST_LAUNCH, and the
migration-timing trio is ranked below CREATOR_RECYCLING**, not above
it — because:

1. The three migration-timing tags are the LEAST specific rules
   measured (RAPID_MIGRATION alone matches 93.5% of the population) —
   ranking them above CREATOR_RECYCLING or BURST_LAUNCH would mean the
   least-specific rule wins first, which is backwards from "most
   specific matching rule" (the task's own stated goal in Phase 4's
   pseudo-logic: `if matches(rule_1): behaviour = RULE_1` where rule_1
   is meant to be the MOST specific, checked first).
2. CREATOR_RECYCLING and BURST_LAUNCH both require reasoning about the
   launch in the context of a broader population (other launches by
   the same creator, or other launches in the same time window)
   respectively — this is a more specific class of evidence than "this
   single launch's own two timestamps differ by less than N seconds,"
   which any launch with valid timestamp data can trivially satisfy.

The task's own example is explicitly labeled "Example only (derive from
actual rules)" — this audit follows that instruction and derives the
ordering from the measured specificity of the actual rules in this
codebase, rather than reproducing the illustrative tree verbatim.

## Boundary rule: ties and non-evaluable rules

- A rule whose required evidence is **unavailable** (e.g.
  RAPID_BIRTH_LAUNCH when the mint has no `wt_watchtower_launches` row
  at all) is treated as "did not match," per every existing classifier
  in this codebase's own governing principle ("absence of a match is
  not evidence of absence of behaviour... never inference, never
  estimation") — the precedence chain simply falls through to the next
  rule.
- No two rules in this precedence tree can both match at the "same"
  rank — each tier is checked in strict order, and the first tier whose
  rule matches wins, exactly per the task's pseudo-logic.
