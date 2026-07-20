# X31.2 — High-Similarity Candidate Conversion Audit

Investigation only, per the brief. No code changed. This sprint required freezing X31.0's exact 96-hour window rather than recomputing "last 96 hours" fresh — the live cascade never stops, so a fresh `now()` silently drifts the population between sprints (166 rows recomputed live vs. 165 reported in X31.0 — a 1-row drift from ongoing detection in the intervening time, not an error, but worth stating so the two reports' populations are understood as the same frozen set, not two different samples).

## Conservation check (the brief's explicit requirement, done first)

All 166 `wt_subprov_evidence` rows in the frozen window, matched against their corresponding `wt_candidate_websocket_watches` outcome:

| Outcome | Count |
|---|---|
| `EXPIRED/ttl` | 103 |
| `BUY_SWARM/swapped` | 43 |
| `EXPIRED` (no reason logged) | 15 |
| `NEVER_OPENED_AS_CANDIDATE` | 5 |
| **Total** | **166** |

**166 = 166. Conservation holds — no unaccounted rows.** But the 5 `NEVER_OPENED_AS_CANDIDATE` rows are themselves a finding, not a clean zero: **a genuine gap in the state machine, not in WATCHTOWER's behaviour**, per the brief's own framing of what a conservation mismatch would mean. Traced directly: 4 of the 5 have `funding_mechanism='SEEDED_ACCOUNT_CLOSE'` (Mechanism B) with `amount_sol=NULL`; the fifth is `WSOL_WRAP_CLOSE` with a real amount (0.008 SOL) that also never opened a candidate despite otherwise looking identical to dozens of rows that did. This points at `open_candidate_watch`'s call site in `ws_cascade.py` (traced structurally in X30.1/X30.2 — `promote_to_subprov` always writes `wt_subprov_evidence` unconditionally, but the candidate-open step is a separate, later call) silently skipping when a required field (most plausibly `base_amount_sol`) is `None` — a real, distinct pipeline gap worth flagging to the team separately from the conversion question this sprint targets, since it means an unknown number of genuine provisioning events across all of WATCHTOWER's history may never have reached observation at all, independent of any BUY_SWARM/TTL outcome.

## Building the exact High-Similarity population, and a filter correction

Applying the brief's 5 criteria required first correcting a methodological assumption carried over from X31.0: X31.0's ≥0.1 SOL + "genuine rent-tail" filter was built for the `creator_funders` table (a *raw on-chain transfer amount*, which genuinely includes the WSOL rent remainder as a lamport tail). Applying that same rent-tail check to `wt_subprov_evidence.amount_sol` returned **zero rows** — checked directly, this column never carries the rent-tail pattern (0 of 166 non-null amounts end in `...39280`), because `promote_to_subprov` (X30.1's trace) stores `base_amount_sol` — the wrap-close principal, already computed with rent-plumbing subtracted out — not the raw wire amount. The rent-tail check simply does not apply to this table; requiring it was a category error carried over from a different data source. Corrected filter: substantive funding (`amount_sol >= 0.1`) plus a wrap-shaped mechanism (`WSOL_WRAP_CLOSE`/`SEEDED_ACCOUNT_CLOSE`), which is what "genuine wrap-close signature (not ATA-rent-only)" actually resolves to for this table.

**Result: 36 evidence rows, across 11 distinct subprovers, satisfying all 5 of the brief's criteria** (substantive amount, genuine wrap-close mechanism, creator-like funding profile — a distinct recipient wallet, not the subprov's own address — and opened as a candidate, confirmed next). This is a different, corrected count from the "27 subprovider wallets" language in X31.0 (that figure counted subprov wallets showing *any* wrap-shaped evidence regardless of amount; the ≥0.1 SOL substantive-amount filter, applied strictly, narrows to 11 subprovs / 36 individual funding events).

## Conversion outcome for all 36 — a clean, single-outcome population

Every one of the 36 High-Similarity candidates was opened as a candidate (none is among the 5 never-opened rows). Their terminal states:

| Outcome | Count |
|---|---|
| `EXPIRED/ttl` | 22 |
| `EXPIRED` (no reason logged) | 14 |
| `BUY_SWARM` | **0** |
| `FIRED_CREATE` | **0** |

**This is the most important structural finding of this sprint: not one of the 36 High-Similarity candidates was ever classified BUY_SWARM.** Buy-swarm misclassification, the leading hypothesis carried from X31.0/X31.1, is **completely absent from this specific population** — every single High-Similarity candidate expired on its own TTL, untouched by any swap-detection logic at all. Whatever is preventing these candidates from converting to CREATE, it is not the buy-swarm gate.

## Lifecycle reconstruction — 7-wallet RPC-traced sample (whale, convergent, and burst-member cases)

Selected to cover the population's structural variety: the two largest single fundings (a wallet funded 806.99 SOL by one subprov and 128.23 SOL by a second, different subprov, within the same ~10-minute window), a wallet funded by 3 different subprovs, a wallet funded twice by the same subprov, one standalone mid-size funding, and 3 members of the single largest burst (`BWwpES2oYug1...`, which alone accounts for 22 of the 36 rows in one tight cluster on 2026-07-16).

| Wallet | Funding | Total lifetime sigs | Sigs before first funding | Sigs after candidate close | Current balance | Pump.fun program in post-closure sample? |
|---|---|---|---|---|---|---|
| `4231KLYipw...` (whale) | 806.99 SOL + 128.23 SOL from 2 subprovs, ~10min apart | 156 | 16 | 140 | 1,521,892,693 lamports (≈1.52 SOL) | **No** (0/20 sampled) |
| `Cz62f2yuun...` (3-subprov convergence) | 2.12 + 2.14 + 2.93 SOL from 3 different subprovs | 17,593 (fully paged) | 0 (all sigs postdate first funding, but span starts before the *sampled* funding tx used as the close-reference) | 12,154 | 0 | **No** (0/20 sampled) |
| `7YtJuczaj8...` (2x-funded, same subprov) | 0.1713 + 0.212 SOL, same subprov | 10,000 (page-capped; true count higher) | 10,000 | 0 | 0 | N/A (no post-closure activity in this window) |
| `DmoG9vDaYTf8...` (standalone) | 5.0 SOL | 7,897 | 7,897 | 0 | 0 | N/A |
| `3puTvniy8wfT...` (burst member) | 4.31 SOL | 11 | 1 | 10 | 0 | **No** (0/10 sampled) |
| `EyrKSNmAkQ8g...` (burst member) | 5.44 SOL | 14 | 2 | 12 | 0 | **No** (0/12 sampled) |
| `J7Mt2fC3zrhm...` (burst member) | 4.26 SOL | 8 | 5 | 3 | 0 | **No** (0/3 sampled) |

**Direct inspection of the post-closure transaction contents** (not just program-ID membership) for the two highest-activity wallets:

- `4231KLYipw...` (the whale): post-closure transactions are bare `system` + `ComputeBudget` instructions — plain SOL transfers, no token program, no swap, no pump.fun. It currently still holds ≈1.52 SOL. This looks like ongoing capital movement (consistent with a distribution/treasury-adjacent role, or a hop in a longer relay chain), not creator or trading behaviour.
- `Cz62f2yuun...` (the 3-subprov convergence wallet): post-closure transactions are `system`/`spl-token`/`spl-associated-token-account` — token-account plumbing and plain transfers, again with **no PumpSwap or pump.fun program present** in the sampled transactions, and a current balance of 0. Its 17,593-signature, ~5.9-hour-dense history is the same "not a fresh, single-use wallet" profile X31.1 already found for its own high-post-closure-activity sample — this wallet, too, is a pre-existing, heavily-used address, not a disposable WATCHTOWER creator-candidate wrap wallet.

## Direct answer to the brief's 5 possible outcomes

- **They never created a token.** Confirmed for all 7 RPC-traced wallets — zero pump.fun program interaction found in any sampled post-closure transaction, including the whale and the highest-activity convergent wallet.
- **They created a token that was missed.** Not found in this sample — no evidence of a missed CREATE.
- **They were closed incorrectly.** Cannot be ruled in or out from this evidence alone for the TTL closures specifically (a TTL expiry is, by definition, "we stopped watching before anything else happened" — this audit confirms nothing else *did* happen afterward, in the sampled window, but cannot prove the TTL window itself was well-calibrated). What *can* be ruled out: they were not closed incorrectly by the buy-swarm gate, since none of the 36 was ever buy-swarm-classified at all.
- **They transformed into another behavioural pattern.** Partially yes, but not into creator or swap-trader behaviour: the whale wallet and the 3-subprov convergence wallet both show ongoing, high-frequency **plain capital movement** (system transfers, token-account plumbing) after closure — behaviour more consistent with a distribution/relay role than either "creator" or "trader." This is a genuinely different pattern from both EXPIRED's implicit "went quiet" assumption and BUY_SWARM's swap-trader profile, and it is not currently captured by any classification in the pipeline (per X30.1's inventory, there is no "relay"/"distributor" terminal state).
- **The operator deliberately abandoned them.** Plausible for the wallets with genuinely zero post-closure activity (`7YtJuczaj8...`, `DmoG9vDaYTf8...`), but indistinguishable, from this evidence alone, from "the operator simply moved capital elsewhere and this specific wallet's role in that operation ended," which is a routine and unremarkable outcome, not necessarily "abandonment" in an adversarial sense.

## What this rules in and out, precisely

- **Ruled out for this population**: buy-swarm misclassification (zero BUY_SWARM outcomes among all 36), and a missed CREATE (zero pump.fun program hits across every sampled post-closure transaction, including the two highest-activity wallets checked in full transaction detail, not just program-ID presence).
- **Ruled in, as the dominant pattern**: TTL expiry with no dramatic aftermath for most of the sample (`7YtJuczaj8...`, `DmoG9vDaYTf8...`, and the majority of unsampled burst members by inference from the outcome table), consistent with genuine, correctly-expired provisioning activity that simply never converted — not evidence of a detection gap.
- **A new, distinct finding this sprint surfaces**: two of the seven sampled wallets (the whale and the 3-subprov convergence wallet) show **substantial ongoing capital-movement activity** after their candidate watch closed — a pattern the current classification vocabulary has no name for (not Creator, not BUY_SWARM's swap-trader profile, not a quiet/abandoned wallet). Whether this represents a genuine operational role WATCHTOWER's model is currently blind to, or simply reflects that large, well-connected wallets are naturally busy for reasons unrelated to this specific funding event, is not resolvable from this evidence alone and would need a purpose-built follow-up (e.g., checking whether `4231KLYipw...` or `Cz62f2yuun...` appear as a funding source to any *other* known subprov or treasury in the corpus — a natural extension of this exact tracing method, not a new investigation).

## Scope discipline note

Per the explicit instruction not to broaden this investigation further, this report stops at the 36-candidate High-Similarity population and the 7-wallet RPC-traced sample within it — it does not expand into the whale/convergence wallets' own upstream or downstream funding networks, which would be a legitimate but separate next sprint.
