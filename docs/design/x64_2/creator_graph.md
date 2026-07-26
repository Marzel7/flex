# X64.2 — Phase 3: Creator Relationships

## Creator census (17 distinct creators across 18 mints)

Only one creator appears more than once:
**`B1cJJMstShf6oGhJ1bmBMK1XBjjr4n58kWHKYUNWygbL`** — 3 launches inside
this 18-row dataset:

| mint | disposable wallet | funder_block_time (UTC) | amount (SOL) |
|---|---|---|---|
| `AGumPoj6jUXMsJv1s9iuXa7uiWj18gBSXuM4bLVQpump` | `Dbvr7ktCbxqJJv3gDtAuK9AjXBsJuqBAh8sCsandLfQz` | 2026-07-19T21:29:30* | 0.001994 |
| `3uJNC2pJESYdGBPfrxnwyk7ULXjqqhsXoxu49wp2pump` | `GxyGhyQKvc1csUrzwB4xtnUv3wG5xV2ChXTGAp2VQE1h` | 2026-07-20T11:55:41 | 0.003994 |

(*A third, earlier row for this same creator+wallet pair,
`87RGBzxbheCo5H4zJjVxAkQh2VA4AZxiekd6dGmopump` funded 2026-07-19T21:29:30,
falls outside the X64.1 24h window and outside this audit's 18-launch
scope — it is the *first* known appearance of `Dbvr7ktCbxq…`, referenced
here only to date the wallet's own first-seen time; it is not one of the
18 launches under audit.)

This creator therefore appears **twice** within the 18-launch dataset
(`AGumPoj6j…` and `3uJNC2pJ…`), using **two different** disposable
wallets. Both migrated in ≤2 seconds (rapid-migration signature,
consistent with the other 17 launches).

No other creator overlap exists — checked directly: `SELECT creator,
COUNT(*) ... GROUP BY creator` across the 18-row set returns exactly one
creator with count > 1.

## Shared disposable wallets

Already covered above — the only shared-wallet relationship in this
dataset is `B1cJJMstShf…`'s own reuse of `Dbvr7ktCbxq…` across its own two
launches. **No two DIFFERENT creators share a disposable wallet anywhere
in this 18-row set.**

## Funding cadence

Per-creator cadence is not meaningfully computable beyond the one
repeat-creator (single data points can't establish a cadence). For
`B1cJJMstShf…`: three known fundings (including the out-of-scope
2026-07-19T21:29:30 one) at roughly 21:29 (7/19), 09:15 (7/20, funding
time of the AGumPoj6j mint), 11:55 (7/20) — irregular spacing (~11.75h,
then ~2.7h), not a fixed-interval automated cadence signature.

## Funding amounts

No two creators in this dataset received the same funding amount (see
`disposable_wallet_analysis.md` — all 18 amounts are distinct, no
clustering around round numbers). `B1cJJMstShf…`'s own two in-scope
amounts (0.001994, 0.003994 SOL) are both very small and differ by
exactly 0.002 SOL — plausibly coincidental at this sample size, not
established as a fingerprint (see `falsification.md`).

## Launch timing

17 of 18 mints cluster within a genuine 40.4-hour span
(2026-07-19T01:58 through 2026-07-20T18:23, using `funder_block_time`);
see `cluster_report.md` for the full burst analysis. `B1cJJMstShf…`'s two
in-scope launches are ~2.6 hours apart within that span — not
tightly co-timed with each other, and not adjacent to each other in the
sorted timeline (four other creators' launches fall between them).

## Migration timing

17 of 18 tokens migrated in 1-2 seconds after CREATE (rapid-migration
signature, consistent across nearly the whole set — not specific to
`B1cJJMstShf…`, whose own two launches migrated in 2s and 2s
respectively, matching the cohort average, not standing out from it).

## Behaviour patterns

No behavioural-queue table (`wt_behaviour_queue`, per project context) was
queried in this pass beyond `token_analysis`'s own `migrated_at`/
`created_at`/`lifecycle_stage` fields — see `x64_2_treasury_emergence.md`
Phase 7 for the full behavioural-marker table across all 18 mints.
`B1cJJMstShf…`'s two launches show no distinguishing behavioural markers
beyond the shared rapid-migration signature common to the whole cohort.

## Operationally-related creators — direct conclusion

**Only one creator (`B1cJJMstShf6oGhJ1bmBMK1XBjjr4n58kWHKYUNWygbL`)
relates to itself across multiple launches** — that is definitionally
true (same wallet, same private key controls both launches), not evidence
of a *multi-creator* operator relationship. **No two DISTINCT creator
addresses in this 18-row dataset share any stored evidence** (no shared
disposable wallet, no shared funding amount, no shared vanity prefix — see
`x64_2_treasury_emergence.md` Phase 1's vanity-prefix check) that would
indicate they belong to a common operator. The creator graph for these 18
launches is, on current stored evidence, **17 disconnected single-creator
components plus one two-launch self-loop** — not a connected multi-creator
graph.
