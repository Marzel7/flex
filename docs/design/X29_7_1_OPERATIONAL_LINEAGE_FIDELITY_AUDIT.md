# X29.7.1 — Operational Lineage Fidelity Audit

Investigation only, per the brief. No code changed. Every claim below is traced against the actual on-chain transaction (fetched live via `getTransaction`/`getSignaturesForAddress`, 1cr each, user-configured Helius key, cached to disk) and the live `wt_ops_v2.db`, not inferred from table/column names.

## 1. The exact on-chain path

The single transaction connecting all three wallets is `mhLsAQCC8DPWHMb3i9z4S933SBLPEEePcYLKVNoEZfxUKymZqXvPn35Ef43vMMymQgpc1gZo5Z28cQUHg35zWkH` (slot 432893542, blockTime 1784048632 — 1 second before the launch's `create_time`). Its instructions, in order:

| # | Program | Type | Detail |
|---|---|---|---|
| 0 | System | `transfer` | **ANen → HZB2**, 1,112,039,280 lamports (1.112 SOL) |
| 1 | Associated Token Account | `createIdempotent` | HZB2 creates its own WSOL ATA (`FrYikCfUM6MEC2FVbm6Abii3E3fyweQZMfeSbWueuoKn`) |
| 2 | System | `transfer` | **HZB2 → FrYikC... (its own WSOL ATA)**, 1,110,000,000 lamports |
| 3 | SPL Token | `syncNative` | wraps the SOL sitting in the ATA into WSOL balance |
| 4 | SPL Token | `closeAccount` | **destination = HTR9U7...** — closes the WSOL ATA, sending its lamports to the creator |

Both `ANen` and `HZB2` are **transaction signers** (confirmed from `accountKeys[].signer`). `FrYikC...` is a WSOL associated-token-account, never a signer, never independently funded — pure plumbing for the wrap/unwrap. `HTR9U7` is the `closeAccount` destination, never a signer in this transaction (consistent with it being the passive recipient/creator).

**HZB2's exact role: it is the operational SOL custody + WSOL-wrap wallet for this single provisioning cycle.** It received capital directly from the subprovider, funded and owned its own WSOL ATA, and directed the final unwrap to the creator. This is a real, meaningful funding hop — not a system account, not a stray signer, not the WSOL token account itself (that's `FrYikC...`, a distinct address). Calling it "temporary plumbing" would be wrong: it holds and moves the actual SOL. Calling it a fourth persistent *identity* on par with a subprovider would also be wrong, per the finding below.

**HZB2's lifecycle**: `getSignaturesForAddress(HZB2)` returns **exactly one transaction, ever** — this transaction. It was created, funded, used to wrap/route capital, and never touched again. It is genuinely single-use and ephemeral by construction, consistent with a disposable per-launch provisioning wallet, not a wallet an operator reuses.

## 2. Where HZB2 appears (and disappears) in persisted data

| Table | HZB2 present? |
|---|---|
| `wt_watchtower_launches` | **No.** The row for this launch stores `subprov_wallet=ANen`, `creator_wallet=HTR9U7`, `wrap_close_signature=mhLsAQ...` (the transaction itself is referenced), but no column stores the intermediate wrap-wallet address. |
| `wt_provisioning_edges` | **No.** Only one edge exists for this launch: `edge_type=SUBPROV_TO_CREATOR, from_wallet=ANen, to_wallet=HTR9U7`. |
| Any candidate/watch/detected-create table | Not checked individually beyond the two above — the detection pipeline's own extraction method (`creator_extraction_method=CLOSE_ACCOUNT_DESTINATION`) confirms the code deliberately reads `closeAccount.destination` (HTR9U7) as the creator and never separately records the closer's owner (HZB2) anywhere.

**HZB2 is not "discarded by a bug" — it is never captured at all.** The detection pipeline's design (per its own `creator_extraction_method` field) only extracts two facts from the wrap-close transaction: who funded the wrap wallet (recorded as `subprov_wallet`) and who received the close (recorded as `creator_wallet`). The wrap wallet's own address was never a field the schema was built to hold.

## 3. What the persisted edge actually means

`ANen → HTR9U7, edge_type=SUBPROV_TO_CREATOR` is a **compressed two-hop relationship**, not a direct on-chain transfer. The real on-chain shape is `ANen → HZB2 → (WSOL wrap/unwrap) → HTR9U7` within one atomic transaction. The edge is accurate in the sense that ANen's capital genuinely reached HTR9U7 in one transaction (so "who funded whom, transitively, in this atomic event" is correctly captured) — but it is **not** a record of a direct wallet-to-wallet SOL transfer, and the intermediate signer/custody wallet (HZB2) is silently removed from the graph. This is a deliberate compression baked into the wrap-close mechanism's extraction logic, not an accident of this one transaction.

## 4. Fan-out validation — the persisted count is wrong

`fan_out_count=1` / `historical_launches=1` (from `wt_watchtower_launches`/`wt_provisioning_edges`) is **not** an accurate corpus fact. On-chain evidence directly contradicts it:

- `getSignaturesForAddress(ANen)` returns **at least 350 transactions** within a roughly 144-second window (blockTime 1784050980–1784051124), immediately after this launch's wrap-close (1784048632). Paging stopped at 350 for RPC-budget discipline, not because history was exhausted — the true count is at least this high.
- Sampling 6 transactions across that range found: one full wrap-close pattern (transfer→createIdempotent→transfer→syncNative→closeAccount — identical structure to the HTR9U7 transaction) and five plain `system:transfer` instructions, including **ANen sending SOL to a new wallet (`Ezvu1xhHDKuWVjeN8VM3obLkQEhK7SBqdu5JFgnK6NR6`)** that looks exactly like the ANen→HZB2 seeding step.
- `Ezvu1x...` does **not** appear anywhere in `wt_provisioning_edges` or `wt_watchtower_launches` — confirming this is a real downstream branch the persistence layer never captured.

**Why the UI reports 1**: `_fan_out_count()` (added in X29.7) counts `DISTINCT to_wallet` from `wt_provisioning_edges WHERE edge_type='SUBPROV_TO_CREATOR'` unioned with `DISTINCT creator_wallet` from `wt_watchtower_launches WHERE subprov_wallet=ANen`. Both sources only have the one HTR9U7 row for this subprovider — the counting logic itself is correct arithmetic over its inputs; **the inputs are incomplete**. This is not a counting-semantics bug, a time-window bug, or a UI-presentation bug — it is a **detection/persistence gap**: the live cascade evidently has not (yet, or at all) recorded every wrap-close cycle this subprovider has executed, only the one that happened to be caught. Whether that's an incomplete backfill (older activity never walked) or a live-detection miss (the WS listener didn't catch every wrap-close in the burst) cannot be determined from this trace alone — both are plausible given the burst's density (350+ tx in ~144s is a rate that could plausibly outpace a single-threaded WS consumer) — but either way, the deficiency sits upstream of `operational_lineage.py`'s counting logic, not within it.

## 5. Treasury-independent discovery test

Reasoning with the treasury and canonical WATCHTOWER attribution deliberately set aside, using only ANen/HZB2/HTR9U7 and the one sibling branch found (`Ezvu1x`):

- **Would the persisted graph form an operation candidate?** Weakly. The only persisted anchor is `ANen` itself (as `subprov_wallet`), with exactly one downstream creator recorded. A single subprov→creator edge is indistinguishable from a one-off, and nothing in the persisted data currently proves ANen is a *repeating* subprovider.
- **What common anchor would group its launches?** `ANen`'s own address is the only anchor currently available in `wt_provisioning_edges`/`wt_watchtower_launches` — but since only 1 of its likely-dozens-or-more funding actions is persisted, that anchor cannot yet group more than the one launch it already has.
- **How many creators/downstream wallets would be grouped?** Persisted: 1 (HTR9U7). On-chain evidence (this trace alone, not exhaustive): at least 2 downstream wallets in the same burst (HZB2 and Ezvu1x), and the true number is almost certainly much higher given 350+ transactions in the window.
- **Would ANen surface as a repeating subprovider?** Not from the current persisted data — `fan_out_count=1` reads as a one-off funder, not a repeating subprovider, which is the opposite of what the on-chain evidence shows.
- **Does the database currently contain enough evidence to recognise this operation without its treasury?** **No.** The treasury (`9hGcx...`) is the only reason this launch is currently attributable to a broader operation at all (via `operation_identity.py`'s treasury-mesh resolver). Strip the treasury column away and this subprovider looks like an isolated, one-time funder — exactly the opposite of the true on-chain pattern of a high-frequency, repeatedly-active wrap-close subprovider.
- **What evidence is missing, exactly?** Persisted records for the other ~349+ transactions ANen executed in this same burst — specifically, for however many of those are wrap-close cycles like the sampled one, the resulting `(subprov=ANen, creator=<destination>)` edges and `wt_watchtower_launches` rows. Without backfilling (or re-detecting) those, `ANen`'s true fan-out and repeat-subprovider signature are invisible to the persisted graph.

## Summary answers (per the brief's exact deliverable list)

- **On-chain path**: `ANen → HZB2 (signer, custody+wrap wallet) → WSOL ATA (FrYikC..., plumbing only) → closeAccount destination HTR9U7`, one atomic transaction.
- **Persisted path**: `ANen → HTR9U7` (`SUBPROV_TO_CREATOR`), HZB2 entirely absent.
- **Compression**: the HZB2 hop and the WSOL ATA hop are both dropped; the persisted edge represents "capital reached this creator via this subprovider in one transaction," not a literal wallet-to-wallet transfer.
- **HZB2's correct role**: a real, signer-controlled, single-use SOL-custody-and-WSOL-wrap wallet for one provisioning cycle — not a system account, not a bare token account (that's a separate address), not permanent operational infrastructure. It is genuine but *ephemeral* operational plumbing, not incidental noise.
- **True observed fan-out for ANen**: at minimum several (confirmed at least one additional downstream wallet, `Ezvu1x`, from a 6-transaction sample), very likely dozens, out of 350+ transactions in a ~144-second burst — the persisted `fan_out_count=1` significantly undercounts reality.
- **Why the UI reports 1**: correct arithmetic over incomplete inputs — `wt_provisioning_edges`/`wt_watchtower_launches` only contain the one launch's worth of evidence for this subprovider.
- **Could WATCHTOWER currently be surfaced as the same operation without knowing its treasury?** No — the treasury column is currently load-bearing for operation identity; the subprovider-level evidence alone is too sparse.
- **Where the deficiency sits**: **detection/persistence**, not role derivation or counting semantics. `operational_lineage.py`'s counting logic (built in X29.7) is arithmetically correct over what exists; the gap is that the live cascade/backfill has not captured the full set of this subprovider's wrap-close cycles. X29.7's UI presentation of "fan_out_count" is honest about what it's counting — it is the underlying corpus that is incomplete for this subprovider.

## What X29.7 should be understood as, going forward

X29.7 is validated as a correct **presentation and navigation** milestone: given whatever lineage evidence exists, it renders it accurately, generalizes to variable depth, and never fabricates a node. This audit shows that milestone should **not** yet be read as proof that Discovery can identify an operation independent of its treasury — the underlying provisioning-edge corpus needs a completeness pass (backfill or improved live capture of high-frequency subprovider bursts) before fan-out counts and repeat-subprovider signals can be trusted as ground truth rather than as "at least this many, possibly far more."
