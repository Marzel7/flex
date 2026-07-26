# X65.2 — Phase 6: Historical Recoverability

Classification only — **no recovery performed**, per the task's
explicit constraint. This assesses whether each launch's missing
CREATE/lineage evidence *could* be recovered later, not whether it
should be, and does not touch any table.

## Classification definitions applied

- **RECOVERABLE**: the original evidence (or an equivalent independent
  derivation of it) still exists somewhere and could be restored with a
  bounded, well-defined operation.
- **PARTIALLY_RECOVERABLE**: some but not all of the missing evidence
  chain can be restored (e.g., the CREATE signature but not the
  downstream funding lineage, or vice versa).
- **NOT_RECOVERABLE**: no path exists to recover the missing evidence
  from any currently-accessible source.

## Per-launch classification

| Mint | create_tx_signature recoverable? | Funding lineage recoverable? | Classification | Basis |
|---|---|---|---|---|
| 9Mn2t7yX2TmSSM... | **Yes** — `wt_walkback_queue.create_anchor_signature` already holds an independently-recovered, `VALID`-audited signature for this exact mint, just never propagated to `token_analysis`/`wt_create_event_ledger` | Depends on funder wallet's own upstream lineage, which is separately un-indexed (Phase 3) — recovering the signature alone would not by itself resolve funding origin | **PARTIALLY_RECOVERABLE** | Direct persisted evidence already exists in a sibling table |
| CmoCuZ9J2YT1QH... | Likely — a fresh RPC lookup by mint (`getSignaturesForAddress`/`getTransaction` against the mint's create instruction) could re-derive the original CREATE signature, since the mint address itself is permanent on-chain | Same as above — funder wallet has no indexed lineage regardless of CREATE recovery | **PARTIALLY_RECOVERABLE** | Requires new RPC investigative work (out of scope for this read-only task; per project's own standing RPC-investigation-discipline memory, would need a user-supplied temp key) |
| HHcXBLbnuSWdYi... | Likely, same basis | Same as above | **PARTIALLY_RECOVERABLE** | Same basis |
| EQZfBpWpQc5BEU... | Likely, same basis | Same as above | **PARTIALLY_RECOVERABLE** | Same basis |
| DpTtRHY6PSuxxJ... | Likely, same basis | Same as above | **PARTIALLY_RECOVERABLE** | Same basis |
| CvP9vVUCpoDuMd... | Likely, same basis | Same as above | **PARTIALLY_RECOVERABLE** | Same basis |
| 4WfoYERYFw3AQW... | Likely, same basis | Same as above | **PARTIALLY_RECOVERABLE** | Same basis |
| EDNvjVDjKVfRsq... | Likely, same basis | Same as above | **PARTIALLY_RECOVERABLE** | Same basis |
| c5Zye8yFd1AGrS... | Likely, same basis | Same as above | **PARTIALLY_RECOVERABLE** | Same basis |
| FzNgpR11RYACas... | Likely, same basis | Same as above | **PARTIALLY_RECOVERABLE** | Same basis |
| B3Fq8SqBtsxsWw... | Likely, same basis (mint is permanent regardless of log retention) | Same as above | **PARTIALLY_RECOVERABLE** | Same basis; log-retention gap does not affect on-chain recoverability |
| 71TKvknpvwRcjd... | Likely, same basis | Same as above | **PARTIALLY_RECOVERABLE** | Same basis |

## Why every launch lands at PARTIALLY_RECOVERABLE, not RECOVERABLE or NOT_RECOVERABLE

- **Not fully RECOVERABLE**: recovering `create_tx_signature` alone
  (whether from the one already-sitting `wt_walkback_queue` value or
  via a fresh RPC lookup) does not, by itself, resolve Funding Origin
  or Operation Attribution — the actual blocker identified in Phase 3
  is that the creator's *funder wallet* has zero presence in any
  sub-provisioner/treasury lineage table, which is an entirely
  separate, independent gap that a recovered CREATE signature does not
  close. Marking these fully "RECOVERABLE" would overstate what
  restoring the signature alone would accomplish.
- **Not NOT_RECOVERABLE**: the underlying on-chain data (the mint's
  CREATE transaction) is permanent and immutable on Solana — there is
  no evidence any of these 12 launches involve a pruned/unavailable
  RPC history window (all within the last 6 days, well within typical
  RPC provider retention), and one launch (`9Mn2t7yX...`) already
  demonstrates a *sibling system* successfully recovered exactly this
  evidence for an identical scenario — proving the recovery mechanism
  is not merely hypothetical.

## Summary

| Classification | Count |
|---|---|
| RECOVERABLE | 0 |
| PARTIALLY_RECOVERABLE | 12 |
| NOT_RECOVERABLE | 0 |

No recovery action was taken for any of the 12 launches in this phase
or at any point in this investigation.
