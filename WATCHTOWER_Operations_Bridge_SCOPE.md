# SCOPE — Bridge launch-attributed WATCHTOWER creators into the Operations layer

**Date:** 2026-06-04 · **Status:** Scoping only (no code written) · **Depends on:** lineage-aware Rule 2 fix (creator_risk_scores → 262, branch `watchtower-lineage-rule2`)

## Goal

Surface the **launch-side** WATCHTOWER records on `/watchtower/operators` without polluting it with extraction/collector relationships.

```
Dashboard  = ALL watchtower_related creators (262)        ← already done
Operators  = launch / campaign / operator STRUCTURE only  ← this task
```

**In scope (13 creators):** `LAUNCH_PROVISIONING` (7) + `LAUNCH_DIRECT` (6).
**Out of scope (81):** `EXTRACTION_PROFIT_RELAY` (72) + `COLLECTOR_FLOW` (9) — stay on dashboard/evidence unless they later form a campaign.

## Why they're missing today (verified on-chain + in DB)

The Operations engine (`_discover_operations`, main.py:29503) builds `wt_operations` from `watch_candidate_tokens` joined on funding **corridors** + timing. Confirmed gaps:

| Check | Result |
|-------|--------|
| Launch creators in `watch_candidate_tokens` (engine input) | **0 / 7** |
| Provisioning hubs in `wt_known_operator_hubs` | **0 / 8** |
| Launch tokens in `token_prediction_scores` | only **5 / 8** (and not `risk_level='WATCH'`) |
| Launch tokens in `token_analysis` **with `migrated_at`** | **present** ✓ |

So the launches **bypass the watch-pipeline input filter** (`risk_level='WATCH' AND creator_was_fresh=1`) — the same classification blind spot the lineage fix addressed at the creator level. The Operations engine never sees them.

## Key enabler — the engine's identity logic ALREADY works for these creators

`_discover_operations` tags `operator_identity='WATCHTOWER'` via a **3-hop WT-lineage check** over `creator_funders` (main.py:29703-29724): if any member creator's funding ancestry reaches `wt_addresses` (WT infra), it's WATCHTOWER. **Our hub→creator and TREASURY→hub edges are already hydrated into `creator_funders`**, so these 13 creators satisfy that check as-is (`creator ← hub ← TREASURY`). We do NOT need to touch the identity logic — only get the tokens into the engine's input/membership.

## Design options

### Option A — Register hubs + inject launch tokens as operation members (RECOMMENDED)
1. **Populate `wt_known_operator_hubs`** with the 8 CONFIRMED provisioning hubs → `operator_identity='WATCHTOWER'`, evidence = the hub birth signature. This makes `_funding_root()` resolve hub-seeded creators, so future engine passes group them correctly *and* split them from ALPHA/other operators sharing a corridor.
2. **Build a dedicated bridge function** `bridge_launch_operations(conn)` that:
   - selects launch-side creators: `creator_risk_scores.watchtower_related=1 AND evidence_basis category IN ('LAUNCH_PROVISIONING','LAUNCH_DIRECT')`
   - joins their migrated tokens from `token_analysis` (mint, migrated_at, creator)
   - groups by **provisioning hub** (LAUNCH_PROVISIONING) / by TREASURY corridor+timing (LAUNCH_DIRECT) — one operation per hub-campaign, mirroring the existing union-find discipline
   - upserts `wt_operations` (auto_name `WATCHTOWER_HUB_<prefix>`, `operator_identity='WATCHTOWER'`, `identity_confidence='LINEAGE_CONFIRMED'`, `discovery_signals=['provisioning_hub','treasury_corridor','create_+1s']`, state `DISCOVERED`)
   - inserts `wt_operation_members` (operation_id, token_mint, creator_wallet, funding_amount=hub seed, migrated_at, join_signal='hub_seed')

**Pros:** clean separation; reuses existing identity logic + display; hubs become first-class operators; idempotent. **Cons:** new bridge fn (~80 lines) + one registry populate.

### Option B — Inject launches into `watch_candidate_tokens`, let `_discover_operations` do the rest
Force the 13 into `watch_candidate_tokens` (classified_as='WATCHTOWER') and let the existing engine pick them up on its next pass.
**Pros:** minimal new code; single existing pipeline. **Cons:** misuses the watch-candidate table (these never passed the WATCH predictor — semantically wrong); corridor join requires the 700/800 + 0.1xxx seed amounts to behave as corridors (they have ≥5 decimals so likely OK, but unverified); harder to keep launch-vs-extraction boundary crisp.

### Option C — Operators page reads a UNION (engine ops + launch-attributed view)
Leave the tables alone; add a read-time view on the operators API that unions existing `wt_operations` with a synthesized launch-ops view from `creator_risk_scores`.
**Pros:** zero write to ops tables, fully reversible. **Cons:** two code paths for "an operation"; the page's mutation actions (name/confirm/merge/noise) wouldn't apply to synthesized rows; diverges from the single-source design.

## Recommendation
**Option A.** It respects the existing layer boundaries, reuses the engine's WATCHTOWER identity logic (already satisfied by our hydrated edges), makes the provisioning hubs first-class operators (which they are), and keeps Operators = launch/campaign structure. Register the hubs first (also benefits the *existing* `_discover_operations` going forward), then run a scoped, idempotent bridge for the 13.

## Open decisions (need a call before building)
1. **Grouping granularity:** one operation **per hub** (8 hubs → up to 8 ops, each 1 launch) vs. one **WATCHTOWER mega-operation** for all 13? On-chain these are distinct ephemeral hubs but one operator. Lean: **per-hub operations** (matches the existing "UNKNOWN_OPERATION_NNN = one campaign" granularity), all tagged `operator_identity='WATCHTOWER'`.
2. **LAUNCH_DIRECT (6) grouping:** these have no provisioning hub (direct TREASURY/root edge). Group by TREASURY corridor + 7-day timing window like the existing engine? Or one "WATCHTOWER_DIRECT" operation?
3. **Naming:** `WATCHTOWER_HUB_<hubprefix>` vs. the engine's `UNKNOWN_OPERATION_NNN` + identity tag convention.
4. **Run cadence:** one-time bridge now + fold hub-registration into the live `discover_provisioning_hubs` path so future hubs auto-create operations.

## Risks / safety
- **No fingerprint-driven grouping** — group only on confirmed hub lineage or TREASURY corridor, never on a bare amount (same discipline as the existing engine; consistent with the fingerprint downgrade already shipped).
- **Idempotency:** bridge must upsert by a stable key (hub address or operation auto_name) so re-runs don't duplicate ops/members.
- **Maintenance-window write** (single-writer lock) — same controlled apply as the 94 backfill; the rescore-queue trigger ON-CONFLICT issue does **not** apply here (different tables), but verify no triggers on `wt_operations`/`wt_operation_members`.
- **Don't broaden to extraction/collector** — hard-filter category to the two launch categories.
- **Scope creep guard:** 8 hubs may map to ≥8 ops; confirm the 13 creators resolve to the expected token set before writing.
