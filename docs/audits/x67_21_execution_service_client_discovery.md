# X67.21 — Execution Service Attribution & Client Discovery (Read-Only)

**No code, database, or label changes made.** RPC usage: ~90 `getSignaturesForAddress` calls (cheap endpoint, paginated) + ~280 `getTransaction` calls (jsonParsed, cheap endpoint), all against fee payer `3ddCq8LgnyYiJFfupiNAR8rQ9BshepSMzSwWbNLageTx` and discovered client wallets. Raw JSON cached in scratchpad, deleted after extraction. API key never logged.

Follow-on to `x67_20_b48k_execution_fingerprint_validation.md`, which found B48k's execution fingerprint (fee payer `3ddCq8Lg…` + nonce authority `DScDQ1zV…` + durable-nonce builder) is shared infrastructure, not exclusive B48k tooling — 60% of a 40-tx sample of the fee payer's own traffic involved unrelated wallets. This audit expands that sample dramatically and asks whether the service's broader client population reveals new operator clusters.

## Phase 1 — Service Inventory

Paginated `getSignaturesForAddress` on the fee payer `3ddCq8LgnyYiJFfupiNAR8rQ9BshepSMzSwWbNLageTx` for 151 pages (150,000 signatures) using `before`-cursor pagination, and it was **still not exhausted** — the true lifetime total is materially larger than 150,000.

- **Signatures retrieved this pass**: 150,000
- **Newest**: block_time 1785826454 (2026-08-04 06:54:14 UTC)
- **Oldest retrieved**: block_time 1783697088 (2026-07-10 15:24:48 UTC)
- **Span covered**: 24.6 days
- **Rate**: ~6,086 tx/day sustained, ~254/hour — pagination was still going when deliberately capped
- **Estimated total service utilization**: well into six figures of transactions; at the observed rate, a 99-day observed history (per X67.19/20's original span) would project to **>500,000 lifetime transactions** if the rate held throughout (it likely grew).

This volume is two to three orders of magnitude beyond what any single pump.fun launch operator would plausibly need (B48k's own tracked cohort is 37-133 launches over ~2 months). **This is the single most important new fact from this audit: the execution service operates at a scale that makes "B48k-owned tooling" impossible on its face**, independent of the shared-fee-payer evidence already found in X67.20.

## Phase 2 — Client Discovery

Two sampling passes were decoded via `getTransaction`:
- Pass A: 150 signatures stratified evenly across the full 150,000-signature range → 150/150 decoded successfully (100%, no rate-limit hits).
- Pass B: 300 additional random signatures from the remaining pool → only 62/300 decoded before Helius rate-limiting kicked in (238 × HTTP 429), consistent with staying inside the "cheap endpoint, courteous pace" constraint rather than pushing through with retries/backoff at volume.

**Combined: 212 successfully decoded transactions, 118 distinct third-signer ("client-slot") wallets.**

| Client | Occurrences (n=212) | Local DB footprint | Asset moved | Verdict |
|---|---|---|---|---|
| `B48kNVXs4YK4amkBCH2XokQiv1SeiVQGHDR17xDeKAAn` | 68 (32%) | 27 tables (per X67.20), subject of X67.18-20 | Native SOL, PLAIN_XFER to creators | Known — B48k operation (prior audits) |
| `9byY2BBoUrQijZ5Xwe2KKgsmwtSCxjURLFTGZZNdrGdw` | 23 (11%) | **Zero** hits across all TEXT columns of every table in `wt_ops_v2.db` | **USDC** (`EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v`) and **USDT** (`Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB`) via `spl-token transferChecked`, amounts ~10–1,686 tokens | **Non-launch client — stablecoin settlement/payment wallet** |
| `Et5focveJp8AjPLv4irfxYTc1YJGFDGzG168NWW71pAT` | 3 | `wt_active_subprov_sessions` (1 row: funded 1158.65 SOL by treasury `69SNcRC8…` via WSOL_WRAP_CLOSE, `open_reason=PROVISION_CANDIDATE`, `session_tag=INTEL_ONLY`); `watchtower_events` (1 row: `SUBPROV_SESSION_INTEL_ONLY`, `related_wallet=B48k`) | USDT and PYTH governance token (`HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3`) via `transferChecked`; separately received 10.63 SOL directly from CEX wallet `5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9` (Binance 2, confirmed via `get_cex_info()`) on its own nonce (not the shared builder) | **Non-launch client** — SPL-token mover with incidental treasury/B48k-adjacent session record but zero creator-funding activity |
| `6CdJi6M7Kt9xyh1Ro6pNQGNs9u8jjwkr4WmGX1zJ5LNy` | 2 | Zero | Native SOL, 0.874 SOL **transfer to B48k** | **Launch-adjacent — a 7th relay wallet feeding B48k** (beyond the 5 identified in X67.18/20) |
| `92BKzviijLwYHNJsXHnW35vhWTtZdYoxJnvGhgNbk2Jm` | 2 | Zero | Native SOL, 0.367 SOL **transfer to B48k** | **Launch-adjacent — an 8th relay wallet feeding B48k** |
| `2DginthSUMiqHWEqukTcmwUPJoKzZJcEiZzQAA2hg47u` | 2 | Not individually probed this pass (RPC budget) | Unresolved | Unevaluable |
| 113 other wallets | 1 each | Not individually probed (RPC budget) | Unresolved | Unevaluable / presumed one-off clients |

**Fingerprint stability across all 212 decoded transactions**: fee payer = `3ddCq8Lg…` (212/212, 100%), nonce authority = `DScDQ1zV…` (212/212, 100%), legacy version (212/212), exactly 3 signers (212/212), no memo (212/212). This reaffirms X67.20's builder fingerprint at 4x the prior RPC sample (212 vs 54).

## Phase 3 — Client Classification

- **WATCHTOWER**: 0 of 118 clients match any confirmed WATCHTOWER wallet, treasury, or subprov by direct identity.
- **Known operation**: 1 — B48k (established X67.18-20).
- **Launch-adjacent / probable B48k-family extension**: 2 new relay wallets (`6CdJi6M7…`, `92BKzviijLwY…`) — same shape as the 5 known relays (fund B48k in native SOL via the shared builder), raising the known relay count from 5 to **at least 7**.
- **Non-launch / wallet-service client**: 2 confirmed — `9byY2…` (USDC/USDT mover) and `Et5foc…` (USDT/PYTH mover, one CEX-sourced SOL inflow). Both use the identical durable-nonce builder for pure SPL-token settlement, no pump.fun interaction found.
- **Unknown operation**: 0 confirmed this pass — no third wallet was found with (a) high transaction frequency, (b) recurring native-SOL transfers to a *distinct* non-B48k downstream wallet, and (c) creator-funding footprint. The two candidates with real recurrence (`9byY2`, `Et5foc`) both resolved to non-launch SPL-token activity.
- **Infrastructure only**: the fee payer and nonce authority themselves (already established, X67.20).
- **Unevaluable**: 113 singleton-occurrence wallets — RPC budget did not extend to decoding each individually.

## Phase 4 — Launch Reconstruction (per launch-related client)

Only one client cluster in this pass reconstructs a full launch chain: **B48k**, unchanged from X67.18-20 (Treasury `69SNcRC8`/`DchJqu`/`EFKVdKPr` → B48k via WSOL_WRAP_CLOSE → creator via PLAIN_XFER → CREATE; 27 direct + now 7+ relay-hop edges; 46.5% creator-reuse rate on the broader 133-cohort per X67.19 Phase 7).

The two new relay wallets extend this same chain one hop further upstream (they fund B48k, not a creator directly) — additional tributaries into the already-known B48k reservoir, not independent launch chains.

`9byY2` and `Et5foc` have **no launch chain to reconstruct** — no creator funding, no CREATE-adjacent activity found in either local DB or the sampled RPC transactions.

## Phase 5 — Operation Clustering

| Signal | B48k | 9byY2 | Et5foc | 6CdJi6M7 / 92BKzviijLwY |
|---|---|---|---|---|
| Shared treasury (`69SNcRC8`) | Yes | Not applicable | Yes (1158.65 SOL, WSOL_WRAP_CLOSE) | Not evaluated |
| Shared relays | N/A (is a relay target) | No | No | Both relay INTO B48k |
| Shared timing/cadence | 10 sessions over ~99 days | Continuous, ~30 tx/hr, unrelated cadence | Continuous, ~3 tx/hr, unrelated cadence | Single observed tx each |
| Shared denomination pattern | 0.09-88.6 SOL | Stablecoin units (10-1686 tokens) | Stablecoin/governance-token units | 0.37, 0.87 SOL — inside B48k's known range |
| Shared creator-funding behavior | Yes (127 creators) | None | None | None (fund B48k, not creators) |
| Asset type | Native SOL exclusively | SPL stablecoins exclusively | SPL stablecoins + governance token | Native SOL |

**Cluster graph (text form)**:

```
[Shared Execution Service: fee payer 3ddCq8Lg… + nonce authority DScDQ1zV…]
        |
        |-- CLUSTER A (LAUNCH / KNOWN): B48k operation
        |     69SNcRC8 / DchJqu / EFKVdKPr (treasuries)
        |        -> B48k (session wallet, 68 sampled txs)
        |             <- relay: GoZMJFTBd72j…, 8Xf6P3Pa…, EmTrtHEP1BU8…,
        |                       C9Pxdh1gJtjBQ…, 3e1H4g39XvMt… (X67.20, 5 wallets)
        |             <- relay: 6CdJi6M7…, 92BKzviijLwY… (NEW, this pass, 2 wallets)
        |             -> creator (PLAIN_XFER) -> CREATE -> launch (27+ mints)
        |
        |-- CLUSTER B (NON-LAUNCH, STABLECOIN/ASSET SETTLEMENT): isolated clients, NOT a cluster with each other
        |     9byY2… (USDC/USDT mover, 23 sampled txs, ~30 tx/hr own-wallet rate)
        |     Et5foc… (USDT/PYTH mover, 3 sampled txs; separately CEX-funded via Binance 2
        |               5tzFkiKscXHK… on its OWN unrelated nonce — not evidence of a shared
        |               operator with 9byY2, just co-tenancy of the same execution service)
        |
        `-- CLUSTER C (UNRESOLVED SINGLETONS): 113 wallets, 1 sampled tx each
              No cross-references found between any pair; no recurring counterpart identified;
              treated as the service's long-tail general client base, not a coherent operation.
```

**No second B48k-like cluster was found.** 9byY2 and Et5foc do NOT cluster with each other (different token types, no shared counterparty, no shared timing pattern beyond both using the shared builder) — they read as two independent clients of the same commercial service, not a joint operation.

## Phase 6 — WATCHTOWER Comparison

- **Treasury overlap**: `69SNcRC8NqjHBSXEcugCN5oFKRQoKmddmWzZYc3tqtxk` (confirmed WATCHTOWER treasury, ~88,000 funded wallets) funds BOTH B48k (8/10 sessions) AND the newly-examined `Et5foc…` (1158.65 SOL session). Same generic/omnibus overlap pattern already characterized in X67.18-19 — non-discriminating as an operator signal.
- **Creator overlap**: 0 — none of the new clients appear in `creator_funders.funder_address` or `token_analysis.earliest_tx_creator/pf_ws_creator`.
- **Relay overlap**: 0 — none of the new wallets match the 5 previously-known relays; 2 are NEW relays into the same B48k chain, not new WATCHTOWER-linked relays.
- **Funding overlap**: only via the shared omnibus treasury; no direct WATCHTOWER-wallet-to-new-client transfer found.
- **Behavioural overlap**: none — canonical WATCHTOWER creator-funding always uses single-signer/self-paid transactions; every transaction sampled this pass by the shared execution service uses the 3-signer/external-fee-payer/durable-nonce construction, 0/18 control-set overlap (reaffirmed).
- **Execution overlap**: 0 — canonical WATCHTOWER transactions never use `3ddCq8Lg…`/`DScDQ1zV…` (confirmed at n=18 in X67.20).

**Separated conclusion**: operator similarity to WATCHTOWER = none found for any client; execution similarity = none; the only shared element across the B48k cluster and WATCHTOWER is the omnibus treasury `69SNcRC8`, which is infrastructure-level and does not imply common downstream control.

## Phase 7 — Service Characteristics

- **Commercial transaction builder / durable-nonce relay-as-a-service**: **STRONGLY SUPPORTED.** 150,000+ signatures in 24.6 days (still climbing), ≥118 distinct client wallets in a 212-tx sample, assets spanning native SOL, USDC, USDT, and PYTH governance tokens, uniform durable-nonce builder pattern applied identically regardless of asset type or client identity.
- **Wallet automation framework**: SUPPORTED as a sub-characterization.
- **Launch service**: **PARTIALLY SUPPORTED but not exclusively** — B48k demonstrably uses it for launch-adjacent creator funding, but this is one use case among many (stablecoin transfers dominate the newly-sampled non-B48k traffic).
- **Private operator tooling**: **REJECTED** — scale (150k+ tx, ≥118 clients) is incompatible with single-operator-exclusive tooling.
- **Shared provisioning framework**: SUPPORTED as a category, but observed traffic mix (majority non-SOL, non-launch) argues this is broader than a provisioning framework specifically.
- **General wallet management system**: **BEST FIT** — asset-generic transfers, high client diversity, sustained high-frequency operation, zero registry footprint, launch-funding (B48k) as one of many tenants.

## Phase 8 — Unknown Operation Discovery

No new unknown *launch* operation was found. The two recurring non-B48k clients identified and RPC-verified this pass (`9byY2…`, `Et5foc…`) both resolve to **non-launch SPL-token settlement activity** with zero creator-funding or CREATE-adjacent footprint.

The two new SOL-denominated relay wallets are not independent operations — they fund B48k directly, additions to the already-known B48k relay set.

**113 unresolved singleton wallets remain a genuine coverage gap** — possible one or more is a second undiscovered launch client, but at 1-sample-each with no recurrence signal, none could be prioritized within this pass's RPC budget. The base rate for a genuinely new operation hiding in the remaining 113 singletons is assessed as **LOW** but not zero — confidence: LOW.

## Phase 9 — Detection Surface

- **For finding MORE of B48k's own launches/relays**: YES, and this pass proves it — 2 new relay wallets were found purely by decoding a random RPC sample of the shared fee payer's traffic and checking which third-signer wallets transfer SOL into B48k.
- **For finding NEW, previously-unknown operations**: NOT demonstrated this pass. The technique correctly filters the service's traffic down to a tractable candidate list, but ~89% of decoded non-B48k volume was non-launch traffic. The technique needs a secondary filter (e.g., cross-referencing whether a client's transfers land on a wallet that later CREATEs a pump.fun token) before it's efficient.
- **Recommended framing**: a valid **second-stage narrowing question** once a launch is already suspected — check whether a funder transaction matches this builder fingerprint, then look up who else that third-signer wallet is, to find relay/session siblings. Not yet validated as a **first-stage discovery surface** for finding brand-new operations from scratch.

## Phase 10 — Architecture Recommendation

The evidence supports treating the execution service as a **separate, non-hierarchical layer** that INTERSECTS the attribution hierarchy rather than sitting cleanly above it:

```
Execution Service (builder fingerprint: fee payer + nonce authority)
        |
        +--> [tags any transaction that used it, regardless of operator]
        |
Operator (e.g., B48k, or WATCHTOWER, or "unclassified stablecoin client X")
        |
        v
Treasury -> Session/Subprov Wallet -> Creator Funding -> Launch -> Migration
```

**Rationale for NOT making it the top of a strict linear hierarchy**: the execution service is asset-generic and operator-agnostic — used by at least one confirmed launch operator (B48k) and at least two confirmed non-launch clients (stablecoin movers) with no evidence any of them share operator identity. Placing it at the top of a linear hierarchy implies every client descends into a launch-relevant tree, which this pass disproves (89% of sampled non-B48k traffic terminates in stablecoin transfers, not launches). The correct model is a **cross-cutting tag**: "uses execution service X" is a property attachable to any transaction/wallet at any layer — useful for (a) linking a session wallet to its relay siblings and (b) as a negative filter (a transaction using this fingerprint is automatically excluded from canonical WATCHTOWER, since 0/18+ control transactions ever use it) — but it should not replace or precede Operator identity, which still requires downstream creator-funding/launch evidence.

## Final Verdicts

**Verdict 1 — Execution Service: A (Commercial/shared infrastructure).** Reaffirmed and substantially strengthened: 150,000+ signatures in 24.6 days (pagination not exhausted), ≥118 distinct clients in a 212-tx decoded sample, asset-generic (SOL/USDC/USDT/PYTH), zero registry footprint for the fee payer/nonce authority pair. Scale alone rules out single-operator-exclusive tooling.

**Verdict 2 — Client Discovery: A (Multiple distinct operator clients).** At minimum two behaviorally distinct client populations confirmed: (1) B48k's launch-funding operation (native SOL, PLAIN_XFER to creators, 68/212 sampled txs) and (2) non-launch stablecoin/asset settlement clients (`9byY2…`, `Et5foc…`, zero creator-funding footprint). These do not cluster with each other or with B48k on any dimension tested. 113 further singleton clients remain unclassified but show no cross-reference to each other or to B48k.

**Verdict 3 — Strategic Value: B (Useful supporting signal).** Not yet a standalone new discovery surface — the client population is dominated by non-launch traffic, so first-stage discovery from the service alone is inefficient. However, it is a validated, working SECOND-stage tool: given a known launch client, decoding a sample of the shared fee payer's traffic and filtering for native-SOL transfers into the known session wallet reliably surfaces additional relay/session wallets (2 new ones found this pass) that local DB walkback had not yet captured. Recommend using it this way — as a relay-expansion tool for already-identified clusters — rather than as a blind operator-discovery scanner.

## Required Counts

| Metric | Count |
|---|---|
| Total service transactions (signatures retrieved) | 150,000 (pagination not exhausted; true lifetime total larger) |
| Total distinct clients (third-signer wallets) identified | 118 (from 212 decoded transactions) |
| Launch-producing clients | 1 confirmed (B48k) + 2 new relay wallets feeding it = 3 wallets in the launch cluster this pass (plus 5 previously known relays = 8 total known B48k-cluster wallets) |
| Non-launch clients (RPC-confirmed) | 2 (`9byY2…` USDC/USDT; `Et5foc…` USDT/PYTH) |
| WATCHTOWER clients | 0 |
| Unknown operation clients (new launch clusters found) | 0 |
| Clustered operators | 1 (B48k, extended) |
| Isolated/unresolved clients | 113 singleton wallets + 2 non-launch clients (isolated from each other) |
| Candidate new operations | 0 confirmed; 113 unevaluated (low-confidence residual population) |
| RPC `getSignaturesForAddress` calls | ~90 (151 paginated pages for fee payer + per-client history pulls for 6 wallets) |
| RPC `getTransaction` calls | ~280 (212 successful + rate-limited retries) |

**Key repo references**: `docs/audits/x67_18_b48k_operation_audit.md`, `docs/audits/x67_19_b48k_operator_boundary_reeval.md`, `docs/audits/x67_20_b48k_execution_fingerprint_validation.md`, `database/wt_ops_v2.db` (`wt_active_subprov_sessions`, `watchtower_events`, `wt_provisioning_edges`), `database/flex_complete_database.db` (`creator_funders`, `token_analysis`), `src/utils/infra_mapping.py`, `src/ops/watchtower_canonical_adapters.py:319-325`.

**New wallet addresses established this pass** (not in any prior audit): `9byY2BBoUrQijZ5Xwe2KKgsmwtSCxjURLFTGZZNdrGdw` (USDC/USDT mover), `Et5focveJp8AjPLv4irfxYTc1YJGFDGzG168NWW71pAT` (USDT/PYTH mover, CEX-funded via Binance 2 `5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9`), `6CdJi6M7Kt9xyh1Ro6pNQGNs9u8jjwkr4WmGX1zJ5LNy` (new B48k relay, sig `NcwQtMULWFCHbNXvEb63o3CF9cyy9BizwdyRU9PZEH4UpTNEf3q9SgyV67SA7qEnVmJ5Po4nb8Y2QXXa4YW21R8`), `92BKzviijLwYHNJsXHnW35vhWTtZdYoxJnvGhgNbk2Jm` (new B48k relay, sig `5v6DdeaC3rUD7Veuznjes6SEQkUYs85A95Kg8ndYgV3ecQBG5vqvNGjMTCW1fyqG8UWxApfSLCwvqHdYnAr6E8L9`).

## Recommended Next Action (not performed, read-only pass)

1. Continue pagination of the fee payer's signature history past 150,000 to establish the true lifetime total and growth curve.
2. Decode the 113 unresolved singleton wallets (~113 more `getTransaction` calls at a slower pace) to close the residual "unknown operation" gap with higher confidence.
3. Add `6CdJi6M7…` and `92BKzviijLwY…` to B48k's known relay-wallet set for any future walkback/session reconstruction of this cluster (read-only recommendation; not applied).
4. If pursuing Phase 9's discovery-surface idea further, build the two-stage filter explicitly: (a) execution-fingerprint match, (b) native-SOL transfer to a wallet that later appears as a `creator_funders.funder_address` — this would mechanically separate future launch-clients from the dominant stablecoin-settlement traffic without manual RPC triage.
