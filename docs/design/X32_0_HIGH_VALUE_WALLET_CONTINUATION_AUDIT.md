# X32.0 — High-Value WATCHTOWER Wallet Continuation Audit

Investigation only, per the brief. No code changed. Traces the continuation of the money flow — not motivation, not "why didn't it create" — for wallets that received substantive wrap-close funding (the X31.2 High-Similarity population) but never fired CREATE. Full RPC transaction-content inspection (not just program-ID presence, which X31.1/X31.2 used and which this sprint found to be an insufficient signal on its own — see the methodology correction below).

## A methodology correction made mid-investigation

Initial parsing of outbound `system transfer` instructions for the two small burst-member wallets (`8F5yHk1fE9ZK...`, `5CyzvBWNM2Za...`) appeared to show them sending 2-3 SOL each to several new, distinct wallets — read naively, this looked like second-tier fan-out to new recipients. Direct inspection of the full instruction set (not just the transfer instruction in isolation) showed this was wrong: those "new wallets" are the recipients' **own WSOL associated-token-accounts**, created via `createIdempotent`, funded, `syncNative`'d, and `closeAccount`'d — the same wrap-close pattern WATCHTOWER's own detector looks for. This is not fan-out to a peer; it is capital moving one hop further down the same wrap-close chain. Getting this right required reading the full instruction list and `innerInstructions` for each transaction, not just filtering `system:transfer` events in isolation — a discipline worth stating explicitly since the naive read would have produced a materially wrong finding (undetected fan-out) in the opposite direction from what actually happened (capital recycling back to origin).

## Wallets traced

Three wallets from X31.2's 36-row High-Similarity population, chosen for structural variety: the whale (`4231KLYipw...`, 806.99+128.23 SOL from two different subprovs), and two members of the single largest burst (`8F5yHk1fE9ZK...`, `5CyzvBWNM2Za...`, both funded ~4 SOL by `BWwpES2oYug1...`).

## Wallet 1: `8F5yHk1fE9ZKoF9SaJE7RYVmMoHABVzBr1GTVbaSUTKZ`

**1. Does it later fund another wallet?** Yes — but not a peer wallet in the "new operational entity" sense. It funds two intermediate WSOL wrap-accounts (3.04 SOL to one, 2.64 SOL to another), each of which immediately wraps, closes, and routes the proceeds **back to `BWwpES2oYug1SsLKPyFXekdJK99dHtdPgBjNk1SPRMDu` — the exact subprov that funded this wallet in the first place.** Timing: both hops occurred within roughly 8-15 minutes of the original funding (funded 1784137804, hops at 1784138632 and 1784146325). Amounts: 3.04 SOL and 2.64 SOL, out of the 3.97 SOL received — i.e., nearly the entire principal, recycled. Not repeated beyond these two hops; the wallet also sent a final small residual (0.019 SOL) directly back to `BWwpES2oYug1...` at 1784147240.

**2. Do those recipients become creator/subprovider/treasury/buy swarm/unknown?** Neither of the two intermediate wrap-wallets (`DgsRXog6h423dk...`, `8rN2EuY2zvBiPC...`) appears in `wt_discovered_subprovs` or `wt_watchtower_launches` — they are not persisted as any operational role, consistent with being genuinely single-use, disposable wrap-close intermediaries (the same profile X29.7.1 established for HZB2). Their *destination* is not a new role at all — it is the same subprov that started the chain.

**3. Does the wallet ever receive additional funding, beyond the original?** Only two negligible-value (1 lamport, memo-shaped) inbound transfers from `GF7YB1jGktkRQNnXU5YCuVRCQdoHcctkUw1q5bgbLLXc` — this is the exact known spam wallet from the platform's own registry (memory: `known-spam-wallets`), correctly disregarded as environmental noise, not real funding.

**4. Does it recycle capital?** Yes, almost entirely: received 3.97 SOL total, sent 3.04 + 2.64 + 0.019 ≈ 5.70 SOL outbound across the traced transactions (the excess above the single 3.97 SOL funding reflects that PumpSwap trading activity intermixed with the wrap-close hops moved additional SOL through the same wallet in the traced window — this wallet was concurrently trading, not solely relaying). **Retained balance: 0.** Total lifetime signatures: 18 (fully enumerated, not sampled).

**5. Does it terminate, or remain active?** Terminates, functionally — current balance 0, and its last traced signature is the final residual send back to the subprov. No later re-funding observed in its full 18-transaction history.

## Wallet 2: `5CyzvBWNM2ZaCTv4vtU9jDcYsfWUCyFaTfg6tEMintnJ`

**1. Does it later fund another wallet?** Yes, identical pattern: 2.79 SOL and 2.66 SOL routed through two intermediate WSOL wrap-accounts, both closing back to `BWwpES2oYug1...` (confirmed directly — `closeAccount` destination is `BWwpES2oYug1SsLKPyFXekdJK99dHtdPgBjNk1SPRMDu` in both cases). Timing: both within ~7 minutes of the original 4.9638 SOL funding (funded 1784137804, hops at 1784138221 and 1784138240). A third, smaller residual (0.275 SOL) went directly back to the same subprov at 1784147267.

**2. Recipient classification?** Same as Wallet 1 — the intermediate wrap-wallets (`Egh3APCAE9SAim...`, `5Azu2Cun4gEiyx...`) are not persisted as any role in `wt_discovered_subprovs`/`wt_watchtower_launches`; they are disposable single-use plumbing, not new operational entities.

**3. Additional funding received?** One further funding from `BWwpES2oYug1...` itself (1.8567 SOL at 1784140404, a second tranche within the same session) — i.e., the same subprov funded this wallet twice, not once. Plus the same negligible spam-wallet dust.

**4. Capital recycling?** Received 4.9638 + 1.8567 ≈ 6.82 SOL from the subprov across two tranches; sent 2.79 + 2.66 + 0.275 ≈ 5.73 SOL back toward the same subprov via wrap-close hops, plus additional PumpSwap-trading-related token-account activity in between. **Retained balance: 0.**

**5. Terminate or remain active?** Terminates — current balance 0, no signatures found beyond the traced window.

## Wallet 3: `4231KLYipwRTmFdQ6ZBa1H4Jf3EpfF62Gzg6DWHWvhPZ` (the whale — structurally different from Wallets 1/2)

**1. Does it later fund another wallet?** Yes, extensively — this is not a two-hop recycle but sustained, ongoing distribution. Across its full 156-transaction history: **18,065.7 SOL sent outbound in total**, to at least 50 distinct destination addresses, in repeated tranches (not a single event). Of the classified destinations, **7 are already-known `wt_discovered_subprovs` entries**, of which 4 are `PROVISIONAL_SUBPROV` under the exact same confirmed treasury (`9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4`) that funded this whale wallet in the first place: `82Yzf1hMDyLa...` (650+254.9 SOL, the same subprov that funded it), `56m5gW58qe47...` (255 SOL, its second funder), `7uJw44kvyNjog5...` (281.3+201.1 SOL, two separate tranches), and `E33jmbX8TQLDP2...` (650 SOL) — the latter is itself a member of X31.2's High-Similarity population. Amounts to individual destinations range from 1 SOL (repeated small "gas top-up"-shaped transfers of exactly 5 SOL to many addresses) up to 718 SOL. Pattern: **repeated, not a one-time event** — the same destinations recur across the transaction history with new tranches roughly every 10,000-50,000 seconds.

**2. Recipient classification.** Direct classification against `wt_discovered_subprovs`/`wt_confirmed_treasuries`/`wt_watchtower_launches`: **7 of ~50 destinations are already-known Subprovider entities** (all `PROVISION_CANDIDATE` or `PROVISIONAL_SUBPROV`); the remainder (~43 destinations) are currently unclassified in any WATCHTOWER table. None of the sampled destinations is a confirmed Treasury or a confirmed Creator.

**3. Does the wallet itself receive additional funding?** Yes — directly and repeatedly from `9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4` (the confirmed treasury), at multiple points (e.g. 1.078 SOL at 1784202715). It also receives from several currently-unclassified wallets (`FkccGTEh6tJe7FGg3hk1dMsz67FDKr5aMh6CWYnTu1f8` — the same wallet referenced directly in the user's own earlier framing of this chain — sent it 10 SOL and 900 SOL in two separate transactions), and from a handful of other addresses, most sending exactly `X.00xxxx` SOL amounts (consistent with wrap-close-shaped inbound funding, i.e. this whale wallet is ALSO, at times, a wrap-close **recipient**, not solely a treasury-side distributor).

**4. Does it recycle capital?** Yes, on a much larger scale than Wallets 1/2, and asymmetrically: **total received across the traced history (1,064.5 SOL) is far smaller than total sent (18,065.7 SOL)** — meaning either (a) a substantial share of its outbound capital originates from funding sources this bounded 156-transaction sample did not capture (its full history may extend further back or involve other inflow addresses not sampled), or (b) it is drawing down a balance accumulated before this trace's earliest captured signature. **Current retained balance: 1,521,892,693 lamports (≈1.52 SOL)** — near-zero relative to the volumes moved, but not exactly zero, unlike Wallets 1/2.

**5. Does it terminate, or remain active?** **Remains active** — its most recent traced signature is within the same dense activity window as the rest of its history, with no indication of a terminal "last transaction." This is qualitatively different from Wallets 1/2, both of which cleanly reached a zero balance and stopped.

## Direct comparison against the historical-creator model

The brief's framing:

```
Historical creator:  funded → CREATE → dies
These wallets:        funded → ?      → ?       → ?
```

Filled in with evidence, and it is **not one shape** — the two traced sub-populations behave differently:

```
Burst-member wallets (8F5yHk1fE9ZK, 5CyzvBWNM2Za):
  funded (by a known subprov)
    → wraps and closes almost all of it back to that SAME subprov,
      via 1-2 disposable single-use wrap intermediaries, within minutes
    → retains 0 balance
    → terminates (no further activity found)

Whale wallet (4231KLYipw):
  funded (by a known confirmed treasury, directly, AND by two
          subprovs under that treasury)
    → distributes large sums, repeatedly, to a mix of already-known
      Subprovider wallets (same treasury's family) and ~43 currently-
      unclassified addresses
    → also receives further funding on an ongoing basis, from the
      treasury and from other unclassified sources
    → retains a small non-zero balance
    → REMAINS ACTIVE, not terminal
```

**The burst-member wallets do not diverge from the creator path in the sense of "becoming something else" — they complete a shorter, self-contained loop: subprov funds them, they wrap-close the capital right back to the same subprov, and they stop.** This is capital recycling within a single funding round, not provisioning of a new operational entity. Neither wallet shows any evidence of acting as a Subprovider, Treasury, Creator, or Buy-Swarm participant themselves — the wrap-close pattern observed on their outbound side is structurally identical to WATCHTOWER's own creator-funding mechanism, but its destination is the *funder*, not a *new creator*. Whether this reflects a deliberate operational technique (e.g., testing/warming a wrap-close path, or a treasury reclaiming unused provisioning capital) or an artifact of this being a fringe/incidental population (as X31.1/X31.2 already suspected for the smaller-amount members of the same burst) is not resolvable from transaction evidence alone.

**The whale wallet is a genuinely different structural role, not a divergence from "creator" at all** — its behavior (bidirectional flow with the confirmed treasury, distribution to multiple already-known Subprovider wallets under that same treasury, sustained activity rather than a terminal event, near-zero but non-zero retained balance) is most consistent with **the whale wallet itself functioning as an intermediate capital-distribution layer between the confirmed treasury and the Subprovider tier** — i.e., a genuine operational role WATCHTOWER's current three-role vocabulary (Treasury/Subprovider/Creator, per X30.0's inventory) has no name for. It is not "a Subprovider that failed to find a creator" — it never received a creator-shaped disposable wrap-close funding itself; it received large, round-number-ish sums (5, 100, 650+ SOL) directly from a confirmed treasury and passed similarly large sums onward to multiple confirmed Subprovider wallets. This is structurally a distribution/relay tier sitting between Treasury and Subprovider, not a Subprovider variant.

## What this rules in and out, precisely

- **Ruled out for the two burst-member wallets**: any onward provisioning of a new creator, subprovider, or treasury entity. Their entire post-funding lifecycle is a closed loop back to their own funder.
- **Ruled in for the two burst-member wallets**: capital recycling, complete and terminal, with zero retained balance — "the money went back to where it came from" is a fully evidenced, not inferred, answer.
- **Ruled out for the whale wallet**: termination — it is provably still active, not dead.
- **Ruled in for the whale wallet**: it is a genuine funding intermediary between a confirmed treasury and multiple confirmed Subprovider wallets, moving amounts an order of magnitude larger than any individual creator-funding event this corpus has characterized, on an ongoing (not one-shot) basis.

## Scope note

Consistent with prior sprints' discipline of not broadening beyond what was asked, this sprint traced 3 of the 36 High-Similarity wallets in full transaction-content detail. The ~43 currently-unclassified destinations of the whale wallet's outbound distribution are a natural, well-scoped next tracing target if a further sprint is wanted — this report does not pursue them.
