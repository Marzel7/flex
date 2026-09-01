# X67.19 — B48k Operator-Boundary Re-Evaluation (Read-Only)

Re-evaluates X67.18's finding on wallet `B48kNVXs4YK4amkBCH2XokQiv1SeiVQGHDR17xDeKAAn` ("B48k") under the corrected model that PLAIN_XFER is a genuine WATCHTOWER creator-funding mechanism (X67.15). No code, database, or RPC-write actions taken. 9 RPC `getTransaction` calls made this pass (5 B48k, 4 canonical PLAIN_XFER); raw JSON deleted from scratchpad after extraction; API key never logged.

## Phase 1 — Corrected Comparison Baseline

**Canonical registry composition** (`wt_watchtower_launches`, 176 total rows):
- `WSOL_WRAP_CLOSE`: 153
- `SEEDED_ACCOUNT_CLOSE`: 18
- `PLAIN_XFER`: **exactly 5** — `5Rg9Ay22nwhhgE3adzvwsGxMCKTyrPn3joYhiLZEpump`, `gQcrSg6acMHon1RHfMAwGtdVFvW2mJNF1T6dkgmpump`, `8XaVic8H3Rr8jiWhnrEdpVWoSakTygj3R5NdQtr9pump`, `E7AAwze6ch19cmexjsNHgz7tT27yzzvjqZ79AD8Zpump`, `CVdByCD7SLsj2Kv7UAqGyNJgSVc4Nvd8qdL2U1shpump` (identified in `src/ops/watchtower_canonical_adapters.py:319-325`, `x67_15_verified_plain_xfer_mints`).

**Critical correction confirmed by direct query**: all 5 rows carry `confidence='WALKBACK'` at the row level (`wt_watchtower_launches` has **no `evidence_strength` column at all** — the schema doesn't persist it). This matches X67.17 §8 exactly: X67.15's 75 raw transaction verifications are real, but only these 5 mints are in the *current registry* with PLAIN_XFER, and the RPC-verified fact lives only in the audit output / the adapter's hardcoded frozenset, not as a persisted per-row field. **The other ~70 of X67.15's 75 verified transactions are NOT additional canonical registry rows** — the task brief's framing that would inflate this to "75 canonical launches" is explicitly wrong; the accurate count is 5.

Subprov wallets behind the 5: `97K6nsFhBWDKwQf6heDDhDtsCRC4779LPHSFZkc2zqK4` (2 mints), `F7p3dFrjRTbtRp8FRF6qHLomXbKRBzpvBLjtQcfcgmNe` (2 mints), `FpwQQhQQoEaVu3WU2qZMfF1hx48YyfwsLoRgXG83E99Q` (1 mint). Treasuries: `69SNcRC8NqjHBSXEcugCN5oFKRQoKmddmWzZYc3tqtxk` (4 of 5), `Dtwi1eLMTLaUuCwbztpFEoepBdzhjhjmLoTyAdsR3p3u` (1 of 5).

**No other PLAIN_XFER rows exist in the canonical registry beyond these 5** — confirmed by direct `funding_mechanism='PLAIN_XFER'` filter returning exactly 5 rows. So the "broader, lower-confidence PLAIN_XFER comparison set" anticipated does not exist as a separate cohort within `wt_watchtower_launches` — the 5 *are* the entire canonical PLAIN_XFER population.

**Control cohort for this audit**: the 5 canonical PLAIN_XFER rows and their 3 subprov wallets (session/edge history), plus the 153+18 account-close rows used only for high-level mechanism contrast (not deep session comparison, per task scope).

## Phase 2 — Session Architecture Comparison

| Dimension | B48k (10 sessions) | Canonical PLAIN_XFER subprovs (318 sessions across 3 wallets) | Type |
|---|---|---|---|
| Treasury→session-wallet mechanism | WSOL_WRAP_CLOSE, 100% | Same (per X67.17, account-close leg is structural) | No difference |
| Session-wallet reuse | Yes, `open_reason` mostly `SUBPROV_REACTIVATED` | Yes, `SUBPROV_REACTIVATED`/`CONTINUING_OPERATION` | No difference |
| Creator-funding mechanism | PLAIN_XFER, 100% | PLAIN_XFER, 100% (by definition of cohort) | No difference |
| Direct vs relay-assisted | Both — 27 direct, 6 relay-hop | Not sampled for relay this pass; X67.16 established ~6% relay-assisted globally | Evidence-coverage gap |
| Sessions per subprov wallet | 10 | 318 total across 3 wallets (106/wallet avg) | **Behavioural/scale difference** — canonical subprovs show far higher session churn |
| Top-up behaviour | avg `topup_count`=0.9/session | avg `topup_count`=7.09/session | **Behavioural difference** — canonical wallets topped up ~8x more per session |
| Sweep/cleanup | avg `sweep_count`=0.6/session | avg `sweep_count`=0.20/session | Behavioural difference (B48k sweeps more per session) |
| Funding amount range | 0.09–88.6 SOL (avg 12.2) | 0.05–1061.8 SOL (avg 7.1, wide outliers) | Weak signal, wide overlap |
| Creators funded per subprov (tracked edges) | 27 (B48k) | 1, 1, 2 respectively for the 3 canonical wallets | Evidence-coverage gap — `wt_provisioning_edges` is sparse for canonical wallets relative to session count |
| Durable nonce / fee-payer reuse | Not in local schema | Not in local schema | Resolved via RPC in Phase 4 |
| Memo format | Not in local schema | Not in local schema | Resolved via RPC in Phase 4 |

None of the local-DB-visible dimensions are individually operator-defining on their own; the topup/session-count divergence is genuine but not dispositive without more context.

## Phase 3 — Treasury Relationship Analysis

`69SNcRC8NqjHBSXEcugCN5oFKRQoKmddmWzZYc3tqtxk` funds **87,957 distinct subprov wallets** total (`wt_active_subprov_sessions`). It directly funds:
- B48k (8 of B48k's 10 sessions)
- **2 of the 3 canonical X67.15-verified PLAIN_XFER subprovs** (`97K6nsFhBWDKwQf6heDDhDtsCRC4779LPHSFZkc2zqK4`, `F7p3dFrjRTbtRp8FRF6qHLomXbKRBzpvBLjtQcfcgmNe`) directly

New finding versus X67.18: **the same treasury that funds B48k also directly funds 2 of the 5 canonical rows' subprov wallets**, using the identical WSOL_WRAP_CLOSE inbound mechanism. Given the treasury funds ~88,000 wallets total, this establishes only that B48k and the canonical subprovs draw from the same omnibus pool — not that the treasury treats B48k identically to confirmed subprovs (top-up cadence differs: 0.9 vs 7.09 avg). **Verdict: treasury relationship is generic/pooled, not exclusive or preferential** — consistent with the treasury being omnibus-scale infrastructure.

`DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK` funds B48k once and is linked to the Hello-payment operator identity (`21wG4F3ZR8gwGC47CkpD6ySBUgH9AABtYMBWFiYdTTgv`) — real but single-instance, not corroborated by canonical-subprov overlap this pass.

`EFKVdKPrxMpofZMkPBWNe9Jp3hREmtoMZmNo7yFAMUo5` — weaker MANUAL confirmation, single session, not further explored this pass.

## Phase 4 — Common-Control Signals (the decisive new evidence)

RPC-decoded 5 B48k SUBPROV_TO_CREATOR transactions and 4 canonical PLAIN_XFER transactions (`getTransaction`, `jsonParsed`, cheap endpoint only):

**B48k transactions (5/5 identical pattern)**:
- Signatures: `37MTcQrfwibJkzAq67aNK1GnaMzw4CtWdsnw6br3wDBFUT9VQmsGbtxVZp1bzaY8Wb6EytmHdpuKm2dHcFsDjyxa`, `5LojoQt3CZso4xHPPevJ5DaPqVUgJfNQs2a7bMQwwZiJGrREDnWkCuf5vbb4AznDDo8BEpEDEMMKVaiYFxgrVRvG`, `56E6mpPoChRKY6j17kBR7zy3M3uxzQu44UXxGKxLgqf1isaspQqVoVmEibK9XQV3N4Dg2s28L1c5Ry4LCRR8n4ao`, `jMa9NzUVvVbrbrwedvFzA1W9Sc8gXtQmskkB5An1dNrA1M9Ua1yoYWsBw8mkJ3c6cZif2rjBVfnmmreAnaTBoEK`, `2GaFnajdsXY1AgXyfUs6yagEsk8sPLpfwrRj8p2AWkCacsEg2NpuqDndiviZCbPx2PPdWjdD4YkhjsAgYfoxjWn1` — block_times 1777145508 to 1784356950 (~2 months apart).
- **Every single one**: 3 signers = `3ddCq8LgnyYiJFfupiNAR8rQ9BshepSMzSwWbNLageTx`, `B48kNVXs4YK4amkBCH2XokQiv1SeiVQGHDR17xDeKAAn`, `DScDQ1zV4qVMU8HQmfcJkjZhfo5QqCWdV7dbxkb2gU9C` — identical set every time.
- **Every single one**: fee payer = `3ddCq8LgnyYiJFfupiNAR8rQ9BshepSMzSwWbNLageTx` (NOT B48k itself).
- 4 instructions, ComputeBudget present, no memo.
- Neither `3ddCq8Lg…` nor `DScDQ1zV…` appear anywhere else in `wt_provisioning_edges`, `wt_confirmed_treasuries`, or `is_known_account()`/`get_cex_info()`/`get_account_info()` — unregistered, B48k-exclusive infrastructure wallets in every table checked.

**Canonical PLAIN_XFER transactions (4/4 sampled)**:
- `4RHYAz9Hn1PWrJpLa4kvCjBV9WY9Q2mzZicoSsjnGXhNz5Zzn6nKg7fNgZkTLZMSJ3xQWu9e1CtSyNje2EA5ZnA6` (subprov `97K6ns...`), `3DgdWrHN9vs8L5twigZW9Wyw9RjwFAuo8xxt5dnu6WXES4vPJmZAzpCXkeA86s2jQAcaVw181Sq2PtB2C5dFcMNb` (subprov `F7p3dF...`), `4n21DGkhp3sCFk8C24WNnEnYa5UXgYnRFkHET7dvV2xX6Tbb81LziXjND11GxEWcXVW6kuPhKFozmbf8pZzz3ZCB` (subprov `F7p3dF...`), `4KrZzbndNF5NNNFtaocYvRufabKiioHbsR9pSfQ9myWu3S7D39tXKKvWy2P7TZe2Pd6Ed4J28xE4FQVVTmB9kXs8` (subprov `FpwQQh...`).
- **Every single one**: single signer = the subprov wallet itself; fee payer = the subprov wallet itself (self-signed, self-paid — no external co-signer, no shared fee payer across any of the 3 different subprov wallets).
- One sample (`F7p3dF...`) used a memo instruction (`0x6ff86c7a...` hex memo); B48k and the other 2 canonical samples had none.

**Signal classification**:

| Signal | Classification | Basis |
|---|---|---|
| Signer-set identity (B48k tx vs canonical tx) | **CONTRADICTORY** | B48k always uses a 3-wallet multisig-style signer set with a dedicated non-B48k fee payer; canonical rows always single-signer self-paying. Different transaction-construction architecture entirely. |
| Fee-payer pattern | **CONTRADICTORY** | B48k never pays its own fee (`3ddCq8Lg…` does, 5/5); canonical subprovs always pay their own fee (4/4, 3 different wallets). |
| Fee-payer/co-signer reuse across canonical population | **UNAVAILABLE (as negative control)** — the 3 canonical subprov wallets show no shared fee payer or co-signer among themselves either | Strengthens the CONTRADICTORY read — not sampling noise |
| Compute-budget usage | WEAK_OR_GENERIC | Both cohorts use ComputeBudget instructions — ubiquitous, non-discriminating |
| Memo format | WEAK_OR_GENERIC / UNAVAILABLE | Inconsistent within canonical cohort itself (1 of 4 had a memo); too small a sample to call a pattern |
| Treasury inbound mechanism (WSOL_WRAP_CLOSE) | WEAK_OR_GENERIC | Shared by both, but also shared by ~87,957 other wallets funded by the same treasury — non-discriminating at this scale |
| Durable nonce | UNAVAILABLE | Not present in any of the 9 sampled transactions for either cohort |
| B48k's co-signer wallets appearing in ANY canonical-adjacent table | **CONTRADICTORY (absence as signal)** | Zero hits anywhere in `wt_ops_v2.db` or the known-account registry — private to the B48k execution stack |

**This is the single strongest finding of this audit**: B48k's transaction-builder fingerprint (persistent 3-signer set, dedicated non-self fee payer) is **structurally distinct from all 4 sampled canonical PLAIN_XFER transactions**, which are uniformly single-signer/self-paying. This argues against common downstream execution control — independent of the (now-corrected) PLAIN_XFER mechanism question.

## Phase 5 — External Service / Shared Infrastructure Test

- B48k's two identified co-signer/fee-payer wallets (`3ddCq8Lg…`, `DScDQ1zV…`) show **zero footprint** in any WATCHTOWER-adjacent table or the known-account registry — no evidence they serve any other operator.
- B48k's 5 relay wallets (`GoZMJFTBd72j…`, `8Xf6P3Pa…`, `EmTrtHEP1BU8…`, `C9Pxdh1gJtjBQ…`, `3e1H4g39XvMt…`) not re-probed this pass beyond X67.18's findings — carried forward as UNRESOLVED, not re-confirmed.
- Upstream treasury `69SNcRC8` is definitively shared infrastructure (87,957 wallets) but that is treasury-level, not evidence B48k itself serves multiple operators downstream.
- No downstream creator, mint, or relay wallet found this pass to be independently linked to a confirmed non-WATCHTOWER operator identity.
- **Conclusion: B48k does not test positive as shared infrastructure servicing multiple independently-identified operators.** It reads as a single, internally coherent, privately-operated execution stack drawing treasury capital from a shared/omnibus source — consistent with Hypothesis H4, not H3.

## Phase 6 — B48k Family Internal Consistency

Grouping the 5 RPC-sampled + local-DB B48k transactions by fee payer/signer set: **100% homogeneous** — all 5 sampled transactions (spanning ~2 months) share the identical 3-wallet signer set and fee payer. The 4 previously-closed candidates were not RPC-resampled this pass but carry real signatures in `wt_provisioning_edges` consistent with the rest of the family per X67.18. No evidence of a WATCHTOWER-consistent subgroup splitting off — the family reads as one coherent unit, structurally distinct from canonical WATCHTOWER as a whole.

## Phase 7 — Fresh-Creator and Launch Validation

Query against `flex_complete_database.db: token_analysis` (`earliest_tx_creator`/`pf_ws_creator`) for the 133-creator cohort:
- **133 distinct creators funded by B48k** (`creator_funders`).
- **129 matched to at least one token_analysis row** (4 unmatched, `UNRESOLVED`).
- **60 of 129 (46.5%) are multi-token creators** — classified `REUSED_CREATOR`, the established WATCHTOWER false-positive signature per the single-token-creator-filter heuristic.
- **69 of 129 (53.5%) are single-token** — classified `VALID_LAUNCH_CREATOR` (necessary but not sufficient).
- 4 creators `UNRESOLVED`.

Previously-unapplied check (X67.18 flagged it as a gap, never ran it): nearly half the broader B48k cohort fails the single-token freshness filter that is itself part of the WATCHTOWER-detection toolkit — significant caution warranted before treating the full 133-cohort as one coherent operation.

## Phase 8 — Direct Comparison With Verified WATCHTOWER PLAIN_XFER Rows

| Dimension | Canonical (5 rows, 3 subprovs) | B48k | Divergence |
|---|---|---|---|
| Treasury | 69SNcRC8 (4/5), Dtwi1eL… (1/5) | 69SNcRC8, DchJqu, EFKVdKPr | Partial overlap |
| Session wallet mechanism in | WSOL_WRAP_CLOSE | WSOL_WRAP_CLOSE | Match |
| Creator funder mechanism | PLAIN_XFER | PLAIN_XFER | Match |
| Transfer amount range | 0.07–2.9 SOL (sampled edges) | 0.09–79.19 SOL | Divergence, weak signal |
| Session count per subprov | 106 avg (318/3) | 10 | Large scale divergence |
| Top-up rate | 7.09/session avg | 0.9/session avg | Large behavioural divergence |
| **Transaction signer set** | **Single signer = subprov itself, 4/4** | **3-signer fixed set, 5/5** | **STRUCTURAL DIVERGENCE** |
| **Fee payer** | **Self-paid, 4/4** | **Third-party (`3ddCq8Lg…`), 5/5** | **STRUCTURAL DIVERGENCE** |
| Creator freshness | Not resampled this pass | 46.5% reused (fails freshness filter) | Divergence (B48k weaker) |
| Time-to-create | Not measured this pass | Not measured this pass | Evidence gap both sides |

**Answer: No — B48k is not simply another member of the already-proven WATCHTOWER plain-transfer family.** The divergence is the signer/fee-payer construction plus materially different session-volume/top-up behavior plus a substantially worse creator-freshness rate. PLAIN_XFER mechanism match alone is not sufficient.

## Phase 9 — Existing Non-Promotion Reassessment

`wt_provisioning_candidate_workflow.funding_mechanism` has a hard `CHECK` constraint: `funding_mechanism IS NULL OR funding_mechanism IN ('WSOL_WRAP_CLOSE','SEEDED_ACCOUNT_CLOSE')`. **PLAIN_XFER cannot be stored as a valid mechanism value in this table at all.** Path A's evaluation of B48k's 4 candidates was structurally incapable of representing the true PLAIN_XFER mechanism — the closures (`MULTI_SOURCE_RELAY` ×3, `INSUFFICIENT_EVIDENCE` ×1) were generated under a schema/workflow that couldn't correctly classify B48k's actual mechanism.

- **6ANRcu9SxHyWr5MCbBWLehYzgVPhMrS9j9sszCxfpump, FB44zC6s2jkysjaB2NC8u6XqwhPJwir1DYFzEhXbpump, BNz8HBXTkYUtsn22fZSzu3Fb461AttKwScGgwHR7a5sp** (closure=`MULTI_SOURCE_RELAY`): **OUTDATED_RULE** for the promotion question specifically — BUT this audit's own Phase 4/8 findings (distinct signer/fee-payer fingerprint) provide a separate, valid reason these should stay non-canonical, so the ultimate non-promotion outcome is **still correct**, just not for the reason originally recorded.
- **73Ldwtam8mZZALK4veHMDsnMBcsPJMQcapaYk8bHpump** (closure=`INSUFFICIENT_EVIDENCE`): same schema limitation applies; also **VALID_NON_PROMOTION** independently, given Phase 4/7 findings.
- **Net reassessment**: closure *reason codes* were artifacts of an outdated mechanism gate (OUTDATED_RULE), but closure *outcomes* (non-promotion) remain independently justified by fresh evidence.

## Phase 10 — Operator-Boundary Hypotheses

- **H1 (WATCHTOWER plain-transfer family)** — REJECTED. Contradicted by Phase 4's signer/fee-payer divergence and Phase 7's creator-freshness gap.
- **H2 (WATCHTOWER sub-operation)** — REJECTED. No signer/infrastructure overlap with canonical rows found.
- **H3 (Shared infrastructure)** — REJECTED. Phase 5 found no evidence B48k serves any other confirmed operator.
- **H4 (Separate operator using shared treasury infrastructure)** — **SUPPORTED, primary verdict.** Treasury overlap is real but generic/omnibus-scale; downstream signer set, fee-payer, session/top-up cadence, and creator-freshness profile are all independently divergent.
- **H5 (Mixed family)** — Partially plausible but not evidenced: Phase 6 found the sampled transactions 100% homogeneous.
- **H6 (Unevaluable)** — REJECTED. Sufficient new evidence was obtainable and decisive.

**Confidence: MEDIUM-HIGH for H4.** Supporting: consistent 5/5 RPC signer-set divergence, consistent 4/4 canonical self-pay pattern, zero cross-registry hits for B48k's co-signers, 46.5% creator-reuse rate contradicting WATCHTOWER freshness norms, session-volume/top-up-rate mismatch. Contradicting/unresolved: only 4 canonical transactions sampled (small n), relay-set wallets not re-verified this pass, `DchJqu`/Hello-operator linkage still real and unexplained by H4 alone.

## Phase 11 — Operation Signature Test

- **WATCHTOWER Plain-Transfer Signature** run against B48k positives: under the *original narrow* definition (confirmed treasury + PLAIN_XFER + fresh creator, no signer/fee-payer discriminator), B48k's 27 direct edges would pass on mechanism/treasury alone. This audit argues the signature should be tightened to include signer/fee-payer, which it currently is not.
- **B48k-Specific Signature** (persistent reuse + specific signer/fee-payer pattern + PLAIN_XFER + fresh creator): 27/27 direct B48k edges pass; 0/4 canonical rows pass.
- **Overlap**: under the *current* (mechanism-only) WATCHTOWER definition the two signatures overlap heavily. Under a signer/fee-payer-augmented WATCHTOWER signature (recommended), B48k would be cleanly excluded — the signatures would become disjoint. **Key actionable recommendation**: the current WATCHTOWER PLAIN_XFER signature is under-specified (mechanism + treasury only) and would incorrectly admit B48k; augmenting it with the Phase 4 signer/fee-payer discriminator resolves the ambiguity.

## Phase 12 — Per-Launch Classification

- 27 direct B48k→creator edges: **SEPARATE_B48K_OPERATION**.
- 6 relay-hop edges: **SEPARATE_B48K_OPERATION** (lower confidence — relay signer pattern not independently RPC-verified this pass).
- 4 previously-closed candidates: **SEPARATE_B48K_OPERATION** (non-promotion outcome affirmed).
- Broader 133-creator cohort: **69 single-token creators: PROBABLE_SEPARATE_B48K_OPERATION**; **60 multi-token/reused creators: AMBIGUOUS_CREATOR**; **4 unresolved: UNEVALUABLE**.

## Required Final Verdicts

**Verdict 1 — Operator boundary: D (Separate operator using shared treasury infrastructure).** Treasury overlap with confirmed WATCHTOWER treasuries (69SNcRC8, which also directly funds 2 of the 5 canonical PLAIN_XFER subprovs) is real but generic/omnibus-scale (87,957 total funded wallets). Downstream execution — signer set, fee-payer pattern, session/top-up cadence, creator-reuse rate — is independently and consistently divergent from the canonical WATCHTOWER PLAIN_XFER population across every RPC-sampled transaction.

**Verdict 2 — Existing closures: B (Some closures relied on outdated assumptions).** The workflow table's CHECK constraint structurally cannot represent PLAIN_XFER, meaning the 4 closures were generated by an outdated/incomplete mechanism-gate — but independent evidence (Phase 4, 7) affirms the non-promotion outcome remains correct for different, valid reasons.

**Verdict 3 — Detection readiness: B (Ready as a distinct B48k family only).** The B48k-specific signature is well-evidenced and internally consistent (5/5 RPC sample) — sufficient to track as its own operation entry. Not ready for WATCHTOWER attribution (Phase 4/8 argue against common control); not shadow-only (evidence quality exceeds shadow-detection-grade uncertainty).

## Required Counts

| Metric | Count |
|---|---|
| Tracked launches (wt_ops_v2 cohort) | 37 |
| Broader creator cohort | 133 |
| Valid launch creators (single-token, fresh) | 69 |
| Confirmed WATCHTOWER | 0 |
| Probable WATCHTOWER | 0 |
| WATCHTOWER sub-operation | 0 |
| Shared infrastructure | 0 |
| Separate B48k operation (high confidence: direct+relay+closed) | 37 |
| Separate B48k operation (probable, broader cohort, single-token) | 69 |
| Unrelated | 0 |
| Ambiguous/reused-creator (broader cohort) | 60 |
| Unevaluable | 4 |
| RPC transactions inspected (this pass) | 9 (5 B48k, 4 canonical) |
| Common-control matches | 0 |
| Common-control contradictions | 2 strong (signer-set mismatch 5/5 vs 4/4; fee-payer mismatch 5/5 vs 4/4), consistent across every transaction sampled |

## Recommended Next Action

1. Expand the RPC signer/fee-payer sample beyond 4 canonical transactions (ideally all 5 canonical mints' underlying signatures plus a larger canonical account-close control set) to firm up Phase 4's finding from "strong small-sample signal" to "confirmed population-level discriminator."
2. Formally augment the WATCHTOWER PLAIN_XFER operational signature (X67.17/X67.19) to include signer-set/fee-payer identity as a discriminator — the current mechanism+treasury-only definition would incorrectly admit B48k.
3. Open a distinct, separate B48k operation entry (read-only recommendation only, not performed here) capturing the 27 direct + 6 relay-hop + 4 previously-closed launches, explicitly NOT as a WATCHTOWER sub-entry.
4. Apply the single-token-creator freshness filter finding (46.5% reuse rate) as a hard qualifier before treating the full 133-cohort as one coherent B48k operation — the 60 reused-creator rows likely need their own sub-investigation.
5. Update `wt_provisioning_candidate_workflow`'s `funding_mechanism` CHECK constraint (separately, as its own reviewed change) to allow PLAIN_XFER, so future candidates aren't mechanically miscategorized the way B48k's 4 closures were.
6. RPC-verify the 5 relay wallets for co-signer/fee-payer overlap with the `3ddCq8Lg…`/`DScDQ1zV…` pattern, to determine whether the relay-hop launches share B48k's core signature or represent a further sub-variant.
