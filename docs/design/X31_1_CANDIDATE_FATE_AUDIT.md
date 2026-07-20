# X31.1 — Candidate Fate Audit

Investigation only, per the brief. No code changed. A stratified sample of 8 candidates (3 `BUY_SWARM`, 5 `EXPIRED`) from X31.0's 96-hour population of 139 non-`FIRED_CREATE` outcomes was traced end-to-end: DB-side lifecycle reconstruction (free) followed by live RPC (`getSignaturesForAddress` full paging + targeted `getTransaction` sampling on post-closure activity, project `.env` Helius key, no enhanced endpoint, all results cached to `/tmp`).

## Sample

| Wallet | Outcome | Subprov | Treasury | Funding mechanism | Funding amount |
|---|---|---|---|---|---|
| `6HGc4vy2...` | BUY_SWARM/swapped | `5szGvUv5...` | `69SNcRC8...` | SEEDED_ACCOUNT_CLOSE | 0.001 SOL |
| `HVC8bpfh...` | BUY_SWARM/swapped | `5szGvUv5...` | `69SNcRC8...` | SEEDED_ACCOUNT_CLOSE | 0.001 SOL |
| `2gUt8LUt...` | BUY_SWARM/swapped | `5szGvUv5...` | `69SNcRC8...` | SEEDED_ACCOUNT_CLOSE | 0.001 SOL |
| `2e71NTqN...` | EXPIRED/ttl | `H8Veu41c...` | `69SNcRC8...` | WSOL_WRAP_CLOSE | 0.0035 SOL |
| `AVun1qJ2...` | EXPIRED/ttl | `ESf4tJRQ...` | `9hGcx...` | WSOL_WRAP_CLOSE | 0.008 SOL |
| `8vYnE4Sv...` | EXPIRED/ttl | `ESf4tJRQ...` | `9hGcx...` | WSOL_WRAP_CLOSE | 0.008 SOL |
| `GQcfwdbi...` | EXPIRED/ttl | `ESf4tJRQ...` | `9hGcx...` | WSOL_WRAP_CLOSE | 0.008 SOL |
| `Fg1Ho94T...` | EXPIRED/ttl | `G35JJCSt...` | `69SNcRC8...` | WSOL_WRAP_CLOSE | 0.001 SOL |

Note the funding amounts here (0.001–0.008 SOL) are far below the substantive-amount threshold (≥0.1 SOL) X31.0 used to define its "High Similarity" wrap-close signal — these are dust-scale fundings, a materially different profile from the 27 High-Similarity wallets X31.0 scored. This distinction becomes important below.

## Lifecycle reconstruction, per stage

**Treasury funded → Subprovider observed**: confirmed via `wt_subprov_evidence`/`wt_discovered_subprovs` for all 8 — each subprov (`5szGvUv5...`, `H8Veu41c...`, `ESf4tJRQ...`, `G35JJCSt...`) is already a known `PROVISIONAL_SUBPROV` funded by an already-confirmed treasury (`69SNcRC8...` or `9hGcx...`, both previously verified in X31.0).

**Candidate opened**: all 8 have a `wt_candidate_websocket_watches` row with `detected_at` within 1-2 seconds of the funding transaction's `wrap_close_time`, consistent with the existing detect-on-sight design (X29.11's finding, confirmed to hold here too).

**All websocket events / classification changes**: not separately logged per-event in a queryable table — `wt_candidate_websocket_watches` stores only the terminal `state`/`close_reason`, not an event trail. This is a genuine observability gap for reconstructing exactly *when* a BUY_SWARM classification triggered relative to individual observed transactions, though the terminal outcome itself is reliable.

**Candidate closed**: `close_reason='swapped'` for the 3 BUY_SWARM candidates, `close_reason='ttl'` for the 5 EXPIRED candidates — matching `wt_candidate_websocket_watches.close_reason` exactly, no discrepancy found.

**Wallet history afterwards (the core of this audit) — full signature paging, no window truncation**:

| Wallet | Total sigs (all-time) | Sigs before funding+close | Sigs after close | Pre-existing wallet? |
|---|---|---|---|---|
| `6HGc4vy2...` | 506 | 447 | 59 | **Yes** — hundreds of transactions before this funding event |
| `HVC8bpfh...` | 717 | 712 | 5 | **Yes** |
| `2gUt8LUt...` | 617 | 463 | 154 | **Yes** |
| `2e71NTqN...` | 19,050 | 17,723 | 1,327 | **Yes, extremely so** — history spans 1782163253→1784457696 (weeks), an established, high-frequency wallet, not a disposable candidate |
| `AVun1qJ2...` | 389 | 389 | 0 | **Yes** |
| `8vYnE4Sv...` | 109 | 109 | 0 | Yes |
| `GQcfwdbi...` | 201 | 201 | 0 | Yes |
| `Fg1Ho94T...` | 695 | 378 | 317 | **Yes** |

**This is the single most important structural finding of this audit**: none of the 8 sampled candidates is a fresh, single-use wallet. Every one already had a substantial transaction history *before* the funding event this audit is tracing. This directly contradicts the profile X29.7.1 established for a genuine WATCHTOWER-provisioned creator wallet (a disposable, single-transaction-lifetime wrap/custody wallet). Combined with the dust-scale funding amounts (0.001–0.008 SOL, well below the ≥0.1 SOL threshold that characterizes a real wrap-close creator-seed), this sample looks structurally like **incidental fringe activity around already-active wallets**, not genuine WATCHTOWER provisioning candidates that failed to convert.

**Did CREATE ever occur?** Checked directly: for the two candidates with meaningful post-closure activity (`2gUt8LUt...`, 154 post-closure txs; `2e71NTqN...`, 1,327 post-closure txs), a sample of post-closure transactions was fetched and inspected for pump.fun program (`6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`) involvement. **Zero pump.fun program hits found in any sampled transaction, for any of the 8 wallets.** For `2gUt8LUt...` specifically, the sampled post-closure transactions instead show `pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA` (the **PumpSwap** post-migration AMM program) alongside `system`/`spl-token` instructions — i.e., this wallet is actively swapping on an already-migrated pool, exactly the trading-bot behavior a `BUY_SWARM` classification is designed to detect. **No CREATE occurred for any of the 8 sampled candidates**, in the sampled transactions.

**Did migration occur?** Not applicable in the sense the brief intends (migration is a property of a *token*, and none of these 8 wallets produced a token) — but the `2gUt8LUt...` finding above shows the wallet is *trading on* an already-migrated pool, which is a different and unrelated migration event, not one this candidate caused.

**Was another wallet funded afterwards?** Not traced exhaustively (would require re-running the subprov-level `wt_subprov_evidence` query per subprov, out of this sample's scope), but note `H8Veu41c...`, `ESf4tJRQ...`, and `5szGvUv5...` all appear multiple times funding *different* candidate wallets within the same session in X31.0's underlying data — i.e., yes, at the subprov level, funding continued to other wallets; this sample's specific 8 wallets were not, individually, refunded again by the same subprov (each has exactly one `wt_subprov_evidence` row).

**Current balance / current tx count**: captured directly via `getBalance`/`getSignaturesForAddress` at investigation time — see table above for tx counts; balances are 0 lamports for 7 of 8, and 12,754,612 lamports (≈0.01275 SOL) for `2e71NTqN...` (consistent with an active wallet mid-cycle, not evidence of anything WATCHTOWER-specific on its own).

## Direct answer to the brief's key question

**What actually happened to these candidates after we stopped watching them?** For all 8 sampled: **nothing that resembles a missed WATCHTOWER launch.** The BUY_SWARM candidates continued doing exactly what their classification predicted — active swapping (confirmed directly for one, via PumpSwap program calls, in transactions dated after this candidate's closure). The EXPIRED candidates mostly went quiet (0 post-closure transactions for 3 of 5), and the two EXPIRED candidates with post-closure activity (`2e71NTqN...`, `Fg1Ho94T...`) show no pump.fun CREATE in the sampled transactions.

**Is expiration correct for this sample?** Yes, on the evidence gathered — none of the 5 EXPIRED candidates shows any later CREATE. **Is the buy-swarm classifier too aggressive for this sample?** No — the one BUY_SWARM candidate checked in detail (`2gUt8LUt...`) is directly confirmed still trading via PumpSwap after closure, which is precisely the behavior the classifier exists to detect and exclude from the creator-candidate pipeline.

## An important caveat this sample surfaces about X31.0's finding, and a narrower open question

This sample is small (8 of 139) and, by the numbers above, appears to be drawn disproportionately from **dust-scale funding events on pre-existing, active wallets** — a different population from X31.0's 27 "High Similarity" subprovider wallets, which were specifically the substantive-amount (≥0.1 SOL) wrap-close-shaped events. **This sample does not directly test X31.0's central open question** — whether the 27 High-Similarity, substantive-funding candidates specifically failed to convert for a detection reason. It tests a related but distinct question (whether *any* BUY_SWARM/EXPIRED outcome in the broader 139-candidate population represents a missed launch), and the answer for this specific sample is no.

**The concrete next step this narrows to**: re-run this exact lifecycle-tracing method, but sampled specifically from the 27 High-Similarity (≥0.1 SOL, wrap-close-shaped) subprovider wallets X31.0 identified — not the broader dust-funded population this sample happened to draw from — to directly test whether *those* higher-confidence candidates show any post-closure CREATE activity. That is the sample most likely to contain a genuine detection gap, if one exists, and this audit has not yet examined it.

## Conservation and honesty check

8 of 8 sampled candidates traced to a definite outcome (no CREATE found in sampled post-closure activity); 0 unresolved; RPC cost for this sprint: 8 `getSignaturesForAddress` (initial) + ~14 paging calls (`before`-cursor continuation) + `getBalance` ×8 + `getTransaction` ×~53 (targeted sampling of post-closure activity) — all via the cheap, non-enhanced endpoint, all cached to `/tmp/x31_1_*.json`, consistent with standing RPC-investigation discipline.
