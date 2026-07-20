# X24.9 — Operational Classification Taxonomy (Design Only)

Status: DESIGN ONLY. No code, schema, or UI changed by this document.

Data sources: `database/wt_ops_v2.db` — `wt_watchtower_launches` (43 rows),
`wt_provisioning_sessions` (274 rows), `wt_attribution_outcomes` (3,727 rows），
`wt_confirmed_treasuries` (58), `wt_discovered_subprovs` (1,226),
`wt_creator_birth_launch` (95). All numbers below are measured directly against
these tables; none are assumed.

---

## 0. Executive summary

The current proposal (`EMERGENT`, `CREATOR_REUSE`, `LINEAGE_REUSE`, `INFRA_REUSE`)
does not survive contact with the data in its original form:

- **`CREATOR_REUSE` should not exist as its own axis.** In 43 confirmed
  launches, creator reuse is 0%. Every WATCHTOWER creator is single-use by
  construction (`single-token-creator-filter` is already a hard selection
  filter, per existing memory) — there is nothing left to classify. Fold this
  into **Launch Profile** as a binary flag, not an axis.
- **`EMERGENT`'s "rapid birth→launch" clause is not discriminating.** 40/41
  measured launches (97.6%) complete birth→launch in under 60 seconds, median
  2 seconds. This is a near-universal constant of the WATCHTOWER-detected
  population, not a variable that separates operations. Keep it as a
  documented characteristic of the population, not a classification criterion.
- **`LINEAGE_REUSE` is real and is the strongest discriminator measured.**
  Treasury reuse is 71% (5/7 treasuries fund >1 launch; one funds 15). This is
  the one axis that actually separates distinct operational campaigns from
  one-off activity.
- **The infrastructure-reuse hypothesis in the brief is CONFIRMED, with a
  precise mechanism identified.** "Infrastructure reused inside the
  operational funding chain" and "infrastructure reached later by walkback"
  are different populations with different failure modes, and conflating them
  produces false positives today (detailed in §4).

Recommended taxonomy: **six independent axes**, reduced from the proposed six
by merging Creator Classification into Launch Profile and by tightening
Infrastructure Classification to a boundary-based (not hop-count-based)
definition. See §1.

---

## 1. Canonical taxonomy

### 1.1 Launch Profile
**Question answered:** What kind of launch is this, structurally?

Definition (revised): a launch is **PROVISIONED** if it has a
`wt_watchtower_launches` row with a non-null `subprov_wallet` and
`funding_mechanism` in `{WSOL_WRAP_CLOSE, SEEDED_ACCOUNT_CLOSE}` (i.e. it went
through the wrap-close/seeded-close creator-funding mechanism this platform
exists to detect). This is a factual, binary structural classification, not
a behavioral one.

Two population-level facts are **descriptive statistics of this class**, not
sub-classifications:
- `rapid_birth_to_launch`: birth-to-launch under 60s. Measured: 97.6% (40/41).
  Report as a fact ("this population is characteristically rapid"), never as
  a discriminating flag, because it fails to separate anything — it is
  present in essentially the entire population.
- `creator_is_fresh`: always true by construction (0/43 creator reuse,
  confirmed by the existing `single-token-creator-filter` selection rule).
  This is **not** a finding about operator behavior; it is an artifact of how
  candidates are admitted into this table in the first place. Do not present
  it as a discovered characteristic.

Values: `PROVISIONED` (matches WATCHTOWER's own mechanism) vs `OBSERVED_ONLY`
(a launch known to Discovery via walkback/attribution but never matching a
`wt_watchtower_launches` row — 30/43 of the confirmed-launch table currently
has `detection_source IS NULL`, meaning many rows are present without a
positive live/catchup detection event; see §1.5).

**Verdict on `CREATOR_REUSE`:** reject as an independent axis. There is
nothing to classify — reuse is 0% by construction of the input filter, not
by operator behavior. If a *future* population admits creator reuse (i.e. the
filter is relaxed), this should re-enter as a flag on Launch Profile, not a
new top-level axis — it is a property of the launch, not an independent
question a user asks.

### 1.2 Funding Lineage Classification
**Question answered:** What funding lineage does this launch belong to?

Definition: lineage reuse is evaluated **per role, independently**, against
the full historical `wt_watchtower_launches`/`wt_confirmed_treasuries`
population:

| Role | Reuse test | Measured (confirmed launches, n=43) |
|---|---|---|
| Creator | same `creator_wallet` appears >1 time | 0/43 (0%) |
| Sub-provisioner | same `subprov_wallet` appears >1 time | 0/43 (0%) |
| Treasury | same `treasury_wallet` appears >1 time | 5/7 treasuries (71%), one treasury (`DchJquEZzM6V…`) funds 15 launches |

Classification value is **not a single label** — it is a per-role tuple:
`{creator_reuse: bool, subprov_reuse: bool, treasury_reuse: count}`. A single
`LINEAGE_REUSE` boolean would destroy the one signal that actually
distinguishes operations (which specific treasury, and how many launches it
has funded). Recommend exposing the **treasury's launch count** directly
(an integer, already computable) rather than collapsing it into a
boolean — "reuse: yes/no" throws away exactly the information (magnitude)
that separates a 15-launch campaign from a 2-launch one.

Percentage/partial overlap and confidence: not measured as a percentage
anywhere in the current schema, and none is needed — role identity is exact
(same wallet address or not). "Confidence" belongs to *how the role was
established* (walkback confidence, already a column on
`wt_watchtower_launches.confidence` and `wt_provisioning_sessions`), not to
whether reuse occurred. Do not invent a probabilistic reuse score; reuse is
a deterministic fact once role identity is established.

### 1.3 Infrastructure Classification
**Question answered:** Is operational infrastructure being reused *within
the funding chain that produced this launch*?

This required the deepest investigation, per the brief. Findings in §4 below
answer options A/B/C directly:

**Recommendation: (B) boundary-based, not (A) hop-count-based, and NOT (C)
replaced.** Definition:

> Infrastructure reuse means an address that plays a **funding role inside
> this launch's own `wt_provisioning_sessions` chain** (treasury, subprov, or
> an address one hop upstream of a confirmed treasury/subprov) is an address
> that **also appears in that same role for another launch or campaign**.

This is explicitly **not** hop-count based, because hop count is an artifact
of how many intermediate wallets a given campaign happens to use, not a
meaningful operational boundary — a 2-hop chain and a 4-hop chain from the
same treasury are the same operation. It is boundary-based: the boundary is
"was this address part of the reconstructed provisioning chain for this
launch" (yes → in-chain, candidate for reuse classification) vs "was this
address reached only by walkback continuing past the chain's own terminal
node, with no independent evidence it played a funding role" (no → belongs
to Attribution Outcome, §1.4, not here).

**Hypothesis validation (the brief's "important question"): CONFIRMED.**
See §4 for full evidence. Summary: 66/69 (96%) of `KNOWN_RELAY_REACHED`
terminal entities and 294/303 (97%) of `KNOWN_CEX_REACHED` terminal entities
are addresses that *also* appear in `wt_discovered_subprovs`/
`wt_confirmed_treasuries` — but drilling into the actual `wt_provisioning_sessions`
rows for the Axiom/Raydium-Authority cases shows these are **not** genuine
provisioning roles: `treasury=NULL`, mechanism=`PLAIN_XFER`, amounts of
0.001–0.74 SOL, `discovery_source='WALKBACK_RECURRING_FUNDER'`,
`confidence=0.4` (the lowest confidence tier). This is a large, universally-
connected automation/fee wallet (Axiom, Raydium authorities) that happens to
have paid a tiny amount to a wallet that later became a creator through an
unrelated path — walkback's recurring-funder heuristic mistakenly promotes
it to "discovered subprov" status. **This is exactly the false-positive
mechanism the hypothesis predicts**, and it is why Infrastructure
Classification must be scoped to the launch's own reconstructed chain
(`wt_provisioning_sessions`, which requires `treasury_to_subprov_block_time`/
`subprov_to_creator_block_time` and a real wrap-close/seeded-close
mechanism) rather than "any known-infrastructure address reached by walkback
at all," which is what today's `KNOWN_RELAY_REACHED`/`KNOWN_CEX_REACHED`
effectively measure.

### 1.4 Attribution Outcome
**Question answered:** Where did attribution terminate?

Unchanged from the existing `src/ops/attribution_outcome.py` taxonomy
(`CANONICAL_OPERATOR_REACHED`, `KNOWN_MULTI_TOKEN_CREATOR`,
`KNOWN_CEX_REACHED`, `KNOWN_BRIDGE_REACHED`, `KNOWN_RELAY_REACHED`,
`UNKNOWN_INFRASTRUCTURE`, `LINEAGE_GAP`, `AMBIGUOUS_BRANCH`, `MAX_DEPTH`,
`INSUFFICIENT_EVIDENCE`). Measured distribution (n=3,727):
`INSUFFICIENT_EVIDENCE` 2,484 (67%), `LINEAGE_GAP` 601 (16%),
`KNOWN_CEX_REACHED` 303 (8%), `UNKNOWN_INFRASTRUCTURE` 178 (5%),
`CANONICAL_OPERATOR_REACHED` 73 (2%), `KNOWN_RELAY_REACHED` 69 (2%),
`KNOWN_MULTI_TOKEN_CREATOR` 22 (0.6%). Zero `KNOWN_BRIDGE_REACHED`,
`AMBIGUOUS_BRANCH`, or `MAX_DEPTH` rows exist historically — these codes are
defined but not yet observed in this dataset.

**These must never influence Launch Profile or Infrastructure
Classification**, confirmed by measurement: mint-level outcome rows are
already 100% mutually exclusive (0 mints with >1 outcome row in 3,727 rows),
and detection provenance (§1.5) was observed to vary freely against
`CANONICAL_OPERATOR_REACHED` (both `PROGRAM_LOGS` and `ACTIVE_CATCHUP`
detection sources co-occur with it) — the two axes are already empirically
independent in the data; the taxonomy must preserve that, not just assert it.

### 1.5 Operator Identity
**Question answered:** Who operates it, if a genuine canonical operator has
been confirmed?

Unchanged: gated strictly on `canonical_identity.operator_name` being
non-null (already the correct, narrow condition per X24.8's audit). Not
derivable from Attribution Outcome alone — `CANONICAL_OPERATOR_REACHED` is
the *outcome code* that happens to accompany a real operator match, but
Operator Identity is the record itself (`operator_id`, `operator_name`,
`confidence`, `identity_signals`), which can theoretically be looked up
independent of how walkback terminated.

### 1.6 Detection Provenance
**Question answered:** How did WATCHTOWER discover this launch, and nothing
else.

Unchanged from X24.8/X24.1: `LIVE_DETECTED`, `RECONCILED`,
`WALKBACK_RECOVERED`, `PIPELINE_INCONSISTENCY`, derived from
`detection_source` and `wt_active_subprov_sessions` state at CREATE time.
Measured `detection_source` distribution on the 43 confirmed launches:
`None` 13, `ACTIVE_CATCHUP` 13, `PROGRAM_LOGS` 12, `PENDING_CREATE_RETRY` 3,
`OPENING_CATCHUP` 1, `MANUAL_USER_ATTESTATION` 1. Confirmed empirically
independent of Attribution Outcome (see §1.4) and of Launch Profile (both
`INSTANT` and non-`INSTANT` launch modes appear across detection sources).

---

## 2. Independence matrix

| | Launch Profile | Funding Lineage | Infrastructure | Attribution Outcome | Operator Identity | Detection Provenance |
|---|---|---|---|---|---|---|
| **Launch Profile** | — | Independent | Independent | Independent | Independent | Independent |
| **Funding Lineage** | Independent | — | Hierarchical (infra reuse is evaluated using the same chain roles lineage reuse identifies) | Independent | Independent | Independent |
| **Infrastructure** | Independent | Hierarchical | — | Derived-adjacent (infra reuse and attribution outcome can both reference the same terminal address, but answer different questions about it — see §4) | Independent | Independent |
| **Attribution Outcome** | Independent | Independent | Derived-adjacent | — | Hierarchical (`CANONICAL_OPERATOR_REACHED` is necessary but not sufficient evidence for Operator Identity; the identity record is the source of truth) | Independent (measured: 0 mint-level collisions; both detection sources observed against `CANONICAL_OPERATOR_REACHED`) |
| **Operator Identity** | Independent | Independent | Independent | Hierarchical | — | Independent |
| **Detection Provenance** | Independent | Independent | Independent | Independent (measured) | Independent | — |

No pair is mutually exclusive at the *dimension* level — every dimension can
in principle take any value regardless of the others. Mutual exclusivity
only applies **within** a dimension (a launch has exactly one Attribution
Outcome, exactly one Detection Provenance classification, etc.), which is
already enforced by the DB (`wt_attribution_outcomes.mint` is a PRIMARY KEY).

The two "Hierarchical"/"Derived-adjacent" relationships are the ones to
watch during implementation:
- Funding Lineage → Infrastructure: Infrastructure Classification's scope
  (§1.3) is defined *using* the same treasury/subprov roles Funding Lineage
  already identifies. They ask different questions (is this role reused vs.
  is this specific address's role itself infrastructure-grade reuse) but
  share an input.
- Attribution Outcome → Operator Identity: `CANONICAL_OPERATOR_REACHED`
  correlates with an operator record existing, but the outcome code itself
  must never be treated as the operator-identity claim (this was the exact
  X24.8 defect) — the identity record is the only source of truth.

---

## 3. Decision tree

Given a new launch (mint), determine each axis independently, in any order —
none blocks another:

```
1. Launch Profile
   Does wt_watchtower_launches have a row for this mint with subprov_wallet
   set and funding_mechanism in {WSOL_WRAP_CLOSE, SEEDED_ACCOUNT_CLOSE}?
     YES → PROVISIONED (report birth_to_launch_seconds as a fact, not a class)
     NO  → OBSERVED_ONLY

2. Funding Lineage
   For each of {creator_wallet, subprov_wallet, treasury_wallet}:
     does this exact address appear on >1 launch in wt_watchtower_launches?
       → per-role reuse count (report treasury's total launch count, not a boolean)

3. Infrastructure Classification
   Does this launch have a wt_provisioning_sessions row (a reconstructed
   chain, not just a walkback-reached address)?
     NO  → not applicable; infra questions belong to Attribution Outcome only
     YES → for each role address in that session (treasury, subprov):
             does it appear in another launch's wt_provisioning_sessions
             row in the SAME role, with a real funding mechanism (not
             PLAIN_XFER-only, not treasury=NULL)?
               YES → INFRA_REUSE (in-chain)
               NO  → no in-chain infra reuse (silent — do not report
                     "no reuse" as if it were itself a finding)

4. Attribution Outcome
   Look up wt_attribution_outcomes.outcome_type for this mint (0 or 1 rows
   by construction). Render exactly that code's own wording. Never infer
   Infrastructure Classification or Operator Identity from it directly.

5. Operator Identity
   Does a canonical_identity record exist with operator_name populated for
   this launch's resolved entity?
     YES → render operator card
     NO  → render nothing (no placeholder claim)

6. Detection Provenance
   Classify via existing classify_walkback_confirmed_launches() logic
   (LIVE_DETECTED / RECONCILED / WALKBACK_RECOVERED / PIPELINE_INCONSISTENCY).
   Render using X24.8 wording. Never mention operator identity or
   infrastructure boundaries here.
```

---

## 4. Historical validation

### 4.1 Distribution of every proposed class

| Class | Population | Result |
|---|---|---|
| Launch Profile: rapid (<60s birth→launch) | 41 launches with timing data | 40/41 (97.6%) — **not discriminating** |
| Launch Profile: creator fresh (single-use) | 43 confirmed launches | 43/43 (100%) — **artifact of input filter, not a finding** |
| Funding Lineage: creator reuse | 43 confirmed launches | 0/43 (0%) |
| Funding Lineage: subprov reuse | 43 confirmed launches | 0/43 (0%) |
| Funding Lineage: treasury reuse | 7 distinct treasuries | 5/7 (71%); counts 15, 13, 7, 4, 2, 1, 1 |
| Attribution Outcome distribution | 3,727 outcome rows | INSUFFICIENT_EVIDENCE 67%, LINEAGE_GAP 16%, KNOWN_CEX_REACHED 8%, UNKNOWN_INFRASTRUCTURE 5%, CANONICAL_OPERATOR_REACHED 2%, KNOWN_RELAY_REACHED 2%, KNOWN_MULTI_TOKEN_CREATOR 0.6%; 0 BRIDGE/AMBIGUOUS/MAX_DEPTH |
| Detection Provenance distribution | 43 confirmed launches | None 13 (30%), ACTIVE_CATCHUP 13 (30%), PROGRAM_LOGS 12 (28%), PENDING_CREATE_RETRY 3 (7%), OPENING_CATCHUP 1, MANUAL_USER_ATTESTATION 1 |

### 4.2 Overlap between classes

- Attribution Outcome × mint: 0 mints with more than one outcome row (fully
  mutually exclusive, as designed).
- Detection Provenance × Attribution Outcome: both `PROGRAM_LOGS` and
  `ACTIVE_CATCHUP` detection sources co-occur freely with
  `CANONICAL_OPERATOR_REACHED` and with `None` (no outcome yet) — confirmed
  independent, not merely assumed.
- "Infrastructure reached" (`KNOWN_RELAY_REACHED`/`KNOWN_CEX_REACHED`
  terminal_entity) × known treasury/subprov address sets: 360/445 (81%)
  overlap in the naive test. Drilling into the actual session rows for the
  highest-volume overlaps (Axiom, 46 hits; Raydium Authority V4, 14 hits)
  shows these sessions have `treasury=NULL`, `PLAIN_XFER` mechanism, sub-SOL
  amounts, and `discovery_source='WALKBACK_RECURRING_FUNDER'` at
  `confidence=0.4` — i.e., they are **not** genuine in-chain provisioning
  roles despite appearing in `wt_discovered_subprovs`. This is the precise
  mechanism validating the hypothesis in §4.3.

### 4.3 Does the taxonomy actually separate operations?

Only one axis currently separates operations with any real signal:
**treasury-level Funding Lineage reuse.** The treasury `DchJquEZzM6V…`
(15 launches) and `9hGcxVHFajR4…` (13 launches) each represent identifiable,
distinct, ongoing campaigns; the four treasuries with 1–2 launches are much
weaker signals of an "operation" versus incidental activity. Creator and
subprov reuse currently carry zero separating power in this population
(both are 0%), **not because the concepts are wrong, but because the
current admission filters already force single-use creators/subprovs** —
this is worth re-measuring if that filter is ever relaxed, per §1.1.

**Hypothesis validation — CONFIRMED:** "Infrastructure reuse should only
describe infrastructure reused inside the operational funding chain, while
infrastructure reached later should remain an attribution outcome only." The
evidence in §4.2 shows that treating "any known-infrastructure address
reached by walkback" as infrastructure reuse (today's implicit behavior via
`wt_discovered_subprovs` promotion) produces false positives — recurring
low-value payouts from large automation hubs (Axiom, Raydium program
authorities) get mistaken for provisioning relationships. Scoping
Infrastructure Classification strictly to `wt_provisioning_sessions` rows
with a real funding mechanism and non-null treasury/subprov (§1.3) excludes
these false positives while keeping genuine in-chain reuse (if/when it
exists — not yet observed at n=0/43 subprov reuse, but the mechanism is
sound for future data).

---

## 5. Recommendations

1. **Adopt the six-axis model in §1**, with Creator Classification merged
   into Launch Profile as a non-discriminating structural fact, not an
   independent axis.
2. **Do not implement `EMERGENT` as a classification label at all** in its
   current form — "rapid birth→launch" fails to separate anything in 97.6%
   of the population. If a future population shows genuine bimodal timing
   (some campaigns slow, some fast), revisit as a real Launch Profile flag
   at that time, backed by a fresh measurement.
3. **Expose treasury reuse as a count, not a boolean.** This is the
   strongest, and currently only, real operational-clustering signal
   available. Operator clustering and campaign detection should be built on
   this axis first.
4. **Scope Infrastructure Classification to `wt_provisioning_sessions`
   membership**, explicitly excluding `wt_discovered_subprovs` rows sourced
   from `WALKBACK_RECURRING_FUNDER` at low confidence with `PLAIN_XFER`-only,
   `treasury=NULL` sessions — these are a known false-positive class (Axiom/
   Raydium-authority fee-wallet noise), not genuine infrastructure reuse.
5. **Keep Attribution Outcome, Operator Identity, and Detection Provenance
   exactly as X24.8 already established them** — this investigation found
   no additional leakage or coupling beyond what X24.8 already fixed; the
   independence claims in §2 are now backed by direct measurement, not just
   code-reading.
6. For **behavioral fingerprinting and predictive analytics**, prioritize the
   treasury-reuse axis and the Infrastructure Classification (in-chain,
   boundary-scoped) as the two real discriminators found; do not build
   fingerprinting logic on Launch Profile's rapid-timing flag or on Creator/
   Subprov reuse until a population exists where those are not fixed at 0%
   or 100% by construction.

---

## 6. Explicitly out of scope (confirmed unchanged)

No files under `src/`, `templates/`, or any DB schema were modified to
produce this document. All queries were read-only `SELECT`s against
`database/wt_ops_v2.db`. No renames, no migrations, no UI changes.
