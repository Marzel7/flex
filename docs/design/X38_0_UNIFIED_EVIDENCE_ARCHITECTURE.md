# X38.0 — Unified Evidence Architecture

Design exercise only. No code changes, no implementation. Builds directly on the frozen
primitive model (X34.0) and the coverage findings of [X37.0](X37_0_STRUCTURAL_COVERAGE_AUDIT.md):
identity confirmation (`wt_confirmed_treasuries`) and structural capture
(`wt_provisioning_edges`) are separately-evolved pipelines with 0% direct overlap.

## A finding that changes the shape of this design (discovered during this pass)

Before designing anything new, existing partial "Operation" entities were checked against
`wt_confirmed_treasuries` for overlap, since a solution may already exist in embryonic
form:

| Candidate entity table | Rows | Overlap with confirmed treasuries |
|---|---|---|
| `wt_provisioning_edges` (`from_wallet`) | 1,022 edges | **0 / 58 (0%)** |
| `wt_watchtower_launches` (`treasury_wallet`) | 43 launches | 6 / 58 (10.3%) |
| `wt_ops_v2` (`treasury_root`) | 125 operations | 11 / 58 (19.0%) |
| `operator_entities` (`entity_address`) | 65 entity rows, 1 operator | **58 / 58 (100%)** |

`operator_entities` already has full identity coverage of every confirmed treasury. This
is the single most important input to this design: **the identity-linkage layer this
exercise might otherwise propose building from scratch already exists and is fully
populated.** The gap is not "no linking entity exists" — it's that `operator_entities`
currently links to only 1 `operators` row and is not wired to `wt_provisioning_edges`,
`wt_watchtower_launches`, or the primitive-observation layer. The design below treats
`operator_entities` as the seed of the canonical `Operation` entity rather than proposing
a new table.

## Phase 1 — Canonical Domain Model

| Entity | Purpose | Lifecycle | Immutable fields | Mutable fields | Unique ID | Owner |
|---|---|---|---|---|---|---|
| **PrimitiveObservation** | One raw, on-chain-verifiable occurrence of Primitive A or B (a wrap/seed-close event, or a funding transfer with its amount) | Created once, never revised | `signature`, `primitive_type` (A/B), `from_wallet`, `to_wallet`, `amount_sol`, `block_time`, `instruction_shape` | none — purely factual | `signature` (the tx sig is naturally unique) | The chain itself; FLEX only records what it observed |
| **FundingRelationship** | A directed edge between two wallets, derived from one or more PrimitiveObservations, carrying a role hypothesis (treasury→subprov, subprov→creator) | Created on first observation, strengthened on repeat observation | `edge_type`, `from_wallet`, `to_wallet`, `first_observed` | `last_observed`, `observation_count`, `confidence` | `(edge_type, from_wallet, to_wallet)` | Derived — owns nothing new, aggregates PrimitiveObservations |
| **Wallet** (generic — Treasury/Subprovider/Creator are *roles*, not separate entities) | A single address with a role history | Never deleted; roles can be added/revised | `address` | `roles[]` (each role itself has confidence + evidence pointer), `display_name` | `address` | Owns identity-role assignment only |
| **Launch** | One CREATE event and its immediate lifecycle (fanout→create timing, migration) | Created at CREATE detection, enriched post-hoc (migration, backfill) | `mint`, `creator_wallet`, `create_signature`, `create_time` | `migration_time`, `launch_mode`, `detection_source` | `(creator_wallet, create_signature)` | Owns launch-specific facts only; does not own treasury/subprov identity |
| **AttributionEvidence** | A single piece of evidence supporting (or reverting) a Wallet's role/operation membership — the audit-ledger entry, not the current-state row | Append-only, never revised | `wallet`, `claimed_role`, `method`, `evidence_ref` (e.g. tx sig, human reviewer id), `decision`, `timestamp` | none | auto-increment id | Owns provenance/history — this is what `wt_treasury_fingerprint_decisions` already does correctly and should remain the model for |
| **Operation** | The aggregate: a coordinated cluster of Wallets + their FundingRelationships + Launches, with a rolled-up confidence and fingerprint | Formed when evidence density crosses a threshold, can merge/split as evidence accumulates | `operation_id` | `member_wallets[]`, `confidence`, `status` (FORMING/CONFIRMED/DORMANT), `fingerprint_summary`, `last_seen` | `operation_id` (uuid, matches `wt_ops_v2.operation_uuid` convention already in use) | Owns nothing primary — it is a materialized view over the other five entities, kept as a real table only for query performance |

**Key design principle embedded in this model**: only `PrimitiveObservation`,
`AttributionEvidence`, and the base `Launch` facts are genuinely primary data. `Wallet`
roles, `FundingRelationship` confidence, and `Operation` membership are all **derived**
— they should be computable from the primary layer, not independently written by
multiple pipelines (which is exactly the failure mode X37.0 found).

## Phase 2 — Evidence Ownership Matrix

| Fact | Current authoritative location(s) | Duplicates / derived copies found | Canonical owner (proposed) |
|---|---|---|---|
| "This wrap/seed-close event happened" | `wt_subprov_evidence` (append-only, correct model already) | `wt_watchtower_launches.wrap_close_signature`, `wt_provisioning_edges` (re-derives the same fact from a different path) | `PrimitiveObservation` — `wt_subprov_evidence`'s existing design is closest to correct and should be the template |
| "Wallet X funds Wallet Y" (edge existence + amount) | `wt_provisioning_edges` | Re-derivable from `wt_subprov_evidence` + `wt_walkback_queue.funder_*` fields (duplicate capture of the same underlying transfer) | `FundingRelationship`, computed from `PrimitiveObservation`, not independently walked |
| "Wallet X is a confirmed treasury" | `wt_confirmed_treasuries` (current-state, allows UPDATE/DELETE) | `operator_entities.entity_type` (separate, currently the more complete of the two) | `Wallet.roles[]`, sourced from `AttributionEvidence`, not a mutable standalone table |
| "Why/when Wallet X was confirmed" | `wt_treasury_fingerprint_decisions` (already append-only — this is the one table in the current schema that already matches the target model) | none found — this table is not duplicated elsewhere | `AttributionEvidence` (this table is effectively already this entity; keep it, rename conceptually) |
| "This CREATE happened" | `wt_watchtower_launches` (real-time + narrow backfill) | none found as a duplicate, though the underlying wrap-close tx is also present in `wt_subprov_evidence` under a different key | `Launch`, but sourced also from FULL_WALKBACK reconstruction, not only the live cascade — this is the fix for the 89.7% non-coverage found in X37.0 |
| "This is a coordinated operation" | `wt_ops_v2` (treasury-rooted, 125 rows, 19% overlap with confirmed identity) AND `operators`/`operator_entities` (1 operator, 100% identity overlap) — **two competing implementations today** | Both are partial; neither is complete | `Operation`, unifying both — `operator_entities`' identity completeness + `wt_ops_v2`'s treasury-rooted structure should merge into one table |
| "This mint's funding lineage was walked back" | `wt_walkback_queue` (queue + history hybrid) | Its terminal `funder_wallet`/`funding_mechanism`/`funder_amount_sol` fields duplicate what `PrimitiveObservation`/`FundingRelationship` should hold | Queue *processing state* stays in a queue table (that's a legitimate distinct concern — work orchestration); the *evidentiary result* of a completed walkback should write into `PrimitiveObservation`/`FundingRelationship`, not stay queue-local |

**Where duplication currently exists**: the same underlying wrap-close transaction can be
independently represented in `wt_subprov_evidence` (real-time capture), `wt_watchtower_launches.wrap_close_signature`
(same event, launch-table copy), and `wt_walkback_queue`'s funder fields (a *third*
independent capture via the walkback path) — three pipelines each doing their own partial
capture of the same on-chain fact, with no shared canonical row. This is the concrete
mechanism behind X37.0's 0% edge-table overlap: the walkback pipeline's version of "we saw
a wrap-close" never gets reconciled with the cascade's version.

## Phase 3 — Pipeline Architecture (as currently wired)

| Pipeline | Inputs | Outputs (tables written) | Entities affected | Evidence created | Evidence consumed |
|---|---|---|---|---|---|
| WebSocket cascade (`ws_cascade.py`) | Real-time Solana WS subscription | `wt_subprov_evidence`, `wt_watchtower_launches` | PrimitiveObservation, Launch | Wrap-close observations, launch records | Live tx stream only — consumes nothing from other pipelines |
| Walkback (`walkback_worker.py` + `provisioning_edges.py`) | `wt_walkback_queue` rows | `wt_provisioning_edges`, `wt_walkback_queue` (status) | FundingRelationship | Structural edges from RPC replay | Migration-intake events; does NOT consume cascade's `wt_subprov_evidence` output, so it can independently re-observe the same tx |
| Launch detection (bundled inside cascade) | Same WS stream | `wt_watchtower_launches` | Launch | CREATE records | Same stream as cascade — not a separate pipeline in practice, listed separately here only because the spec asked for it |
| Confirmation (`treasury_bank.py`, dashboard routes) | Manual dashboard actions + `operation_scheduler.py`'s LAUNCH_CHAIN signal | `wt_confirmed_treasuries`, `wt_treasury_fingerprint_decisions` | Wallet (role), AttributionEvidence | Identity confirmations | Reads `wt_watchtower_launches`/session state for LAUNCH_CHAIN detection, but writes nothing back to the structural tables it read from — this is the specific gap X37.0 identified |
| Migration (`watchtower_attribution.py` intake) | On-chain migration events | `wt_walkback_queue` (enqueue) | (queue trigger only) | none directly — triggers walkback | Migration event stream |
| Attribution outcome (`attribution_outcome.py`) | Walkback results | `wt_attribution_outcomes` | (a fourth, separate outcome-tracking table not covered in X37.0's five-table scope) | Outcome classification | Walkback completion events |
| Operation scheduler (`operation_scheduler.py`) | Session state, LAUNCH_CHAIN detection logic | `wt_confirmed_treasuries` (via `auto_confirm_from_launch_chain`), `wt_ops_v2` (separately, per its own schema) | Wallet role, Operation (partial) | Automated confirmations | Session/launch state — reads across pipelines but writes to two different "operation-ish" tables without reconciling them |

**Where evidence currently diverges** (the core finding this phase is meant to surface):
the cascade pipeline and the walkback pipeline each independently capture wrap-close
observations for what can be the *same* underlying transaction, with no shared identifier
or reconciliation step between them. The confirmation pipeline reads state produced by
the cascade/scheduler but never writes back into the structural tables. And there are
**two separate "operation" tables** (`wt_ops_v2` and `operators`/`operator_entities`)
maintained by different code paths that don't sync with each other.

## Phase 4 — Operation Assembly

An `Operation` should be constructed as a materialized aggregation, not an independently
authored record:

**Mandatory evidence to form an Operation:**
- At least one confirmed `Wallet` with a `treasury` role (from `AttributionEvidence`)
- At least one `FundingRelationship` rooted at that wallet (derived from `PrimitiveObservation`)

**Optional evidence (strengthens confidence, not required for existence):**
- `Launch` records reachable via the funding graph
- Additional `FundingRelationship` edges (fan-out, multi-tier chaining)
- Fingerprint dimensions from X36.0 (Primitive-A rate, bulk/dust ratio, fan-out breadth)
- Vanity-family co-occurrence (supplementary, never sufficient alone — per X33.0)

**Confidence model:** a weighted function of (a) how many mandatory-evidence types are
present, (b) how many independent `AttributionEvidence` entries support the root wallet's
role, (c) fingerprint similarity to previously-confirmed Operations (X36.0's methodology).
This is explicitly NOT proposing a specific formula — that is a detection-accuracy
question, out of scope for this architecture exercise (per the spec's success criterion,
which is about structure, not accuracy).

**Lifecycle:** `FORMING` (mandatory evidence present, confidence below threshold) →
`CONFIRMED` (confidence crosses threshold, at least one human or automated high-confidence
signal present) → `DORMANT` (no new evidence for N days — a windowing concern, not a
deletion) → potential `MERGED` state when two Operations are later found to share a root
wallet or family relationship (this state does not exist in `wt_ops_v2` today but is
implied by its `family_uuid` column, suggesting the merge concept was anticipated but not
fully implemented).

## Phase 5 — Unified Evidence-Flow Diagram

```
Blockchain (Solana)
   │
   ▼
PrimitiveObservation  ◀── single canonical capture point for wrap-close / seed-close /
   │                      funding-transfer events, regardless of WHICH pipeline (cascade
   │                      or walkback) first observes it — keyed by tx signature so both
   │                      pipelines can write to the SAME row without duplication
   ▼
FundingRelationship   ◀── derived/aggregated from PrimitiveObservation; this is where
   │                      X37.0's "edge table" concern gets resolved structurally —
   │                      there is only one edge table, fed by both real-time and
   │                      walkback paths
   ▼
Launch                ◀── attached to the FundingRelationship chain that terminates in
   │                      a CREATE; enriched later by migration timing
   ▼
Wallet.roles[]         ◀── AttributionEvidence entries (manual or automated) assign or
   │                       revise a wallet's role; this is the ONLY place role state
   │                       lives — no parallel `wt_confirmed_treasuries`-style table
   ▼
Operation              ◀── materialized aggregate over Wallet + FundingRelationship +
   │                       Launch, keyed by root treasury wallet; this absorbs both
   │                       `wt_ops_v2` and `operator_entities`' current responsibilities
   ▼
Runtime Detection       ◀── fingerprinting (X36.0 methodology) queries Operation directly,
   │                        no longer needs to reconcile across disjoint tables
   ▼
Historical Intelligence ◀── the append-only AttributionEvidence + PrimitiveObservation
                            layers ARE the historical record; no separate "archive" needed
```

**Where evidence is created**: only at `PrimitiveObservation` (on-chain facts) and
`AttributionEvidence` (identity/role facts) — everything else in the diagram is derived.
**Where evidence is enriched**: `Launch` (migration timing added post-hoc), `Operation`
(confidence recalculated as new evidence arrives). **Where evidence is linked**: the
`FundingRelationship` → `Launch` → `Wallet.roles[]` chain, all keyed by wallet address and
transaction signature — no separate join table needed if `Wallet` remains a single
canonical row per address. **Where evidence is persisted**: everything, permanently —
this is deliberately NOT a windowed/pruned model (the current windowing X37.0 identified
as a limitation was an artifact of separate pipelines each keeping their own narrow
retention, not a deliberate design choice worth preserving).

## Phase 6 — Current vs Target Architecture

| Current table | Disposition | Justification |
|---|---|---|
| `wt_subprov_evidence` | **Retain, reframe as PrimitiveObservation seed** | Already append-only, immutable, correctly scoped to raw fact capture — closest existing table to the target model |
| `wt_provisioning_edges` | **Merge into FundingRelationship, fed by both cascade and walkback** | Currently only walkback-fed; the target model has it consume from PrimitiveObservation regardless of origin pipeline |
| `wt_walkback_queue` | **Split**: processing-state columns (`status`, `attempts`, `started_at`) **retain** as a legitimate work-queue; evidentiary result columns (`funder_wallet`, `funding_mechanism`, `funder_amount_sol`) **derive** into PrimitiveObservation/FundingRelationship instead of living queue-locally | Conflates orchestration state with evidentiary content today; these are different concerns |
| `wt_watchtower_launches` | **Retain structure, broaden inputs** | The Launch entity's shape is fine; the problem is it's fed by only 2 of the possible evidence sources (live cascade + 2-day backfill) instead of also being backfillable from FULL_WALKBACK reconstructions |
| `wt_confirmed_treasuries` | **Replace with Wallet.roles[] derived from AttributionEvidence** | Currently a mutable current-state table that competes with `operator_entities` for the same responsibility; the target model has exactly one role-assignment surface |
| `wt_treasury_fingerprint_decisions` | **Retain as-is, generalize to AttributionEvidence** | Already the correct shape (append-only decision ledger) — this table is the existing template the rest of the identity layer should be rebuilt to match |
| `wt_ops_v2` | **Merge with `operators`/`operator_entities` into Operation** | Two competing partial implementations of the same entity; `operator_entities`' 100% identity coverage should absorb `wt_ops_v2`'s treasury-rooted structural fields |
| `operators` / `operator_entities` | **Merge into Operation (see above)** | Same reasoning, opposite direction — this table has better identity coverage but weaker structural linkage than `wt_ops_v2` |
| `wt_attribution_outcomes` | **Retain, treat as a specialized view of AttributionEvidence** | Not deeply investigated in this pass; flagged as needing its own review before merging, since it wasn't part of X37.0's five-table scope |

## Phase 7 — Migration Strategy (order and dependencies only, no effort estimates, no code)

1. **PrimitiveObservation first** — this is the foundational layer everything else derives
   from; it can be populated by reconciling `wt_subprov_evidence` (already closest to
   correct) with the walkback pipeline's independently-captured wrap-close signatures,
   deduplicating on transaction signature. This has no dependency on any other migration
   step and de-risks everything downstream.
2. **FundingRelationship second** — once PrimitiveObservation exists as a single source,
   `wt_provisioning_edges` can be rebuilt to derive from it rather than being fed only by
   the walkback path. Depends on step 1.
3. **AttributionEvidence third** — `wt_treasury_fingerprint_decisions` already has the
   right shape; this step is primarily about making it the *sole* write path for role
   assignment, superseding direct writes to `wt_confirmed_treasuries`. Independent of
   steps 1-2, but should follow them so the eventual Operation assembly (step 5) has both
   identity and structural layers ready.
4. **Launch fourth** — broadening `wt_watchtower_launches`'s inputs to include
   FULL_WALKBACK-reconstructed launches, not just live-cascade + narrow backfill. Depends
   on step 2 (needs FundingRelationship to identify which walked-back mints terminate in
   a CREATE).
5. **Operation fifth** — merging `wt_ops_v2` and `operator_entities` is the highest-risk
   step (see below) and should come last, once steps 1-4 give it a complete evidentiary
   foundation to aggregate over. Depends on all prior steps.

**Backward compatibility**: every current table can remain readable (even if
deprecated) during migration, since the target model is additive at the observation layer
— nothing needs to be deleted until the new layer is verified to reproduce the old
tables' query results.

**Highest-risk transitions**:
- **Merging `wt_ops_v2` and `operator_entities`** — these have different keys
  (`treasury_root` vs `entity_address`+`operator_id`) and different confidence
  vocabularies (numeric `confidence REAL` vs string `confidence TEXT`); reconciling them
  risks silently conflating two different operators if the merge logic is wrong, since
  `wt_ops_v2` has looser identity coverage (19%) than `operator_entities` (100%).
- **Deduplicating PrimitiveObservation across cascade and walkback** — if a transaction
  signature isn't a reliable enough key (e.g. if one pipeline stores a different
  signature for what's conceptually "the same event," as could happen with multi-instruction
  transactions), naive dedup could either double-count or incorrectly merge distinct events.
- **Moving `wt_confirmed_treasuries` write-access to AttributionEvidence-only** — this is
  a behavior change for every manual dashboard action (`promote_to_confirmed`, approve-
  candidate, subprov-link) and the automated LAUNCH_CHAIN path; any of these four call
  sites not fully migrated would silently continue writing to the old table, recreating
  exactly the divergence this design is meant to eliminate.

## Architectural Risks

- The merge of two competing "Operation" tables is inherently the riskiest single step,
  because it's the only place two independently-evolved identity models must agree on
  ground truth.
- A "single canonical owner per fact" design only holds if every writer is actually
  migrated — a single missed call site (e.g. a script that still does
  `INSERT INTO wt_confirmed_treasuries` directly) silently reintroduces the divergence.
  This is a process/discipline risk more than a schema risk.
- PrimitiveObservation's correctness depends on transaction-signature-based dedup being
  reliable across two pipelines that were never designed to share a key space — this
  should be empirically verified (not assumed) before treating step 1 as complete.

## Design Principles (summary)

1. Only record genuinely new facts once, at the lowest possible layer (on-chain
   observation or human/automated attribution decision) — everything else derives.
2. Append-only by default; mutable "current state" tables are the exception, reserved
   only for orchestration/queue concerns (like `wt_walkback_queue`'s processing status),
   never for evidentiary facts.
3. One entity, one writer surface — the recurring failure mode found across X37.0 and
   this design (`wt_confirmed_treasuries` vs `operator_entities`; `wt_ops_v2` vs
   `operators`) is multiple independently-evolved tables competing for the same
   responsibility.
4. Prefer merging into the more complete existing table over building a new one —
   `operator_entities`' 100% identity coverage and `wt_treasury_fingerprint_decisions`'
   already-correct append-only shape should be the templates other entities converge
   toward, not replaced by a fresh design.

## Answer to the stated success criterion

**Yes, architecturally — the current system can be reorganised into a single evidence
architecture with one authoritative owner per fact**, and importantly, the reorganisation
is less about inventing new structures than about **consolidating existing ones that
already partially work**: `wt_subprov_evidence` and `wt_treasury_fingerprint_decisions`
are already close to the target append-only observation/evidence model, and
`operator_entities` already has complete (100%) identity coverage that the structural
tables lack. The real work is (1) establishing PrimitiveObservation as the single
reconciliation point between the cascade and walkback pipelines, and (2) merging the two
currently-competing Operation implementations (`wt_ops_v2` and `operators`/
`operator_entities`) into one. Neither requires abandoning what exists — both are
consolidation problems, not green-field design problems, which meaningfully lowers the
risk of this migration compared to a full rebuild.
