# Sprint X21 — Operator Evolution & Intelligence Timeline

**Status:** architecture only — no code, schema, or UI implemented in this document
**Depends on:** the persistence audit performed as a prerequisite to this design (summarized in §1)
**Constraint carried forward:** no detector, walkback, attribution, behaviour-engine, assessment-engine, forecast-engine, or promotion-logic changes. Consume persisted intelligence only, or add narrowly-scoped new persistence where the audit proves none exists.

---

## 1. Source of truth: the persistence audit

A read-only investigation of the live schema and write paths (both DBs, `flex_complete_database.db` and `wt_ops_v2.db`) produced this capability matrix. It is treated as ground truth for everything below — no capability is assumed reconstructable unless the audit found append-only, timestamped evidence for it.

| Capability | Existing persisted source | Reconstructable | New persistence required |
|---|---|---|---|
| First seen | `operators.first_seen` | Yes | None |
| First launch | `operator_observations` (`observation_type='LAUNCH'`, MIN(timestamp)) | Yes | None |
| Campaign history | `operator_observations` (`CAMPAIGN`/`LAUNCH` types) | Partial (sparse today, structurally sound) | None |
| Treasury history (who, when) | `operator_entities` (accumulates rows), `operator_evidence` | Yes | None |
| Treasury rotations (A→B) | none — `wt_rotation_candidates` schema exists but empty; no "replaced by" event anywhere | No | Lightweight event log |
| Sub-provisioner history | `wt_discovered_subprovs` (current-state only, UPDATE-in-place) | Partial | Lightweight event log |
| Funding template changes | `wt_discovered_subprovs.funding_mechanism` — confirmed UPDATE-in-place (`walkback_worker.py:400`) | No | Lightweight event log |
| Identity confidence over time | not stored; computed live in `PromotionDecisionEngine.decide()` from append-only observations/evidence | Yes, via replay | None (if replay-on-read is acceptable) |
| Identity class acquisition sequence | `operator_evidence.evidence_type`/`created_at`, `operator_observations.observation_type`/`timestamp` | Yes | None |
| Promotion state transitions | `operator_promotion_reviews` (decision events, immutable via DB triggers) logs only APPROVE/REJECT/DEFER, not every intermediate computed state | Partial | Lightweight event log |
| Behaviour evolution | none — `BehaviourEngine`/`BehaviourChangeEngine` compute on demand, no operator-level persistence found | No | New snapshot/capture |
| Assessment evolution | none — `AssessmentEngine.assess()` computes in-process, never written | No | New snapshot/capture |
| Forecast evolution | in-memory only (`collections.deque`, `forecast_routes.py`, max 20, lost on restart) | No | New snapshot/capture |
| Similarity evolution | in-memory only (`OperatorSimilarityEngine._store()`, single snapshot, overwritten) | No | New snapshot/capture |
| Observation growth | `operator_observations.timestamp` (append-only) | Yes | None |
| Infrastructure growth | `operator_entities.first_seen` per `entity_type` | Yes | None |

Two operator data models coexist: the legacy cluster model (`wt_operator_clusters`, `wt_operator_treasuries`, `wt_operator_launches`, all in `flex_complete_database.db`, mostly UPDATE-in-place) and the newer identity model (`operators` / `operator_entities` / `operator_evidence` / `operator_observations` / `operator_promotion_reviews`, all in `wt_ops_v2.db`, append-only by construction).

## 2. Architectural decision: canonical model

**`operators`, `operator_entities`, `operator_evidence`, `operator_observations`, and `operator_promotion_reviews` — all in `wt_ops_v2.db` — are the only canonical operator model.** This is not a preference among options; it is the single source of truth for everything X21 builds. No other table, in either database, is ever a second source of truth for operator identity, history, or state.

`wt_operator_clusters` and its siblings (`wt_operator_treasuries`, `wt_operator_launches`, all in `flex_complete_database.db`) are **legacy provenance/context only.** They may be read for historical color when a specific operator happens to have cluster-era data predating its entry into the canonical model, but they are:
- never a write target for anything X21 produces,
- never a second timeline source consulted at read time alongside the canonical model,
- never silently merged, joined, or reconciled against canonical rows in any query the timeline UI depends on.

**If historic cluster-era data is ever migrated in, it must be a one-time, explicitly-scoped migration that writes into the canonical model (e.g. backfilled `operator_observations`/`operator_evidence` rows carrying their true historical timestamps) — never a permanent dual-read architecture where the timeline queries both models at request time.** A dual-read design would mean every future timeline feature has to reason about two different trust levels and two different consistency guarantees simultaneously; a one-time migration collapses that back down to one model, one ledger, one set of guarantees. This distinction is treated as non-negotiable for X21 — it is the single most important structural decision in this document, because every downstream Fact/Metric/Opinion definition below assumes one ledger, not two.

Rationale: the canonical model is the only one architected as append-only from the ground up (no compensating triggers were needed because writes never overwrite prior rows — confirmed for `operator_observations`, `operator_evidence`; `operator_promotion_reviews` additionally has DB-enforced immutability triggers). The legacy model is mostly UPDATE-in-place and was never designed to preserve history. Extend the model that already behaves like a ledger; don't resurrect the one that doesn't, and don't build a bridge that lets it masquerade as one.

## 3. Facts, Metrics, and Opinions

Everything the timeline shows falls into exactly one of three categories. These categories are not implementation tiers (that's §5's job) — they are a classification of *what kind of truth a piece of information is*, and that classification determines how it may ever be labelled in the UI.

### FACTS — immutable, append-only, historical truth

A Fact is something that is recorded as it happens and never changes once recorded. A Fact is real history — if the UI shows a Fact, it is asserting "this occurred," not "this is our current best guess."

Examples: treasury added, treasury removed, treasury rotation, sub-provisioner added, sub-provisioner removed, funding template changed, identity class acquired, promotion decision, observation created.

Facts are exactly the rows already covered by the append-only tables in §1 (`operator_observations`, `operator_evidence`, `operator_entities`, `operator_promotion_reviews`) plus the new structural events proposed in §4.1–4.3 (`operator_timeline_events`). A Fact table is never updated or deleted after insert; that is what makes it a Fact rather than a Metric or an Opinion.

### METRICS — derived from Facts, reconstructed, not independently authoritative

A Metric is a computation performed over Facts. It has no existence independent of the Facts it's computed from — recomputing it from the same Facts always produces the same answer, and if the underlying Facts are wrong, the Metric is wrong too, but the Metric itself was never "recorded" as a separate act of observation.

Examples: launch frequency, campaigns over time, infrastructure growth, observation growth, confidence replay, operator maturity.

**Metrics are never persisted unless there is a proven operational reason to do so** (e.g. a specific metric is measured to be too expensive to recompute on every page load, and that cost is demonstrated, not assumed). The default is: compute Metrics at read time from Facts, every time. This is exactly what Tier 1 / X21A does (§5) — it is a Metrics layer, not a Facts layer, and it must never write anything.

### OPINIONS — engine interpretations, legitimately revisable

An Opinion is a judgment produced by an engine — Behaviour, Behaviour Change, Assessment, Forecast, Similarity. Opinions differ from Metrics in a way that matters: a Metric recomputed from the same Facts is always the same number, but an Opinion recomputed with an *improved engine* can legitimately produce a *different* answer for identical underlying Facts. That's not a bug or a data inconsistency — it's the engine getting better at its job.

**Opinions must never be described as historical truth.** If an Opinion is shown for a past date, the UI must make unambiguous that it reflects what today's engine concludes when looking at that date's Facts — not what was concluded at that time (see §4 below on replay vs. recorded history).

### Three-tier architecture, restated against this classification

- **Tier 1 (X21A) — Metrics, computed at read time from Facts.** No new tables. No new writes. Covers: confidence evolution (replay — a Metric, and specifically the retrospective-analysis case discussed in §4 below, not recorded history), identity-class acquisition, observation growth, infrastructure growth, promotion decision history (Facts, read directly — this specific item is actually a Fact passthrough, not a Metric, since `operator_promotion_reviews` rows are the recorded decisions themselves).
- **Tier 2 (X21B) — new Facts.** One new table, `operator_timeline_events`, recording genuinely new Facts (treasury rotation, sub-provisioner rotation, funding-template change) that the audit proved have no compensating history today.
- **Tier 3 (X21C) — Opinions, optionally persisted.** Snapshot tables for Behaviour, Behaviour Change, Assessment, Forecast, Similarity — see §6 for the now-narrowed scope and the condition under which this tier is built at all.

## 4. Replay vs. historical truth

`PromotionDecisionEngine.decide()` is not a stored history — it is an engine that can be re-run against any time-bounded slice of Facts (`WHERE timestamp <= cutoff` on `operator_observations`/`operator_evidence`). Running it against a past cutoff produces an Opinion about that past date, computed with today's logic. This must be sharply distinguished from a Fact that actually occurred at that date:

- **Recorded history** — an immutable Fact that actually occurred: "on 2026-05-02, a `PROMOTION_APPROVED` decision was recorded" (an `operator_promotion_reviews` row, with its own real timestamp). This is not replay. It happened.
- **Retrospective analysis** — a Metric/Opinion produced by asking today's engine to look at yesterday's Facts: "using the current identity model, this operator would have scored 0.71 on 2026-05-02." This did not happen on 2026-05-02 — it is a computation performed *now*, about *then*, using logic that may not have existed then.

**The UI must never imply that retrospective replay is recorded history.** Concretely: any confidence value, promotion-stage classification, or similar Metric/Opinion computed by replaying an engine against a historical cutoff must be labelled

> Reconstructed using the current identity model.

and must **never** be labelled

> Historical confidence.

The distinction is not cosmetic. "Historical confidence: 0.71" tells an analyst the system actually believed 0.71 back then. "Reconstructed using the current identity model: 0.71" tells the analyst the system believes it *now*, applied retroactively — which is the true and only claim that can honestly be made, since no confidence value was ever persisted at the time (§1, "Identity confidence over time" — reconstructable via replay, not recorded). This labelling rule applies to every Tier 1/X21A view that touches confidence-over-time, and to any Tier 3/X21C Opinion ever shown against a date before that Opinion type had persisted snapshots (see §6).

## 5. Derived Explanation Events ("Reason Events")

The timeline should not just show *that* something changed — it should explain *why*, in terms an analyst can verify against evidence. This is a new architectural concept, but it introduces **no new persistence**.

A Derived Explanation Event is generated at render time from Facts (and, where relevant, from the Metric/Opinion computation that produced the change being explained). It is not stored, has no independent identity, and is regenerated fresh every time the timeline is rendered — if the underlying Facts change (e.g. more observations arrive, or an X21B rotation Fact is added), the explanation regenerates accordingly.

Illustrative shape (rendering concept, not a schema):

```
Identity confidence increased
  0.56 → 0.74
  Reason: Second independent identity class acquired
  Evidence: CONFIRMED_INFRASTRUCTURE_REUSE

Assessment changed
  MEDIUM → HIGH
  Reason: 12 campaigns observed

Forecast changed
  OBSERVING → ESCALATING
  Reason: Campaign tempo exceeded historical baseline
```

Each Reason Event is produced by a small, explicit rule that maps a detected change (a Metric delta, or a new Opinion differing from the prior Opinion) to the specific Fact(s) or evidence category that most plausibly explains it — e.g. "confidence rose because a second `evidence_type` first appeared in `operator_evidence`" is a direct, citable link to real rows, not a guess. Where no single Fact cleanly explains a change (e.g. an Opinion changed because of gradual accumulation rather than one discrete event), the Reason Event should say so rather than inventing a specific cause — a Reason Event is only as trustworthy as the Fact it cites, and fabricating a plausible-sounding but unverified reason would be worse than omitting one.

Reason Events apply uniformly to Metric changes (Tier 1) and Opinion changes (Tier 3, if/when built) — in both cases they improve analyst understanding of *why* a number moved, without requiring a new table, a new write path, or any change to the engines producing the underlying numbers.

---

## 6. Event/snapshot specifications

Every new persisted row (Tier 2 and Tier 3) follows the same design template, using conventions already established in this codebase (`operator_observations`/`operator_evidence`/`operator_promotion_reviews` in `src/ops/operator_model.py`, `src/ops/observation_store.py`): string-UUID primary key, epoch-second integer timestamp, JSON-text metadata blob, FK to `operators(operator_id)` where applicable, written exclusively through `database_write_service.submit(database, command, transaction_callback)` (`src/core/database_write_service.py:194`) — never a direct `sqlite3` write, since that service is the sole writer-thread owner for `wt_ops_v2.db`.

### 6.1 Treasury rotation event

- **Exact producer:** whatever code path currently updates `wt_confirmed_treasuries`/`wt_treasury_review` state for an operator's treasury set — most likely `src/core/treasury_bank.py` or the walkback worker's treasury-confirmation step (needs a precise call-site identification pass before implementation; not pinned down by the read-only audit, which found the *tables* but not every writer).
- **Emission trigger:** a treasury previously associated with an operator (via `operator_entities`, `entity_type='TREASURY'`) stops being the active/current treasury and a different treasury becomes active for the same operator. This is a *comparison* event — it fires when a write-time check finds "operator X's active treasury set changed since the last observation," not on every treasury-related write.
- **Canonical fingerprint:** `sha256(operator_id | 'TREASURY_ROTATED' | old_treasury_address | new_treasury_address)` — deterministic on the (operator, old, new) triple so the same rotation detected twice (e.g. by two overlapping scans) collides rather than duplicating.
- **Deduplication rule:** `UNIQUE(operator_id, event_type, fingerprint)` constraint on `operator_timeline_events`; producer does an idempotent `INSERT ... ON CONFLICT(fingerprint) DO NOTHING` inside the `submit()` transaction callback (mirrors the manual pre-check + deterministic-UUID pattern already used in `promotion_service.py:249,263,328`).
- **Database target:** `wt_ops_v2.db`, table `operator_timeline_events`.
- **Write path:** `database_write_service.submit("wt_ops_v2", "operator-timeline-treasury-rotated", transaction_callback)` — one callback per detected rotation, following the exact pattern in `promotion_service.py:261-319` (idempotency check via SELECT, then INSERT, all inside one service-owned transaction).
- **Retention policy:** none (append-only, matches every other `operator_*` table — the audit confirmed zero pruning exists for this table family; do not introduce pruning for this new table either without a separate, explicit decision).
- **Backfill feasibility:** **not feasible from current data.** `wt_confirmed_treasuries`/`wt_treasury_review` are UPDATE-in-place with no history; the audit found no prior-value trail. Rotations that happened before X21B ships cannot be reconstructed.
- **Historical gap disclosure:** **required.** The UI must show "treasury rotation tracking began [X21B ship date]" and explicitly render pre-ship treasury changes as "not captured" rather than silently starting the timeline at zero rotations (which would misleadingly imply stability).

### 6.2 Sub-provisioner rotation event

- **Exact producer:** the walkback worker's subprov-discovery/update path (`src/core/walkback_worker.py`, same file that does the confirmed UPDATE-in-place on `funding_mechanism` at line 400 — needs precise function-level identification before implementation).
- **Emission trigger:** an operator's associated sub-provisioner set (via `operator_entities`, `entity_type='SUBPROV'`) changes membership — a new subprov appears as active for an operator that previously had a different one.
- **Canonical fingerprint:** `sha256(operator_id | 'SUBPROV_ROTATED' | old_subprov | new_subprov)`.
- **Deduplication rule:** same `UNIQUE(operator_id, event_type, fingerprint)` constraint, same ON CONFLICT DO NOTHING pattern.
- **Database target:** `wt_ops_v2.db`, `operator_timeline_events`.
- **Write path:** same `database_write_service.submit()` pattern, distinct command name `"operator-timeline-subprov-rotated"`.
- **Retention policy:** none (append-only).
- **Backfill feasibility:** **not feasible.** `wt_discovered_subprovs` is current-state-per-subprov with no per-operator association history (confirmed by audit: 1,082 rows, each a snapshot, no join-over-time table).
- **Historical gap disclosure:** required, same framing as 6.1.

### 6.3 Funding-template change event

- **Exact producer:** `src/core/walkback_worker.py:400`, the exact line the audit confirmed does `UPDATE ... funding_mechanism=?, updated_at=?` — the emission hook is a direct addition next to this existing write, not a new scan.
- **Emission trigger:** the walkback worker is about to overwrite `wt_discovered_subprovs.funding_mechanism` for a subprov, and the new value differs from the value currently in the row (a plain "before != after" check read immediately before the UPDATE, inside the same transaction).
- **Canonical fingerprint:** `sha256(operator_id | subprov_address | 'FUNDING_TEMPLATE_CHANGED' | old_mechanism | new_mechanism | rounded_timestamp_bucket)` — the timestamp bucket (e.g. day-granularity) guards against the same logical change firing twice if the worker re-derives the same before/after pair on a retry within the same run.
- **Deduplication rule:** same table/constraint as above; this is the one event type where the "before" value is known precisely (it's the row being overwritten), so the fingerprint can be exact rather than approximate.
- **Database target:** `wt_ops_v2.db`, `operator_timeline_events`.
- **Write path:** the emission call sits *inside* the same `database_write_service.submit()` transaction that performs the `UPDATE` on `wt_discovered_subprovs` — both writes commit atomically together, or neither does. This is the one event type where "hook without modifying logic" means literally one extra `INSERT` statement adjacent to an existing `UPDATE`, not a new call site.
- **Retention policy:** none.
- **Backfill feasibility:** **not feasible for prior changes** (only the current value is visible; 1,050 of 1,082 subprov rows don't even have a value set today, per the audit). Feasible **going forward from ship date only.**
- **Historical gap disclosure:** required.

### 6.4 Behaviour / Behaviour Change / Assessment / Forecast / Similarity snapshots (X21C, conditional — see §7)

These are Opinions, not Facts (§3). Persisting them is **not assumed necessary** — this specification exists so that *if* §7's condition for building X21C is met, the design is ready, not so that X21C is treated as a default part of the sprint. All five follow one shared pattern, differing only in producer and payload shape:

- **Exact producer:** the existing engine's existing call site — `BehaviourEngine`/`BehaviourChangeEngine` (wherever `forecast_routes.py` currently imports and invokes them), `AssessmentEngine.assess()` (`src/ops/assessment_engine.py`), the forecast computation in `forecast_routes.py`, `OperatorSimilarityEngine.compute_snapshot()` (`src/ops/operator_similarity.py:787`). **No engine's internal computation changes.** The hook is a single new line immediately after each engine returns its result object, before that result is handed to the caller/response.
- **Emission trigger:** every time the engine is invoked and produces a result (i.e., emission frequency equals current invocation frequency — not a new schedule, not a new trigger condition). If the engine is only ever called on-demand per page load today, snapshots accumulate at that same on-demand cadence; X21C does not add a cron job to force periodic snapshots, since that would be new engine-adjacent scheduling behavior beyond "hook the existing output."
- **Canonical fingerprint:** `sha256(operator_id | snapshot_type | serialized_result_payload)` — content-addressed, so if the engine is invoked twice in quick succession with an unchanged input (e.g. two page loads with no new observations in between) and produces byte-identical output, the second call does not create a redundant row.
- **Deduplication rule:** `UNIQUE(operator_id, snapshot_type, fingerprint)`; write path does `INSERT ... ON CONFLICT(fingerprint) DO NOTHING` — cheap no-op on unchanged output, real row on genuine change. This makes "how many times did behaviour actually change" a direct row-count query rather than requiring de-noising at read time.
- **Database target:** `wt_ops_v2.db`, four new tables — `operator_behaviour_snapshots`, `operator_assessment_snapshots`, `operator_forecast_snapshots`, `operator_similarity_snapshots` (kept separate per engine rather than one polymorphic table, matching the existing convention of `operator_evidence` vs `operator_observations` being separate typed tables rather than one blob table).
- **Write path:** `database_write_service.submit("wt_ops_v2", "<engine>-snapshot", transaction_callback)`, called from the route/service layer immediately after the engine call, not from inside the engine itself — preserves "no engine changes."
- **Retention policy:** none initially (append-only, matches family convention); flagged as a future capacity concern since these four engines are likely invoked far more often than treasury/subprov rotations (page-load cadence vs structural-change cadence) — **this is the one place in the design where unbounded growth risk is materially higher than the rest of the operator_* family**, and a follow-up decision (e.g. day-bucketed retention, or capping snapshot frequency to "first-per-day unless content changed") should be made once real volume is observed, not preemptively.
- **Backfill feasibility:** **not feasible.** Forecast and similarity are confirmed in-memory-only and already lost on every process restart; behaviour and assessment were never persisted at all. History for all four starts at X21C ship date, zero exceptions.
- **Historical gap disclosure:** required, and the most important one — the UI's "Behaviour Evolution," "Assessment Evolution," "Forecast Evolution," and "Similarity Evolution" sections must not imply continuous history predating X21C. Render explicitly: "Snapshot history available from [date]; no prior data exists for this operator."

---

## 7. Staged implementation plan

**X21A — Derive-only timeline (Metrics + Facts passthrough + Reason Events)**
Build `TimelineDerivationService` reading `operator_observations`, `operator_evidence`, `operator_entities`, `operator_promotion_reviews`, and generating Derived Explanation Events (§5) at render time. No new tables, no new writes. Ships: first-seen, first-launch, confidence-at-time-T (retrospective analysis, labelled per §4 — never "historical confidence"), identity-class acquisition order, observation-count growth curve, infrastructure-count growth curve, promotion decision history (approve/reject/defer Facts, read directly — not fine-grained intermediate states), and reason-for-change explanations wherever a Metric moved. This alone answers "how did we get here" for everything except structural rotations and the (as-yet-unpersisted) engine Opinions.

**X21B — Lightweight structural-event log (new Facts)**
Add `operator_timeline_events` (single table, typed by `event_type`) plus the three emission hooks (§6.1–6.3). Ships: treasury rotation, sub-provisioner rotation, funding-template change Facts — **only from ship date forward**; historical gap disclosed per §6.1–6.3.

**X21C — Versioned engine snapshots (Opinions) — optional, conditional on production evidence**

X21C is **not an assumed requirement of this sprint.** The sequencing is deliberate:

1. **X21A ships and runs in production first**, with no Opinion history persisted at all — Behaviour, Assessment, Forecast, and Similarity remain exactly as they are today (computed on demand, forecast/similarity still in-memory-only).
2. **Real analyst usage determines whether persistent Opinion history is actually needed.** Specifically: do analysts, in practice, need to see how an Opinion changed over multiple past instants, or is "what does the engine conclude right now, with a Reason Event explaining the current value" sufficient for how the timeline is actually used? This is an empirical question, not one this document can answer in advance.
3. **Only if that need is demonstrated** does X21C — the snapshot tables and hooks specified in §6.4 — get built. Until then, §6.4 is a ready specification, not a commitment.

**Unnecessary persistence must not be introduced merely because it is technically possible.** The four proposed snapshot tables in §6.4 are the highest-write-volume, highest-unbounded-growth-risk piece of this entire design (per the note already in §6.4's retention discussion) — building them speculatively, before any analyst has asked "what did the forecast say last week," would be exactly the kind of premature persistence this refinement is meant to prevent.

Each stage is independently shippable; X21B does not block on X21C, and X21C should not be started at all until X21A has real production usage to justify it.

---

## 8. Unresolved decisions

1. **Exact producer identification for §6.1 and §6.2.** The audit found the *tables* (`wt_confirmed_treasuries`, `wt_discovered_subprovs`) but not the precise function/line that would need the emission hook, unlike §6.3 where `walkback_worker.py:400` is pinned exactly. Needs a targeted code-reading pass before X21B implementation starts.
2. **Snapshot frequency ceiling for X21C (if built).** Content-addressed dedup (§6.4) prevents identical-output duplication, but if an engine is invoked on every page load and produces slightly different output each time (e.g. floating-point similarity scores that drift by noise rather than real signal), the table could still grow one row per page view. Whether to add a minimum-delta threshold (e.g. "only snapshot if confidence changed by >0.02") is unresolved — doing so would be a judgment call about what counts as a "real" change vs noise, deferred to when real data is available, and moot entirely unless §7's condition for building X21C is ever met.
3. **Whether `operator_promotion_reviews`' decision-only granularity (Facts passthrough, X21A) is sufficient, or whether the brief's "Monitoring → Review Candidate → Promotion Eligible → Canonical" staged narrative requires X21B-style intermediate-state events sooner than planned.** Currently scoped as "good enough for X21A via retrospective replay of `PromotionDecisionEngine.decide()`, clearly labelled per §4" but this assumes replay produces a stable, explainable staged narrative — unverified until X21A is built and tested against real operators.
4. **Legacy cluster-model backfill.** §2 requires that any future migration of cluster-era history be a one-time write into the canonical model, never a dual-read bridge — but whether that migration is ever worth doing (some canonical operators may have meaningful cluster-era history predating their promotion into the new model) is left for a future decision, not part of X21A/B/C.
5. **Mission Control event surfacing cadence.** The brief wants Mission Control to show evolution events ("identity confidence increased from 0.42 to 0.71") — this depends on X21A existing first (and, for Opinion-based events, on whether X21C is ever built) and is not separately staged here; it's a consumer of the timeline and its Reason Events (§5), not a fourth persistence tier, but its exact surfacing rules (which event types, what threshold makes something "mission-control-worthy" vs merely timeline detail) are unresolved.
6. **Reason Event rule coverage (§5).** The mapping from a detected Metric/Opinion change to a specific citable Fact is only sketched by example here — the actual rule set (which change patterns map to which Fact categories, and what the fallback text is when no single Fact explains a change) needs to be worked out against real operator data during X21A implementation, not assumed complete from this document alone.

## 9. Recommended minimum viable scope

**X21A only**, as a first deliverable: the Metrics/derivation layer, the Facts passthrough for promotion history, and Reason Event generation (§5) — all read-only, all computed at render time. It requires zero new tables, zero new writes, and zero risk to existing engines or write paths. It directly answers the sprint's most valuable questions (first seen, how confidence evolved, identity-class acquisition order, growth curves, promotion decisions, and *why* each of those moved) without touching anything currently in flux (unresolved decision #1) or carrying unbounded-growth risk (unresolved decision #2, which does not even apply unless X21C is later triggered). X21B should be scoped and reviewed as its own follow-on decision once X21A is running against real operators and the producer-identification question has a concrete answer. **X21C should not be scheduled at all** until X21A's production usage demonstrates that persisted Opinion history is actually needed (§7) — it remains a specified-but-optional capability, not an assumed third phase.
