# X67.23 — Emerging Operators Operational Audit

**Read-only.** No code, database, commit, or label changes made. Diagnostic only — architecture trace, DB queries, and a manual read-only replay of the gate logic. No RPC used.

## Phase 1 — Architecture (verified end-to-end, traced from code)

```
UI: templates/emerging_operators.html  (route /intelligence/emerging-operators)
     sidebar link: templates/partials/sidebar.html:18
        │
        ▼
API: src/ops/operator_routes.py
     _get_emerging_service() (lazy singleton, lines 29,49-55)
     GET /api/ops/emerging-operators              -> EmergingOperatorService.list()
     GET /api/ops/emerging-operators/<entity>      -> EmergingOperatorService.get()
        │
        ▼
Service: src/ops/emerging_operator_service.py (340 lines, fully read)
     EmergingOperatorService.__init__(ops_db_path, live_db_path)   — read-only connections
     ._compose() (line 128):
        1. calls OperatorResolver(...).evaluate()  → src/ops/operator_resolver.py (identity graph)
        2. calls PromotionDecisionEngine().decide(evaluation) → src/ops/identity_framework.py
        3. seeds = emerging_operator_seeds(ops_conn)   [THE membership contract]
        4. for each seed: joins wt_walkback_queue / wt_token_lifecycle / (live db) wt_ops_v2_creators
           to enrich creators/subprovs/treasuries/funding templates
        5. attaches identity_evidence + promotion_handoff from steps 1-2
        │
        ▼
Membership contract: src/ops/attribution_outcome.py:641 emerging_operator_seeds(conn)
     SELECT wt_unknown_infrastructure_registry r
     WHERE r.eligible=1
       AND EXISTS (SELECT 1 FROM wt_attribution_outcomes o
                   WHERE o.terminal_entity=r.terminal_entity
                     AND o.outcome_type='UNKNOWN_INFRASTRUCTURE'
                     AND o.should_seed_emerging_operator=1)
        │
        ▼
Writer of wt_unknown_infrastructure_registry + wt_attribution_outcomes:
     src/ops/attribution_outcome.py
       derive_outcome()   (line 417) — THE classification engine (NOT watchtower_funnel.py)
       persist_outcome()  (line 548) — UPSERTs wt_attribution_outcomes,
                                        and (lines 576/598) INSERT/UPDATE wt_unknown_infrastructure_registry
       materialize_outcome() (attribution_outcome.py ~line 605) — orchestrates derive+persist per mint
        │
        ▼
Trigger / scheduler:
     src/core/walkback_worker.py:736,751,768 — calls materialize_outcome() after every terminal
        walkback status; walkback_worker runs as supervisord program [program:walkback_worker]
        (config/supervisor/supervisord.conf:268-294), loop mode, every 45s, batch=8.
        CONFIRMED RUNNING: `ps aux` shows live PID 48255,
        `python -m src.core.walkback_worker --loop`, 7h17m CPU time accumulated.
     src/core/walkback_queue.py:409-410,442-443,467-468 also calls materialize_outcome()
        inline at other terminal-state transition points.
        │
        ▼
watchtower_funnel.py — NOT part of the write/classification path at all. It is a
     READ-ONLY reporting funnel (src/core/operation_dashboard_routes.py:8686-8687);
     build_watchtower_funnel() re-derives population counts from persisted tables
     for a stage-loss dashboard, and separately recomputes a cosmetic
     "should_seed_emerging_operator": key=='UNKNOWN_INFRASTRUCTURE' flag purely
     for its own outcomes-breakdown display (line 188) — this has ZERO write
     effect and does not gate anything. This corrects the audit-background
     assumption that watchtower_funnel.py is "the decision engine."
```

Promotion path: `EmergingOperatorService._candidate()` attaches `promotion_handoff` (only if `PromotionDecisionEngine` returns `PROMOTION_ELIGIBLE`), linking to `/intelligence/operator-promotions`, `requires_analyst_approval: true`. Promotion itself is a human/analyst-gated separate workflow (not auto-promotion) — matches the memory note "SCORE roles + human-confirm, NEVER auto-re-root."

## Phase 2 — Data Model

**`wt_attribution_outcomes`** (`database/wt_ops_v2.db`) — PK `mint`. Purpose: one immutable typed terminal-outcome row per walkback-completed mint. Columns include `outcome_type` (CHECK-constrained to 10 values), `terminal_entity`, `terminal_entity_type`, `confidence`, `evidence_json`, `should_seed_emerging_operator`, `should_retry`, `completed_at`, `materialized_at`. Row count: **13,813** total (`6033 INSUFFICIENT_EVIDENCE + 3535 LINEAGE_GAP + 1113 KNOWN_CEX_REACHED + 663 UNKNOWN_INFRASTRUCTURE + 295 KNOWN_RELAY_REACHED + 151 CANONICAL_OPERATOR_REACHED + 22 KNOWN_MULTI_TOKEN_CREATOR + 1 KNOWN_BRIDGE_REACHED`). Most recent `completed_at`=`materialized_at`=**1785826491** (≈1h before audit time 1785830228) — actively current. Write path: `persist_outcome()` (attribution_outcome.py:548, UPSERT). Read paths: `emerging_operator_seeds()`, `watchtower_funnel.py` (reporting only), `src/ops/discovery_triage.py`, `src/core/main.py:40263`, `src/discovery/service.py:167,1049`.

**`wt_unknown_infrastructure_registry`** — PK `terminal_entity`. Purpose: the actual emerging-operator membership roll (aggregates repeat UNKNOWN_INFRASTRUCTURE observations per entity). Columns: `observation_count`, `confidence`, `eligible`, `first_seen_at`, `last_seen_at`, `first_source_mint`, `latest_source_mint`, `evidence_json`. Row count: **68**, all `eligible=1`. Most recent `last_seen_at`=**1785818261** (~2.8h before audit — current). Oldest `first_seen_at`=1783169776 (~30 days back). Write path: `persist_outcome()` lines 576 (INSERT)/598 (UPDATE) — only fires when `derive_outcome()` returns `UNKNOWN_INFRASTRUCTURE`.

**`wt_treasury_review`** and **`wt_discovered_subprovs`** — the two tables `_known_unknown_infrastructure()` gates on (attribution_outcome.py:399-414). These are the SOLE inputs deciding UNKNOWN_INFRASTRUCTURE vs LINEAGE_GAP. Neither table is `wt_infrastructure_candidates`.

**`wt_infrastructure_candidates`** (2,462 rows, PK `wallet`) — the treasury/hub role-scoring table from prior X67.18-22 audits. **Confirmed via code read: `derive_outcome()`/`_known_unknown_infrastructure()` never queries this table.** It is structurally disjoint from the Emerging Operators pipeline. Overlap check: of the 68 current registry entities, only **9** also appear in `wt_infrastructure_candidates` — i.e., 59/68 (87%) of "emerging operators" have never been scored by the treasury/hub role engine at all, and conversely 2,453/2,462 infrastructure candidates (including B48k and Dv34) never reach the emerging-operators registry.

## Phase 3 — Discovery Pipeline

`derive_outcome()` (attribution_outcome.py:417-541) is a strict if/elif decision tree evaluated once per completed/skipped/failed walkback row:
1. No completed queue row → `None` (no outcome).
2. Treasuries resolve to exactly one canonical `operator_id` → `CANONICAL_OPERATOR_REACHED`.
3. Multiple distinct operator_ids → `AMBIGUOUS_BRANCH`.
4. `_boundary()` hits a known CEX/bridge/relay wallet → `KNOWN_*_REACHED`.
5. Creator is a known serial deployer with an established launcher profile → `KNOWN_MULTI_TOKEN_CREATOR`.
6. Legacy queue flags indicate graph-quality/role-mismatch issues → `AMBIGUOUS_BRANCH`.
7. Legacy `MAX_DEPTH` → `MAX_DEPTH`.
8. **`_known_unknown_infrastructure(conn, terminal)` returns truthy → `UNKNOWN_INFRASTRUCTURE`, `should_seed_emerging_operator=True`.** This function ONLY checks:
   - `wt_treasury_review` row where `has_walkback_evidence` OR `detected_via='walkback_hop2'` OR `distinct_subprovs>=2`, OR
   - `wt_discovered_subprovs` row where `creator_count>=2` OR `wrap_close_count>=2`.
9. Else, if legacy `LINEAGE_GAP` or any treasury/subprov resolved at all → `LINEAGE_GAP` (should_retry=True but never consumed — see Phase 9).
10. Else → `INSUFFICIENT_EVIDENCE`.

**Minimum evidence for UNKNOWN_INFRASTRUCTURE**: the terminal wallet must independently clear one of two thresholds in `wt_treasury_review`/`wt_discovered_subprovs` — critically, `wt_discovered_subprovs` requires `creator_count>=2` OR `wrap_close_count>=2`, i.e., **the wallet must show ≥2 wrap-close creator fan-outs**. This structurally assumes the WSOL_WRAP_CLOSE mechanism. PLAIN_XFER-mechanism wallets like B48k/Dv34 populate `wt_discovered_subprovs` with `creator_count=1, wrap_close_count=1` (both confirmed in DB) — one short of the gate, independent of how many real PLAIN_XFER edges exist elsewhere. This is a genuine structural miss, confirmed empirically below.

Trigger: `walkback_worker.py --loop` (supervisord-managed, confirmed live PID), every 45s.

## Phase 4 — Liveness

- Most recent outcome row: `completed_at=1785826491` (~1.05h before audit time `1785830228`). **Pipeline is current, not stalled.**
- Outcome rows last 7d (cutoff 1785225428): **397**. Last 30d (cutoff 1783238228): **11,369**.
- UNKNOWN_INFRASTRUCTURE specifically: **18** in last 7d, **623** in last 30d.
- `should_seed_emerging_operator=1` total = 663 (matches UNKNOWN_INFRASTRUCTURE count exactly, as expected since it's set 1:1 with that outcome type), 18 in last 7 days.
- `walkback_worker` confirmed running (`ps aux`, PID 48255, 7h17m CPU), supervisord program block at `config/supervisor/supervisord.conf:268-294`.
- Both DB files (`wt_ops_v2.db`, `flex_complete_database.db`) have `mtime` of today (Aug 4), confirming active writes.
- **Conclusion: the pipeline is NOT stalled or dormant.** It is actively classifying new mints daily; the gap is correctness (Phase 6-10), not liveness.

## Phase 5 — Existing Candidates (TRUE count, verified)

**68 candidates** in `wt_unknown_infrastructure_registry` with `eligible=1`, all 68 also satisfying the `emerging_operator_seeds()` EXISTS join (i.e., all 68 are the live projection — this coincidentally matches the number quoted in the task prompt, but is verified independently from the DB, not assumed). Observation-count distribution is heavily skewed: 28/68 (41%) have exactly 1 observation (thin/single-touch), only a handful have double-digit+ observations (one outlier at 455). Sample top-recency rows include `GF7YB1jGktkRQNnXU5YCuVRCQdoHcctkUw1q5bgbLLXc` with 455 observations, last_seen 1785817087. 13 of the 68 have `last_seen_at` older than 30 days (stale — no fresh corroborating observation in the last month, though still `eligible=1` since nothing demotes eligibility besides new evidence).

## Phase 6 — B48k Test

B48k (`B48kNVXs4YK4amkBCH2XokQiv1SeiVQGHDR17xDeKAAn`) **was observed by this pipeline** — 33 rows in `wt_attribution_outcomes` with `terminal_entity=B48k`, spanning `completed_at` 1783386268 → 1785812523 (i.e., actively recurring across the full audit window, latest just ~4.5h before audit time). **Every single one classified `LINEAGE_GAP`, `should_seed_emerging_operator=0`.** Zero rows in `wt_unknown_infrastructure_registry`. Zero rows in `wt_treasury_review`. B48k's `wt_infrastructure_candidates` row: `candidate_role=OPERATIONAL_TREASURY, role_score_treasury=45, role_score_hub=25, status=SHADOW, review_state=PENDING_REVIEW`. B48k's `wt_discovered_subprovs` row: `state=PROVISION_CANDIDATE, confidence=0.5, creator_count=1, wrap_close_count=1` — **fails the `>=2` gate by exactly 1** on both dimensions. Cross-reference confirmed: the gate structurally privileges wrap-close counting (`wrap_close_count`), and B48k's PLAIN_XFER mechanism never produces wrap-close events, so `wrap_close_count` can never organically exceed low single digits regardless of how many real creator-funding edges exist (27 direct + 7 relay-hop confirmed by prior audit). This is the root cause of the miss.

## Phase 7 — Dv34 Test

Dv34prGm (`Dv34prGm2BT7Ph2n6qKLgzeLgjnii87RJJ7Db6ZQQvKM`) — 51 outcome rows, all `LINEAGE_GAP`, spanning 1783383902→1785791134 (recurring through the full window). Zero registry rows. `wt_treasury_review` row: `status=REJECTED, detected_via=auto_fingerprint_nearmiss, has_walkback_evidence=0, distinct_subprovs=1` — this row EXISTS but fails all three gate conditions (`has_walkback_evidence=0`, `detected_via` is not `walkback_hop2`, `distinct_subprovs=1 <2`), and additionally has `status=REJECTED` which the gate function doesn't even check (it only checks the evidence fields, not status — an analyst REJECTED decision is silently ignored either way since the underlying fields already fail). `wt_discovered_subprovs` row: `state=dismissed, creator_count=1, wrap_close_count=1` — same 1-short-of-2 shortfall as B48k. `wt_infrastructure_candidates` row confirms prior audit: `role_score_treasury=65, role_score_hub=25, status=SHADOW`.

**Both misses trace to the identical structural cause**: the `_known_unknown_infrastructure` gate's `wt_discovered_subprovs` threshold (`creator_count>=2 OR wrap_close_count>=2`) was evidently calibrated for wrap-close-mechanism WATCHTOWER subprovs, and both family members sit at exactly `1/1`, one observation short.

## Phase 8 — Historical Coverage

- Canonical WATCHTOWER contamination check: `SELECT COUNT(*) FROM wt_attribution_outcomes o JOIN operator_entities oe ON oe.entity_address=o.terminal_entity WHERE o.outcome_type='UNKNOWN_INFRASTRUCTURE'` → **0**. Canonical WATCHTOWER entities correctly never leak into UNKNOWN_INFRASTRUCTURE (expected — `derive_outcome()` checks `CANONICAL_OPERATOR_REACHED` before ever reaching the unknown-infra branch).
- `wt_infrastructure_candidates` vs registry overlap: **9/68** (13%) — pipelines are mostly disjoint, not subset/superset.
- Weak family candidates: `9cDDJ5g2wPqVZUZwpPuwqzxN7ouvc6QFauFwrX2TTTAX` and `6tckHFBpiJ8YgYN8FUskvtvTpXQZ55g5LHeo1kvELoDQ` — both hit the pipeline (2 outcome rows each), both `LINEAGE_GAP`, neither in the registry. Same gate-failure pattern (thin session-level evidence never reaching `>=2` wrap-close/creator counts).
- **Conclusion**: the entire B48k/Dv34 PLAIN_XFER family (4/4 known members) is present in the walkback pipeline's input but 0/4 reach the emerging-operators registry. This is a systematic, mechanism-class-wide miss, not a one-off.

## Phase 9 — Rule Audit

Rules in `_known_unknown_infrastructure()` (attribution_outcome.py:399-414):
1. `wt_treasury_review.has_walkback_evidence=1` — still valid in principle, but it's an analyst-populated/derived flag whose upstream population process wasn't traced here; empirically 0 for B48k (no row at all) and 0 for Dv34 (row exists, flag false).
2. `wt_treasury_review.detected_via='walkback_hop2'` — a specific detection-method literal string match; brittle (any other detection path, e.g. `auto_fingerprint_nearmiss` as seen on Dv34, silently fails even if substantively equivalent evidence exists).
3. `wt_treasury_review.distinct_subprovs>=2` — reasonable in principle but Dv34 sits at 1.
4. `wt_discovered_subprovs.creator_count>=2 OR wrap_close_count>=2` — **the load-bearing rule for most cases**, and the one directly falsified by X67.18-22: `wrap_close_count` structurally cannot grow for a PLAIN_XFER-only operator (X67.19/20 explicitly established PLAIN_XFER as a legitimate parallel WATCHTOWER-adjacent mechanism, not noise). This rule has not been updated to also count PLAIN_XFER-mechanism creator-funding edges. **This rule is now stale relative to the codebase's own established findings.**
5. No rule anywhere considers `wt_infrastructure_candidates.role_score_treasury`/`role_score_hub` (the dedicated, more sophisticated scoring engine) — despite that table having 2,462 scored candidates including both family members at meaningfully high `role_score_treasury` (45, 65). The emerging-operator gate duplicates, less precisely, work already done better elsewhere and doesn't consult it.
6. Execution/client-layer fingerprinting (fee-payer/nonce-authority signer patterns from X67.20/21) is **absent** from `derive_outcome()` entirely — confirmed by full read of the function; no reference to signer/fee-payer/nonce anywhere in attribution_outcome.py.
7. Three-axis classification (Risk/CreatorState/Attribution) — grep confirms **no reference** to those axis names or that redesign anywhere in `attribution_outcome.py` or `watchtower_funnel.py`. This module predates or is unaware of that redesign — an un-migrated consumer.

## Phase 10 — Fresh Replay

Manually replayed the gate logic against all `LINEAGE_GAP` outcomes from the last 30 days, checking whether the CURRENT state of `wt_treasury_review`/`wt_discovered_subprovs` would now pass the gate (simulating "if the funnel evaluated it again today with current evidence"):

```sql
SELECT COUNT(DISTINCT o.terminal_entity) FROM wt_attribution_outcomes o
WHERE o.outcome_type='LINEAGE_GAP' AND o.completed_at>=1783238228
  AND o.terminal_entity IN (
    SELECT treasury FROM wt_treasury_review WHERE has_walkback_evidence=1 OR detected_via='walkback_hop2' OR distinct_subprovs>=2
    UNION
    SELECT subprov FROM wt_discovered_subprovs WHERE creator_count>=2 OR wrap_close_count>=2)
```
Result: **9 distinct entities** would now pass the gate if re-walked (top: `88arhHJpbuCYpWJSj9Gba7xwaoCh2xSb5NgYab8WA888` with 4 stale LINEAGE_GAP rows). **Neither B48k nor Dv34prGm appear in this replay set** — confirming their gate failure is not a transient staleness issue that will self-heal; their underlying evidence (`creator_count=1/wrap_close_count=1`, `distinct_subprovs=1`) genuinely has not moved and, per the PLAIN_XFER mechanism argument in Phase 9, will not move without a rule change. Since `should_retry` is written but never consumed by any re-enqueue logic (confirmed: no code path reads `wt_attribution_outcomes.should_retry`), these 3,535 LINEAGE_GAP rows (including all 33+51 B48k/Dv34 rows) are only re-evaluated if the same mint re-enters `wt_walkback_queue` for an unrelated reason — an incidental, not designed, retry mechanism.

Comparing replay output vs actual current registry content: the pipeline's on-disk state (68 registry rows) is **consistent with what re-running the exact SQL gate would currently produce** — i.e., the pipeline is not stale/broken in the sense of falling behind its own logic; it is faithfully executing a logic that has a structural class-level blind spot.

## Phase 11 — Gap Analysis

- **Treasury intelligence used?** No. `derive_outcome()` never touches `wt_infrastructure_candidates` (the confirmed-treasury/omnibus-scale scoring table). The `wt_treasury_review.distinct_subprovs` field is a weak proxy for the same concept but isn't populated for either family member meaningfully.
- **Execution/client-layer fingerprinting used?** Confirmed absent, as expected.
- **Session-volume-scale check** (the discriminator X67.22 found necessary to separate the family from canonical WATCHTOWER, ~10 vs ~106 sessions)**?** Not present anywhere in this pipeline.
- **What the funnel evaluates that the family model doesn't:** operator_entities/canonical-operator collapsing (dedup against already-known operators), CEX/bridge/relay boundary detection, serial-deployer/launcher-profile check — these are useful, orthogonal signals the family model doesn't have.
- **What the family model has that the funnel doesn't:** treasury-sharing-at-omnibus-scale awareness, PLAIN_XFER mechanism recognition, session-volume-scale discrimination, execution-fingerprint discrimination between B48k's shared 3-signer service and Dv34's independent single-signer builder.
- Net: the two systems are complementary but currently non-interacting. Neither consumes the other's output.

## Phase 12 — Strategic Assessment

Architecturally the subsystem is well-built: read-only projection, immutable observation history, clean separation of concerns (identity/promotion via `OperatorResolver`+`PromotionDecisionEngine`, membership via a narrow SQL contract), continuously live (confirmed running worker, hourly-fresh data). The mechanism is sound engineering. But its **sole discovery gate is a single narrow rule (`wrap_close_count>=2` OR thin proxy fields) that was empirically falsified by the codebase's own later findings** (X67.18-22: PLAIN_XFER is a real, distinct, confirmed WATCHTOWER-adjacent mechanism). It is not evaluating the richer `wt_infrastructure_candidates` scoring engine that already exists and already scored both missed wallets at treasury_score 45/65. This is a **classic un-migrated consumer** of a later, better model — the infrastructure to fix this (wt_infrastructure_candidates) already exists in the same database; the gap is that `derive_outcome()`'s gate function was never updated to consult it.

Given clean architecture + live operation + a well-isolated, single-function root cause (one gate function, ~15 lines), this is a strong candidate to **evolve into the generic Operation Discovery foundation** rather than be replaced — the fix is additive (widen `_known_unknown_infrastructure()` to also accept `wt_infrastructure_candidates` role-score thresholds, or a PLAIN_XFER-edge-count analogue to `wrap_close_count`), not a rearchitecture. No code changes made — this is diagnostic only.

## Required Verdicts

- **Verdict 1 — Operational Status: B (Operational with gaps).** Pipeline is live, worker running, DB current within the hour, but its single classification gate has a structural blind spot for an entire confirmed mechanism class.
- **Verdict 2 — Discovery Quality: C (Missing major discoveries).** 68 live candidates are real, but 4/4 known B48k-family members (representing 27+40+~15 confirmed creator-funding edges, ~150 total launches) are structurally invisible to it, and the gap is provably not a transient/staleness issue (Phase 10 replay confirms).
- **Verdict 3 — Future: A (Evolve into Operation Discovery).** Architecture is clean, live, and read-only-safe; the fix is a narrow, additive rule change (consult `wt_infrastructure_candidates` and/or add a PLAIN_XFER-aware evidence path), not a replacement.

## Required Counts

| Metric | Value |
|---|---|
| Candidate rows currently in emerging-operators projection | 68 |
| Active discoveries (eligible=1) | 68 |
| Discoveries (UNKNOWN_INFRASTRUCTURE outcomes) in last 7 days | 18 |
| Discoveries (UNKNOWN_INFRASTRUCTURE outcomes) in last 30 days | 623 |
| Stale candidates (registry, last_seen_at > 30d old) | 13 |
| Replay discoveries (Phase 10 manual gate replay over 30d LINEAGE_GAP backlog) | 9 (none are B48k/Dv34) |
| Missed B48k-family members (of B48k, Dv34prGm, 9cDDJ5g2, 6tckHFBp) | 4 of 4 missing (0 present in registry) |
| Missed WATCHTOWER-family (canonical) members in UNKNOWN_INFRASTRUCTURE | 0 (correct — sanity check passes) |
| Promotion-eligible candidates (of the 68) | Not independently countable via SQL alone — gated by `PromotionDecisionEngine` (Python-side identity/decision logic in `src/ops/identity_framework.py`), not a DB column; would require invoking `EmergingOperatorService.list()` live (out of scope for pure read-only SQL archaeology; flagged as unresolved without a live app-context call) |

All file paths referenced: `src/ops/emerging_operator_service.py`, `src/ops/attribution_outcome.py`, `src/ops/watchtower_funnel.py`, `src/ops/operator_routes.py`, `src/core/walkback_worker.py`, `src/core/walkback_queue.py`, `config/supervisor/supervisord.conf:268-294`, `database/wt_ops_v2.db`, `database/flex_complete_database.db`.
