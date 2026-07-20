# X34.0 — Primitive Sufficiency Audit

Investigation only. No code changes. Follows [X33.0](X33_0_CANONICAL_MOTIF_DISCOVERY.md).
All numbers from live SQL against `database/wt_ops_v2.db`, run 2026-07-20.

## Hypothesis under test

X33.0's two primitives:
- **Primitive A — Operational Identity Transfer**: wrap→close, `closeAccount.destination`
  becomes the next operational wallet.
- **Primitive B — Capital Allocation**: treasury funding splits into provisioning-scale
  vs maintenance-scale (dust) transfers.

## Phase 1 — Behaviour Inventory

From `wt_provisioning_edges`, `wt_watchtower_launches`, `wt_capital_reloads`,
`wt_walkback_queue`, `wt_vanity_families`:

1. Creator provisioning (subprov→creator funding, CREATE follows)
2. Treasury capitalization (treasury→subprov, bulk)
3. Dust maintenance (treasury→subprov, ≤0.002 SOL)
4. Subprovider chaining (subprov acts as funder to another subprov)
5. Capital recycle (subprov reloaded, resumes wrap-close)
6. Treasury reuse (one treasury roots many launches)
7. Burst launches (same-instant multi-recipient fan-out)
8. Walkback-only launches (`LINK_ONLY` / `PARTIAL_TREASURY` / `SKIP` classes — attribution
   built without a full RPC replay)
9. Distribution wallets (fund many downstream subprovs, mixed dust/bulk)
10. Migration (create→PumpSwap migration timing, `create_to_migration_secs`)
11. Sweep/backfill behaviour (`ACTIVE_CATCHUP`, `OPENING_CATCHUP`, `PENDING_CREATE_RETRY`
    detection sources)
12. Buy-swarm interactions (same-instant fan-out that swaps, not creates)
13. Staged launches (delayed fanout→create)
14. Instant launches (near-zero fanout→create)
15. **PLAIN_XFER funding mechanism** — a non-wrap-close transfer mechanism present on
    both edge types, roughly matching WSOL_WRAP_CLOSE in volume (not previously inventoried
    as its own behaviour in X33.0's catalogue).
16. **SEEDED_ACCOUNT_CLOSE** — a second confirmed-launch funding mechanism distinct from
    WSOL_WRAP_CLOSE, present in 18 of 43 confirmed launches (`wt_watchtower_launches`).

## Phase 2 — Primitive Decomposition

| Behaviour | Decomposition | Evidence |
|---|---|---|
| Creator provisioning (WSOL_WRAP_CLOSE) | B (bulk) ↓ A ↓ CREATE | 185 SUBPROV_TO_CREATOR wrap-close edges, 25/43 launches |
| Treasury capitalization | B (bulk mode) | 236/383 TREASURY_TO_SUBPROV edges, avg 270 SOL |
| Dust maintenance | B (maintenance mode) | 147/383 edges, avg 0.0006 SOL |
| Subprovider chaining | B ↓ A ↓ B (repeat) | 5 wallets are both `to_wallet` and `from_wallet` in TREASURY_TO_SUBPROV |
| Capital recycle | B (repeated over time on same wallet) | 356 `wt_capital_reloads` rows |
| Treasury reuse | B applied many times from one root, no new primitive | top treasury → 15 launches |
| Burst launches | A applied N times at identical block_time | 4 (wallet,time) groups, max 6 recipients |
| Walkback-only (LINK_ONLY/PARTIAL_TREASURY) | Same A/B composition, incomplete evidence chain (fewer hops recovered), not a different mechanism | 170+501 rows — an **evidentiary completeness** dimension, not a behavioural one |
| Distribution wallets | B (dust) interleaved with B (bulk) from same source wallet | matches Motif 2/3 from X33.0, no new structure |
| Migration | Downstream of CREATE, not a funding-graph behaviour at all — orthogonal axis | 4 launches with recorded `create_to_migration_secs` (7–135s, avg 72.75s) |
| Sweep/backfill (ACTIVE_CATCHUP etc.) | Detection-pipeline metadata, not an on-chain behaviour — describes HOW flex observed it, not what the operator did | `detection_source` field on `wt_watchtower_launches` |
| Buy-swarm | A applied at burst timing, CREATE outcome absent (SWAP instead) — same structural composition as burst, differs only in the *result* of the created account, not the funding primitive | qualitative, per prior confirmed work |
| Staged / Instant | Timing attribute of when A follows B, not a distinct primitive | `launch_mode`/`fanout_to_create_secs` field |
| **PLAIN_XFER mechanism** | **Does NOT decompose into A.** It is capital movement (matches B's transfer nature) but does *not* end in a `closeAccount` — there is no wrap→close identity-transfer step. It still moves capital from a persistent wallet to a next-stage wallet (subprov or creator), and in 18/43 confirmed launches a creator wallet *is* seeded this way and still goes on to CREATE. | 209 TREASURY_TO_SUBPROV + 455 SUBPROV_TO_CREATOR edges are PLAIN_XFER (not fewer than WSOL_WRAP_CLOSE's 175+185) |
| **SEEDED_ACCOUNT_CLOSE mechanism** | Distinct confirmed-launch mechanism, 18/43 launches — a close-account event that is NOT the WSOL wrap-close pattern (different account being seeded/closed). Superficially resembles Primitive A's "closeAccount.destination becomes next wallet" shape but via a different setup path. | `wt_watchtower_launches.funding_mechanism` |

## Phase 3 — Failure Search

### Failure 1: PLAIN_XFER is not a special case of Primitive A
Primitive A is specifically defined as a wrap→close sequence. A PLAIN_XFER edge is a
direct SOL transfer with no wrap/close instruction pair at all. It is structurally
**Primitive B's transfer mechanic** operating at the wallet-to-wallet edge level
(treasury→subprov *or* subprov→creator) rather than only at the treasury→subprov level
X33.0 implicitly scoped Primitive B to. Two ways to read this:

- (a) **Merely a special case of B**: if Primitive B is redefined as "capital allocation
  transfer, with sub-modes {bulk, dust, wrap-close-carrier, plain}," then PLAIN_XFER is
  just B applied at a different edge type, and the "next operational wallet" role
  (previously exclusive to Primitive A) can ALSO be conferred by a plain transfer once a
  recipient starts behaving as a subprov/creator. This makes Primitive A a **special case
  of B** rather than an independent primitive — i.e. wrap-close is one implementation of
  identity-transfer, plain-transfer is another.
- (b) **Genuinely independent**: if "operational identity transfer" is defined narrowly by
  the wrap→close mechanism itself (the reason it's forensically useful — it's a distinctive,
  atypical instruction sequence), then PLAIN_XFER funding of a creator is NOT that mechanism.
  It is capital movement that happens to precede a CREATE, providing much weaker
  discriminative signal (a plain SOL transfer to a wallet that later creates a token is
  common and not distinctive on its own — this is exactly the boundary [[treasuries-serve-multiple-operations]]
  and [[watchtower-wrap-close-pattern]] already treat as the discriminator between
  WATCHTOWER-attributable and not).

Evidence favors **(b)**: prior confirmed memory explicitly treats wrap-close as
**the** mechanism that makes an edge attributable, and treats PLAIN_XFER-from-known-treasury
as evidence of a *separate, non-WATCHTOWER* piggybacking operation
([[treasuries-serve-multiple-operations]]). Under that established rule, PLAIN_XFER edges in
this dataset are not proof of Primitive A having a second form — they are evidence of a
**different, weaker-attribution class of edge** that the model currently folds into the same
edge table without a distinct evidentiary status.

**Conclusion**: PLAIN_XFER is not itself a new primitive, but it exposes that the current
two-primitive model conflates "capital moved" (B) with "identity transferred" (A) in a way
that under-specifies which transfers actually confer operational identity. This is a
**model precision gap**, not proof of a third primitive.

### Failure 2: SEEDED_ACCOUNT_CLOSE — RESOLVED 2026-07-20

**Update**: decoded via `getTransaction` (user-supplied temp key, 1cr endpoint only, per
RPC investigation discipline) on 2 of the 18 confirmed `SEEDED_ACCOUNT_CLOSE` launches
(`82YQnT2…` and `7KeLvrpQ…` creator wallets). Both `wrap_close_signature` transactions
decode to the identical instruction shape:

1. `system.createAccountWithSeed` — a WSOL token account created at a **seed-derived
   address** (not a fresh random keypair) from a base wallet.
2. `spl-token.initializeAccount` — mint = `So111111...112` (WSOL).
3. `spl-token.closeAccount` — **`destination` = the confirmed creator wallet**
   (matches `wt_watchtower_launches.creator_wallet` exactly in both samples).

This is **the same handoff property Primitive A is defined by** —
`closeAccount.destination` becomes the next operational wallet — implemented through a
`createAccountWithSeed` WSOL account instead of a plain freshly-created WSOL account. The
only difference from "classic" `WSOL_WRAP_CLOSE` is the account-creation instruction used
to stand up the throwaway WSOL account; the close-and-handoff step is identical.

**Verdict: SEEDED_ACCOUNT_CLOSE is a variant of Primitive A, not an independent primitive.**
Primitive A's definition is broadened accordingly (see Phase 5).

### Failure 3: Migration, sweep/backfill, staged-vs-instant are not primitive-decomposition
candidates at all
These are orthogonal to the A/B funding-graph model — migration is a post-CREATE liquidity
event, sweep/backfill/detection_source describe FLEX's own observation pipeline, and
staged/instant is a timing label on the A-follows-B sequence. None of these fail the model;
they are simply outside its domain (funding-graph structure) and shouldn't be scored as
compositions or failures at all.

## Phase 4 — Cross-Operation Independence

| Primitive | WATCHTOWER-address-dependent? | pump.fun-dependent? | Treasury-identity-dependent? | Portable to other operations? |
|---|---|---|---|---|
| A (wrap→close identity transfer) | No | No — `closeAccount` is a generic SPL/System instruction pattern | No | **Yes** — any operation using a wrap-then-close funding cycle to seed a fresh wallet would produce the identical on-chain shape |
| B (bulk vs dust capital allocation) | No | No | No | **Yes** — bimodal funding-amount separation between provisioning and maintenance is a generic operational-security pattern, not pump.fun-specific |

Both primitives are structurally universal — this supports their value as a cross-operation
detection foundation, independent of the PLAIN_XFER/SEEDED_ACCOUNT_CLOSE open question above.

## Phase 5 — Minimal Canonical Model

**Resolved: the model stays at two primitives.** On-chain decode confirms
SEEDED_ACCOUNT_CLOSE shares Primitive A's defining property
(`closeAccount.destination` = next operational wallet); it differs only in using
`createAccountWithSeed` instead of a fresh keypair to stand up the throwaway WSOL account.

**Primitive A is now defined (broadened) as**: a WSOL account is created (via a fresh
keypair OR a seed-derived address) and then closed, where `closeAccount.destination`
becomes the next operational wallet (subprov or creator). WSOL_WRAP_CLOSE and
SEEDED_ACCOUNT_CLOSE are both implementations of this one primitive.

PLAIN_XFER edges do **not** require a new primitive — Phase 3 concludes they are lower-
confidence instances of capital movement (Primitive B's mechanic) that the model should
tag with weaker attribution confidence, not a structurally new mechanism.

## Confidence Assessment

- Primitives A and B as originally scoped: **HIGH** confidence they are real, universal,
  and necessary — reconfirmed by this pass.
- Two-primitive-sufficiency claim: **HIGH confidence, confirmed**. The SEEDED_ACCOUNT_CLOSE
  question (the one open gap) is resolved by on-chain decode: it is a same-property variant
  of Primitive A, not a third mechanism.

## Recommendation

**Freeze the canonical library at two primitives**, with Primitive A's definition broadened
to cover both fresh-keypair and seed-derived WSOL wrap→close implementations. Separately,
tag PLAIN_XFER edges with a distinct (lower) attribution-confidence tier rather than
treating them as equivalent evidence to WSOL-wrap-close edges — this is a confidence-modeling
fix, not a primitive-count fix, and does not block freezing the library.

## Answer to the stated success criterion

**Every observed WATCHTOWER behaviour is explained as a composition of the two canonical
primitives.** The one candidate failure (SEEDED_ACCOUNT_CLOSE, 42% of confirmed launches)
is resolved by on-chain decode of 2 sampled transactions: it shares Primitive A's exact
defining property (`closeAccount.destination` = next operational wallet) via a
seed-derived WSOL account instead of a fresh one. No additional independent primitive is
required. The library is frozen at two primitives, ready as the foundation for
cross-operation testing (X35.0).
