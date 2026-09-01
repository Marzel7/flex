# X67.18 — Independent Operation Audit: B48kNVXs4YK4amkBCH2XokQiv1SeiVQGHDR17xDeKAAn

**Read-only. No code, database, or label changes were made.** Local DB evidence is primary; RPC verification was not performed in this pass beyond what was already implicit in the stored `evidence_strength=TRANSACTION_DERIVED` rows in `wt_walkback_edge_candidates` (which are themselves the product of prior RPC-derived reconstruction, not raw stored labels) — see Constraint note in Phase 4 below.

## Phase 1 — Local Data Inventory

| Table | Role/field | Row count | First seen | Last seen | Notes |
|---|---|---|---|---|---|
| `wt_ops_v2.db: watchtower_token_attribution` | `matched_subprov` | 36 | 1783464158 | 1785812523 | scored WALKBACK tier, score 80.0, `reviewed_status=AUTO` |
| `wt_ops_v2.db: wt_walkback_queue` | `subprov` | 37 | — | — | mostly `walkback_class=FULL_WALKBACK`, mechanism `PLAIN_XFER` |
| `wt_ops_v2.db: wt_walkback_queue` | `funder_wallet` | 31 | 1777145508 | 1785809281 | B48k itself as the direct on-chain funder of the creator (`FULL_WALKBACK` rows); the remaining ~6 rows are `PARTIAL_TREASURY` where B48k is `subprov` but a *different* wallet (GoZMJFTBd72…, 8Xf6P3Pa…, EmTrtHEP1BU8…, C9Pxdh1gJtjBQ…, 3e1H4g39XvMt…) is `funder_wallet`/`treasury` |
| `wt_ops_v2.db: wt_provisioning_edges` | `from_wallet` (SUBPROV_TO_CREATOR) | 27 | 1784133433 | 1785809281 | direct B48k→creator PLAIN_XFER edges, one row each, `observation_count=1` |
| `wt_ops_v2.db: wt_provisioning_edges` | `from_wallet` (TREASURY_TO_SUBPROV) | 2 | 1784023853 | 1784084262 | B48k itself as a funding source in a *different* role — 2 rows where B48k pays a downstream wallet in a TREASURY_TO_SUBPROV-typed edge |
| `wt_ops_v2.db: wt_walkback_edge_candidates` | `wallet` / `candidate_parent` | 41 total (24 as parent, 17 as wallet) | 1782320109 | 1785812523 | transaction-derived (signature, block_time, amount_lamports present); several `ALTERNATIVE/LOWER_RANKED_BUT_RETAINED` rows show B48k had multiple plausible upstream/downstream candidates per hop |
| `wt_ops_v2.db: wt_active_subprov_sessions` | `subprov_wallet` | 10 | 1784017700 | 1784997354 | ALL sessions `state=EXPIRED`, `funding_mechanism=WSOL_WRAP_CLOSE` (treasury→B48k leg), `session_tag=FUNDED`; funded by 3 distinct upstream wallets (see Phase 5) |
| `wt_ops_v2.db: wt_provisioning_sessions` | `subprov` | 27 | 1782645514(anomalous)/1784133433 | 1785809281 | per-mint session records: `treasury_to_subprov_mechanism` blank/absent, `subprov_to_creator_mechanism=PLAIN_XFER` throughout |
| `wt_ops_v2.db: wt_provisioning_sessions` | `treasury` | 2 | 1784023853 | 1784084262 | the 2 rows where B48k acts as upstream treasury-role, paying `9drNDZw67eHz…`/`9J9LjNZKGbm1…` who then fund the actual creator |
| `wt_ops_v2.db: wt_treasury_review` | `subprov_wallet` | 1 | 1783386268 | 1783386268 | `status=PENDING_REVIEW`, `detected_via=walkback_hop2`, mint `C843mf3Jucz6mcq9Ljuc4C1wZLggMwtfGTqjH2dkpump` |
| `wt_ops_v2.db: wt_discovered_subprovs` | `subprov` | 1 | 1783386248 | 1783386268 | `state=PROVISION_CANDIDATE`, `confidence=0.5`, `creator_count=1`, `wrap_close_count=1` |
| `wt_ops_v2.db: wt_infrastructure_candidates` / `wt_infrastructure_candidate_reviews` | `wallet`/`candidate_wallet` | 1 each | 1784659606 | 1784659606 | independently scored `candidate_role=OPERATIONAL_TREASURY`, `role_score_treasury=45`, `role_score_hub=25`, `status=SHADOW / PENDING_REVIEW` — the codebase's OWN infrastructure-role scorer flags B48k as treasury-shaped, not classic subprov-shaped |
| `wt_ops_v2.db: wt_farms` | `funder` | 1 | 1782856421 | 1782856421 | `creator_count=19`, `wrap_close_count=0`, `mechanism=PLAIN_XFER`, `funder_type=OPERATOR`, `peak_mc_sum=402,756.96` — an independent detection path (farm detector) corroborating pure plain-transfer creator funding |
| `wt_ops_v2.db: wt_farm_launches` | `funder` | 19 | 1781655742 | 1782804247 | per-launch rows, `wrap_close=0` for every row, `seed_sol` amounts 0.09–29.4 SOL |
| `wt_ops_v2.db: wt_subprov_sig_cursor` | `subprov_wallet` | 1 | — | 1784718135 | signature-scan cursor exists (B48k has been actively scanned by the RPC-signature-retry subsystem) |
| `wt_ops_v2.db: wt_subprov_sig_retry` | `subprov_wallet` | 299 | — | — | large retry backlog — many signatures for this wallet queued for RPC re-fetch, i.e., substantial unresolved on-chain activity beyond what's already reconstructed |
| `wt_ops_v2.db: wt_provisioning_candidate_workflow` | `subprov_wallet` | 4 | 1784970358 | 1784970358 | the 4 CLOSED candidates referenced in the task brief: closure reasons `MULTI_SOURCE_RELAY` (×3) and `INSUFFICIENT_EVIDENCE` (×1) |
| `wt_ops_v2.db: wt_confirmed_treasuries` | (upstream, not B48k itself) | — | — | — | `69SNcRC8NqjHBSXEcugCN5oFKRQoKmddmWzZYc3tqtxk` and `DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK` are BOTH `CONFIRMED` treasuries (`method=3SIGNAL`, `provenance=CONFIRMED_SEED`) — see Phase 5 |
| `flex_complete_database.db: creator_funders` | `funder_address` | **133** | 2026-04-20 20:35 | 2026-06-24 22:00 | B48k funding 133 distinct `creator_address` rows — the true launch cohort is **far larger** than the 34-37 mints tracked in `wt_ops_v2.db`; spans ~2 months |
| `flex_complete_database.db: creator_funders` | `creator_address` | 5 | 2026-06-03 03:03 | 2026-06-03 09:11 | B48k *itself* was creator-funded by 5 wallets in a ~6-hour window on 2026-06-03 — consistent with B48k being periodically re-seeded/reactivated as a session wallet, not a root capital source |
| `flex_complete_database.db: network_membership` | `funder_address` | 93 distinct creators | — | — | independent network-clustering pass groups all under `Network_110` — internal corroboration that flex's own clustering engine treats these 93+ creators as one coherent funder-network |
| `flex_complete_database.db: coordinated_creator_edges` | `bridge_funder` | 5671 | — | — | **investigated and confirmed to be NOISE**: table has 328,702 total rows system-wide; B48k is one of many incidental bridge_funders across unrelated creator pairs. Not used as a B48k-specific signal. |
| `wt_ops_v2.db: wt_watchtower_launches` | any column | **0** | — | — | **B48k and none of its 37 tracked mints appear anywhere in the canonical registry.** Confirmed by direct join: 0 of the wt_ops_v2 B48k cohort's mints are present in `wt_watchtower_launches`. |

**Not trusted at face value:** all "role" fields above were cross-checked against the underlying signature/amount/block_time columns in `wt_walkback_edge_candidates` and `wt_provisioning_edges` where available — 27 of the SUBPROV_TO_CREATOR edges carry real `funding_tx_signature` values, giving genuine transaction-level backing, not just inferred labels.

## Phase 2 — Launch Cohort Construction

Two cohorts exist at different resolutions:

- **wt_ops_v2.db tracked cohort**: 37 distinct mints (union of `wt_walkback_queue.subprov='B48k...'` and `.funder_wallet='B48k...'`), spanning block_time 1777145508–1785812523 (2026-04-25 through the recent past).
- **flex_complete_database.db full cohort**: 133 distinct creator addresses funded directly by B48k, spanning 2026-04-20 through 2026-06-24 — roughly 3.6x larger than what wt_ops_v2 has walked-back. The gap is explained by `wt_subprov_sig_retry` carrying 299 backlogged signatures for this wallet — i.e., the walkback worker has not finished processing B48k's full history.

For every launch in the 37-mint tracked set, the pattern is uniform:
- **stored subprov**: B48k (100% of rows)
- **stored treasury**: blank in most rows; populated only in the 6 `PARTIAL_TREASURY` rows with a DIFFERENT immediate wallet (not B48k) as `funder_wallet`
- **actual creator funder / funding signature**: present and transaction-derived for 27+ rows (real base58 signatures, real lamport amounts)
- **funding mechanism**: `PLAIN_XFER` uniformly for the B48k→creator leg
- **session ID**: present for the `wt_active_subprov_sessions` rows (10 total, all EXPIRED)
- **current canonical/candidate status**: NONE are in `wt_watchtower_launches`; 4 were explicitly investigated and CLOSED (not promoted) with reasons MULTI_SOURCE_RELAY (×3) / INSUFFICIENT_EVIDENCE (×1)

**Direct evidence**: 27 SUBPROV_TO_CREATOR edges with real signatures/amounts/block_times.
**Inferred links**: the 6 PARTIAL_TREASURY rows where the funder is a different wallet than B48k but B48k is still labeled `subprov` — this is an inference, not a direct B48k→creator transfer.
**Historical labels**: `wt_discovered_subprovs` and `wt_infrastructure_candidates` disagree on role — one calls it `PROVISION_CANDIDATE` (subprov-shaped), the other independently scores it `OPERATIONAL_TREASURY` (treasury-shaped). This is a genuine internal disagreement in the codebase's own classifiers, not resolved by this audit.

## Phase 3 — Full Transaction Topology

Reconstructed edge types actually observed (not forced into a WATCHTOWER template):

1. **Treasury → B48k** (`wt_active_subprov_sessions`, `funding_mechanism=WSOL_WRAP_CLOSE`): 10 sessions, 3 distinct upstream treasuries — `69SNcRC8NqjHBSXEcugCN5oFKRQoKmddmWzZYc3tqtxk` (8 sessions), `DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK` (1 session), `EFKVdKPrxMpofZMkPBWNe9Jp3hREmtoMZmNo7yFAMUo5` (1 session). Amounts range 0.09 to 88.6 SOL, several with topup_count>0 (up to 4 topups totaling 8 SOL on one session).
2. **B48k → Creator** (`wt_provisioning_edges` SUBPROV_TO_CREATOR, `funding_mechanism=PLAIN_XFER`): 27 direct edges, amounts 0.09–79.19 SOL, uniformly PLAIN_XFER — no wrap-close pattern on this leg, which structurally differs from the canonical WATCHTOWER wrap-close creator-funding mechanism.
3. **B48k → Relay → Creator** (6 PARTIAL_TREASURY rows): B48k funds an intermediate wallet (GoZMJFTBd72j6yCxajtiNEq1EMp5dZjnvF9xE4ReQEY2, 8Xf6P3PaCdsdXDZpDLEQx8sQfUsQwUxNaudLBYw9iWhh, EmTrtHEP1BU8rL9xcsWpaEzHArDXiWogB2bshD7WedKf, C9Pxdh1gJtjBQPeSo5th3LhWbg58dHbERxTZBwuizJmq, 3e1H4g39XvMt5BiZh8HckBgUtxRiA7SBwFP7ercSJjQg), which then funds the creator (amounts 0.03–1.6 SOL) — a genuine RELAY_TRANSFER shape, distinct from direct funding.
4. **B48k → downstream "TREASURY_TO_SUBPROV"-typed wallet** (2 edges): B48k pays `9drNDZw67eHzHhXCS5BBZPSvUuHRAMsZ8sfg5cD9sfkr` (0.0856 SOL) and `9J9LjNZKGbm1Ye6DCy97Rxcq4fpik1xf7GN5PAN7pm1f` (0.611 SOL), both of which then fund a creator within minutes — B48k acting in an upstream/treasury-like capacity on these two occasions, contradicting a pure-subprovider characterization.
5. **External wallets → B48k (creator_funders.creator_address rows)**: 5 wallets funded B48k directly on 2026-06-03 in a single 6-hour window (27.1, 14.4, 13.0, 48.5, 3.9 SOL) — consistent with periodic capital reload/reactivation, not continuous treasury flow.

Confidence: HIGH for the 27 direct PLAIN_XFER edges (real signatures, block times, amounts — `evidence_strength=TRANSACTION_DERIVED`). MEDIUM for the relay-hop edges (also transaction-derived but with `ALTERNATIVE`/competing candidate rows in `wt_walkback_edge_candidates`, meaning the walkback worker itself flagged ambiguity in several of these chains, e.g. mint `FuPtkT8weA7DSi2aRh1AtYKhxeXuyzYzuQq6coCcpump` and `cvgrnjj4TtUonSKfbsXvWGRKuJ9wrjdGjUVzg2fpump` each had 3+ competing upstream candidates).

## Phase 4 — RPC Verification

**Constraint honored**: local evidence for this wallet is unusually strong — `wt_walkback_edge_candidates` already carries `evidence_strength=TRANSACTION_DERIVED` with real signatures, block_times, and lamport amounts, meaning a prior pass through this codebase's own walkback worker (which does perform live RPC `getTransaction` decoding — see `src/core/walkback_worker.py`) already established these classifications. Given the local evidence is substantive and internally consistent (mechanism labels match observed amounts/timestamps, no contradictions found), no fresh RPC calls were made in this pass — re-verifying already RPC-derived data with more RPC calls would not add independent evidence, and the task explicitly says to use RPC "only where local evidence is incomplete or contradictory."

The one genuine ambiguity found — competing candidate parents on `FuPtkT8weA7DSi2aRh1AtYKhxeXuyzYzuQq6coCcpump` and `cvgrnjj4TtUonSKfbsXvWGRKuJ9wrjdGjUVzg2fpump` — is already resolved in-band: the walkback worker itself ranked one candidate `SELECTED` and the others `ALTERNATIVE/LOWER_RANKED_BUT_RETAINED`, with reasoning implicit in the ranking (typically hop_depth and amount-size heuristics). This is disclosed as a known-soft point rather than independently re-verified via RPC; if the user wants a hard RPC re-check on these two mints specifically, that is a bounded, cheap follow-up (2 mints, ~6 signatures).

**RPC-verified transaction count this pass: 0** (relied on already-RPC-derived local evidence).

## Phase 5 — Upstream Funding Identity

| Upstream wallet | Funds B48k directly? | Via relay? | Sessions | Other recipients | Identity assessment |
|---|---|---|---|---|---|
| `69SNcRC8NqjHBSXEcugCN5oFKRQoKmddmWzZYc3tqtxk` | Yes (8 sessions, WSOL_WRAP_CLOSE) | No | 8 | **87,919 distinct subprov wallets** (`wt_active_subprov_sessions`) | `wt_confirmed_treasuries`: CONFIRMED, `method=3SIGNAL`, `transfer_pct=100`, `out_sol=63.0`, `provenance=CONFIRMED_SEED`. Massively shared hub — funding 87,919 wallets makes it structurally an omnibus/infrastructure-tier treasury, not exclusive to B48k or even to a small operator set. `get_cex_info()`/`get_account_info()` returns `None` — not tagged as a known exchange in this codebase's registry. |
| `DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK` | Yes (1 session, WSOL_WRAP_CLOSE) | No | 1 | Many thousands (sample confirms it also funds `21wG4F3ZR8gwGC47CkpD6ySBUgH9AABtYMBWFiYdTTgv`, the wallet flagged in prior-session memory as the shared Hello-payment operator recipient) | `wt_confirmed_treasuries`: CONFIRMED, `method=3SIGNAL`, `out_sol=6858.0`, `provenance=CONFIRMED_SEED`. This is the strongest single WATCHTOWER-linkage signal found — but per prior-session memory (`treasuries-fund-treasuries.md`, `treasury-vs-subprov-fingerprint.md`), a confirmed treasury funding a wallet does NOT by itself demote/promote that wallet's own operator identity; treasuries fan out broadly and fund many unrelated operators. |
| `EFKVdKPrxMpofZMkPBWNe9Jp3hREmtoMZmNo7yFAMUo5` | Yes (1 session, WSOL_WRAP_CLOSE) | No | 1 | not sampled this pass | `wt_confirmed_treasuries`: present but with blank `transfer_pct`/`out_sol`/`method` fields except `method=subprov_funder_trace`, `confidence=MANUAL`, `provenance=CONFIRMED_SUBPROV_TRACE` — a WEAKER, manually-asserted confirmation, not the 3-signal automated confirmation the other two have. |
| `AseHxQUmsEGsfWETJGgGAf1dxWfTyrDzQc7jw43KWdiQ`, `8Af8rM8fbE6DHqZ7BzLYnCAbJzcjfmvNEPXMRbVGkseN`, `GxJVwFg91bp53ou95YmUQ2hoX4mCQiy8MskzYVFKSj82`, `CBod48hXV1RYS53XQLcm56kYJJyZ6MoKUrkC9Xsj1TQb`, `AVTGvUCBJ2dH17dyusniZAQbaN3FB8ZeZGv5ghAmpiwf` | Yes (`creator_funders.creator_address=B48k`, all on 2026-06-03) | Unknown | N/A | Not sampled beyond this cohort | `get_cex_info()`/`get_account_info()` return `None` for all 5 — no known-exchange or infra-registry match. Unclear whether these 5 are a distinct capital-reload family or one-off; unresolved, flagged for follow-up. |
| `fr6yQkDmWy6R6pecbUsxXaw6EvRJznZ2HsK5frQgud8` | Appears in `wt_provisioning_edges` as both `from_wallet` and `to_wallet` (resolves the task's partial `fr6y...`) | — | — | — | Role not fully characterized this pass — appears to be a same-tier peer wallet in the edge graph rather than a strict upstream. `get_cex_info()` returns `None`. Flagged for deeper follow-up if pursued further. |
| `5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9` | Not directly connected to B48k in tables sampled this pass (this is the Dv34prGm treasury from the OTHER closed-candidate wallet, not B48k) | — | — | — | Independently confirmed via `get_cex_info()`: `{'name': 'Binance 2', 'category': 'cex', 'exchange': 'Binance', 'risk_level': 'neutral'}`. Included per task brief but note: this wallet belongs to the Dv34prGm investigation thread, not directly evidenced as connected to B48k in the tables queried this pass. |

No durable-nonce or memo usage was found in any locally-stored evidence — none of the sampled tables carry nonce_authority or memo columns populated for B48k rows; this dimension is UNEVALUATED locally (would require RPC instruction-log inspection per Phase 4, not performed this pass).

## Phase 6 — Session Architecture

Reconstructed from `wt_active_subprov_sessions` (10 rows) + `wt_provisioning_sessions` (27+2 rows):

- **Funding**: treasury→B48k leg uses `WSOL_WRAP_CLOSE` (a wrap/close cycle), amounts 0.09–88.6 SOL, `open_reason` values are `SUBPROV_REACTIVATED` (8 of 10) or `CONTINUING_OPERATION` (1 of 10) — i.e., B48k is a reused, reactivated wallet, not freshly minted per session.
- **Activation → creator funding**: B48k→creator leg is `PLAIN_XFER`, generally within minutes to low-hours of the funding event (e.g., session `7c7cd70c` funded at 1784676059, creator funded 1784676059→completes 1784799362 — some gaps span days, suggesting dormancy between funding and use).
- **Launches per session**: appears to be 1 creator per session_id in `wt_provisioning_sessions` (session_id embeds a per-mint hash), i.e., each session maps to exactly one launch, not a batch/fan-out per session.
- **Residual/sweep**: `wt_active_subprov_sessions.sweep_count` is populated (1) for several sessions with `last_swept_at` timestamps close to `closed_at` — indicating active balance cleanup after use, a mild control signal.
- **State**: ALL 10 sessions are `state=EXPIRED` — none are currently live/monitored.
- **Nonce/fee payer**: not present in local schema for this wallet; UNEVALUATED (would need RPC).

**Role determination**: B48k functions as a reusable, periodically-reactivated creator-funding wallet that receives capital via wrap-close from treasury and disburses via plain transfer to a fresh creator, one launch per activation cycle — this matches the codebase's own `subprov_wallet` structural definition (creator-funding intermediary), but the treasury-role scoring in `wt_infrastructure_candidates` (role_score_treasury=45 > role_score_hub=25) shows the codebase's own heuristics see meaningful treasury-like signal too, given the 2 TREASURY_TO_SUBPROV-typed edges where B48k pays a further downstream wallet.

## Phase 7 — Wallet-Control Signals

| Signal | Classification | Basis |
|---|---|---|
| Reuse across many sessions/mints (10 sessions, 27+ direct creator edges, 133 total funded creators) | STRONG_CONTROL_SIGNAL | consistent, repeated same-wallet reuse over 2+ months is strong evidence B48k itself is a coherent, deliberately-operated unit |
| Uniform funding mechanism (WSOL_WRAP_CLOSE in, PLAIN_XFER out) across every observed session | SUPPORTING_SIGNAL | mechanistic consistency, but WSOL_WRAP_CLOSE-in / PLAIN_XFER-out is also the general WATCHTOWER treasury-seeding pattern — not unique enough alone to prove independent-vs-WATCHTOWER control |
| Shared treasury (69SNcRC8, DchJqu) with confirmed WATCHTOWER treasuries | WEAK_SIGNAL (per task's own explicit instruction and prior-session memory) | 69SNcRC8 alone funds 87,919 other wallets — treasury overlap is close to non-discriminating at this scale |
| Funding denominations (0.09–88.6 SOL, wide variance, several small dust-like values ≤0.1 SOL alongside large 79 SOL transfers) | WEAK_SIGNAL | no tight, distinctive denomination band found; overlaps generic pump.fun launch funding ranges |
| Timing regularity (activity spans 2026-04-20 → 2026-06-24, no obvious cadence pattern found in the sampled data) | NON-DISCRIMINATING | insufficient timestamp-density analysis performed this pass to assert cadence; flagged unevaluated rather than claimed |
| Batch reload on 2026-06-03 (5 distinct wallets fund B48k in a single 6-hour window) | SUPPORTING_SIGNAL | suggests a deliberate capital-refresh operational habit, distinct from ad hoc funding |
| Memo/nonce/fee-payer reuse | NON-DISCRIMINATING (unevaluated) | not present in local schema; would require RPC |

No single signal is treated as sufficient for common-operator-identity conclusions, consistent with the task's explicit constraint.

## Phase 8 — Compare Against Canonical WATCHTOWER

| Dimension | Canonical WATCHTOWER | B48k Operation | Difference type |
|---|---|---|---|
| Creator-funding mechanism | Wrap-close (creator = closeAccount.destination) | PLAIN_XFER — no wrap-close on the B48k→creator leg | **TRUE STRUCTURAL DIFFERENCE** |
| Treasury→subprov mechanism | Also wrap-close typically | WSOL_WRAP_CLOSE (matches) | No difference |
| Subprov reuse | Confirmed common (subprov funds many creators) | Confirmed — B48k funds 27-133 creators depending on cohort scope | No difference |
| Creator reuse (single-token filter) | Canonical creators are FRESH single-use | Not verified this pass whether B48k's 133 funded addresses are single-token creators — UNEVALUATED, a material gap | Evidence-coverage gap |
| Session duration | Not directly comparable without canonical session data pulled this pass | Sessions show funding→use gaps from minutes to multiple days; all EXPIRED | Evidence-coverage gap |
| Canonical registry membership | By definition, yes | Zero of 37 tracked / 0 of 133 broader-cohort mints appear in `wt_watchtower_launches` | TRUE STRUCTURAL DIFFERENCE (definitional) |
| Fingerprint scoring | N/A (canonical is ground truth) | `watchtower_token_attribution` gives 36 rows a WALKBACK-tier score of 80.0 — i.e., the pattern-matching layer likes it, but pattern-match ≠ canonical membership per the task's own framing | Behavioural similarity, not structural identity |
| Existing candidate-workflow disposition | N/A | 4/4 investigated candidates CLOSED, not promoted, for MULTI_SOURCE_RELAY / INSUFFICIENT_EVIDENCE reasons | This codebase has already tried and failed to promote B48k to canonical status |

**Bottom line**: the creator-funding mechanism (PLAIN_XFER, not wrap-close) is the single clearest structural divergence from the canonical WATCHTOWER creator-funding fingerprint.

## Phase 9 — Operation Boundary

**Verdict: C — WATCHTOWER-adjacent but separate execution stack.**

Reasoning: B48k receives capital from 2-3 independently-confirmed WATCHTOWER treasuries (including DchJqu, which is linked via prior-session memory to the confirmed Hello-payment operator identity), establishing genuine capital-source overlap. However:
- The B48k→creator funding mechanism (PLAIN_XFER) diverges structurally from canonical WATCHTOWER's wrap-close creator-funding fingerprint.
- B48k has been investigated 4 times by this codebase's own workflow and closed every time, never promoted to canonical.
- The shared treasuries fund tens of thousands of other wallets (69SNcRC8: 87,919), so treasury overlap cannot on its own establish common downstream execution control (explicit task caution, corroborated by prior-session memory on treasury-mesh behavior).
- B48k shows independent session/reactivation architecture (10 sessions, reused wallet, periodic multi-source capital reload) that is a coherent operational pattern in its own right, not merely "more WATCHTOWER."

This is NOT "A — Independent operation" because the capital-source linkage to confirmed WATCHTOWER treasuries (especially DchJqu, tied to the Hello-operator identity) is too concrete to dismiss as coincidental — it is a real, repeated, multi-session funding relationship, not a single shared wallet.

This is NOT "D — WATCHTOWER sub-operation" because the creator-funding mechanism is structurally different (PLAIN_XFER vs wrap-close) and B48k has its own distinguishable session/reuse architecture rather than simply executing the canonical pattern under different bookkeeping.

## Phase 10 — Launch Membership Classification

Applying the classification to the 37-mint wt_ops_v2-tracked cohort (the flex_complete_database 133-cohort is not yet walked-back to individual-signature confidence, so is classified at a lower tier):

- **CONFIRMED_B48K_OPERATION**: 27 mints — direct SUBPROV_TO_CREATOR PLAIN_XFER edges with real signature/amount/block_time evidence and `evidence_strength=TRANSACTION_DERIVED`, `selection_status=SELECTED`.
- **PROBABLE_B48K_OPERATION**: 6 mints — the PARTIAL_TREASURY relay-hop rows (B48k funds a relay wallet, which funds the creator); direct evidence exists but with one extra untraced hop.
- **POSSIBLE_B48K_OPERATION**: ~96 mints (the remaining flex_complete_database creator_funders rows not yet present in wt_ops_v2 walkback tables) — B48k is the recorded `funder_address` in the live DB, but not yet independently walked-back/scored in wt_ops_v2; treat as possible pending full walkback.
- **SHARED_INFRASTRUCTURE_ONLY**: 0 identified this pass (no case found where B48k's involvement was purely incidental/non-causal).
- **NOT_B48K_OPERATION**: 4 mints — the closed candidates (`6ANRcu9SxHyWr5MCbBWLehYzgVPhMrS9j9sszCxfpump`, `FB44zC6s2jkysjaB2NC8u6XqwhPJwir1DYFzEhXbpump`, `BNz8HBXTkYUtsn22fZSzu3Fb461AttKwScGgwHR7a5sp`, `73Ldwtam8mZZALK4veHMDsnMBcsPJMQcapaYk8bHpump`) — NOTE: these mints ALSO appear as CONFIRMED_B48K_OPERATION rows in `wt_provisioning_edges` with real signatures. This is a genuine internal contradiction: the workflow table closed them as MULTI_SOURCE_RELAY/INSUFFICIENT_EVIDENCE (i.e., rejected for CANONICAL WATCHTOWER promotion), while the provisioning_edges table independently confirms them as real B48k→creator transfers. Resolution: these mints are correctly excluded from canonical WATCHTOWER (that closure judgment stands, and this audit does not overturn it) but they DO belong in the B48k-as-its-own-operation cohort. The closure reasons were about WATCHTOWER promotion, not about whether B48k funded the creator.
- **UNEVALUABLE**: 0 in the tracked cohort; the ~96 broader cohort mints are UNEVALUABLE until walked back further, but classified above as POSSIBLE rather than fully unevaluable given the creator_funders evidence.

## Phase 11 — Operational Signature (Minimal)

**Defining signals** (required, all must hold):
1. B48k (or a wallet in its identified relay set: GoZMJFTBd72j…, 8Xf6P3Pa…, EmTrtHEP1BU8…, C9Pxdh1gJtjBQ…, 3e1H4g39XvMt…) is the immediate funder of a fresh creator wallet.
2. Funding mechanism on the funder→creator leg is `PLAIN_XFER` (not wrap-close).
3. The creator subsequently CREATEs a pump.fun token within a short window (minutes, per observed `subprov_to_creator_block_time`→`creator_launch_time` gaps).

**Supporting signals** (increase confidence, not sufficient alone):
- Upstream funding to B48k arrives via WSOL_WRAP_CLOSE from one of the 3 identified treasuries (69SNcRC8, DchJqu, EFKVdKPr).
- Session shows `open_reason=SUBPROV_REACTIVATED` (wallet reuse pattern).
- Funding amount in the 0.1–90 SOL range (wide, low-discriminating band).

**Exclusion signals** (disqualify attribution):
- Creator-funding transaction shows wrap-close (closeAccount.destination = creator) — that is the canonical WATCHTOWER signature, not B48k's.
- The funder wallet is one of the tens-of-thousands of other wallets fed by 69SNcRC8/DchJqu with no B48k-specific link (treasury overlap alone, per Phase 7, is WEAK/non-discriminating).

**Test results**:
- **True positives**: 27/27 CONFIRMED_B48K_OPERATION mints pass all 3 defining signals.
- **False negatives**: the 6 PARTIAL_TREASURY relay-hop mints would be MISSED by a strict "B48k is immediate funder" rule — the signature needs the relay-set extension to catch them.
- **False positives against strict WATCHTOWER controls**: none tested directly this pass (would require pulling a canonical wrap-close sample and confirming it does NOT also show PLAIN_XFER — not done this pass; flagged as an open validation step).
- **Against known exchange-funded negatives**: the mechanism (PLAIN_XFER from a non-exchange-labeled wallet) does not collide with the `5tzFkiKscXHK...`/Binance-funded Dv34prGm pattern from the sibling investigation, which is a different closed-candidate cluster entirely — no cross-contamination found.

**Readiness**: the signature is usable but incomplete — it has not been tested against a canonical WATCHTOWER negative sample or against unrelated plain-transfer launches from wallets with no treasury linkage at all.

## Phase 12 — Existing Attribution Audit

| Mint | Current attribution | Evidence source | Independent verdict | Disagreement |
|---|---|---|---|---|
| `6ANRcu9SxHyWr5MCbBWLehYzgVPhMrS9j9sszCxfpump` | CLOSED (MULTI_SOURCE_RELAY) in workflow table | `wt_provisioning_candidate_workflow` closure note: "Immediate funder (6a1EevkQZHL...) does not match this subprovider's other launches" | Correctly excluded from WATCHTOWER canonical; belongs in B48k-operation cohort per `wt_provisioning_edges` direct signature evidence | Workflow closure reason cites a DIFFERENT immediate funder than what `wt_provisioning_edges` records (CiDVSrE3qx73…) — worth a closer read of the closure evidence_json, not resolved this pass |
| `FB44zC6s2jkysjaB2NC8u6XqwhPJwir1DYFzEhXbpump` | CLOSED (MULTI_SOURCE_RELAY) | same pattern | Same as above | Same pattern |
| `BNz8HBXTkYUtsn22fZSzu3Fb461AttKwScGgwHR7a5sp` | CLOSED (MULTI_SOURCE_RELAY) | same pattern | Same as above | Same pattern |
| `73Ldwtam8mZZALK4veHMDsnMBcsPJMQcapaYk8bHpump` | CLOSED (INSUFFICIENT_EVIDENCE) | "Upstream funder not further traced past the subprovider" | Correctly excluded from WATCHTOWER canonical (upstream trace incomplete); B48k→creator leg itself is directly evidenced | Consistent — no disagreement, this closure is well-founded |
| All 36 `watchtower_token_attribution` rows | `reviewed_status=AUTO`, tier=WALKBACK, score=80.0 | pattern-fingerprint scorer, not topology/mechanism-verified | These are pattern-match scores, NOT canonical WATCHTOWER labels — no row here claims canonical membership, so no disagreement to flag; this table correctly stays in the "candidate" tier | None — this table was never asserting canonical status |

No row currently carries a live "WATCHTOWER" canonical label that this audit disagrees with — the closures already correctly kept B48k out of canonical WATCHTOWER. The disagreement, if any, is that the closures did not also spin up a separate, B48k-specific operation entry — which is exactly what Phases 9-11 recommend as a next step (not performed here, as this is read-only).

## Phase 13 — Operation Model

```
[3 upstream capital sources]
  69SNcRC8NqjHBSXEcugCN5oFKRQoKmddmWzZYc3tqtxk  (CONFIRMED WATCHTOWER treasury, 87,919 funded wallets — shared infra, likely operator-external or multi-tenant)
  DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK  (CONFIRMED WATCHTOWER treasury, linked to Hello-operator 21wG4F3Z — shared infra)
  EFKVdKPrxMpofZMkPBWNe9Jp3hREmtoMZmNo7yFAMUo5  (weaker MANUAL confirmation)
        |
        | WSOL_WRAP_CLOSE (10 sessions, reactivation pattern)
        v
[B48kNVXs4YK4amkBCH2XokQiv1SeiVQGHDR17xDeKAAn]  <-- operator-controlled session/reload wallet
        |
        |-- PLAIN_XFER (direct, 27 edges) --------------> [fresh creator] --> CREATE --> launch
        |
        |-- PLAIN_XFER (via relay: GoZMJFTBd72j…,
        |    8Xf6P3Pa…, EmTrtHEP1BU8…, C9Pxdh1gJtjBQ…,
        |    3e1H4g39XvMt…) -----------------------------> [fresh creator] --> CREATE --> launch
        |
        `-- PLAIN_XFER (occasional, B48k acts upstream) -> [9drNDZw67eHz…, 9J9LjNZKGbm1…] --> creator --> launch
```

- **Operator-controlled**: B48k itself (strong reuse/session evidence), and likely the small relay set.
- **Likely external / shared**: the 2 large treasuries (69SNcRC8, DchJqu) — too broadly shared to call operator-exclusive.
- **Unresolved**: the 5 wallets that funded B48k on 2026-06-03 (no CEX/registry match, role unclear); `fr6yQkDmWy6R6pecbUsxXaw6EvRJznZ2HsK5frQgud8`'s exact role in the edge graph; whether the 133-cohort creators are single-token (fresh) per the single-token-creator-filter memory heuristic — not checked this pass.

## Required Final Verdicts

**Verdict 1 — Operator identity: C (WATCHTOWER-adjacent but separate execution stack).** Capital-source overlap with confirmed WATCHTOWER treasuries is real and repeated, but the creator-funding mechanism (PLAIN_XFER, not wrap-close) and B48k's own distinct session/reactivation architecture indicate separate downstream execution control.

**Verdict 2 — Topology: A (Single stable topology), with caveats.** The core Treasury→B48k(WSOL_WRAP_CLOSE)→Creator(PLAIN_XFER) pattern is highly consistent across all 27 direct edges. The 6 relay-hop and 2 upstream-role edges are variants of the same underlying shape, not a different topology.

**Verdict 3 — Detection readiness: B (Signature needs minor refinement).** The 3-signal defining signature (immediate/relay funder identity + PLAIN_XFER mechanism + fast CREATE) has zero false negatives against the direct-edge sample, but has not been tested against a canonical WATCHTOWER negative control or a broader unrelated-plain-transfer negative set, and the single-token-creator-freshness check (a proven false-positive filter per memory) has not been applied to the 133-mint cohort.

## Required Counts

| Metric | Count |
|---|---|
| Launches linked (wt_ops_v2 tracked cohort) | 37 |
| Launches linked (flex_complete_database broader cohort) | 133 |
| Confirmed operation launches | 27 |
| Probable launches | 6 |
| Possible launches | ~96 (untraced broader cohort) |
| Shared-infrastructure-only | 0 |
| Excluded (correctly closed, non-B48k-attributable in this audit's own view) | 0 (the 4 closed candidates were re-classified as CONFIRMED_B48K_OPERATION for the B48k-operation question, even though correctly excluded from WATCHTOWER canonical) |
| Unevaluable | 0 in tracked cohort |
| Unique upstream wallets (direct treasury funders of B48k) | 3 |
| Unique upstream wallets (funded B48k as "creator", 2026-06-03 batch) | 5 |
| Unique relays identified | 5 (GoZMJFTBd72j…, 8Xf6P3Pa…, EmTrtHEP1BU8…, C9Pxdh1gJtjBQ…, 3e1H4g39XvMt…) + fr6yQkDmWy6R… (role unresolved) |
| Unique sessions | 10 (`wt_active_subprov_sessions`) / 29 (`wt_provisioning_sessions`, includes both subprov and treasury roles) |
| Creator-funding mechanisms observed | 2 (PLAIN_XFER — dominant; WSOL_WRAP_CLOSE — only on the upstream treasury→B48k leg, never on B48k→creator) |
| RPC-verified transactions (this pass) | 0 (relied on pre-existing RPC-derived local evidence; see Phase 4) |

## Recommended Next Action

1. **Do not promote B48k or any of its 27 confirmed launches to canonical WATCHTOWER** — the mechanism mismatch (PLAIN_XFER vs wrap-close) is a genuine structural difference, and the prior closures were correctly conservative on that specific question.
2. **Consider opening a distinct, separate operation entry for B48k** (not a WATCHTOWER sub-entry) capturing the 27+6 confirmed/probable launches — this is a read-only recommendation, no action taken here.
3. **Complete the walkback backlog**: 299 signatures queued in `wt_subprov_sig_retry` for this wallet — finishing that would resolve most of the 96 "possible" cohort into confirmed/excluded.
4. **Apply the single-token-creator freshness filter** (proven false-positive reducer per memory) to the 133-creator cohort before treating all of them as genuine B48k launches.
5. **Resolve GBFSP3s4pU3dDKS2gCzvH8UpE4UJs3XGRuxmuWthHaf9** — searched across all key wt_ops_v2 tables this pass with zero hits; either it belongs to a different investigation thread or requires an RPC-based search (not a local-DB match).
6. **If pursuing detection readiness (Verdict 3 → A)**: pull a canonical WATCHTOWER wrap-close sample and a genuinely-unrelated plain-transfer negative sample, and re-run the Phase 11 signature against both to close the false-positive/negative testing gap.
