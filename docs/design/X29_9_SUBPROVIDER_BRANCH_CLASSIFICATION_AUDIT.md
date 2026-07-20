# X29.9 — Subprovider Branch Classification Audit

Investigation only, per the brief. No code changed. **No blockchain RPC calls were made in this sprint** — every fact below comes from tables already present in `wt_ops_v2.db`/`flex_complete_database.db`, per the brief's explicit constraint. Subject: confirmed subprovider `ANenEukvmpYsyP52LgDsZN6kj3n7igjbJDTCtj4xCAXq`.

## Correction to the brief's premise

The brief states "the database already contains 4,315 recorded transactions" for ANen. **This is not accurate** — the 4,315 figure came from X29.8's live `getSignaturesForAddress` RPC calls and was cached only to a `/tmp` scratch file, not persisted anywhere in `wt_ops_v2.db` or `flex_complete_database.db`. Neither database has a raw per-signature transaction log for arbitrary wallets at that scale. What **is** genuinely persisted, and forms the actual corpus for this investigation, is a set of purpose-built evidence tables that each capture a *processed interpretation* of a subset of ANen's activity — enumerated below. This is itself a finding: the "existing transaction corpus" the brief assumes is available is smaller and more fragmented than presumed.

## The already-persisted evidence tables for ANen

| Table | Row count for ANen | What it represents |
|---|---|---|
| `wt_subprov_evidence` | **26** | One row per detected WSOL_WRAP_CLOSE event where ANen is the funding subprov: `wrap_close_sig`, `creator_wallet`, `amount_sol`, `observed_at`. This is the richest and most complete per-event table found. |
| `wt_candidate_websocket_watches` | **25** | Candidate wallets placed under live WS observation, keyed by `subprov_wallet`; carries `state` (`FIRED_CREATE`/`EXPIRED_SIBLING`) and `close_reason`. |
| `wt_fanout_events` | **9** (aggregating 25 candidate creates) | Time-bucketed fan-out summaries: `fanout_count`, `total_sol`, `has_identical_amounts`, one row per detected burst-window. `sum(fanout_count)` across these 9 rows = 25, matching `wt_candidate_websocket_watches` exactly. |
| `wt_capital_reloads` | **1** | The treasury→subprov capital load: 728.0 SOL from `9hGcx...` to ANen, `enrolment_reason='PLAIN_TRANSFER_NEW_SUBPROV'`. **This resolves the X29.7.1 open question** about why `wt_watchtower_launches.subprov_funding_sol=728.0` didn't match the wrap-close transaction amount — 728.0 SOL is the treasury's bulk load into ANen, an entirely separate event from any individual creator-funding wrap-close. |
| `wt_capital_distributor_candidates` | **1** | A tracking record for ANen as a capital-receiving candidate, created at `first_seen=1784048314` (the moment of the 728 SOL reload). Its aggregate columns (`wrap_close_count`, `creator_count`, `fanout_count`, `total_outbound_sol`) are **all zero** — never updated after the initial snapshot, despite 26 real wrap-close events subsequently occurring. |
| `wt_provisioning_edges` | **1** | `SUBPROV_TO_CREATOR, ANen→HTR9U7` — the sole persisted graph edge. |
| `wt_watchtower_launches` | **1** | The sole persisted launch ledger row (HTR9U7). |
| `wt_wrap_close_candidates` | **0** | A separate `DETECTED→ARMED→FIRED→EXPIRED` candidate-lifecycle table. Table-wide (not just for ANen), its newest row is `detected_at=2026-06-23T17:33:05` — **21 days before** ANen's activity (2026-07-14). This pipeline stage was already disused platform-wide before ANen ever became active; its absence for ANen is not a miss specific to this subprovider. |
| `wt_temp_provision_candidates` | **0** (checked against all 26 `wt_subprov_evidence` creator wallets individually) | No match for any of ANen's 26 creator wallets. |

## Classification of the existing corpus (26 wrap-close events, all WSOL_WRAP_CLOSE)

Every one of the 26 `wt_subprov_evidence` rows is tagged `funding_mechanism='WSOL_WRAP_CLOSE'` — no `PLAIN_TRANSFER` or `SEEDED_ACCOUNT_CLOSE` creator-funding rows exist for ANen in any already-persisted evidence table. (X29.8's RPC-based sampling found plain-transfer transactions too, but those were never captured into any of ANen's persisted evidence tables — they exist only on-chain, not in the corpus this sprint is restricted to.)

By destination category (cross-referenced against the treasury address and `wt_capital_reloads`):

| Category | Count | Evidence |
|---|---|---|
| Treasury movement (inbound reload, ANen receiving) | 1 | `wt_capital_reloads` row: 728.0 SOL from `9hGcx...` (treasury) to ANen |
| Creator funding (outbound wrap-close) | 25 | 24 `EXPIRED_SIBLING` + 1 `FIRED_CREATE`, from `wt_candidate_websocket_watches` |
| Creator funding, orphaned (no candidate-watch row) | 1 | `2josE3T5...`, in `wt_subprov_evidence` only |
| Infrastructure / dust / unknown | 0 | none found in any evidence table |

**Total accounted for: 27 events across the two directions (1 inbound reload + 26 outbound creator-funding), all falling into exactly one category — no unclassified residue in the persisted corpus.**

## Persistence reconciliation table

| Stage | Count | Source |
|---|---|---|
| Wrap-close creator-funding events observed (`wt_subprov_evidence`) | 26 | exact |
| → Candidates placed under live watch (`wt_candidate_websocket_watches`) | 25 | exact (1 short — the orphan) |
| → Fan-out events aggregated (`wt_fanout_events`, sum of `fanout_count`) | 25 | exact, matches watches exactly |
| → Creates fired (`state='FIRED_CREATE'`) | 1 | exact |
| → Launches persisted (`wt_watchtower_launches`) | 1 | exact |
| → Provisioning edges written (`wt_provisioning_edges`) | 1 | exact |

**Every reduction from 26 → 1 is accounted for by name, not by inference:**
- 26 → 25: one wrap-close (`2josE3T5...`) never received a `wt_candidate_websocket_watches` row at all — the sole genuinely unexplained gap in this reconciliation (see Missing Branch Analysis).
- 25 → 25: no loss — `wt_fanout_events` fully accounts for every candidate.
- 25 → 1: 24 candidates were explicitly closed with `state='EXPIRED_SIBLING'`, `close_reason='sibling_idle'` — a **known, intentional, auditable classification rule** (the buy-swarm/sibling-suppression gate), not data loss. Only 1 candidate (`HTR9U7`) was classified as the genuine launch and promoted.
- 1 → 1 → 1: the one promoted candidate flows cleanly through to launch and edge persistence with no further loss.

## Missing branch analysis

For every one of the 24 `EXPIRED_SIBLING` branches: **not missing — deliberately excluded by an existing, named rule.** `close_reason='sibling_idle'` is an explicit classification outcome recorded in the row itself. This is the brief's "excluded by existing rule" category, fully evidenced.

For the 1 orphaned branch (`2josE3T5...`, observed 1784048528, 149s after the sibling cluster's last close and 135s before HTR9U7's detection): **genuinely unresolved from persisted evidence.** `wt_subprov_evidence` proves the wrap-close was detected and its mechanism correctly classified (`WSOL_WRAP_CLOSE`), but no corresponding `wt_candidate_websocket_watches` row, `wt_watchtower_launches` row, or `wt_provisioning_edges` row exists for it. Per the brief's required "possible outcomes" list, this matches **"never persisted"** — the evidence-detection stage succeeded (mechanism correctly identified, creator wallet correctly extracted — proven by the row's own existence and populated `creator_wallet`/`amount_sol` fields) but the candidate-promotion stage that would carry it into `wt_candidate_websocket_watches` did not run for this one event. Whether that's a timing edge case (arriving in the ~2.5-minute gap between two detection bursts) or a genuine one-off drop cannot be determined further from persisted data alone — this sprint does not speculate beyond what the tables show.

## Plain-transfer provisioning path

**No persistence path exists for plain-transfer creator funding in the currently-inspected schema.** `wt_subprov_evidence`'s schema (`wrap_close_sig`, `funding_mechanism` defaulting to `'WSOL_WRAP_CLOSE'`) is structured around the wrap-close pattern specifically — a `funding_mechanism` column exists and is populated, so the table is not mechanically incapable of holding a `PLAIN_TRANSFER` value, but zero such rows exist for ANen despite X29.8's RPC trace finding plain-transfer transactions on-chain. `wt_candidate_websocket_watches` similarly has a `funding_mechanism` column (`DEFAULT 'WSOL_WRAP_CLOSE'`) but, again, zero non-wrap-close rows for ANen. `wt_provisioning_edges.funding_mechanism` is populated per-edge but the only edge that exists is a wrap-close one. **Whether a plain-transfer detection/persistence path exists elsewhere in the codebase (outside these evidence tables) cannot be confirmed or denied from this database-only investigation** — the honest finding is that plain-transfer creator-funding evidence for ANen, specifically, does not exist in any of the persisted tables checked, even though X29.8 (using RPC) found the mechanism actually occurring on-chain for this subprovider.

## Conservation summary

```
26 wrap-close events observed (wt_subprov_evidence)
    ↓ (-1: no candidate-watch row written — unresolved gap)
25 candidates placed under watch (wt_candidate_websocket_watches)
    ↓ (0 loss — fully aggregated)
25 accounted for in fan-out events (wt_fanout_events, sum=25)
    ↓ (-24: EXPIRED_SIBLING, close_reason=sibling_idle — a named, intentional rule)
1 creates fired
    ↓ (0 loss)
1 launch persisted (wt_watchtower_launches)
    ↓ (0 loss)
1 provisioning edge written (wt_provisioning_edges)
```

## Deliverable answers

- **Classification of the existing corpus**: 26 detected WSOL_WRAP_CLOSE events for ANen — 1 treasury-reload-adjacent (separately tracked in `wt_capital_reloads`, not a creator-funding event), 25 candidate-watched creator-funding attempts, 1 orphaned creator-funding detection with no downstream record.
- **Creator-funding transactions**: 26 (all wrap-close; no plain-transfer or seeded-account-close creator-funding rows exist for ANen in the persisted corpus).
- **Treasury movements**: 1 (the 728.0 SOL reload in `wt_capital_reloads`).
- **Infrastructure transactions**: 0 found in any evidence table for ANen.
- **Creator branches successfully persisted**: 1 (HTR9U7 → `wt_watchtower_launches` + `wt_provisioning_edges`).
- **Creator branches lost**: 25 did not reach `wt_watchtower_launches`/`wt_provisioning_edges` — but 24 of those were **deliberately excluded by the existing sibling-suppression rule**, not lost to a defect; only 1 (`2josE3T5...`) is a genuine, unexplained persistence gap.
- **Exact reason each missing branch failed to become operational evidence**: 24 × `EXPIRED_SIBLING`/`sibling_idle` (an intentional, auditable classification decision); 1 × detected in `wt_subprov_evidence` but never promoted to a candidate watch (root stage unresolved — "never persisted" per the brief's taxonomy, cause within that stage not further determinable from this database-only evidence).
- **Does the schema fully represent plain-transfer provisioning?** No — no plain-transfer creator-funding row exists anywhere in ANen's persisted evidence, even though such mechanism activity was independently confirmed on-chain in X29.8.

## Success criteria — answered

- **Does the observed corpus already contain enough information to reconstruct the complete operational graph?** No — the persisted corpus (26 wrap-close events) is itself a small, evidence-table-specific slice of ANen's true on-chain activity (X29.8 found ~4,263 real transactions); even within that 26-event slice, only 1 fully reached the operational graph.
- **Are creator-funding branches being correctly recognised?** Partially. The 25 candidates that reached `wt_candidate_websocket_watches` were correctly recognised as creator-funding attempts and correctly classified per the sibling-suppression rule (24 excluded, 1 promoted) — this part of the pipeline is working exactly as designed. The 1 orphaned branch shows recognition can still silently fail even after successful mechanism/creator extraction.
- **Does the persistence model support every observed funding mechanism?** No — while the schema has `funding_mechanism` columns capable of holding `PLAIN_TRANSFER`/`SEEDED_ACCOUNT_CLOSE` values, no evidence of a working plain-transfer persistence path was found for this subprovider despite plain-transfer activity being confirmed on-chain (X29.8).
- **Is the operational graph incomplete because of classification, or observation?** **Both, at different stages, and this audit can now separate them precisely** — a distinction the prior X29.7.1/X29.8 audits could not make: (1) 24 of 25 candidate losses are a **classification decision** (sibling-suppression), fully intentional and evidenced; (2) 1 loss is closer to an **observation/promotion gap** (detected in `wt_subprov_evidence` but never became a candidate); (3) separately, the vast majority of ANen's true on-chain activity (per X29.8's RPC trace) never entered any of these evidence tables at all — that is a pre-classification, pre-persistence observation gap this database-only audit can identify the existence of but not further diagnose from these tables alone.
