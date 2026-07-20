# X40.0 — Consolidation Preconditions and Audit-Ledger Design

Design and investigation only. No production code changes. Follows
[X39.0](X39_0_CANONICAL_ENTITY_RECONCILIATION_AUDIT.md) — the frozen model (Operator ≠
Operation; `wt_ops_v2` is the canonical Operation seed) is treated as fixed. SQL numbers
from live queries against `database/wt_ops_v2.db`; code citations from direct file reads,
run 2026-07-20.

## Phase 1 — Provenance Gap Resolution (the 19 treasuries)

Investigated each of the 19 treasuries X39.0 flagged as having no row in either
`wt_treasury_fingerprint_decisions` or `wt_treasury_approval_audit`, searching every
adjacent table for recoverable evidence rather than accepting the two-ledger check as final.

### Group A — 8 treasuries, method ∈ {3SIGNAL, 3SIGNAL+ORIGINAL, HAND+3SIGNAL}, all `confirmed_at=1781164069` (identical timestamp)

The identical timestamp across all 8, combined with `provenance='CONFIRMED_SEED'`, traces
directly to `treasury_bank.py:89` — a comment reading "provenance: CONFIRMED_SEED
(manual/hand-verified) vs CONFIRMED_AUTO (fingerprint)" next to a schema migration
(`ALTER TABLE ... ADD COLUMN provenance TEXT DEFAULT 'CONFIRMED_SEED'`, line 94). **This
confirms these 8 rows predate the provenance/decision-ledger system entirely** — they were
seed data backfilled with a default value at schema-migration time, not individually
decided-and-logged events. All 8 do have a `wt_confirmed_treasury_webhooks` enrollment row
(1 each, checked directly), confirming they were operationally active and enrolled, but
this is enrollment evidence, not confirmation-decision evidence.
**Classification: method-only provenance recovered.** The `method` field itself
(3SIGNAL/HAND+3SIGNAL/3SIGNAL+ORIGINAL) plus the `transfer_pct`/`out_sol`/`recipients`/
`micro_pings` columns already stored in `wt_confirmed_treasuries` are the full extent of
recoverable evidence — there is no deeper decision record to find because none was ever
written for these pre-migration seed rows.

### Group B — 4 treasuries, method=REVIEW_PROMOTED

Checked `wt_treasury_review` directly (the review-queue table `REVIEW_PROMOTED` reads
from) — **all 4 still have their row**, with `status='CONFIRMED'` and a populated
`detected_via` field: `micro_ping` (×2), `expansion_hub` (×1), `subprov_discovery` (×1).
The richer evidence columns on that same table (`notes`, `evidence_json`,
`evidence_sigs`, `discovery_reasons`) are empty for all 4 — so the *category* of
detection is recoverable but the *specific supporting evidence* (which transactions,
which reviewer reasoning) is not.
**Classification: partial provenance recovered** (detection method/category known;
detailed evidence absent).

### Group C — 7 treasuries, method=subprov_funder_trace

Checked `wt_discovered_subprovs` (the table populated by RPC-verified subprov tracing) —
**all 7 have concrete linked subprov rows**, ranging from 1 to 100 linked subprovs per
treasury (e.g. `9hGcxVHFajR4x…` → 100 subprovs, `Dtwi1eLMTLaU…` → 98,
`5JWii73Qc9Fz…` → 37). This is genuine structural evidence of *why* each was confirmed
(it was traced as the funding root of a specific, enumerable set of subprovisioners) —
it just lives in a different table than the two ledgers checked in X39.0.
**Classification: partial-to-full provenance recovered**, depending on treasury: the 6
with double-digit-or-higher linked subprov counts have strong corroborating structural
evidence; `CfqL3KWt5UVruYTLihjU1jMM9CQySQnMpSPknK3KhpKY` has only 1 linked subprov, which
is thinner support but still a concrete, non-empty evidentiary link.

### Revised classification (supersedes X39.0's blanket "irrecoverably ambiguous" for 19/58)

| Group | n | Classification | Where the evidence actually lives |
|---|---|---|---|
| A (3SIGNAL family, pre-migration seed) | 8 | Method-only provenance | `method`/`provenance` columns + webhook enrollment; no deeper record exists to find |
| B (REVIEW_PROMOTED) | 4 | Partial provenance recovered | `wt_treasury_review.detected_via` (category), evidence detail fields empty |
| C (subprov_funder_trace) | 7 | Partial-to-full provenance recovered | `wt_discovered_subprovs` (concrete linked-subprov evidence) |

**None of the 19 are actually "permanently provenance-ambiguous."** X39.0's Phase 6
conclusion was too pessimistic because it checked only the two ledger tables it had
already identified, rather than searching the wider schema. The correct framing: **every
one of the 19 has at least method-level provenance recoverable from tables that already
exist** — the gap is that this evidence is scattered across three additional
tables (`wt_confirmed_treasury_webhooks`, `wt_treasury_review`, `wt_discovered_subprovs`)
never previously joined against the identity-confirmation registry. No manufactured
evidence was introduced; where a table's detail fields were empty (Group B's `notes`/
`evidence_json`), that emptiness is reported as-is, not filled in.

## Phase 2 — Unified AttributionEvidence Contract

```
AttributionEvidence
  event_id            (autoincrement, surrogate key — append-only, never reused)
  event_type          ENUM: FINGERPRINT_EVALUATION | LAUNCH_CHAIN_CONFIRMATION |
                            MANUAL_APPROVAL | MANUAL_REJECTION | RPC_VERIFIED_TRACE |
                            SUBPROV_FUNDER_LINK | REVERSION | ROLE_CHANGE
  subject_wallet      TEXT (the wallet this event is about)
  claimed_role        TEXT (TREASURY | SUBPROVIDER | CREATOR | ...)
  decision            TEXT (CONFIRMED | REJECTED | NEAR_MISS | REVERTED | ...) —
                       kept as its own field, NOT merged into confidence (Phase 7)
  evidence_refs        JSON (tx signatures, review-queue id, subprov-link ids — whatever
                       the originating pipeline actually captured; nullable per-field,
                       never fabricated if the source pipeline didn't capture it)
  method               TEXT (free-form, preserves today's method vocabulary:
                       LAUNCH_CHAIN / 3SIGNAL / subprov_funder_trace / REVIEW_PROMOTED / etc
                       — kept, not discarded, since it's the only surviving provenance
                       for Group A above)
  actor_or_process     TEXT (a human reviewer id, OR a named automated process like
                       'auto_evaluate' / 'operation_scheduler.launch_chain')
  timestamp            INTEGER (unix, matches existing `confirmed_at`/`decided_at` convention)
  source_pipeline      TEXT (which of the traced pipelines produced this: cascade /
                       walkback / dashboard / operation_scheduler)
  confidence_axis      TEXT tag identifying WHICH confidence vocabulary the accompanying
                       confidence_value uses (see Phase 7) — never a bare unlabeled number
  confidence_value     TEXT or REAL depending on axis — stored alongside its axis tag,
                       never coerced to a shared scale
  superseded_event_id  NULLABLE FK to a prior event_id, set when this event reverses or
                       revises an earlier one (e.g. a REVERSION event points at the
                       CONFIRMED event it reverses) — this is what gives the ledger a
                       navigable history instead of just a flat append log
```

This directly subsumes `wt_treasury_fingerprint_decisions` and
`wt_treasury_approval_audit` (both become `event_type` variants writing to the same
table) and adds explicit slots for `LAUNCH_CHAIN_CONFIRMATION`, `RPC_VERIFIED_TRACE`,
and `SUBPROV_FUNDER_LINK` — the three method categories Phase 1 found were previously
under-logged. Critically, **confidence is never collapsed to one field** — see Phase 7.

## Phase 3 — Operator Identity Semantics

Distinguishing three separate questions the current code conflates:

**Structural similarity** (X36.0's fingerprinting methodology: fan-out breadth,
Primitive-A rate, capital scale) — **insufficient alone** to assert common Operator
identity. Two independent, unrelated operators could plausibly reuse the same generic
mechanism (X35.0 already showed Primitive A/B generalize outside confirmed WATCHTOWER) —
similar behavior is not proof of a shared actor.

**Operation-family similarity** (`wt_ops_v2_families`, the `_matches_playbook` soft-link
mechanism traced in X39.0) — **supporting only**. The code's own comment
(`operation_store_v2.py:253`, "playbook: template+swarm topology (NOT infra merge)")
already states this explicitly. A shared family_uuid means "these operations look like
they follow the same playbook," which nominates a pair for review but is not itself
attribution evidence.

**Common-actor attribution** — requires evidence that goes beyond either of the above:
shared collectors/terminal wallets (already a *hard merge* signal for Operations per
`_find_hard_merge_target`, but merging operations is not the same claim as asserting one
Operator — two genuinely distinct real-world actors could still coincidentally share a
collector via an unrelated service), treasury recycling across confirmed Operations,
repeated Primitive-A implementation details specific enough to be a signature (not just
"uses wrap-close," but e.g. a shared address-derivation seed pattern), or — the only
evidence type that should be able to stand alone — **explicit human intelligence /
review**, where a reviewer asserts common ownership from evidence outside the
funding-graph model entirely (e.g. off-chain intelligence, social/OSINT correlation).

**Sufficiency table:**

| Evidence type | Sufficient alone for Operator attribution? |
|---|---|
| Shared behavioral fingerprint (structural similarity) | No — never sufficient alone |
| Shared family_uuid (playbook similarity) | No — supporting only, nominates for review |
| Shared collectors/terminal wallets (hard Operation merge signal) | No — sufficient to merge Operations, NOT sufficient alone to assert one Operator; merging structural clusters is a narrower claim than asserting real-world common ownership |
| Treasury recycling across confirmed Operations | Supporting only — strengthens a case but should not auto-confirm |
| Explicit human review / off-chain intelligence | **Sufficient alone** — this is the only evidence type in this list that can independently ground an Operator-identity assertion, because it's the only one that reasons about the real-world actor directly rather than inferring it from on-chain structure |

**Explicit answer to the spec's question**: shared behavioural fingerprinting can
**never prove** common Operator identity on its own — it can only ever nominate a
candidate pair for human review. This is consistent with X35.0's finding that Primitive
A/B generalize outside confirmed WATCHTOWER: if the mechanism itself is not
WATCHTOWER-exclusive, then two operators independently using it cannot be distinguished
from one operator using it twice, by structure alone.

## Phase 4 — Operator Membership Lifecycle

| State | Required evidence | Permitted transitions | Authority | Audit event required | Effect on `operator_entities` projection |
|---|---|---|---|---|---|
| **PROPOSED** | Any supporting-only evidence (shared family_uuid, structural similarity, treasury recycling) | → SUPPORTED, → REJECTED | Automated (system can propose) | Yes — `event_type=ROLE_CHANGE`, decision=PROPOSED | Not yet reflected — proposals are not membership |
| **SUPPORTED** | ≥1 additional corroborating signal beyond the original proposal trigger (e.g. structural similarity AND treasury recycling both present) | → CONFIRMED, → REJECTED, remains SUPPORTED pending more evidence | Automated, but cannot self-promote to CONFIRMED | Yes | Not yet reflected — still below the confirmation bar |
| **CONFIRMED** | Human review sign-off OR explicit off-chain intelligence (per Phase 3's sufficiency table) | → SPLIT, → SUPERSEDED | **Human only** — no automated path should be permitted to reach this state, mirroring the existing `auto_evaluate()` never-auto-promotes discipline already in the codebase (`treasury_bank.py:462-467`) | Yes | **Reflected** — this is the only state that should populate the projection |
| **REJECTED** | A reviewer or automated check determines the evidence does not support common ownership | Terminal, or → PROPOSED if new evidence later arrives | Either | Yes | Removes/excludes from projection if previously present |
| **SPLIT** | A previously-CONFIRMED membership is later determined to have wrongly combined two distinct actors | → (two new PROPOSED memberships, one per resulting Operator) | Human only | Yes, and must reference the CONFIRMED event it supersedes | Projection regenerated to reflect the split |
| **SUPERSEDED** | A membership decision is replaced by a later, better-evidenced one (not necessarily a reversal) | Terminal for this event; superseding event becomes current | Either, but see above per-state authority | Yes, referencing `superseded_event_id` | Projection follows the superseding event |

**`operator_entities` becomes a materialized projection**: `SELECT wallet, operator_id
FROM AttributionEvidence WHERE event_type IN (...) AND decision='CONFIRMED' AND state
NOT IN (SPLIT, SUPERSEDED-and-not-current) GROUP BY wallet HAVING <latest event wins>` —
never an independently authored table. This directly resolves X39.0's finding that
`operator_entities` today is populated by a hardcoded-constant pipeline rather than
derived from evidence.

## Phase 5 — Operation Merge Evidence (complete inventory, traced from `operation_store_v2.py`)

| Rule | Exact condition (verbatim from code) | Evidence used | Threshold | Deterministic? | Source evidence queryable? | Reversible? |
|---|---|---|---|---|---|---|
| Same-root merge | `treasury_root` already has an existing `operation_uuid` (`_find_hard_merge_target`, line 194-196) | Exact treasury address match | Exact match, no threshold | Yes, fully deterministic | Yes — `wt_ops_v2.treasury_root` is directly queryable | No un-merge path found |
| Direct treasury membership | `treasury_root in infra` of an existing operation (line 208) | Treasury address appears as a wallet in another operation's infra set | Exact set membership | Yes | Yes | No |
| Shared decisive infra (collector/terminal/direct-funder) | ≥1 wallet shared between the new chain's infra and an existing operation's infra, where that shared wallet has role `COLLECTOR`/`TERMINAL`/`DIRECT_FUNDER` in the existing operation (lines 209-215) | Wallet-role intersection | ≥1 shared decisive-role wallet | Yes | Yes — `wt_ops_v2_wallets` is queryable | No |
| Broad infra overlap | ≥3 wallets shared between the new chain's infra and an existing operation's infra, regardless of role (line 210, `len(shared) >= 3`) | Wallet-set intersection cardinality | Exactly 3 (hardcoded) | Yes | Yes | No |
| Soft family link | `_matches_playbook`: seed creator's template is in `FAMILY_TEMPLATE_BASES` AND at least one chain member has role `collector` or `treasury` (lines 237-241) | Template signature + role presence | Boolean match, no numeric threshold | Yes | Yes — `wt_ops_v2_families`/`wt_ops_v2_operation_family_links` | Not applicable — it's additive tagging, not a merge; nothing to reverse |
| Root reassignment (`resolve_true_treasury`) | Exists in the codebase but is **explicitly NOT auto-applied** — feeds a human review panel only (comment, lines 371-374: "re-rooting is a deliberate, verified action, never automatic") | N/A — not an active merge path | N/A | N/A — gated to human review | Feeds a review panel, not directly queryable as a merge decision | N/A |

**Distinguishing the three categories the spec asks for:**
- **Hard structural merge**: same-root, direct-treasury-membership, shared-decisive-infra,
  and broad-infra-overlap rules — all four fold multiple treasury roots into one
  `operation_uuid`, and all four are fully deterministic and currently irreversible.
- **Soft family relationship**: `_link_family` — tags with a shared `family_uuid`, never
  merges rows, and is correctly already documented in-code as non-merging.
- **Operator-level attribution**: not implemented anywhere in `operation_store_v2.py` —
  confirming X39.0's finding that Operator identity is currently asserted by routing to a
  hardcoded constant, not derived from any of these merge rules.

**Critical gap found**: none of the four hard-merge rules currently write any record of
*why* the merge happened beyond the resulting row state — there is no merge-event log.
This is the exact gap Phase 6 below is designed to close.

## Phase 6 — Operation Merge Ledger Contract

```
OperationMergeLedger
  event_id             (autoincrement, append-only)
  event_type           ENUM: OPERATION_CREATED | TREASURY_ADDED | HARD_MERGE |
                             MERGE_REJECTED | SPLIT | ROOT_REASSIGNED |
                             FAMILY_LINKED | FAMILY_UNLINKED | MANUAL_OVERRIDE
  source_operation_uuid   (the operation being absorbed, for HARD_MERGE/TREASURY_ADDED)
  target_operation_uuid   (the operation being merged into / the operation itself for
                          OPERATION_CREATED)
  affected_wallet         (the specific treasury/wallet whose membership changed)
  merge_rule              TEXT — which of Phase 5's exact rules fired (e.g.
                          'SHARED_DECISIVE_INFRA', 'BROAD_INFRA_OVERLAP≥3',
                          'SAME_ROOT') — always populated for HARD_MERGE events, since
                          Phase 5 showed every current rule is deterministic and nameable
  evidence_refs           JSON — the specific shared wallet(s)/roles that triggered the
                          rule, so the merge is independently re-verifiable later
  reviewer_or_rule        TEXT — 'AUTOMATED:<rule name>' for the four hard-merge rules
                          (all currently automatic), or a human reviewer id for any
                          future manual override/split
  timestamp
  previous_state          JSON snapshot of the affected operation row(s) before the event
  resulting_state         JSON snapshot after
  reverses_event_id       NULLABLE FK — set on SPLIT/MANUAL_OVERRIDE events that undo an
                          earlier HARD_MERGE
```

**Design note**: since Phase 5 found merges today are fully deterministic and
irreversible, retrofitting this ledger for *historical* merges requires re-deriving which
rule fired for each existing `wt_ops_v2` row with more than one associated treasury —
this is mechanically possible (the rules are deterministic, so re-running
`_find_hard_merge_target`'s logic against historical `wt_ops_v2_wallets` snapshots should
reproduce the same decision) but was not attempted in this pass; it is listed as a
Phase 8 precondition instead of assumed complete.

## Phase 7 — Confidence Axis Ownership Matrix

| Axis | Canonical entity | Allowed values | Producing pipeline | Derivable from another axis? | Comparable to another axis? |
|---|---|---|---|---|---|
| **Structural confidence** | `Operation` (`wt_ops_v2.confidence`) | REAL, unbounded (no CHECK constraint found in schema) | `_score(trace)` in `operation_store_v2.py`, computed at persist/merge time from graph shape | No | No |
| **Treasury-role attribution confidence** | `AttributionEvidence` (today: `wt_confirmed_treasuries.confidence`) | TEXT tier: CERTAIN / CONFIRMED / STRICT / LOW / MEDIUM / MANUAL | Whichever of the 4 confirmation paths fired | No | Only weakly comparable to human-review confidence (see below), not to any other axis |
| **Operator-identity confidence** | `Operator` (`operators.confidence`, `operator_entities.confidence`) | TEXT (values not fully enumerated in this pass) | `operator_store.py` re-derivation + human review decision | No | No |
| **Human-review confidence** | `AttributionEvidence` (today: `wt_treasury_approval_audit.confidence`) | TEXT: HIGH / MEDIUM / LOW | Human input at approval time | No | Only weakly comparable to treasury-role attribution confidence — both describe "how sure are we this wallet is a treasury," but via different tier granularities (5-tier vs 3-tier) built by different processes; X39.0 already flagged this pair as unvalidated (only 2 overlapping rows) |
| **Categorical evaluation outcome** | `AttributionEvidence` (today: `wt_treasury_fingerprint_decisions.decision`) | ENUM: CONFIRMED / NEAR_MISS / NO_ROOT / READY_3OF3 / REJECT | `auto_evaluate()` | No — this is not actually a confidence value, it's a decision outcome, and should never be treated as one | Not comparable to any confidence axis — it's a different kind of field entirely (decision, not confidence) |

**Strict "must not convert" matrix**: every cell below is a pairing with no defensible
mapping found in this pass.

| | Structural | Treasury-role | Operator-identity | Human-review | Categorical outcome |
|---|---|---|---|---|---|
| **Structural** | — | ✗ no shared scale | ✗ different concept entirely | ✗ no shared scale | ✗ not a confidence value |
| **Treasury-role** | ✗ | — | ✗ different concept (identity vs attribution) | ~ weakly related, unvalidated (n=2) | ✗ decision ≠ confidence |
| **Operator-identity** | ✗ | ✗ | — | ✗ | ✗ |
| **Human-review** | ✗ | ~ (see above) | ✗ | — | ✗ |
| **Categorical outcome** | ✗ | ✗ | ✗ | ✗ | — |

Only one cell (treasury-role ↔ human-review) is marked `~` rather than a flat `✗`,
and even that pairing should not be converted without first validating it against more
than the 2 overlapping rows available today (per X39.0's Phase 5 finding, unchanged here).

## Phase 8 — Migration Readiness Invariants (checked against the spec's list)

| Invariant | Currently true? | Evidence |
|---|---|---|
| Every future treasury confirmation creates an AttributionEvidence event | **Not yet — design only** | Requires implementing the Phase 2 contract; today 2 of 4 confirmation paths bypass any ledger (per X39.0) |
| Every manual and automated confirmation path uses the same evidence-writing surface | **Not yet** | Phase 1 found 3 additional evidence-bearing tables (`wt_confirmed_treasury_webhooks`, `wt_treasury_review`, `wt_discovered_subprovs`) that are never unified into one write surface today |
| Historical provenance gaps are explicitly marked rather than silently backfilled | **Satisfied by this audit's methodology** — Phase 1 classified rather than fabricated; Group A's absence of deeper evidence is stated as genuine, not papered over | This document itself |
| Every hard `wt_ops_v2` merge creates an immutable merge event | **Not yet** | Phase 5 found zero merge-event logging in current code; Phase 6 designs the fix |
| Operator membership cannot be created solely because a wallet entered `wt_confirmed_treasuries` | **Currently violated** | X39.0 confirmed `reconcile_confirmed_treasury()` is called on every `wt_confirmed_treasuries` write and directly populates `operator_entities` via a hardcoded constant — exactly the violation this invariant prohibits |
| `operator_entities` can be regenerated from canonical membership evidence | **Not yet — depends on Phase 4's lifecycle existing first** | Currently there is no canonical membership evidence to regenerate from |
| `wt_ops_v2` current state can be explained from its merge-event history | **Not yet** | Phase 5/6 gap — no merge-event history exists to explain from, though Phase 6 notes retroactive reconstruction is mechanically plausible |
| Structural, attribution and Operator confidence remain distinct | **Satisfied by Phase 7's design; not yet enforced in code** | The three axes are already stored in different columns today (accidentally correct), but nothing prevents a future writer from conflating them — the "must not convert" matrix should be enforced, not just documented |
| No migration treats `operator_id` as equivalent to `operation_uuid` | **Satisfied by X39.0's decision, carried forward here** | This audit's entire Phase 3/4 design keeps them structurally separate |
| Soft family membership cannot trigger a hard Operation merge or Operator attribution by itself | **Currently satisfied in code** | `_link_family`'s own comment and behavior (line 253) confirm it never merges; Phase 3 explicitly re-confirms family similarity is supporting-only for Operator attribution too |

**Summary**: 3 of 10 invariants are already satisfied (by this audit's own discipline, or
by current code's accidental correctness); 7 require implementation work before a
migration could proceed, none of which were designed in X38.0 or resolved by X39.0 alone.

## Revised Go/No-Go Recommendation

**No-go for implementation, but the corrected model from X39.0 stands and is now
auditable in design.** Before any code is written: (1) implement the `AttributionEvidence`
contract (Phase 2) as the single write surface for all confirmation paths, closing the
"two of four paths bypass the ledger" gap directly; (2) implement the
`OperationMergeLedger` (Phase 6) alongside the existing `_find_hard_merge_target` logic,
so every future merge is logged going forward even before historical backfill is
attempted; (3) attempt the historical merge-ledger backfill (flagged in Phase 6 as
mechanically plausible but unattempted) and treat its success/failure as a gating check
before trusting `wt_ops_v2`'s current state as fully explained; (4) implement the Phase 4
Operator-membership lifecycle so `operator_entities` becomes a genuine projection rather
than a hardcoded-constant target. None of these four steps require redesigning the
frozen entity model again — they are the concrete implementation of what X39.0 already
decided was correct.

## Answer to the stated success criterion

**Not yet, but the path to yes is now fully specified and does not require further design
iteration.** Every future treasury attribution, Operation merge, and Operator-membership
decision *can* be represented as immutable evidence under the contracts designed in
Phases 2, 4, and 6 — but none of these contracts exist in code today, so the system's
*current* state is only partially explainable (Phase 1 recovered method-level or better
provenance for all 19 previously-ambiguous treasuries, which is better than X39.0
concluded, but 7 of Phase 8's 10 invariants remain unimplemented). The corrected model
from X39.0 (Operator ≠ Operation) is preserved throughout this design — no invariant here
reopens that question — and the migration-risk ranking from X39.0 is superseded by this
document's more granular Phase 8 checklist, which should be the actual implementation
gate.
