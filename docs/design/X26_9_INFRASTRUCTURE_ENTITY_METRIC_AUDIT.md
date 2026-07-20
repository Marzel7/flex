# X26.9 — Infrastructure Entity Metric Audit

Status: Investigation only, per scope. No detection, attribution, walkback,
operation identity, Launch Profile, or schema logic changed.

**Headline finding, matching the framing that motivated this sprint**: "2
creator-funding observations" and "46 launches" are **not the same concept
measured inconsistently** — they are genuinely different, both-correct
units (distinct creator wallets vs. distinct launches/mints), further
complicated by "2" specifically reflecting a narrow historical subset
rather than even the true creator count. The real defects this audit found
are elsewhere: two dashboard surfaces (`/api/ops/subprov-intelligence` and
`watchtower_operator_detail.html`) display a known-infrastructure wallet's
raw activity numbers with **no infrastructure-awareness at all** — a
materially different and more serious problem than a labelling
inconsistency between two already-correct numbers.

---

## Phase 1 — Metric inventory (Axiom, across the whole platform)

| # | Location | Displayed value | Source | Entity type assumed |
|---|---|---|---|---|
| 1 | `discovery.html` `relayGroupCard` (drill-down for `KNOWN_RELAY_REACHED`) | "Axiom · 46 launches · N treasuries · N sub-provisioners" | `wt_attribution_outcomes`, grouped client-side by `terminal_entity` | Known infrastructure/relay (correct — this outcome type exists specifically for infra dead-ends) |
| 2 | `discovery.html` `operationalBehaviour` (X26.8's fix) | "Infrastructure wallet (Axiom) funded 2 observed creators" | `wt_discovered_subprovs.creator_count`, relabelled via `is_known_account`/`get_funder_label` | Correctly relabelled infrastructure wallet |
| 3 | `watchtower_operations.html` `renderSubprov` (Sub-Provisioner Intelligence table) ← `/api/ops/subprov-intelligence` | Raw `creator_count` column (2), state text "REJECTED INFRASTRUCTURE" (readable, not hidden) | `wt_discovered_subprovs` + fanout/launch/session joins | **No infrastructure-awareness in the backend** — `rejected_reason` is returned but never used to exclude/annotate; state text is visible but the numeric column has no distinguishing treatment from a genuine subprov's count |
| 4 | (contrast) `/api/ops-v2/intel/subprovs` | Excludes known-infra wallets from the leads list entirely; only contributes to an aggregate `known_count` | `wt_discovered_subprovs` + `is_known_account` | Correct pattern — the one other endpoint in this comparison set that does this right |
| 5 | `ops_tokens.html` `fundChip`, per-token "🛠 Axiom·N↗" | `wt_farms.creator_count`, independently computed | `wt_farm_launches`/`wt_farms`, written by `farm_detector.py` | Deliberately infra-aware **by design** — tagged (`funder_type=PLATFORM/INFRA`), not excluded, with an explanatory tooltip; a different, and arguably better, design choice than exclusion |
| 6 | `watchtower_operator_detail.html` ← `/api/watchtower/operator/<address>` | "Tokens Launched" stat tile, launch-wallet count, upstream-funder list | `watchtower_operator_graph`, `token_analysis`, `creator_funders` | **No infrastructure-awareness at all** — no `is_known_account` check on the *queried* address; a separate, narrower, hardcoded 5-wallet tuple only tags *upstream funders of* the queried address, not the address itself |
| 7 | `main.py` `_classify_funder` (`SERVICE_PROVIDER` bucket) | Aggregate count of creators funded by a service/automation wallet, Axiom-specific branch | `creator_funders`, `infra_wallets`, `cex_wallets` — **a separate, older DB-table-based registry**, plus a literal Axiom address string hardcoded 5 times across `main.py` (lines 30563, 30591, 30830, 32130, 32751) | Infra-aware, but via a duplicated, independently-maintained classification path — not the canonical `src/utils/infra_mapping.py` registry used everywhere else in this audit |

## Phase 2 — Every candidate metric traced to its exact write/read logic

**`wt_discovered_subprovs.creator_count`** (Axiom's stored value: **2**):
Two structurally different increment mechanisms exist for this one column:
- `promote_to_subprov()` (`ws_cascade_store.py:1040,1051`) —
  `creator_count = (SELECT COUNT(DISTINCT creator_wallet) FROM
  wt_subprov_evidence WHERE subprov=?)` — a live, idempotent recount from
  actual detected wrap-close/seeded-account-close evidence rows. **Axiom has
  zero rows in `wt_subprov_evidence`** (confirmed live) — this path never
  touched Axiom's count at all.
- `promote_recurring_funders()` (`walkback_worker.py:855`) —
  `creator_count = MAX(creator_count, ?)` where the new value is
  `COUNT(DISTINCT creator) FROM wt_walkback_queue WHERE funder_wallet=?
  AND intelligence_outcome='NO_ATTRIBUTION_FOUND'`, computed **only over
  rows whose walkback specifically resulted in NO_ATTRIBUTION_FOUND** — a
  narrow, historically-frozen subset. Confirmed live: this query, re-run
  today, still returns exactly `2` — matching the stored value exactly,
  confirming this is the sole source. X26.3 later added an
  `_is_known_infrastructure()` skip *before* this UPDATE/INSERT, so this
  path can no longer touch Axiom going forward — the `2` is a frozen
  historical artifact from before that skip existed.

**`wt_walkback_queue` (Axiom in either `funder_wallet` or `subprov` column)**:
`funder_wallet=Axiom`: 23 rows (2 `NO_ATTRIBUTION_FOUND`, 21 `LINEAGE_GAP`).
`subprov=Axiom`: 44 rows. Combined distinct mints (`funder_wallet=Axiom OR
subprov=Axiom`): **46**, exactly matching item #1's "46 launches". Combined
distinct creators: **23** — not 2, and not 46. This resolves the apparent
mismatch precisely: `wt_attribution_outcomes.terminal_entity=Axiom` (46) is
counting **launches (mints)**; distinct creators across those same 46
launches is 23; `wt_discovered_subprovs.creator_count` (2) is a frozen
subset of that 23, reflecting only the two funding relationships that
specifically produced a `NO_ATTRIBUTION_FOUND` outcome historically.

**Why `wt_attribution_outcomes.terminal_entity` picks up 44 more mints than
`wt_walkback_queue.funder_wallet` alone**: traced `derive_outcome()`'s
terminal resolution (`attribution_outcome.py:360`):
`terminal = queue.get("treasury") or queue.get("funder_wallet") or
queue.get("subprov") or creator`. For the 23 "extra" mints, `funder_wallet`
was `NULL` but `subprov=Axiom` — a different walkback-queue column entirely,
populated by a different part of the walkback pipeline (subprov discovery
via wrap-close-shaped detection, not the recurring-funder promotion path).
So `wt_attribution_outcomes`'s boundary resolution is deliberately broader
than any single `wt_walkback_queue` column — it's the platform's own
"whichever upstream address we actually recorded" logic, not a bug.

**`wt_provisioning_edges`/`wt_provisioning_sessions` (Axiom)**: 5 distinct
creators/mints each — a much narrower subset again, reflecting only the
funding relationships that also produced a full provisioning-edge/session
record (a separate, session-tracking pipeline with its own coverage gaps,
as documented in X26.6's audit).

**Contrast with a genuine sub-provisioner**
(`Hk6AxTQZyK7zsPfQLmgGdw8t9nzaD3zDeRjduNHGxbXF`, `state=PROVISIONAL_SUBPROV`):
`creator_count=16` closely tracks its own live `wt_subprov_evidence`
recount (15 — off by one, likely an unreconciled `+1` at initial insert,
not itself investigated further as out of scope). Critically: **zero**
rows in `wt_attribution_outcomes` with this wallet as `terminal_entity` —
because attribution *passes through* a genuine sub-provisioner (to the
treasury/creator beyond it), it never *terminates* there. This is not an
implementation inconsistency — it is the direct, structural consequence of
what a sub-provisioner *is* versus what an infrastructure boundary *is*:
one is a pass-through role, the other is a dead-end.

**Registry summary metrics**: `src/utils/infra_mapping.py` (the canonical
`INFRASTRUCTURE_ACCOUNTS`/`CEX_ACCOUNTS` registry) carries **zero** activity
counts of its own — confirmed via grep, no `creator_count`/`launch_count`
fields anywhere in that module. Every metric above is derived entirely
from the operational tables, never from the registry.

## Phase 3 — Precise definition of every metric

| Metric | Precise definition |
|---|---|
| `wt_discovered_subprovs.creator_count` | The number of distinct creator wallets this address is recorded as having funded, via whichever of two independent write paths last touched this row (live wrap-close-evidence recount, OR a frozen historical recurring-funder promotion count scoped to a specific outcome type) |
| `wt_attribution_outcomes` rows where `terminal_entity=X` | The number of distinct **launches (mints)** whose attribution walkback resolved this address as the point where a reviewed/registry boundary was reached (i.e. attribution legitimately stopped here) |
| `wt_walkback_queue` distinct creators (`funder_wallet=X OR subprov=X`) | The number of distinct creator wallets this address is recorded as an upstream party to, across every walkback-queue row regardless of final outcome |
| `wt_provisioning_edges`/`wt_provisioning_sessions` counts | The number of distinct creators/mints for which a *full provisioning session record* (not just a walkback-queue row) was captured — the narrowest, most-verified subset |
| `wt_farms.creator_count` (item #5) | The number of distinct migrated/tracked launches sharing this address as their immediate funder, independent of walkback/attribution outcome — a clustering signal, tagged by funder type rather than gated by it |

## Phase 4 — Entity semantics audit

| Metric | VALID_SUBPROVISIONER | REJECTED_INFRASTRUCTURE / CEX / Bridge / Relay |
|---|---|---|
| `creator_count` | Canonical, live-recounted, meaningful measure of the sub-provisioner's own provisioning volume | Merely historical — a frozen count from before the wallet was correctly excluded; still a real observation, but not a "how active is this sub-provisioner" signal since the wallet was never a sub-provisioner to begin with |
| `wt_attribution_outcomes.terminal_entity` count | **Structurally always zero** — attribution passes through, never terminates here | The canonical, correct measure of "how many launches dead-ended at this infrastructure" — this is the metric this entity class is *for* |
| Provisioning session/edge counts | Meaningful — confirms live provisioning behavior | Still meaningful as "how many of this infrastructure's funding relationships also produced a full session record" — narrower evidence, not wrong, just a different confidence tier |

This is the crux of Phase 4: **the same metric name does not carry the same
meaning across entity classes, and that's correct, not a bug** —
`creator_count` and `terminal_entity` count are simply not commensurable
across a pass-through role (sub-provisioner) and a dead-end role
(infrastructure boundary).

## Phase 5 — Analyst question audit

For reviewed infrastructure (Axiom-class wallets), the questions an analyst
actually asks, and which metric answers each:

| Analyst question | Best-answering metric |
|---|---|
| "How many launches terminated here?" | `wt_attribution_outcomes.terminal_entity` count (46) — the canonical answer |
| "How many creators interacted with this infrastructure?" | Distinct creators across `wt_walkback_queue` (23) — NOT `wt_discovered_subprovs.creator_count` (2), which is a stale subset |
| "How many funding relationships were observed?" | Distinct `wt_provisioning_edges`/`wt_provisioning_sessions` rows (5) — the most-verified, narrowest number |
| "How many attribution outcomes reached this infrastructure?" | Same as the first question — `wt_attribution_outcomes` row count is already the launch/outcome count (1:1 per mint) |

The clear conclusion: for infrastructure entities, **`wt_attribution_outcomes`
is the canonical metric**, and `wt_discovered_subprovs.creator_count` should
never be presented as the primary "how active is this wallet" figure for
this entity class — not because it's wrong, but because it answers a
narrower, historically-scoped question ("how many creators triggered a
specific 2026-era promotion heuristic") that isn't what an analyst is
actually asking when they look at a known-infrastructure wallet.

## Phase 6 — Cross-platform consistency

**"2 creator observations" vs. "46 launches" — Option A: different
concepts, correctly labelled once corrected.** They describe genuinely
different phenomena (creators funded, historically and narrowly scoped,
vs. launches terminating at a boundary, comprehensively scoped) — this is
not the "same concept implemented inconsistently" failure mode the brief
asked me to rule in or out. **Recommendation: clearer labelling**, not a
fix to either number. X26.8 already took a first correct step here by
relabelling item #2's wording from "Sub-provisioner..." to "Infrastructure
wallet... funded N *observed* creators" — the word "observed" already
signals partial/historical scope, though it doesn't yet make explicit that
it's a frozen historical figure rather than a live one.

**Genuinely inconsistent implementations found** (Option B), unrelated to
the 2-vs-46 question but surfaced by this same audit:
- **Item #3** (`/api/ops/subprov-intelligence` → `watchtower_operations.html`)
  is the one dashboard surface in this inventory with **zero**
  infrastructure-awareness in its backend query — no `is_known_account`
  check, no exclusion, no relabelling — despite the exact same
  `wt_discovered_subprovs` table and `rejected_reason` column being
  available and already correctly consumed by the sibling endpoint
  `/api/ops-v2/intel/subprovs` (item #4) two hundred lines away in the
  same file. This is a genuine implementation inconsistency: the same
  underlying data is handled correctly in one place and not in another.
- **Item #6** (`watchtower_operator_detail.html` /
  `/api/watchtower/operator/<address>`) has no infra check on the *queried*
  address at all — if Axiom's address happens to have any row in
  `watchtower_operator_graph`/`watchtower_fee_payers`/`token_analysis`, this
  page will present it as a generic "operator" with "Tokens Launched"
  stats, with nothing distinguishing it from a genuine candidate operator.
- **Item #7** (`main.py` `_classify_funder`) does correctly flag Axiom as
  `SERVICE_PROVIDER`, but via **a separate, older classification
  path** (`infra_wallets`/`cex_wallets` DB tables plus a literal Axiom
  address string duplicated five times across `main.py`), entirely
  independent of the canonical `src/utils/infra_mapping.py` registry every
  other correct consumer in this audit uses. This is a maintenance risk
  (a future registry update to `infra_mapping.py` would silently not
  propagate to this path) rather than a currently-observable display defect.

## Phase 7 — Canonical presentation model by entity class

| Entity class | Primary metric to display | Rationale |
|---|---|---|
| Valid sub-provisioner | "Creators funded: N" (from `wt_discovered_subprovs.creator_count`, live-recounted) | Canonical, live, meaningful measure of this role |
| Reviewed infrastructure (Axiom-class automation/relay) | "Launches attributed here: N" (from `wt_attribution_outcomes.terminal_entity` count) as the primary figure; "N creators observed historically (per wt_discovered_subprovs, frozen count)" as a clearly-labelled secondary/legacy figure if shown at all | `terminal_entity` count answers the question an analyst is actually asking; the `creator_count` figure, if retained, must be labelled as historical/frozen, not live |
| CEX | Same as infrastructure — "Launches attributed here: N" | CEX wallets share the same dead-end/boundary structural role as automation/relay wallets |
| Bridge | Same as infrastructure | Same reasoning |
| Relay | Same as infrastructure | Same reasoning |

Not forcing all entity classes onto identical metrics is the correct model
— a valid sub-provisioner's activity is inherently about creators it
funded (a pass-through count), while an infrastructure boundary's activity
is inherently about launches that stopped there (a terminal count). Trying
to force both onto "creator_count" (as items #3 and #6 effectively do by
omission) is precisely the semantic mismatch this audit was commissioned
to find.

## Phase 8 — Recommendation

**No implementation performed in this sprint — audit only, per scope.**
Two classes of finding, with different urgency:

1. **The originally-suspected "2 vs 46" discrepancy is not a defect.**
   Both numbers are correct, measuring genuinely different things
   (historically-scoped creator count vs. comprehensively-scoped launch
   count). X26.8's existing wording ("...funded N *observed* creators")
   already partially signals this; a small follow-up wording tweak — e.g.
   appending "(historical, from an earlier promotion pass)" — would make
   the scope fully explicit, but this is a minor polish item, not a
   correctness fix, and is **not implemented here** per the brief's "do
   not implement unless the audit proves a genuine defect" instruction.

2. **Two genuine implementation gaps were found, recommended for a
   follow-up sprint** (not fixed here, since this sprint is explicitly
   investigation-only):
   - `/api/ops/subprov-intelligence` (backing
     `watchtower_operations.html`'s Sub-Provisioner Intelligence table)
     has no infrastructure-awareness at all in its backend query, unlike
     its sibling `/api/ops-v2/intel/subprovs` two hundred lines away in the
     same file. Recommend applying the same `is_known_account()` exclusion
     or at minimum a visible relabelling, consistent with item #4's
     pattern.
   - `/api/watchtower/operator/<address>` (backing
     `watchtower_operator_detail.html`) has no check against any infra
     registry on the queried address itself. Recommend adding an
     `is_known_account()` guard before rendering "Tokens Launched"/operator
     stats for a queried address.
   - The duplicated, independently-maintained Axiom classification path in
     `main.py` (`_classify_funder`, 5 hardcoded literal occurrences plus a
     separate `infra_wallets`/`cex_wallets` DB-table registry) is a
     maintenance-risk finding, not a display defect — flagged for a future
     registry-consolidation decision, not urgent.

## Success criteria assessment

An analyst viewing Axiom today, across the platform, would see: a correct
"46 launches" figure in the Discovery drill-down (item #1); a correctly
relabelled but historically-scoped "2 creators" figure in Operational
Behaviour (item #2, already fixed by X26.8); and, in two other dashboard
surfaces not touched by X26.8 (items #3, #6), the same wallet's raw
activity numbers displayed with no distinguishing treatment from a genuine
candidate sub-provisioner/operator at all. The first two are internally
consistent once their different scopes are understood; the latter two are
the actual metric-appropriateness gaps this audit set out to find.
