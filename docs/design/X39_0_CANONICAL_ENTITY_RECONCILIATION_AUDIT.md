# X39.0 — Canonical Entity Reconciliation Audit

Investigation only. No code changes, no implementation. Follows
[X38.0](X38_0_UNIFIED_EVIDENCE_ARCHITECTURE.md). Code-lineage findings are from a
dedicated read-only Explore pass (citing file:line); row-level crosswalk numbers are from
live SQL against `database/wt_ops_v2.db`, run 2026-07-20.

## Headline correction to X38.0

X38.0 proposed merging `wt_ops_v2` and `operators`/`operator_entities` into one
`Operation` entity, on the basis that `operator_entities` had 100% identity coverage and
`wt_ops_v2` had richer structural fields. **This audit finds that merge would be wrong.**
The two tables are not different completeness-levels of the same concept — `operators`
is a single hardcoded reconciliation target (`WATCHTOWER_OPERATOR_ID`, a literal
constant), while `wt_ops_v2` rows are genuinely distinct, independently-discovered
operations (125 of them) with real merge logic of their own. Collapsing them would fold
125 structurally-distinct operations into a label that currently exists to answer "is this
wallet part of what we call WATCHTOWER," not "which operation is this."

## Phase 1 — Semantics From Code (per table)

**`operators`** (1 row today): INSERT via `OperatorStore.create_operator()`
(`src/ops/operator_store.py:96`, generic — could seed any new candidate operator) and
`PromotionService._approve()` (`src/ops/promotion_service.py:271`, human clicks "approve"
on a promotion proposal — this is the path that produced today's one `CONFIRMED` row,
`display_name='WATCHTOWER'`). UPDATE via `operator_store.py:204` (confidence/status
re-derivation — same entity, revised belief) and `:278` (human decision
CONFIRMED/REJECTED/MERGE/SPLIT). No DELETE. Represents: an **operator identity claim**,
current-state, not historical evidence. Confidence is a coarse TEXT tier reflecting how
sure the system/reviewer is that this operator identity is real, not a per-fact score.

**`operator_entities`** (65 rows, all under the single operator_id): the live populating
path is `watchtower_alignment.reconcile_confirmed_treasury()`
(`src/ops/watchtower_alignment.py:139`), which hardcodes `WATCHTOWER_OPERATOR_ID` at
line 22 into every write. PK is `(operator_id, entity_address)`, so an address can't
repeat under the *same* operator, but nothing at the schema level prevents it repeating
under a different operator — an explicit `TreasuryOwnershipConflict` check (lines 121-131)
guards this at the application level instead, only against *other active* operators.
Represents: **membership of a wallet in an operator**, current-state, revisable
(REJECTED/MERGE/SPLIT states exist per the docstring).

**`wt_ops_v2`** (125 rows): `treasury_root UNIQUE NOT NULL`, schema-enforced and
empirically zero-duplicate. Two writers (`operation_store_v2.py:persist()`,
`operation_discovery_poc.py:persist_operation()`) both look up by `treasury_root` first
and merge-if-found. Critically, `_find_hard_merge_target()`
(`operation_store_v2.py:184-214`) also merges **distinct treasury_roots** into one
`operation_uuid` when infrastructure overlaps (shared collector/terminal wallet, or ≥3
shared infra wallets) — a genuine structural merge decision, not idempotent re-insertion.
`family_uuid` (28/125 rows) is explicitly "soft grouping only... NOT infra merge" per an
inline code comment (line 253) — it tags a shared behavioral-playbook match without
merging rows. Represents: **one discovered operation, persistent, with its own internal
merge/lifecycle logic** — this is structurally the most "Operation-like" of the five
tables audited.

**`wt_confirmed_treasuries`** (58 rows, PK = treasury): 4 insert triggers — 2 manual
dashboard actions (`promote_to_confirmed`, subprov-link set, review-queue approve) and 1
fully automated (`auto_confirm_from_launch_chain`, on-chain chain completion). Notably,
`auto_evaluate()` (the fingerprint scorer) **never auto-promotes**, even at 3/3
CONFIRMED — it only queues to `wt_treasury_review` (docstring, `treasury_bank.py:462-467`).
Every write here also triggers `reconcile_confirmed_treasury()`, which is what drives
`operator_entities` — i.e., **this table is upstream of and the direct cause of**
`operator_entities` population, not a parallel or competing source. Represents: **current
state of "is this wallet a confirmed treasury,"** revisable/deletable
(`revert_auto_promotion`, seed rows protected).

**`wt_treasury_fingerprint_decisions`**: schema `id, wallet, decision
(CONFIRMED|NEAR_MISS|NO_ROOT|READY_3OF3|REJECT), signals_json, evidence_txs_json,
source_migration, promoted_at, webhook_status, decided_at`. Append-only via
`_log_decision()`, called from `auto_evaluate()` (both accept and reject branches),
`auto_confirm_from_launch_chain()`, and `revert_auto_promotion()`. **Does not** log the
two human dashboard confirmation paths — those write to a separate table,
`wt_treasury_approval_audit` (schema: `treasury, action (APPROVED|REJECTED|
WEBHOOK_ENROLLED), reviewer, confidence (HIGH|MEDIUM|LOW), notes, evidence_json,
created_at`), newly identified in this pass. Represents: **append-only evidence of
automated-fingerprint decisions only** — a partial, not complete, provenance ledger for
"why was this wallet confirmed."

## Phase 2 — Row-Level Crosswalk (by confirmation method, n=58 confirmed treasuries)

| Method | n | In `operator_entities` | In `wt_ops_v2` | In decision ledger | In approval audit |
|---|---|---|---|---|---|
| LAUNCH_CHAIN | 37 | 37/37 (100%) | 0/37 (0%) | 37/37 (100%) | 0/37 |
| subprov_funder_trace | 7 | 7/7 (100%) | 5/7 (71%) | 0/7 | 0/7 |
| REVIEW_PROMOTED | 4 | 4/4 (100%) | 2/4 (50%) | 0/4 | 0/4 |
| 3SIGNAL | 4 | 4/4 (100%) | 1/4 (25%) | 0/4 | 0/4 |
| human_review_recovery_safe | 2 | 2/2 (100%) | 1/2 (50%) | 0/2 | 2/2 (100%) |
| HAND+3SIGNAL | 2 | 2/2 (100%) | 2/2 (100%) | 0/2 | 0/2 |
| 3SIGNAL+ORIGINAL | 2 | 2/2 (100%) | 0/2 (0%) | 0/2 | 0/2 |

**Classification per group**:
- **LAUNCH_CHAIN (37)**: `operator_entities` presence is a **derived tautology** (that
  table is populated *because* the treasury got confirmed — not independent corroboration).
  `wt_ops_v2` absence (0/37) is a genuine gap, not semantic mismatch — the same on-chain
  chain that triggered LAUNCH_CHAIN confirmation should, in principle, also have produced a
  `wt_ops_v2` row via the discovery pipeline, but evidently didn't for any of these 37.
  Decision-ledger presence (100%) confirms complete provenance for this group specifically.
  **Classification: missing counterpart in `wt_ops_v2` (not conflicting, just absent).**
- **subprov_funder_trace / REVIEW_PROMOTED / 3SIGNAL / human_review_recovery_safe / HAND+3SIGNAL
  / 3SIGNAL+ORIGINAL (21 combined)**: partial and inconsistent `wt_ops_v2` presence
  (0-100% depending on method) with zero decision-ledger coverage (by design — these are
  human/manual paths that never call `_log_decision`). Provenance for *why* these were
  confirmed lives only in `wt_treasury_approval_audit` (2 of 21) or is **not captured in
  any ledger at all** for the remaining 19 (REVIEW_PROMOTED, 3SIGNAL, subprov_funder_trace,
  HAND+3SIGNAL, 3SIGNAL+ORIGINAL calls — none of these are among the 2 rows in
  `wt_treasury_approval_audit`, meaning their approval reasoning is either uncaptured or
  captured elsewhere not investigated in this pass). **Classification: ambiguous
  provenance** for 19/58 (33% of all confirmed treasuries) — not conflicting, but the
  "why" is not reconstructable from the tables examined.

No wallet in this crosswalk shows a **conflicting representation** (e.g., different
confidence tiers implying contradictory conclusions about the same wallet across tables) —
every table that has a row for a given wallet agrees it's confirmed; the disagreements are
about *coverage/completeness*, not *contradiction*.

## Phase 3 — Is `operator_id` Equivalent to `operation_id`?

**No.** The single `operators` row is not evidence that all 58 confirmed treasuries are
proven to be one real-world actor — it is the target of a hardcoded constant
(`WATCHTOWER_OPERATOR_ID`, `watchtower_alignment.py:22`) that every live confirmation
funnels into by construction, not by independent corroboration. The code contains generic
machinery (`create_operator`, `_approve`) that *could* create a second operator for a
genuinely distinct discovered cluster, but no code path in the traced pipeline
(`watchtower_alignment.py`, `operator_store.py`, `promotion_service.py`,
`operation_identity.py`, `emerging_operator_service.py`, `discovery/service.py`) actually
does so today. In other words: **every confirmed treasury is *routed* to one operator
because the pipeline currently only knows to route there, not because 58 independent
lines of evidence converged on "one actor."** Meanwhile `wt_ops_v2` has 125 genuinely
distinct rows with their own merge logic that *does* independently decide when two
treasuries belong together (via shared infrastructure). **`operator_id` and `operation_id`
are not interchangeable — `operator_id` is closer to a top-level label/bucket, while
`operation_id` is the actual unit of structural discovery.**

## Phase 4 — `wt_ops_v2` Granularity

One `wt_ops_v2` row = one persistent, independently-discovered operation, rooted at a
unique `treasury_root`, updated (never re-created) for its lifetime — confirmed both by
the `UNIQUE` constraint and by both writer paths' look-up-before-insert behavior. Rows
are NOT one-per-snapshot or one-per-campaign — there is exactly one row per
`treasury_root` for as long as that operation exists, with `last_seen`/`confidence`
advancing in place. However, `_find_hard_merge_target` means the row can absorb
*additional* treasuries beyond its root via shared-infrastructure evidence — so a
single `wt_ops_v2` row can legitimately represent more than one treasury's activity once
merged. `family_uuid` is explicitly a soft/behavioral tag, not a merge — it should NOT be
read as "these rows are the same operation," only "these rows share a detected playbook."

**Answer: `wt_ops_v2` rows should become the canonical `Operation` entity directly** (not
a Campaign or OperationInstance) — they already have the right grain (one persistent
entity per discovered structural cluster, with real merge semantics), and no evidence
found in this pass suggests a finer-grained "campaign" or "instance" concept is needed
beneath it. If a finer concept is ever needed, `wt_ops_v2_operation_family_links` (the
soft-family join table) is closer to what a "Campaign" grouping would look like than
anything in `operator_entities`.

## Phase 5 — Confidence Vocabulary Reconciliation

| System | Range/enum | Meaning | Source | Describes |
|---|---|---|---|---|
| `wt_ops_v2.confidence` | REAL, unbounded in schema (no CHECK constraint found) | A numeric score for how confident the discovery pipeline is that this cluster is a real coordinated operation | Computed by `operation_store_v2.py`/`operation_discovery_poc.py` at persist/merge time | **Structure** — confidence in the discovered graph shape |
| `operators.confidence` / `operator_entities.confidence` | TEXT (values not fully enumerated in this pass — observed values include tiers consistent with a coarse scale) | How confident the reviewer/system is in the operator-identity claim itself | `operator_store.py` re-derivation logic + human review decision | **Identity** — confidence in "this is one real actor," not in any specific structural fact |
| `wt_confirmed_treasuries.confidence` | TEXT tier: CERTAIN/CONFIRMED/STRICT/LOW/MEDIUM/MANUAL | How strong the identity-confirmation evidence was for this specific wallet | Set at confirmation time by whichever of the 4 insert paths fired | **Attribution** — confidence in "this specific wallet is a treasury," method-dependent |
| `wt_treasury_fingerprint_decisions.decision` | Enum: CONFIRMED/NEAR_MISS/NO_ROOT/READY_3OF3/REJECT | Not a confidence score at all — a categorical outcome of one fingerprint evaluation pass | `auto_evaluate()` | **Attribution, but a decision snapshot, not a confidence value** |
| `wt_treasury_approval_audit.confidence` | TEXT: HIGH/MEDIUM/LOW | Reviewer's subjective confidence at the moment of manual approval | Human input at approval time | **Attribution**, human-sourced, structurally simplest of the four |

**These four vocabularies are NOT demonstrably equivalent and should NOT be collapsed
into one field.** `wt_ops_v2.confidence` describes structural graph confidence;
`wt_confirmed_treasuries.confidence` and `wt_treasury_approval_audit.confidence` both
describe attribution confidence but via different scales (5-tier vs 3-tier) built by
different processes (automated method-tagging vs human judgment at review time);
`operators`/`operator_entities` confidence describes identity-claim confidence, a third
distinct axis. **Non-convertible pairs**: `wt_ops_v2`'s numeric REAL has no defined mapping
to any of the three TEXT tier systems (no shared scale, no code found deriving one from
another). The conversion matrix requested by the spec is therefore mostly a table of
**non-conversions**: only `wt_confirmed_treasuries.confidence` and
`wt_treasury_approval_audit.confidence` are close enough in intent (both "how sure are we
this wallet is a treasury") that a coarse mapping (CERTAIN/CONFIRMED→HIGH,
STRICT/MEDIUM→MEDIUM, LOW/MANUAL→LOW) might be defensible, but this was not verified
against enough overlapping rows in this pass to assert with confidence — the two tables
share only 2 wallets in common (the `human_review_recovery_safe` rows), too few to validate
a mapping empirically.

## Phase 6 — Historical-Provenance Check

- **LAUNCH_CHAIN (37/58, 64%)**: **partially reconstructable**. The decision ledger has
  a complete row for each (confirmed via crosswalk: 37/37), including `signals_json` and
  `evidence_txs_json` — the "why" is preserved. What is NOT preserved is a corresponding
  structural (`wt_ops_v2`) record, so reconstructing "which operation this became part of"
  would require re-running discovery against these 37 wallets, not just reading existing
  rows.
- **REVIEW_PROMOTED / 3SIGNAL / subprov_funder_trace / HAND+3SIGNAL / 3SIGNAL+ORIGINAL
  (19/58, 33%)**: **irrecoverably ambiguous** as far as this pass could determine — no
  decision-ledger row, no approval-audit row, and the crosswalk found no other table
  investigated here that captures the human reasoning behind these specific confirmations.
  The *fact* of confirmation (method, confirmed_at, provenance string) is preserved in
  `wt_confirmed_treasuries` itself, but the *evidentiary basis* (which signals, which
  transactions, what a reviewer actually looked at) does not appear to survive anywhere
  queried in this pass. This should be treated as a real, not theoretical, provenance gap.
- **human_review_recovery_safe (2/58)**: **lossless** — fully captured in
  `wt_treasury_approval_audit` (reviewer, confidence, notes, evidence_json snapshot).
- **Whether two entities were ever merged or an operation split**: `wt_ops_v2`'s
  `_find_hard_merge_target` performs real merges, but this pass did not find a persisted
  "merge event log" — only the *result* (one row now covering multiple treasuries) is
  visible; the merge decision itself does not appear to be separately audited the way
  `wt_treasury_fingerprint_decisions` audits confirmation decisions. **This specific gap
  (no merge-event ledger for `wt_ops_v2`) should be flagged as needing its own resolution
  before treating `wt_ops_v2` merges as fully auditable.**

## Phase 7 — Canonical Mapping Decision (revising X38.0)

| Table | Disposition | Justification (semantic, not coverage-based) |
|---|---|---|
| `wt_ops_v2` | **Canonical seed for `Operation`** | Correct grain (one row per discovered structural cluster), has its own principled merge logic, is the closest existing thing to X38.0's target `Operation` entity — should NOT be merged with `operators`. |
| `operators` | **Retain as a distinct domain concept: `Operator`** | Represents a higher-level identity claim ("this label groups these operations as one real-world actor") that is currently under-evidenced (hardcoded single-target routing, not independently derived) but conceptually legitimate and distinct from `Operation` granularity. Do not force into `Operation`. |
| `operator_entities` | **Derived projection of `Operator` membership** | Should continue to express "which wallets/operations belong to which Operator," but should be re-derived from `wt_ops_v2` operation membership + human MERGE/SPLIT decisions, not populated by a separate hardcoded-constant pipeline as it is today. |
| `wt_confirmed_treasuries` | **Compatibility view over `AttributionEvidence`, per X38.0** — this finding is unchanged from X38.0 | Confirmed here: it is genuinely upstream of `operator_entities`, so once `AttributionEvidence` exists as the canonical evidence source, this table's role collapses cleanly into a projection. |
| `wt_treasury_fingerprint_decisions` | **Historical ledger for the automated-evaluation subset of `AttributionEvidence`** | Correct shape (append-only), but must be explicitly documented as *partial* provenance (automated paths only) — do not present it as if it covers all confirmations. |
| `wt_treasury_approval_audit` | **A second, separate historical ledger, for human-approval provenance** — newly identified in this pass, not part of X38.0's original scope | Should be merged conceptually with `wt_treasury_fingerprint_decisions` into one unified `AttributionEvidence` ledger, since together they cover different subsets of the same real concept (why was this wallet confirmed) — but as of today they are two incomplete halves, and 19/58 confirmed treasuries have provenance in **neither**. |

**Explicit answer on Operator vs. Operation vs. Campaign**: X38.0's proposal to unify
`wt_ops_v2` and `operators`/`operator_entities` into one `Operation` entity is **revised
here** — `Operator` and `Operation` must remain distinct. An `Operator` is a
(currently under-evidenced) claim that one or more `Operation`s belong to the same
real-world actor; an `Operation` is the actual discovered structural cluster. No evidence
supports introducing a third, finer-grained `Campaign`/`OperationInstance` concept —
`wt_ops_v2`'s own grain already matches "one discovered structural cluster," and nothing
in the audited tables shows repeated snapshots or sub-episodes within one operation that
would need a separate entity.

## Phase 8 — Migration Preconditions (invariants required before implementation)

1. **Every `operator_entities` membership must have an `AttributionEvidence` reference** —
   currently false for the 19/58 (33%) confirmed treasuries with no decision-ledger or
   approval-audit row; this must be resolved (either by locating the missing provenance
   elsewhere, or by explicitly accepting the gap) before any consolidation.
2. **`wt_ops_v2` and `operators` must NOT be merged** — established above; any migration
   plan that treats them as the same entity should be rejected at review.
3. **The `operators` table's single-operator routing must be replaced with real
   derivation logic** (e.g., deriving Operator membership from `Operation` merge/family
   evidence) before it can be trusted as more than a placeholder label — currently it
   reflects pipeline wiring, not an evidenced conclusion.
4. **No confidence value may be silently converted between the four vocabularies
   identified in Phase 5** without an explicit, separately-justified mapping — the
   evidence for even the closest pair (`wt_confirmed_treasuries` ↔
   `wt_treasury_approval_audit`) is too thin (2 overlapping rows) to validate today.
5. **A merge-event ledger must be added for `wt_ops_v2`** before its `_find_hard_merge_target`
   behavior can be considered fully auditable — currently only the merged *result* is
   visible, not the decision.
6. **All direct writers to `wt_confirmed_treasuries` must be enumerated and migrated
   together** — this was already identified as a risk in X38.0 and is reconfirmed here;
   the 4 insert paths traced in Phase 1 are the complete list found in this pass, but a
   fresh grep should be re-run at implementation time in case new call sites were added.

**Ranked by migration risk**:
1. **(Highest)** Treating `operators`/`operator_entities` and `wt_ops_v2` as the same
   entity — this was X38.0's original plan and this audit found it to be a semantic error,
   not just a coverage gap; proceeding on the original plan would silently conflate a
   single hardcoded label with 125 independently-discovered operations.
2. Collapsing the four confidence vocabularies without validating the mapping empirically.
3. Treating the 19/58 provenance-ambiguous confirmed treasuries as if their history is
   fully known when it is not.
4. Proceeding without a merge-event ledger for `wt_ops_v2`, leaving future operation
   merges unauditable.

## Go/No-Go Decision

**No-go on X38.0's original merge plan as specified.** The consolidation direction from
X38.0 remains largely sound (PrimitiveObservation/FundingRelationship/Launch/
AttributionEvidence consolidation is unaffected by this audit), but the `Operation`
entity must be seeded from `wt_ops_v2` alone, with `Operator` retained as a distinct,
higher-level entity that is currently under-evidenced and should not be treated as
equivalent or be merged with `Operation`. Implementation should not proceed until
invariants 1, 3, and 5 above are addressed, since they represent genuine unresolved
ambiguity (missing provenance, an under-evidenced identity claim, and an unaudited merge
mechanism) rather than mere schema inconvenience.

## Answer to the stated success criterion

**Operator, Operation, and (implicitly, via `wt_ops_v2`'s existing grain) the structural
cluster concept must remain distinct entities — they cannot be losslessly consolidated
into one.** `operators`/`operator_entities` and `wt_ops_v2` look like they represent the
same thing at different completeness levels (which is what X38.0 assumed), but they
actually represent two different levels of operational meaning: a single, currently
under-evidenced identity label (`Operator`) versus 125 independently-discovered,
individually-merged structural clusters (`Operation`). No `Campaign`/`OperationInstance`
concept is separately needed — `wt_ops_v2`'s existing grain already serves that role.
The confirmation-ledger tables (`wt_confirmed_treasuries`,
`wt_treasury_fingerprint_decisions`, `wt_treasury_approval_audit`) can be consolidated
into a single `AttributionEvidence` ledger as X38.0 proposed, but only after closing the
33%-of-treasuries provenance gap identified here.
