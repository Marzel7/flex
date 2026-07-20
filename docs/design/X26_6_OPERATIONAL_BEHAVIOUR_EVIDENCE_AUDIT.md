# X26.6 — Operational Behaviour Evidence Audit

Status: Investigation only, per scope. No detection, walkback, attribution,
operation identity, launch profile, or schema logic changed. One genuine
semantic defect (an identity-string leak) identified and recommended for a
narrow follow-up fix; everything else in this section holds up well.

---

## Phase 1 — Complete inventory

Backend entrypoint: `src/discovery/service.py:532-540` builds
`OperationalBehaviourService(ops_db_path, core_db_path).build(...)`, result
on JSON key `operational_behaviour`. Frontend entry:
`templates/discovery.html:628`, rendered by `operationalBehaviour()`
(`discovery.html:496-508`) inside a Level 2 disclosure labelled
"Operational behaviour."

`build()` (`src/ops/operational_behaviour.py:51-108`) returns six keys:
`behaviour_summary`, `timing`, `infrastructure_pattern`,
`operational_consistency`, `missing_evidence`, `entities`. **`entities`
is dead code** — built (a plain echo of the resolved
treasury/subprov/creator/source_mint addresses) but never read anywhere in
`discovery.html`'s render path. Not a semantic defect, just unused;
noted for cleanup but out of this sprint's scope (no logic change).

Full field-by-field inventory (renderer, backend field, source
function/table/column, raw-vs-derived) is in the table below (Phase 2),
combined with classification to avoid duplicating the same rows twice.

## Phase 2 — Classification table (also serves as the Phase 4 evidence trace)

Legend: **DF** = Direct Fact, **DER** = Derived Fact, **HIST** = Historical
Observation, **INF** = Inference, **HEUR** = Heuristic.

### Behaviour Summary (`_build_behaviour_summary`, `operational_behaviour.py:207-234`)

| # | Displayed text | Backend source | Class | Trace complete? |
|---|---|---|---|---|
| 1a | "Creator funded after sub-provisioner (observed order, per persisted block times)" | Two `wt_provisioning_edges.funding_block_time` values compared (`s_bt >= t_bt`) | **DER** — boolean over two raw timestamps | Yes |
| 1b | "Sub-provisioner funded creator via {mechanism}" | `wt_provisioning_edges.funding_mechanism` (SUBPROV_TO_CREATOR) | **DF** | Yes |
| 1c | "Treasury funded sub-provisioner via {mechanism}" | `wt_provisioning_edges.funding_mechanism` (TREASURY_TO_SUBPROV) | **DF** | Yes |
| 1d | "Sub-provisioner has funded {count} creator(s) (per wt_discovered_subprovs)" | `wt_discovered_subprovs.creator_count` | **DF** — cites its own source table in-sentence | Yes |
| 1e | "Walkback completed successfully (provisioning session recorded)" | `wt_provisioning_sessions` row-exists check | **DER** — existence boolean | Yes |

### Timing Observations (`_build_timing`, `operational_behaviour.py:238-250`)

| # | Displayed text | Backend source | Class | Trace complete? |
|---|---|---|---|---|
| 2a | "— Not yet captured" | absence of any latency field | **DER** (negative fact) | Yes |
| 2b-d | "{Stage} {N}s" (three stages) | `wt_provisioning_sessions.*_latency_seconds` | **DF** — raw persisted values | Yes |

### Infrastructure Pattern (`_build_infrastructure_pattern`, `operational_behaviour.py:254-306`)

| # | Displayed text | Backend source | Class | Trace complete? |
|---|---|---|---|---|
| 3a | "Sub-provisioner funded {N} creator(s)" | `wt_discovered_subprovs.creator_count` | **DF** | Yes |
| 3b | "First time this exact sub-provisioner→creator funding path was observed" | `wt_provisioning_edges.observation_count == 1` | **HIST** — states a first-occurrence fact | Yes |
| 3c | "This...funding path observed {N} times" | `wt_provisioning_edges.observation_count > 1` | **HIST** | Yes |
| 3d | "Wrap-close creator funding" | `wt_provisioning_edges.funding_mechanism == 'WSOL_WRAP_CLOSE'` | **DF** | Yes |
| 3e | "Treasury (review lead) linked to {N} distinct creators" | `wt_treasury_review.distinct_creators >= 2` | **DER** — threshold(2) applied to a raw count | Yes |
| 3f | "...linked to {N} distinct sub-provisioners" | `wt_treasury_review.distinct_subprovs >= 2` | **DER** — threshold(2) | Yes |
| 3g | "Known provisioning hub ({operator_identity})" | `wt_known_operator_hubs.operator_identity` | **DF, but see Phase 8/9 — identity leak** | Yes, but wrong section |
| 3h | "Confirmed provisioning hub address" | `wt_provisioning_hubs.status='CONFIRMED'` | **DF** — registry-confirmed classification | Yes |

### Operational Consistency (`_build_consistency`, `operational_behaviour.py:310-347`)

| # | Displayed text | Backend source | Class | Trace complete? |
|---|---|---|---|---|
| 4a | "Infrastructure reuse: {status}" | `wt_known_operator_hubs` OR `wt_provisioning_hubs` existence | **DER** — composite OR | Yes |
| 4b | "Creator funding structure (wrap-close): {status}" | `funding_mechanism == 'WSOL_WRAP_CLOSE'` | **DER** | Yes |
| 4c | "Repeated treasury: {status}" | `distinct_creators >= 2` | **DER** — threshold(2), duplicates 3e's condition under different framing | Yes |
| 4d | "Full provisioning sequence recorded: {status}" | both edges present (3-way: True/False/None) | **DER** — composite presence | Yes |
| 4e | "Observed timing: {status}" | `timing.available`, **inline 2-state** (bypasses the shared `_status()` 3-state helper — see Phase 5) | **DER**, but structurally inconsistent with 4a-4d | Yes, but see wording note |

### Missing Evidence (`_build_missing_evidence`, `operational_behaviour.py:351-368`)

| # | Displayed text | Fires when... | Class | Trace complete? |
|---|---|---|---|---|
| 5a | "Repeated treasury (multiple creators funded by the same treasury)" | `distinct_creators < 2` or null — exact negation of 3e/4c | **DER** (negative) | Yes |
| 5b | "Repeated provisioning edges..." | `observation_count < 2` or null — exact negation of 3b/3c | **DER** (negative) | Yes |
| 5c | "Observed timing history" | `not timing.available` — exact negation of 2a/4e | **DER** (negative) | Yes |
| 5d | "Multiple launches from this sub-provisioner" | `creator_count < 2` or null — exact negation of 3a | **DER** (negative) | Yes |
| 5e | "Provisioning hub reuse" | neither hub table matched — exact negation of 3g/3h/4a | **DER** (negative) | Yes |

**No field is unclassified. No field failed to trace.** Every displayed
line resolves to an exact table/column and a deterministic Python
condition; nothing here is a genuine judgment-call **HEUR** in the sense
the brief warns about (no ML score, no weighted composite, no tunable
magic number beyond the two hardcoded `>=2` thresholds in 3e/3f/4c/5a,
which are simple, documented, and consistently applied).

## Phase 3 — Section purpose

| Section | Intended question | Does every field answer it? |
|---|---|---|
| Behaviour Summary | "What happened in this launch's provisioning sequence?" | Yes — all five lines are sequence-of-events facts |
| Timing Observations | "How long did each provisioning stage take?" | Yes — pure latency values, nothing else |
| Infrastructure Pattern | "What repeated infrastructure relationships exist?" | **Mostly** — 3g embeds an identity name, arguably answering "who," not "what pattern" |
| Operational Consistency | "How similar is this launch to previously observed behaviour?" | Yes, with the caveat that 4e's 2-state status is a narrower answer than the other four rows give |
| Missing Evidence | "What does the platform normally expect but hasn't yet established here?" | Yes — every line is the honest negative of an established Pattern/Consistency signal |

## Phase 5 — Semantic audit (certainty / wording)

Two real wording issues, one non-issue explicitly checked and ruled out:

1. **3g interpolates a bare operator name with no hedge.** "Known
   provisioning hub (Axiom)" reads identically whether "Axiom" came from a
   fully reviewed, high-confidence registry entry or a low-confidence
   candidate row — `wt_known_operator_hubs` has a `confidence REAL` column
   that is never surfaced here. This isn't wrong (the module correctly
   never emits a percentage per its own Facts-vs-Opinions rule), but the
   text reads as more definitive than "confidence" implies is guaranteed by
   the table itself. Distinct from the Phase 9 identity-leak issue, which
   is about section placement, not certainty wording.
2. **4e's "Observed timing" cannot express "not observed"** — it is hand-
   written as `"Observed" if timing.get("available") else "Not yet
   available"`, bypassing the shared `_status()` helper the other four
   Consistency rows use, which supports a real third state
   ("Not observed" = actively checked and confirmed absent, vs. "Not yet
   available" = never checked/no data at all). For timing specifically,
   "not observed" isn't really a meaningful third state (a
   `wt_provisioning_sessions` row either has a latency value or it
   doesn't — there's no "we checked and confirmed no latency exists"
   condition distinct from "we have no session row"), so this is
   **not a defect**, just a structurally different (and, on reflection,
   correctly simpler) case than the other four rows — flagged only so it's
   understood as intentional, not overlooked.
3. **Checked but ruled out**: none of the wording overstates certainty
   beyond what its threshold check supports — "linked to N distinct
   creators" (3e) states the exact number, not an adjective like "heavily
   linked"; "First time... observed" (3b) is a literal count-equals-1 fact,
   not an inference about novelty or suspicion.

## Phase 6 — Contradiction audit

Checked every plausible contradictory pair from the brief and beyond:

- **"Infrastructure reuse" (4a) vs. "Provisioning hub reuse" in Missing
  Evidence (5e)**: mutually exclusive by construction — 5e's condition is
  the exact Python negation of 4a's condition (`hub_facts` values are
  always `None` or a real dict, never `False`, so `not X` and `X is None`
  are equivalent here). **Cannot co-occur.**
- **"Operational consistency" vs. "Missing evidence" in general**: each of
  the five Missing Evidence lines (5a-5e) is the exact negation of exactly
  one Pattern/Consistency signal (5a↔4c/3e, 5b↔3b/3c, 5c↔4e/2a, 5d↔3a,
  5e↔3g/3h/4a) — by construction, none can appear alongside its own
  positive counterpart. **No contradictions found.**
- **3b ("First time... observed") vs. 5b ("Repeated provisioning edges" in
  Missing Evidence)**: these **do co-occur** when `observation_count == 1`
  — not a contradiction (both correctly describe "this path hasn't
  repeated yet"), but they are the same fact stated twice in different
  sections with different framing (a positive Infrastructure Pattern line
  and a negative Missing Evidence line for the identical underlying
  condition) — this is a duplication finding, addressed in Phase 7, not a
  contradiction.

**No genuine contradictions were found.** The five Missing Evidence
conditions are systematically constructed as exact negations of five
Pattern/Consistency conditions, verified directly in the source
(`operational_behaviour.py:357-367` vs. `:260-304, :320-339`), and all five
functions run against the identical shared inputs within one `build()`
call (`:89-96`), so there is no path for stale/inconsistent inputs to
produce a genuine contradiction between sections.

## Phase 7 — Simplification opportunities

The five Missing Evidence lines are, without exception, the logical
negation of five other already-displayed lines elsewhere in the same
card. This is a real duplication of *signal* (not of wrong information —
each half is individually correct), and could be consolidated: e.g. a
single row per underlying fact showing "Repeated treasury: 1 of 2+ needed
(not yet established)" style framing, rather than a positive line in one
sub-section and a wholly separate negative line in another. Recommended
as a **structural simplification opportunity**, not a defect — the current
design is honest and traceable, just verbose. Left unimplemented per the
brief's "do not implement unless a genuine semantic defect" instruction;
this is a UX/consolidation call, not a correctness fix.

Separately: 3e/3f (Infrastructure Pattern) and 4c (Operational
Consistency) both derive from the identical `wt_treasury_review` fields
with the identical `>=2` threshold, just phrased differently ("linked to N
distinct creators" vs. "Repeated treasury: Observed") — another
same-fact-twice case, same recommendation.

## Phase 8 — Registry evidence audit

Per the brief, `wt_known_operator_hubs` and `wt_provisioning_hubs` are
legitimate reviewed-registry evidence sources and their classifications
(item 3g, 3h) are correctly treated as **DIRECT FACTS** in this audit's
classification table above, not heuristics — consistent with the brief's
explicit instruction.

Important correction to the sprint brief's framing: **this section does
NOT use `src/utils/infra_mapping.py`'s `INFRASTRUCTURE_ACCOUNTS`/
`CEX_ACCOUNTS`/`CUSTOM_ACCOUNTS` registries at all** (confirmed: zero
references anywhere in `operational_behaviour.py`). The brief's example
list ("Axiom, CEX wallets, Bridge wallets, Relay infrastructure, Automation
platforms") describes the `infra_mapping.py` registry used elsewhere
(Attribution Outcome's `_boundary()`, X26.3's `_is_known_infrastructure()`)
— a **different, independent registry** from the one this section actually
reads (`wt_known_operator_hubs`/`wt_provisioning_hubs`, populated by
`src/ops/operator_resolver.py` and `src/analysis/watchtower_detector.py`
respectively). Both are legitimate "reviewed registry" evidence sources per
the brief's general principle, but they are not the same registry, and a
future engineer should not assume Behaviour's "Known provisioning hub"
label is backed by the same static Python dict Attribution Outcome uses.

Where each registry classification enters the pipeline:
- `wt_known_operator_hubs`: populated by `operator_resolver.py`'s own
  identity-resolution logic (confirmed: this table is also read directly by
  `operator_resolver.py:481-530` for canonical-operator lineage — the same
  row backs both a Behaviour Pattern line and part of the identity
  resolution graph elsewhere).
- `wt_provisioning_hubs`: populated by `src/analysis/watchtower_detector.py`'s
  own detection logic, filtered to `status='CONFIRMED'` before this section
  will ever surface it — an already-reviewed classification, correctly
  treated as fact rather than a raw candidate.

Both are correctly and consistently treated as evidence (not heuristics) by
this module's own logic. The one issue is not whether they're evidence —
they are — but **where** one of them (3g) surfaces: see Phase 9.

## Phase 9 — Behaviour versus Identity

Checked every requested leak vector:

- **Operator identity**: `operational_behaviour.py` never imports or reads
  `canonical_identity`, `operator_entities`, `operators`, or any
  `_canonical_identity()`-adjacent construct. **Confirmed one real leak**:
  item 3g's label `f"Known provisioning hub ({hub_facts['known_operator_hub']['operator_identity']})"`
  directly interpolates a free-text operator name into a Behaviour-section
  line. This is the same underlying fact (`wt_known_operator_hubs` row)
  that separately feeds real canonical-operator resolution in
  `operator_resolver.py` — so a wallet's *identity* is disclosed inside a
  card whose own module docstring explicitly states its purpose is "how
  did this behave... distinct from 'who is this' (attribution/canonical
  identity)." This is a genuine, narrow semantic defect: the module's own
  stated contract is violated by one specific field.
- **Attribution outcome**: no reference to `outcome_type`, `stop_reason`,
  or any `attribution_outcome.py` construct anywhere in this module.
  **No leak.**
- **Operation identity**: no reference to `operation_identity`,
  `treasury_count`, `ROOT`/`MEMBER`, or `identity_basis`. **No leak.**
- **Launch profile**: no reference to `PROVISIONED`/`OBSERVED_ONLY` or
  `_launch_profile()`. **No leak.**
- **Confidence**: no numeric/percentage confidence value is ever emitted by
  this module (confirmed: `_status()` only returns the three fixed English
  strings; `wt_known_operator_hubs.confidence` and
  `wt_treasury_review`/`wt_provisioning_edges` numeric fields that *could*
  carry a confidence-like value are never surfaced as such). **No leak.**
- **Frontend isolation**: `operationalBehaviour(d.operational_behaviour)`
  (`discovery.html:628`) only ever reads the `operational_behaviour` JSON
  key — never `d.canonical_identity`, `d.attribution_outcome`,
  `d.operation_identity`, or `d.launch_profile` directly. The other four
  Discovery concepts are rendered by entirely separate functions
  (`canonicalIdentity()`, `outcome()`, `operationIdentity()`,
  `launchProfileFacts()`) reading their own separate JSON keys. **The only
  concrete leak channel found is the single `operator_identity` string in
  item 3g** — there is no broader structural leak of the other four
  concepts' objects into this section.

## Phase 10 — Recommendation

**Minor wording/structural change — one item.**

The Behaviour section is, on the whole, exceptionally well-disciplined:
every field traces cleanly to a specific table/column, the Missing
Evidence negations are systematically and correctly constructed, there are
no genuine contradictions, and the module's own docstring correctly
states a Facts-vs-Opinions design intent that holds true for 8 of its 9
distinct underlying signals.

The **one genuine semantic defect** is item 3g: `"Known provisioning hub
({operator_identity})"` discloses an operator's name inside a section whose
own contract says it should describe behaviour, not identity. Recommended
fix (not implemented in this sprint, per its investigation-only scope):
reword to describe the *pattern* without the *name* — e.g. "Known
provisioning hub (registry-matched)" — and let the analyst who wants the
specific operator name follow the Canonical Operator or Operator Discovery
History card, which already exist for exactly that purpose and already
read the same underlying table via a properly identity-scoped path
(`operator_resolver.py`). This is a one-line change (drop the
f-string interpolation, keep the fact that a hub match exists) with no
backend logic, schema, or table change required.

Everything else — the 3e/3f/4c and 3b/5b duplications (Phase 7) — is a
"could simplify" observation, not a defect, and is left as a documented
opportunity rather than an implemented change, consistent with the brief's
"do not implement changes unless a genuine semantic defect is identified."

## Deliverables checklist

- [x] Complete Behaviour inventory — Phase 1/2 above, all 25 distinct
      displayed line-templates across 5 sub-sections.
- [x] Evidence trace for every displayed field — Phase 2 table doubles as
      the trace (source table/column/condition for each row); no field
      failed to trace.
- [x] Classification table — Phase 2; 0 unclassified, 0 genuine HEUR.
- [x] Registry evidence audit — Phase 8; confirms two independent
      registries are in play (`wt_known_operator_hubs`/`wt_provisioning_hubs`
      here vs. `infra_mapping.py`'s static dicts used elsewhere in
      Discovery) and both are correctly treated as fact, not heuristic.
- [x] Contradiction audit — Phase 6; no contradictions found, one
      duplication (not contradiction) identified.
- [x] Simplification recommendations — Phase 7; two duplication clusters
      flagged, left unimplemented per scope.
- [x] Proposed future Behaviour model — no structural reorganisation
      needed; the existing five-section model is sound. Only the single
      3g wording change is recommended, plus optionally consolidating the
      Phase 7 duplications in a future UX-focused sprint (not this one).
- [x] Confirmation that no backend logic changed — no file under
      `src/ops/`, `src/discovery/`, `src/core/`, or any database file was
      modified in this investigation; only this document was written.
