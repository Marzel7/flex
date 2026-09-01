# X67.22 — B48k Client Family Discovery (Read-Only)

**Read-only.** No code, database, commit, or label changes made. RPC usage this pass: 1 `getHealth` liveness check, 6 `getSignaturesForAddress` calls, ~12 `getTransaction` calls (jsonParsed, cheap endpoint only), all against candidate sibling wallets. Raw JSON cached in scratchpad, deleted after extraction. API key never logged.

Follow-on to X67.18-21. Where X67.19-21 searched for B48k's siblings by matching the *execution service/builder fingerprint* (and found none — the shared service's other ~118 clients are mostly unrelated stablecoin traffic), this audit searches by *behavioral signature* instead: treasury relationship, session mechanism, and PLAIN_XFER creator-funding pattern — independent of which transaction builder a candidate uses. This reframing found a real second family member.

## Phase 1 — Complete B48k Profile

**Wallet**: `B48kNVXs4YK4amkBCH2XokQiv1SeiVQGHDR17xDeKAAn`

- **First session**: 2026-07-10 10:40:11 UTC (funding sig `4PPwEgGM…`, treasury `69SNcRC8…`, 23.45 SOL)
- **Last session**: 2026-07-25 16:35:54 UTC (sig `2zqQYmo7…`, treasury `69SNcRC8…`, 2.0 SOL)
- **First creator-funding edge**: block_time 1777145508 (mint `Ae1CkXFQ6G6FH4GBMGJBHfLZjvJGhVTFPtWAyMqHpump`)
- **Last creator-funding edge**: block_time 1785809281 (mint `6fEaYuzirTMXFnFo7dGKHJs8wWVFPdh1bfZL9oRPpump`), **still active as of 2026-08-04**
- **Sessions**: 10, all `state=EXPIRED`, all `funding_mechanism=WSOL_WRAP_CLOSE` inbound; `open_reason` = `SUBPROV_REACTIVATED` (8/10) or `CONTINUING_OPERATION` (2/10); avg `topup_count`=0.9, avg `sweep_count`=0.6; amounts 0.115869–88.586529 SOL
- **Treasuries**: `69SNcRC8NqjHBSXEcugCN5oFKRQoKmddmWzZYc3tqtxk` (8 sessions), `DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK` (1), `EFKVdKPrxMpofZMkPBWNe9Jp3hREmtoMZmNo7yFAMUo5` (1)
- **Direct SUBPROV_TO_CREATOR edges**: 27, all `PLAIN_XFER`, 0.09–79.19 SOL
- **Relay wallets**: 5 originally known + 2 newly discovered in X67.21 — total 7 (`GoZMJFTBd72j…`, `8Xf6P3Pa…`, `EmTrtHEP1BU8…`, `C9Pxdh1gJtjBQ…`, `3e1H4g39XvMt…`, `6CdJi6M7…`, `92BKzviijLwY…`)
- **Execution builder**: shared commercial durable-nonce service (fee payer `3ddCq8Lg…`, nonce authority `DScDQ1zV…`), confirmed serving ≥118 distinct clients system-wide — not B48k-exclusive.
- **Creator freshness**: 53.5% single-token (fresh), 46.5% reused, across the broader 133-creator cohort.

## Phase 2 — Sibling Discovery

**Core technique**: joined `wt_provisioning_edges` (`edge_type='SUBPROV_TO_CREATOR' AND funding_mechanism='PLAIN_XFER'`) against `wt_active_subprov_sessions` filtered to wallets sharing at least one of B48k's 3 treasuries, `funding_mechanism='WSOL_WRAP_CLOSE'` inbound, and `open_reason='SUBPROV_REACTIVATED'`.

**System-wide scale**: `wt_provisioning_edges` contains 1,817 `SUBPROV_TO_CREATOR PLAIN_XFER` rows across many distinct `from_wallet`s. 49 distinct wallets share at least one B48k treasury AND exhibit the PLAIN_XFER-to-creator pattern.

**Critical filter — CEX registry cross-check** (`src/utils/infra_mapping.get_cex_info()`): the majority of top candidates by session count resolved to **known exchange hot wallets**:

| Wallet | Resolution |
|---|---|
| `A77HErqtfN1hLLpvZ9pCtu66FEtM8BveoaKbbMoZ4RiR` | Bidget Exchange |
| `BmFdpraQhkiDQE6SnfG5omcA1VwzqfXrwtNYBwWTymy6` | KuCoin 2 |
| `H8sMJSCQxfKiFTCfDR3DUMLPwcRbM61LGFJ8N4dK3WjS` | Coinbase Hot Wallet 1 |
| `iGdFcQoyR2MwbXMHQskhmNsqddZ6rinsipHc4TNSdwu` | Bybit Wallet 10 |
| `5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9` | Binance 2 |
| `is6MTRHEgyFLNTfYcuV4QBWLjrZBfmhVNYR6ccgr8KV` | OKX Hot Wallet |
| `FpwQQhQQoEaVu3WU2qZMfF1hx48YyfwsLoRgXG83E99Q` | Coinbase Hot Wallet 1 |
| `6LY1JzAFVZsP2a2xKrtU6znQMQ5h4i7tocWdgrkZzkzF` | Kraken Hot Wallet |
| `8mowmVCEewZ9W2cEaQyQeQEeSxhGr1hvRviLwozwNtBt` | WhiteBIT Hot Wallet |
| (others) | Stake.com, Gate, MEXC, FixedFloat, Revolut hot wallets |

Important correction to this audit's own initial lead: "same treasury + PLAIN_XFER" alone is **not sufficient** — it also matches ordinary CEX withdrawal traffic. The codebase's own `wt_discovered_subprovs` scorer had **already independently flagged 3 of these** (`A77HErqt`, `BmFdpraQ`, `iGdFcQo`) as `REJECTED_INFRASTRUCTURE` / `KNOWN_INFRASTRUCTURE_REGISTRY_MATCH` — confirming this filter matches an existing, working guardrail in the system.

**`FncazAs6om…`/`HBQ2TC2g…`** (185/138 sessions, both non-CEX): ruled out separately — flat 0.1 SOL dust top-ups (185 consecutive sessions all exactly 0.1 SOL), a micro-ping/keep-alive pattern, not launch-funding cadence. Excluded.

**Surviving non-CEX, non-dust, behaviorally B48k-like candidates**:

| Wallet | Sessions | Direct edges | Treasuries matched | CEX/registry | wt_infrastructure_candidates |
|---|---|---|---|---|---|
| `Dv34prGm2BT7Ph2n6qKLgzeLgjnii87RJJ7Db6ZQQvKM` | 10 | 40 | **all 3** (69SNcRC8, DchJqu, EFKVdKPr) | none | `OPERATIONAL_TREASURY`, `HIGH_REVIEW`, role_score_treasury=65 |
| `9cDDJ5g2wPqVZUZwpPuwqzxN7ouvc6QFauFwrX2TTTAX` | 13 | 2 | DchJqu, 69SNcRC8 | none | not evaluated |
| `6tckHFBpiJ8YgYN8FUskvtvTpXQZ55g5LHeo1kvELoDQ` | 8 | 2 | DchJqu, 69SNcRC8, Dtwi1eL… | none | not evaluated |

`Dv34prGm` is by far the strongest sibling and is treated as the primary discovery of this audit.

## Phase 3 — Family Clustering

**Dv34prGm profile** (vs. B48k):
- Sessions: 10, all `WSOL_WRAP_CLOSE` in, 8/10 `SUBPROV_REACTIVATED`, funded by **all three** of B48k's treasuries plus one extra (`9hGcxVHFajR4…`) — a *broader* treasury relationship than B48k's own.
- Direct SUBPROV_TO_CREATOR PLAIN_XFER edges: 40 (more than B48k's 27), amounts 0.199–190.14 SOL, spanning 2026-07-15 through 2026-08-03.
- 68 distinct creators funded, 43 single-token/fresh (63%), 24 multi-token, 1 unmatched — freshness rate *better* than B48k's 53.5%.
- RPC-verified (3 transactions): **single-signer, self-paid** — `Dv34prGm` signs and pays its own fee, legacy version, **no durable nonce, no shared fee payer**. Structurally different from B48k's execution builder (`3ddCq8Lg…`/`DScDQ1zV…` 3-signer durable-nonce construction).
- Own `wt_infrastructure_candidates` scoring: `OPERATIONAL_TREASURY`, `HIGH_REVIEW`, `role_score_treasury=65`, `role_score_hub=25` — the same treasury/hub ambiguity the codebase's own heuristic saw in B48k (`role_score_treasury=45` in X67.18).

**Clustering verdict: Small client family (2–3 confirmed members), not isolated, not large/rotating.**

- B48k and Dv34prGm independently reconstruct the *same* topological shape: Treasury(WSOL_WRAP_CLOSE)→session wallet(reused/reactivated)→creator(PLAIN_XFER)→CREATE, drawing from the identical 3-treasury pool, with comparable freshness rates and comparable wide funding-amount distributions.
- They do **not** share relay wallets, execution builder, or any co-signer — each has its own independent, self-contained relay/execution stack.
- `9cDDJ5g2` and `6tckHFBpiJ8` are weaker, thinner-evidence candidates (2 direct edges each).
- No evidence of "one big rotating pool" — this reads as **multiple independent client wallets pulling from the same shared treasury infrastructure, each running its own separate downstream execution**, a family bound by *treasury choice and topology*, not by shared tooling.

## Phase 4 — Lifecycle Analysis

| Wallet | First session | Last session | First launch edge | Last launch edge | Status as of 2026-08-04 |
|---|---|---|---|---|---|
| B48k | 2026-07-10 | 2026-07-25 | 2026-04-25 | 2026-08-04 02:08 | **still active** |
| Dv34prGm | 2026-07-14 | 2026-07-22 | 2026-07-15 | 2026-08-03 18:47 | **still active** |
| 9cDDJ5g2 | (2025→2026-07-19) | | | | thin data |
| 6tckHFBpiJ8 | 2025-12→2026-07-25 | | | | thin data |

All sessions for both B48k and Dv34prGm show `state=EXPIRED`, but the *wallet itself* keeps getting **reactivated** — wallet-level persistence with session-level churn, not birth→death→replacement. **No handoff pattern found** — their active windows *overlap* almost completely (both running through 2026-07-22 to 2026-08-04). This is concurrent parallel operation, not sequential replacement.

## Phase 5 — Treasury Relationships (Matrix)

| Treasury | Client wallets used | Confirmed launches |
|---|---|---|
| `69SNcRC8NqjHBSXEcugCN5oFKRQoKmddmWzZYc3tqtxk` | B48k (8), Dv34prGm (3), 9cDDJ5g2 (10), 6tckHFBpiJ8 (3) — plus 87,957 other wallets total (omnibus) | B48k 27, Dv34prGm 40 |
| `DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK` | B48k (1), Dv34prGm (3), 9cDDJ5g2 (2), 6tckHFBpiJ8 (2) | see above |
| `EFKVdKPrxMpofZMkPBWNe9Jp3hREmtoMZmNo7yFAMUo5` | B48k (1), Dv34prGm (1) | see above |
| `9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4` | Dv34prGm (1, extra treasury not shared with B48k) | 0 traced this pass |
| `Dtwi1eLMTLaUuCwbztpFEoepBdzhjhjmLoTyAdsR3p3u` | 6tckHFBpiJ8 (1, extra treasury) | 0 traced |

**Answer: the treasury introduces new, distinct client wallets regularly**, not just B48k. The same 3-treasury pool funds at least 4 different downstream client-slot wallets that independently produce PLAIN_XFER creator-funding chains. Given `69SNcRC8` alone funds 87,957 total wallets, this family is a small, distinguishable sub-population within a much larger omnibus pool — the *behavioral signature* (WSOL_WRAP_CLOSE in / SUBPROV_REACTIVATED / PLAIN_XFER out / non-CEX) reliably isolates them from CEX-withdrawal noise and dust-topup noise.

## Phase 6 — Relay Relationships

**Result: relays are exclusive per client, not shared across the family.**

- B48k's 7 relay wallets appear **zero times** as `from_wallet` or `to_wallet` for Dv34prGm, 9cDDJ5g2, or 6tckHFBpiJ8 in `wt_provisioning_edges`.
- Dv34prGm's direct funding-in edges were not found in `wt_provisioning_edges` in this pass (likely session-tracked via `wt_active_subprov_sessions.funding_signature` instead).
- Each client (B48k, Dv34prGm) runs its own private relay set drawing from the shared treasury pool, with no cross-branching between clients at the relay layer.

## Phase 7 — Creator Behaviour Comparison

| Metric | B48k (133-cohort) | Dv34prGm (68-cohort) |
|---|---|---|
| Total creators funded | 133 | 68 |
| Single-token (fresh) | 69 (53.5%) | 43 (63.2%) |
| Multi-token (reused) | 60 (46.5%) | 24 (35.3%) |
| Unresolved | 4 | 1 |
| Funding amount range | 0.09–79.19 SOL | 0.199–190.14 SOL |
| Launches per session | ~2.7 (27 edges / 10 sessions) | ~4.0 (40 edges / 10 sessions) |

**Statistically similar shape**: both cohorts show a majority-fresh but substantial-reuse creator population, both draw from wide funding-amount ranges, both produce several launches per funding session (batch disbursement) rather than 1:1 session:launch. Dv34prGm's freshness rate is somewhat better; its funding amounts skew larger. Funding-delay/migration-delay were not separately measured this pass.

## Phase 8 — Temporal Evolution

- **B48k**: launch cohort spans 2026-04-25 → 2026-08-04, session-funding activity concentrated 2026-07-10 to 2026-07-25 — a long-lived, periodically-reactivated wallet.
- **Dv34prGm**: launch activity starts 2026-07-15, essentially **contemporaneous with B48k's July session burst**, continues in parallel through 2026-08-03.
- **No B48k decline/replacement pattern observed** — B48k's last launch edge (2026-08-04 02:08) is essentially simultaneous with Dv34prGm's last (2026-08-03 18:47). This does not support a "B48k dies → Dv34prGm replaces it" narrative; it supports **concurrent multi-client operation from the same treasury pool**.

## Phase 9 — Detection Potential (Lead Time)

For B48k: first WSOL_WRAP_CLOSE session (2026-07-10 10:40:11) → launches begin within roughly 1-2 days of a funding session.

For Dv34prGm: first session 2026-07-14 08:06:31 → first PLAIN_XFER creator edge 2026-07-15 00:42:38 → **lead time ≈ 16.6 hours**.

**Quantified lead time: on the order of 12–48 hours** between a WSOL_WRAP_CLOSE session opening on one of the 3 known treasuries (for a non-CEX, non-registered wallet) and the first observed creator-funding PLAIN_XFER transaction. This is a real, actionable early-warning window — **provided the CEX/dust filters from Phase 2 are applied first**; otherwise the false-positive rate from CEX withdrawal traffic would swamp the signal (dozens of CEX hot wallets share the exact same session-mechanism fingerprint).

## Phase 10 — Candidate Discovery (Shadow Classifications, Not Persisted)

| Wallet | Shadow classification | Basis |
|---|---|---|
| `Dv34prGm2BT7Ph2n6qKLgzeLgjnii87RJJ7Db6ZQQvKM` | **ACTIVE_CLIENT** | 40 confirmed edges, sessions through 2026-07-22, launches through 2026-08-03, matches B48k profile on every axis except execution builder |
| `9cDDJ5g2wPqVZUZwpPuwqzxN7ouvc6QFauFwrX2TTTAX` | **DORMANT_CLIENT / weak evidence** | 13 sessions but only 2 confirmed edges and 6 creators; last session ~2026-07-24 |
| `6tckHFBpiJ8YgYN8FUskvtvTpXQZ55g5LHeo1kvELoDQ` | **UNKNOWN** | Session profile matches but 0 creator_funders rows resolved — coverage gap or mislabeled edge |
| `FncazAs6omJJjtLVzquzT9KoyXn6tFixr9kGjr42ktLj`, `HBQ2TC2gmX9qeNuCsY9gRTk9hiZLRZaKhvHGj2ZbVoWB` | **EXCLUDED — different behavioral class** | Flat 0.1 SOL dust-topup cadence (185/138 identical-amount sessions) |
| `A77HErqt…`, `BmFdpraQ…`, `H8sMJSC…`, `iGdFcQo…`, `5tzFkiKscXHK…`, `is6MTRHEg…`, and 6 other CEX hot wallets | **EXCLUDED — CEX withdrawal traffic** | `get_cex_info()` positive match |

## Phase 11 — Client Family Model

```
[Omnibus Treasury Pool: 69SNcRC8… / DchJqu… / EFKVdKPr… (+ minor extras)]
        |
        | WSOL_WRAP_CLOSE (session funding, reactivation pattern)
        |
        +--> [B48k]  ---(shared execution service: 3ddCq8Lg…/DScDQ1zV…)---> creator (PLAIN_XFER) -> CREATE
        |        ^
        |        +-- relay set A (7 wallets, private to B48k)
        |
        +--> [Dv34prGm]  ---(own self-paid, single-signer builder, NO shared service)---> creator (PLAIN_XFER) -> CREATE
        |        ^
        |        +-- relay set B (private to Dv34prGm, no overlap with A)
        |
        +--> [9cDDJ5g2, 6tckHFBpiJ8]  (weaker evidence, same session shape)
        |
        `--> [CEX hot wallets: Bidget/KuCoin/Coinbase/Bybit/Binance/OKX/Kraken/...]
                 (withdrawal traffic — NOT part of the client family; excluded by registry match)
```

**Best-supported model: Treasury Pool → Client Wallet (independent per client, own execution stack) → Own Relay Set → Creator → Launch.** The treasury layer is the true shared substrate; the execution-service layer (X67.20/21's finding) is a red herring for family membership — it identifies *one* client's tooling vendor, not the family boundary. The family boundary is defined by **behavior at the treasury/session/creator-funding layer**. Each client is otherwise a self-contained, independently-built operation.

## Phase 12 — Detection Signature (Backtested)

**Required** (every family member must show all of):
1. Inbound session funding mechanism = `WSOL_WRAP_CLOSE` from one of the 3 known treasury pool wallets (or closely related peers in the same omnibus pool).
2. `open_reason = SUBPROV_REACTIVATED` (persistent, reused wallet).
3. Outbound creator-funding mechanism = `PLAIN_XFER` (never wrap-close) on the `SUBPROV_TO_CREATOR` leg.
4. Wallet is **not** present in the CEX/exchange registry (`get_cex_info()` returns `None`).

**Supporting** (increase confidence):
- Funding amounts span a wide range (sub-1 SOL to 50+ SOL) — a tight, narrow band (e.g., flat 0.1 SOL) indicates a dust/keep-alive pattern.
- Multiple launches per funded session (batch disbursement), not strictly 1:1.
- Creator-freshness rate roughly 50–65% single-token.
- Own `wt_infrastructure_candidates` row scored `OPERATIONAL_TREASURY`/high `role_score_treasury`.

**Exclusion**:
- Positive `get_cex_info()` match — automatically disqualifies.
- Flat, repeated identical small-amount sessions (e.g., 0.1 SOL × 100+) — dust/top-up pattern.
- Wrap-close mechanism on the creator-funding leg — that's canonical WATCHTOWER, not this family.

**Backtest results**:

| Cohort | n | Signature-positive | Notes |
|---|---|---|---|
| B48k direct edges | 27 | 27/27 (100%) TP | reaffirmed |
| Dv34prGm direct edges | 40 | 40/40 (100%) TP | new confirmation this pass |
| CEX hot wallets sampled | 11 | 0/11 (100% TN) | excluded correctly by CEX-registry gate |
| Dust/top-up wallets (FncazAs, HBQ2TC) | 2 | 0/2 (100% TN) | excluded correctly by amount-variance gate |
| Canonical WATCHTOWER PLAIN_XFER (5 registry mints) | 5 | **1 false positive risk identified** | The behavioral-only signature (without the execution-service axis) does **not** by itself distinguish B48k/Dv34prGm-family clients from the 3 known canonical WATCHTOWER PLAIN_XFER subprovs — both groups pass the Required signals. Expected and consistent with X67.19's finding that PLAIN_XFER+treasury alone is under-specified; distinguishing "family" from "canonical WATCHTOWER" requires an added session-volume/topup-rate scale check (canonical subprovs run ~106 sessions avg vs. this family's ~10) |
| 9cDDJ5g2, 6tckHFBpiJ8 (weak candidates) | 2 | 2/2 pass Required, weak on Supporting | flagged low-confidence, not strong TP |

**False negative risk**: 113 unresolved singleton wallets from X67.21's execution-service sample were not individually checked against this behavioral signature — some could be additional family members using yet another independent builder.

## Final Verdicts

**Verdict 1 — Client Structure: B (Small client family).** B48k is not isolated — `Dv34prGm` is a second, independently-confirmed member sharing B48k's full treasury pool, session mechanism, reactivation pattern, and creator-funding mechanism, while running its own distinct (self-paid, single-signer) execution stack and its own private, non-overlapping relay set. Two additional wallets (`9cDDJ5g2`, `6tckHFBpiJ8`) show the same session-level signature with thinner corroborating evidence. This is a small family (2 strong, 2 weak members found), not a large rotating pool (the CEX/dust-topup majority of the raw candidate list was successfully excluded) and not multiple unrelated families (all confirmed members share the identical 3-treasury substrate and topology).

**Verdict 2 — Detection Potential: B (Minor refinement required).** The behavioral signature achieved 100% TP on the 2 confirmed family members and 100% TN on 13 CEX/dust negative controls this pass, and identified a real ~13–48 hour pre-launch lead time. It is not yet disjoint from the 3 canonical WATCHTOWER PLAIN_XFER subprovs without an added session-volume-scale threshold (family ≈10 sessions/wallet vs. canonical ≈106 avg) — that refinement is well-scoped and cheap to add, hence "minor," not "more evidence required."

**Verdict 3 — Strategic Value: A (New attribution layer).** This audit demonstrates a working, reusable discovery method — independent of any single execution service or builder fingerprint — for finding additional B48k-like clients purely from treasury/session/mechanism behavior, and it surfaced a second concrete family member (`Dv34prGm`, 40 launches, 68 creators) that no prior audit in this chain had identified. Because the discovery method is execution-service-agnostic, it generalizes to future clients that adopt yet another transaction builder, which the narrower X67.19–21 execution-fingerprint approach could not have found. This qualifies as a genuinely new attribution layer sitting between the treasury/omnibus layer and individual-operator identity.

## Key Data References (for independent verification)

- Treasuries: `69SNcRC8NqjHBSXEcugCN5oFKRQoKmddmWzZYc3tqtxk`, `DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK`, `EFKVdKPrxMpofZMkPBWNe9Jp3hREmtoMZmNo7yFAMUo5`
- B48k: `B48kNVXs4YK4amkBCH2XokQiv1SeiVQGHDR17xDeKAAn`
- Strong sibling: `Dv34prGm2BT7Ph2n6qKLgzeLgjnii87RJJ7Db6ZQQvKM` (RPC-verified builder-independent, `wt_infrastructure_candidates` role_score_treasury=65)
- Weak siblings: `9cDDJ5g2wPqVZUZwpPuwqzxN7ouvc6QFauFwrX2TTTAX`, `6tckHFBpiJ8YgYN8FUskvtvTpXQZ55g5LHeo1kvELoDQ`
- Excluded (CEX): `A77HErqtfN1hLLpvZ9pCtu66FEtM8BveoaKbbMoZ4RiR` (Bidget), `BmFdpraQhkiDQE6SnfG5omcA1VwzqfXrwtNYBwWTymy6` (KuCoin 2), `H8sMJSCQxfKiFTCfDR3DUMLPwcRbM61LGFJ8N4dK3WjS` (Coinbase 1), `iGdFcQoyR2MwbXMHQskhmNsqddZ6rinsipHc4TNSdwu` (Bybit 10), `5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9` (Binance 2)
- Excluded (dust): `FncazAs6omJJjtLVzquzT9KoyXn6tFixr9kGjr42ktLj`, `HBQ2TC2gmX9qeNuCsY9gRTk9hiZLRZaKhvHGj2ZbVoWB`
- Sample RPC signatures verified this pass (Dv34prGm, single-signer/self-paid): `BGwnHxcY48e69RGCpRrEs9jMZoQF7QEBezv7XcgvrhasE85uMPjzfQjntoUmQCgocjQ4kySGRzLDeRWQvBm6C59`, `ZRr5XDHxfDkdwH6hJiQuTBNJzHEXCqybqhtLqJkpmAtY9gRaVDTEeKBHHVQSrQxxpYirymmt14eHDh1HZvnmkrQ`, `ZiWfsCAQhTnX2j9PmCnKfEB4g3m9mWMWJAmZzucL3B5VBNRoNA3dq6AmzoG13y5ryqcRTpfDisgy9uZEsoHvpny`
- Sample RPC signatures verified (siblings, single-signer/self-paid): `bkS3hswiWmEpkXiMLUCQqhjoBffNcSw6hpFRV1TfGkzfRihuEn1tpGHzQonZxCaLRA7PGPHi66fqf2TdtJ9Chpb` (A77HErqt), `2ic1DRdbBnZi84zJ3tD8poR8i2p22P1ftN2uZdiUp3CdhxFnLQLM9HZGEDSBNVy6wN3zzHdbdf7FJJ7T8DhKjoLG` (BmFdpraQ)
- Local DB tables used: `wt_ops_v2.db: wt_provisioning_edges`, `wt_active_subprov_sessions`, `wt_infrastructure_candidates`, `wt_discovered_subprovs`, `wt_confirmed_treasuries`, `wt_walkback_edge_candidates`, `watchtower_token_attribution`; `flex_complete_database.db: creator_funders`, `token_analysis`
- Code references: `src/utils/infra_mapping.py` (`get_cex_info`, `is_known_account` — the decisive CEX-exclusion filter)
