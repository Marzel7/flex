# X67.20 — Execution Fingerprint Validation & B48k Operation Formation

**Read-only. No code, database, RPC-write, or label changes made.** 54 `getTransaction` calls + 11 `getSignaturesForAddress` calls, jsonParsed/cheap endpoints only. Raw JSON caches deleted from scratchpad after extraction; API key never logged.

Follow-on to `x67_19_b48k_operator_boundary_reeval.md`, which identified a candidate execution fingerprint (persistent 3-signer set, dedicated fee payer) distinguishing B48k from canonical WATCHTOWER PLAIN_XFER transactions at a small sample (n=9). This audit expands RPC coverage and tests whether that fingerprint is exclusive to B48k, shared with WATCHTOWER, or generic shared infrastructure.

## Phase 1 — Expanded RPC Validation

All 27 direct B48k→creator edges plus 5 relay-hop transactions RPC-decoded (32 total). **All 32 (100%) are byte-identical in structure**: 4-instruction pattern `[advanceNonce, system.transfer, ComputeBudget.setComputeUnitLimit, ComputeBudget.setComputeUnitPrice]`, legacy version (0 loaded address tables), nonce authority `DScDQ1zV4qVMU8HQmfcJkjZhfo5QqCWdV7dbxkb2gU9C` invariant (nonce *account* varies per-tx as expected), fee payer `3ddCq8LgnyYiJFfupiNAR8rQ9BshepSMzSwWbNLageTx` invariant, no memo in any, compute budget present in all 32 (corrects an X67.19 sub-note). Block-time span 1777145508–1785809281 (~99 days).

**Refined signer-set finding**: the 3-wallet set is `{3ddCq8Lg…, DScDQ1zV…, THIRD}` where THIRD = B48k (27/32) or one of 5 relay wallets (5/32). The fixed pair is the true invariant; B48k/relays occupy an interchangeable session-wallet slot. **Direction finding**: at least one relay-hop tx (`4tgDiRgKDgzfZAj3NjUP9Hgtdk5HmTYe1u19jBuDTnnijyTdaymdogMZtP6dQ28uXiCfenPjapFMCDwSfz7yvyc`, `GoZMJFTBd72j…` → B48k, 311,131,660 lamports) is the relay **funding B48k**, not B48k funding a creator — the builder is bidirectional/session-generic.

## Phase 2 — Signer Inventory

| Wallet | Role | Txs (sample) | Local DB footprint | Elsewhere |
|---|---|---|---|---|
| `3ddCq8LgnyYiJFfupiNAR8rQ9BshepSMzSwWbNLageTx` | Fixed fee payer | 32/32 B48k-cohort; ≥1000 total sigs | **Zero hits**, wide per-column exact-match scan of all tables, both DBs | 60% of a 40-tx sample of its own history involves non-B48k counterparties (23 distinct wallets) |
| `DScDQ1zV4qVMU8HQmfcJkjZhfo5QqCWdV7dbxkb2gU9C` | Fixed nonce authority | 32/32; 100% co-occurrence with `3ddCq8Lg…` | **Zero hits**, same wide scan | Always paired, never alone |
| `B48kNVXs4YK4amkBCH2XokQiv1SeiVQGHDR17xDeKAAn` | Session wallet | 27 direct + relay-hop counterparty | Hits in 27 distinct tables | Central node by design |
| 5 relay wallets (`GoZMJFTBd72j…`, `8Xf6P3PaCds…`, `EmTrtHEP1BU8…`, `C9Pxdh1gJtjBQ…`, `3e1H4g39XvMt…`) | Session wallet (5/32), fund B48k | 1 tx each | Not in `is_known_account()`/`get_cex_info()` | Same THIRD-slot role as B48k |
| 15 other wallets (e.g. `7wLvZuQ7ZTHhdTutc8EfTwoZg1phY8t3RFS811SgxiDN`, `4Q93RgFir8sz7h5Q9xfTZBVXH3yXuREzdqCcYwF6ysXR`, `28aBDLi8UBBj9Rbnb4frsUKfSdLumbJXQeBQ6kcNHNRA`, `6VoVswmr95TG4Rub41zieRweezLEGZN2EVebLg7BH3GW`, `9giFQanfBDrQKoRwiXxmWYGEs588gMPmmnUJe2XTbRxD`, +10 more) | **Independent third-party clients of the same infra** | 1 tx each | **Zero hits** in wt_ops_v2.db | One (`7wLvZuQ7ZTHhdTutc8EfTwoZg1phY8t3RFS811SgxiDN`) uses an **SPL-token USDC `transferChecked`**, not SOL — proves the service is asset-generic |

**Wide search result** (X67.19's stated gap, now closed): exact-match search of `3ddCq8Lg…`/`DScDQ1zV…` across every table in both DBs returns **zero rows** in 43 checked tables. Control query for `B48k…` correctly found 27 tables with hits, confirming the null result is genuine, not a methodology failure.

## Phase 3 — Fee Payer Audit

Single fee payer, 32/32 B48k-cohort transactions: `3ddCq8LgnyYiJFfupiNAR8rQ9BshepSMzSwWbNLageTx`. A 40-tx random sample of its own broader signature history (8 pages, block_time 1785709623–1785823332) found only **16/40 (40%) involved B48k**; **24/40 (60%) involved entirely different wallets**, none cross-referencing to B48k, WATCHTOWER, or each other. **Key finding: the fee payer is not B48k-exclusive infrastructure — it is a shared service with B48k as one of many clients.**

## Phase 4 — Relay Audit

All 5 relay wallets RPC-decoded, all use the identical fee payer/nonce-authority/4-instruction builder, but each transaction is **relay → B48k** (funding B48k), not B48k → creator:
- `GoZMJFTBd72j6yCxajtiNEq1EMp5dZjnvF9xE4ReQEY2` → B48k, sig `4tgDiRgKDgzfZAj3NjUP9Hgtdk5HmTYe1u19jBuDTnnijyTdaymdogMZtP6dQ28uXiCfenPjapFMCDwSfz7yvyc`
- `8Xf6P3PaCdsdXDZpDLEQx8sQfUsQwUxNaudLBYw9iWhh` → B48k, sig `3bSDLb2xZKcviynLru3NdNZPXdyKn6mf2JTQF6L8gmHhjaaK4axgoGvzGAGPS7RG8dgvTydTke8aWCt4sZpRtubh`
- `EmTrtHEP1BU8rL9xcsWpaEzHArDXiWogB2bshD7WedKf` → B48k, sig `3NZSAskWCfJ1drBLfqqULAqig21fWdrYiWMkzkzkAD4UTq5PEyXow1hmatkf5zPQgo1tpEGwagWXAjApMyVg3AaH`
- `C9Pxdh1gJtjBQPeSo5th3LhWbg58dHbERxTZBwuizJmq` → B48k, sig `2Zye4EPx576Qq1VTVGcge4b1pMZRssMKV561RaCSqRP5oVeGyerhA2LkuYhTg6EB58PCGMLYEFRtZssjMeXViqU6`
- `3e1H4g39XvMt5BiZh8HckBgUtxRiA7SBwFP7ercSJjQg` → B48k, sig `3KC6JLtq7ifeCZmUcsgxN661J6xkEayy3HLpGmQw9JcjtqQJUbAdKEidAhymkpGugJvoDpspzsQwX8549XzNHNcs`

Relays are upstream capital sources into B48k using the shared builder, not a bespoke downstream tier — none independently registered in `wt_provisioning_edges`.

## Phase 5 — WATCHTOWER Comparison

**Canonical PLAIN_XFER (all 5 registry mints + 1 extra confirmatory sample from `F7p3dFrjRT…`)**: all single-signer, self-paid, no durable nonce; 2 of 6 use v0/memo (`8XaVic8H3Rr8jiWhnrEdpVWoSakTygj3R5NdQtr9pump`, `E7AAwze6ch19cmexjsNHgz7tT27yzzvjqZ79AD8Zpump`); one used treasury `69SNcRC8…` as fee payer instead of self (`CVdByCD7SLsj2Kv7UAqGyNJgSVc4Nvd8qdL2U1shpump`).

**Canonical account-close control (12 sampled WSOL_WRAP_CLOSE)**: `3gosQAi7WAKRnkCibW2hamv9NkVvFLXePAPQZb5Gpump`, `5saAXYWpQvMB5MLi1xUBUTWz2DFBb65fbNJx7kEXpump`, `6YZm2PVLBozyfvGrMTrZbZHxYQz8aFG3sm6rCfw3pump`, `7z4cgsb7egGx4iWXioU5agYP2cU5tyoXZakSCxafpump`, `8J27Jc1iHL2vhCKNb8PqRD27Zfsb2Jd3fawT4UcDpump`, `FqG8PWuoj1zeXVG1i13fZSaBhqfNTcVcRfAFvGkqpump`, `85pXzSdTSA4wu8t3qY8JkgHeMHWzMaE1j94duRTppump`, `AWiaGsus1cVJmFYkp8akfq8ZDsh9dUWuXcdLv7ZXpump`, `Bh819rWjgJvWKeu5G38AcdZm7aKq8RdSfF7stGREpump`, `AyafwyhUhZW4L9bdeG2nBjTpQkt5ek1aA1A78XGopump`, `8ogbE5kVqeTARSLGuXP43ReVbdWzc6cDfA6yfik2pump`, `2PZAgPXXAUWv5EVkYUqDaroCzqW7QcxF8JfsRVKopump` — all 12 use 2 signers (wrap-wallet + subprov pair), legacy version, self/pair-paid; 0/12 durable nonce; 0/12 match the `3ddCq8Lg…`/`DScDQ1zV…` pair.

**Overlap: 0 of 18 control transactions match B48k on any dimension. Exact overlap = 0/18 = 0%.**

## Phase 6 — Global Search

DB-native search returns nothing (fingerprint wallets absent from all tables). RPC-native search of the fee payer's own history (40-tx sample) found a substantially larger, heterogeneous non-B48k population (23 distinct wallets), none of which cross-reference each other or WATCHTOWER — not a second WATCHTOWER-linked cluster, but a general-purpose shared service's broader client base.

## Phase 7 — Fingerprint Stability (32-tx B48k cohort)

- Instruction ordering: 32/32 = 100%
- Fee payer identity: 32/32 = 100%
- Nonce authority identity: 32/32 = 100%
- Legacy version, 0 loaded addresses: 32/32 = 100%
- No memo: 32/32 = 100%
- **Literal 3-wallet signer set**: NOT constant — 6 distinct triples (expected, since the third slot rotates through B48k/5 relays)

No outliers on any builder-level dimension.

## Phase 8 — False Positive Testing

| Cohort | n | Fingerprint-positive | Fingerprint-negative |
|---|---|---|---|
| B48k direct + relay-hop | 32 | 32 (100%, TP) | 0 |
| Canonical WSOL_WRAP_CLOSE | 12 | 0 | 12 (100%, TN) |
| Canonical PLAIN_XFER | 6 | 0 | 6 (100%, TN) |
| CEX-funded launches | — | not identifiable in sample via `get_cex_info()` | — |

**0 false positives, 0 false negatives across 50 RPC-verified transactions** (up from n=9 in X67.19). However, this is only validated against the WATCHTOWER control set — not against the fee payer's full non-B48k client population, which is real and substantial (≥60% of its own traffic).

## Phase 9 — Operation Boundary

- **H1 (WATCHTOWER execution family)** — REJECTED, reaffirmed at n=50 (0/18 overlap).
- **H2 (B48k is a separate operator with bespoke execution infra)** — **PARTIALLY REJECTED, revised down from X67.19.** The fingerprint is not B48k-exclusive; 60% of the fee payer's own sampled traffic is non-B48k.
- **H3 (Shared infrastructure)** — **SUPPORTED, upgraded from REJECTED in X67.19.** X67.19 only tested whether B48k's infra served other *confirmed WATCHTOWER operators* (found none); it does not test whether it served *any* other party — and it demonstrably does, including a non-SOL SPL-token transfer entirely unrelated to token launches.
- **H4 (fingerprint is not operator-specific / generic infra)** — **SUPPORTED, strengthened.** Pattern reads as a generic durable-nonce transaction-relay/session-management service, not bespoke B48k tooling.

**Revised conclusion**: B48k's downstream launch cohort (single consistent fee source → 27+ fresh creators, PLAIN_XFER) remains internally coherent and structurally distinct from canonical WATCHTOWER. But the claim that B48k *owns* unique execution infrastructure is downgraded — it is a distinctive client of a shared service, not the operator of exclusive tooling.

## Phase 10 — Shadow Operation

- **High confidence** (27, direct fee-source = B48k, RPC-confirmed): includes `73Ldwtam8mZZALK4veHMDsnMBcsPJMQcapaYk8bHpump`, `BNz8HBXTkYUtsn22fZSzu3Fb461AttKwScGgwHR7a5sp`, `FB44zC6s2jkysjaB2NC8u6XqwhPJwir1DYFzEhXbpump`, `6ANRcu9SxHyWr5MCbBWLehYzgVPhMrS9j9sszCxfpump` (the 4 previously-closed candidates, confirmed as a subset of the 27) plus 23 others.
- **Probable** (5, relay-hop): `4VgxZm5Gr24uLdP4E2puLRsUQsW6VQUmvRtsewXwpump`, `EZNN9ALUuYdq8McRgx5Qt73UNFpjnx1zhtyVmm2Rpump`, `DNydAFTwm22FiG4baSiHXQ8mjbxGyrD4ymhG8y48pump`, `cvgrnjj4TtUonSKfbsXvWGRKuJ9wrjdGjUVzg2fpump`, `AwWvmm5Lx1Ytm1jQ8nJL9XDvcbBaHxw85YyZ9Qgxpump`.
- **Possible**: none newly identified (broader 133-cohort freshness question out of scope this pass).
- **Excluded**: all 17 control mints (5 canonical PLAIN_XFER + 12 account-close) — 0/17 fingerprint match.
- **Unevaluable**: 15 non-B48k third-party wallets found via the infra pair's history — not part of B48k's operation, identity/purpose unresolved.

## Phase 11 — Detection Signature

**Required**: exactly 3 signers; fee payer = `3ddCq8LgnyYiJFfupiNAR8rQ9BshepSMzSwWbNLageTx` OR first instruction is `advanceNonce` with authority `DScDQ1zV4qVMU8HQmfcJkjZhfo5QqCWdV7dbxkb2gU9C`; legacy version, 0 loaded address tables.

**Supporting**: 4-instruction pattern (advanceNonce/transfer/ComputeBudget×2); no memo; third signer already present in WATCHTOWER's own tables (separates a B48k-cohort tx from an unrelated shared-service client).

**Exclusion**: transaction is an SPL-token instruction rather than native SOL transfer (observed at least once, unrelated to launches); third signer has no cross-reference anywhere and isn't one of the 5 known relays — treat as unaffiliated infra-pair client.

## Final Verdicts

**Verdict 1 — Execution Fingerprint: B (Mostly unique).** 100% consistent within B48k (32/32), 100% absent from WATCHTOWER control (0/18) — clean vs. WATCHTOWER, but demonstrably not exclusive to B48k (60% of the fee payer's own sampled traffic is non-B48k).

**Verdict 2 — Operator Identity: C (Shared infrastructure)**, with the caveat that B48k's downstream launch cohort remains independently coherent. Revises X67.19's Verdict 1 (independent operator using shared *treasury* infra) one layer further: the *execution/builder* layer is also shared, not bespoke.

**Verdict 3 — Production Readiness: C (More investigation required).** Clean at the pattern-match level (0 FP/FN, n=50) but cannot be deployed as an operator-identity detector without a cross-reference gate, or it will admit unrelated shared-service clients (demonstrated by the SPL-token counter-example). Recommended before shadow detection: (a) off-chain research to identify whether `3ddCq8Lg…`/`DScDQ1zV…` is a named public wallet-infra/bot service; (b) expand the non-B48k client sample to bound how much of that traffic could itself be undiscovered WATCHTOWER-adjacent activity.

## Required Counts

| Metric | Count |
|---|---|
| Launches analysed | 37 tracked + 18 control (12 account-close + 6 PLAIN_XFER) |
| RPC transactions decoded | 54 (`getTransaction`) |
| `getSignaturesForAddress` calls | 11 |
| Unique signer wallets (B48k-cohort) | 8 |
| Unique signer wallets (non-B48k clients sampled) | 19 |
| Unique fee payers (B48k-cohort) | 1 |
| Unique relays | 5 |
| Fingerprint matches | 32/32 |
| WATCHTOWER overlaps | 0/18 |
| Unrelated overlaps | ≥15 wallets / ≥24 txs in 40-tx sample (likely hundreds+ across full history) |
| True positives | 32 |
| False positives | 0 |
| False negatives | 0 |

**Key repo references**: `docs/audits/x67_19_b48k_operator_boundary_reeval.md`, `docs/audits/x67_18_b48k_operation_audit.md`, `database/wt_ops_v2.db` (wt_provisioning_edges, wt_walkback_queue, wt_watchtower_launches), `database/flex_complete_database.db` (creator_funders, token_analysis), `src/ops/watchtower_canonical_adapters.py:319-325`, `src/utils/infra_mapping.py`.
