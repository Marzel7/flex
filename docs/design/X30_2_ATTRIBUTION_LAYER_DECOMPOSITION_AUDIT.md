# X30.2 — Attribution Layer Decomposition Audit

Investigation only, per the brief. No code changed. Classifies every attribution rule enumerated in X30.1 by architectural scope (Platform / Pump.fun ecosystem / WATCHTOWER family / Unknown), using the actual code, not abstract reasoning. One new fact traced this sprint that changes the picture from X30.1: `extract_close_destinations()` (`src/core/wrap_close_detector.py:191`) — the function underlying the mechanism→role coupling X30.1 flagged — reads only generic Solana `system`/`spl-token`/`spl-associated-token-account` instructions. It contains **no pump.fun reference anywhere**. This means the wrap-close detection mechanism itself is a Solana-wide primitive, not a pump.fun-specific or WATCHTOWER-specific one — only its *use* (promoting a wallet to Subprovider) is WATCHTOWER-scoped. CREATE detection, by contrast, is genuinely pump.fun-coupled: `PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"` (`ws_cascade.py:270`) is a hardcoded program ID, subscribed to directly via `logsSubscribe` (`ws_cascade.py:787,1371`, the `ProgramWatcher`).

## Rule inventory, classified by scope

| Rule (from X30.1) | Layer | Why | Dependency | Dependency generic or family-specific? |
|---|---|---|---|---|
| **Treasury confirmation** (#1) | **Layer 1 — Platform** | Analyst-driven confirmation, a `method`/`provenance` field in `wt_confirmed_treasuries` (`treasury_bank.py:327,508`). Nothing in the confirmation *mechanism* (a human-reviewed table insert) references pump.fun, wrap-close, or any WATCHTOWER-specific pattern. | Analyst judgment + provenance record | Generic — this is a record-keeping pattern, not a detection rule |
| **Evidence persistence** (`wt_subprov_evidence` insert, inside `promote_to_subprov`) | **Layer 1 — Platform** | `INSERT OR IGNORE INTO wt_subprov_evidence (subprov, wrap_close_sig, creator_wallet, amount_sol, funding_mechanism, observed_at)` — unconditional, mechanism-tagged, never suppressed (`ws_cascade_store.py:1022-1029`). Recording "X funded Y via mechanism Z at time T" is a shape that holds for any funding observation on any chain, not just wrap-close or pump.fun. | Raw funding observation (subprov, destination, amount, mechanism, time) | Generic |
| **Confidence storage** (the `confidence` column pattern itself, as opposed to the formula) | **Layer 1 — Platform** | Storing a numeric/textual confidence alongside a role assignment is a platform-wide pattern (`wt_discovered_subprovs.confidence`, `wt_watchtower_launches.confidence`, `wt_confirmed_treasuries.confidence` all use the same shape). | N/A — a storage convention | Generic |
| **CREATE instruction parsing / launch persistence shape** (`record_launch`'s table schema and idempotency-on-signature pattern) | **Layer 2 — Pump.fun ecosystem** | The *shape* (mint, creator, create_sig, create_time, idempotent on signature) is Pump.fun's specific launch-event structure — any Pump.fun-based operation family (WATCHTOWER or otherwise) produces exactly this event shape. Not Layer 1 because a non-token-creating operation family has no "create" event at all (X30.0's finding: "Creator"/"launch" vocabulary presumes token creation). | Pump.fun program's `create` instruction | Pump.fun-specific, but family-agnostic within Pump.fun |
| **Creator assignment via `create` instruction + `creator_extraction_method`** (#11) | **Layer 2 — Pump.fun ecosystem** | Grounded in the pump.fun program itself (`PUMP_PROGRAM` ID, `ProgramWatcher`'s `logsSubscribe`), not in WATCHTOWER's funding pattern. Any Pump.fun-launching operation, regardless of how its creator was funded (WATCHTOWER wrap-close, a different family's plain transfer, or no detectable funding chain at all), would be assigned Creator the same way. | Pump.fun `create` instruction, `closeAccount` destination match | Pump.fun-specific dependency, but the rule itself is family-agnostic *within* Pump.fun |
| **CDC threshold** (#3, `CDC_MIN_SOL`) | **Layer 3 — WATCHTOWER family** | A single tunable SOL threshold with no corroborating signal (`ws_cascade.py:3172`). Nothing about "≥50 SOL to a non-treasury" is Pump.fun-specific — it's a heuristic invented to catch WATCHTOWER's own observed capital-distribution pattern. | Transfer amount threshold | Family-specific (the threshold value itself is tuned to WATCHTOWER's observed capital sizes, e.g. the 0.6–2.5 SOL per-wallet amounts and 700-800+ SOL treasury loads referenced in memory `provisioning-hub-fleet-confirmed`) |
| **Wrap-close→Subprovider promotion rule** (#4/#5, the *decision* to promote, not the detector) | **Layer 3 — WATCHTOWER family** | The wrap-close *pattern detector* (`extract_close_destinations`) is Layer 1/generic (see above) — but the decision that "observing this pattern once is sufficient to call the funder a Subprovider" is a WATCHTOWER-specific interpretation choice, confirmed in X30.1 to be a direct, single-observation trigger with no statistical corroboration. | Wrap-close observation (generic evidence) + the promotion threshold (family-specific interpretation) | **Split dependency** — the evidence-gathering half is generic, the role-decision half is WATCHTOWER-specific |
| **Sibling suppression** (referenced in the brief; the `EXPIRED_SIBLING`/`sibling_idle` rule from X29.9-X29.11) | **Layer 3 — WATCHTOWER family** | The entire concept exists to resolve WATCHTOWER's specific same-instant burst-funding pattern (X29.11: 24 wallets funded in a 7-second window, closed within 20 seconds of the winning sibling's CREATE). An operation family that funds intermediaries one at a time, or that never produces multiple simultaneous candidates from one subprov, has no sibling-suppression decision to make at all. | Same-burst candidate timing + one candidate's CREATE firing | Family-specific — presupposes WATCHTOWER's burst-funding behavior |
| **Buy-swarm classification** (#7, `buy_swarm_ratio`/thresholds) | **Layer 3 — WATCHTOWER family** | `bsr > 0.7 and n_obs >= 10 and not has_creators` (`ws_cascade.py:2664`) — a statistical signature specifically distinguishing WATCHTOWER's observed buy-swarm behavior from genuine provisioning (memory: `buy-swarm-vs-creator`). The thresholds (0.7, 10) are tuned constants with no general basis. | Historical ratio of swap-destinations vs. creator-destinations for this specific subprov | Family-specific |
| **Dormancy classification** (#8, `_DORMANCY_THRESHOLD_S`, `CONTINUING_OPERATION`/`SUBPROV_REACTIVATED`) | **Layer 3 — WATCHTOWER family** | A single tunable time-gap constant (`ws_cascade.py:2740-2742`) with "no per-operation-family basis," per X30.1. What counts as "dormant" is entirely a function of how frequently *this* family's operators are expected to re-engage — a different family's operational cadence could be much slower or faster. | Time since last operational activity | Family-specific (the *threshold value*; the underlying concept — "has this entity gone quiet" — is more general, but the concrete rule as implemented is a fixed constant with no generalization mechanism) |
| **Historical subprovider recovery** (#9, `is_historical_subprov`) | **Layer 3 — WATCHTOWER family**, leaning Layer 4 | Recovers pre-WATCHTOWER-era evidence for a wallet already known to have wrap-close history — the recovery mechanism itself depends on the same wrap-close evidence table (`wt_subprov_evidence`), so it's really "the same Layer 3 promotion rule, applied retroactively." Not independently generalizable beyond acknowledging the underlying evidence store is Layer 1. | Prior `wt_subprov_evidence` rows for this wallet | Family-specific in application (built to backfill WATCHTOWER's own history) though the underlying storage is generic |
| **Infrastructure veto** (#6, `is_known_account`/`REJECTED_INFRASTRUCTURE`) | **Layer 1 — Platform** | A manually-maintained exception registry (`src/utils/infra_mapping.py`) recording known CEX/infra wallets. Nothing about "this address belongs to KuCoin, not an operator" is WATCHTOWER-specific or even Pump.fun-specific — the same registry would correctly veto a false-positive Subprovider promotion for *any* operation family funneling through the same CEX hot wallet. This is the strongest Layer-1 candidate among the "interpretation" rules because it's a hand-curated fact list, not a behavioral heuristic. | Known-infrastructure address lookup | Generic |
| **Confidence calculation** (the `0.20 + n*0.08` capped-0.74 *formula*, as opposed to the storage pattern above) | **Layer 3 — WATCHTOWER family** | A hardcoded linear function with "no stated derivation or validation" (X30.1). The specific coefficients are not derived from anything platform-wide or Pump.fun-wide — they're an arbitrary WATCHTOWER-only scoring curve. | Count of `wt_subprov_evidence` rows for this subprov | Family-specific |
| **Candidate wallet opening** (#12, `open_candidate_watch`) | **Layer 3 — WATCHTOWER family**, with a Layer-1 storage shape | The *decision* to open a live-WS observation window the instant a wrap-close destination is seen (rather than, say, waiting for corroborating evidence) is a WATCHTOWER-specific choice about how eagerly to pre-stage candidates. The underlying table shape (a wallet, a TTL, a close_reason) is generic bookkeeping, but the trigger condition and TTL value (`CANDIDATE_TTL_SEC`) are tuned to WATCHTOWER's observed timing. | Wrap-close destination + TTL constant | Split — storage shape generic, trigger/TTL family-specific |
| **Non-provisioning-recipient reclassification** (#10) | **Layer 4 — Unknown / family-dependent** | This rule only reads back a *prior* classification (`wt_discovered_subprovs.subprov_type`) rather than deriving one — the audit trail for how a wallet originally got marked `NON_PROVISIONING_RECIPIENT` was not traced in X30.1 or this sprint. Cannot be classified without finding that origin rule. | An already-stored classification, origin unknown | Unclassifiable pending further trace |

## Layer 1 — Platform (applicable regardless of blockchain operation)

- Treasury confirmation (the analyst-confirmation *process*, not any specific evidentiary standard)
- Evidence persistence (`wt_subprov_evidence`'s unconditional, mechanism-tagged insert)
- Confidence storage (the column/shape convention)
- Infrastructure veto (`is_known_account` — a hand-curated address registry)

These four share one property: none of them encode a belief about *how* an operation behaves. They encode *bookkeeping disciplines* (always record raw evidence, always store a confidence value, always let a known non-operator address override a heuristic, always let a human confirm a root entity) that would be exactly as correct for a Layer 2 (any Pump.fun family) or a hypothetical Layer 2′ (a different launch platform entirely) system.

## Layer 2 — Pump.fun ecosystem (applicable to any Pump.fun operation)

- Creator assignment via the `create` instruction (`PUMP_PROGRAM` subscription, `ProgramWatcher`, `creator_extraction_method`)
- The launch-record shape itself (`wt_watchtower_launches`'s mint/creator/create_sig/create_time columns, idempotent on signature)

Both depend on the pump.fun program ID and its specific instruction format — genuinely not Layer 1 (a non-Pump.fun chain event has no `create` instruction to parse), but also not WATCHTOWER-specific: any operation family that launches tokens via pump.fun, regardless of its own funding topology, produces this exact same CREATE event and would be recorded identically.

## Layer 3 — WATCHTOWER family (applicable only because WATCHTOWER behaves this way)

- CDC threshold (`CDC_MIN_SOL`)
- The promotion *decision* half of wrap-close→Subprovider (not the wrap-close detector itself, which is Layer 1)
- Sibling suppression
- Buy-swarm classification thresholds
- Dormancy classification (`_DORMANCY_THRESHOLD_S`, CONTINUING_OPERATION/SUBPROV_REACTIVATED)
- Historical subprovider recovery (in its current WATCHTOWER-scoped application)
- Confidence *calculation* formula (`0.20 + n*0.08`, capped 0.74)
- Candidate-opening trigger condition and TTL (`CANDIDATE_TTL_SEC`)

This is the largest layer by rule count, matching X30.1's overall finding that most of the interpretive logic is embedded and WATCHTOWER-specific — this sprint sharpens that finding by showing the *evidence-gathering* half of several of these rules (wrap-close detection, funding observation) is actually Layer 1, while only the *interpretation* half (what a given observation is taken to mean) is Layer 3. The split is real and consistent across multiple rules, not a one-off.

## Layer 4 — Unknown / family-dependent

- Non-provisioning-recipient reclassification (#10) — origin classification rule not traced by either X30.1 or this sprint.

## The one significant correction from X30.1: wrap-close detection is NOT WATCHTOWER-specific

X30.1 correctly identified `promote_to_subprov` as the site of mechanism→role coupling but did not separately examine whether the *detector function itself* (`extract_close_destinations`) was WATCHTOWER-scoped. Tracing it directly this sprint shows it is not: it pattern-matches generic SPL Token / Associated Token Account / System program instructions with no pump.fun or WATCHTOWER reference. This means the wrap-close-pattern-recognition capability is reusable, unmodified, by any future operation family or even any future non-WATCHTOWER Pump.fun-adjacent detector — **the coupling X30.1 flagged is entirely in the decision layer wrapped around this generic detector** (`promote_to_subprov`'s "one observation ⇒ Subprovider" rule and its confidence formula), not in the detection primitive itself. This narrows the actual WATCHTOWER-specific surface area more precisely than X30.1 could without this trace.

## Minimum shared attribution core (could become shared without modification)

Traced directly against existing implementations — every item below already behaves in a way that requires zero change to serve a second operation family:

1. **`wt_subprov_evidence` writes** (`ws_cascade_store.py:1022-1029`) — already generic, already mechanism-tagged, already never suppressed regardless of role outcome.
2. **`extract_close_destinations()`** (`wrap_close_detector.py:191`) — already a pure, pump.fun-agnostic Solana instruction parser; usable as-is by any family that also happens to use wrap-close-shaped funding, and trivially inert (returns `[]`) for families that don't.
3. **The `wt_confirmed_treasuries` confirmation table and its analyst-confirmation workflow** (`treasury_bank.py`) — the process ("a human confirms a root entity, with a provenance note") is already family-agnostic.
4. **The infrastructure veto (`is_known_account`)** — already a standalone, family-agnostic address registry lookup, cleanly separated from any specific promotion rule.
5. **The confidence-storage column shape** across `wt_discovered_subprovs`/`wt_watchtower_launches`/`wt_confirmed_treasuries` — the *pattern* of storing a confidence value alongside a role is reusable; only the *formula* that computes WATCHTOWER's specific value is not.
6. **`record_launch`'s persistence shape** (`wt_watchtower_launches` schema + idempotency-on-signature) — reusable by any Pump.fun-launching family, since it stores exactly the CREATE event's own fields, not any WATCHTOWER-derived interpretation.

## Minimum WATCHTOWER-specific interpreter (the smallest set of rules that would need to move into a WATCHTOWER interpreter)

Enumerated, not redesigned, per the brief:

1. `CDC_MIN_SOL` threshold check and CDC registration trigger (`ws_cascade.py:3172-3184`)
2. The wrap-close-observation → Subprovider-promotion *decision* (the `if real_dests:` branch in `_handle_cdc_tx`, `ws_cascade.py:3290-3297`, and the `direct_dests` branch, `ws_cascade.py:3199-3218` — NOT `extract_close_destinations` itself, which stays in the shared core)
3. The confidence formula (`0.20 + n*0.08`, capped 0.74) inside `promote_to_subprov` (`ws_cascade_store.py:1077-1078`)
4. Buy-swarm ratio thresholds and the `_is_buy_swarm_burst`/`BUY_SWARM_PROVISIONER` classification (`ws_cascade.py:2659-2665,2686-2691,2744-2783`)
5. Dormancy threshold and `CONTINUING_OPERATION`/`SUBPROV_REACTIVATED` classification (`ws_cascade.py:2712-2742`)
6. Sibling-suppression logic (the `EXPIRED_SIBLING`/`sibling_idle` closure rule, per X29.9-X29.11 — location not re-traced this sprint but already established as WATCHTOWER-behavior-specific)
7. Candidate-opening trigger condition and TTL constant (`CANDIDATE_TTL_SEC`, within `open_candidate_watch`'s call sites)
8. Historical-subprovider recovery as currently scoped (`is_historical_subprov`, in its present WATCHTOWER-only application)

Everything else in X30.1's inventory — Treasury confirmation, evidence persistence, the infrastructure veto, confidence storage, Creator assignment via the `create` instruction, and the launch-record shape — sits outside this list, in Layer 1 or Layer 2.

## Deliverable summary

**Layering, evidence-supported, differing slightly from the brief's proposed 3-layer sketch**: the code supports exactly the four layers the brief allows for as an alternative ("or another evidence-supported layering"), because a clean `Platform → Pump.fun → WATCHTOWER → Discovery` stack undercounts one real structural fact — several WATCHTOWER-layer rules (wrap-close promotion, candidate-opening) have a **split dependency**: their evidence-gathering half is genuinely Layer 1/generic, while only their interpretation half is Layer 3. The most accurate layering is:

```
Layer 1 — Platform
    (evidence persistence, confidence storage, treasury confirmation,
     infrastructure veto, wrap-close PATTERN DETECTION)
        │
Layer 2 — Pump.fun ecosystem
    (create-instruction parsing, launch persistence shape)
        │
Layer 3 — WATCHTOWER family
    (CDC threshold, wrap-close→role PROMOTION DECISION, confidence
     formula, buy-swarm thresholds, dormancy classification,
     sibling suppression, candidate-opening trigger/TTL)
        │
Discovery (X30.0/X30.1's read-only layer, unchanged)
```

with the caveat that Layer 1 and Layer 3 are not cleanly sequential for wrap-close specifically — the *detector* lives in Layer 1 and the *promotion decision* over its output lives in Layer 3, called from the same function. This is the one place the clean stack the brief sketches doesn't quite match the code, and it is worth stating precisely rather than smoothing over: **`promote_to_subprov` is itself a Layer 1/Layer 3 hybrid function today** — its first half (the `wt_subprov_evidence` insert) is already shared-core-ready; its second half (state advancement, confidence formula) is exactly the WATCHTOWER interpreter content enumerated above. This single function is the literal boundary line between the shared core and the family-specific interpreter — not a metaphorical one.
