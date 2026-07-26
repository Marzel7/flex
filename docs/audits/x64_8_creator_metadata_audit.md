# X64.8 Creator Metadata Audit

## Authoritative reusable sources

| Source | Live rows / creators | Existing facts used |
|---|---:|---|
| `token_analysis` | 1,337,111 / 260,021 `pf_ws_creator` values at audit time | creator, mint, CREATE signature/time, migration time, full observed launch count, first/last launch, prior launch gaps |
| `creator_tx_ledger` | 12,622 / 256 | earliest transaction/signature, latest transaction, transaction count |
| `creator_watch` | 1,331 / 1,331 | first observation, CREATE signature, monitoring confidence |
| `creator_state` | 7,078 / 7,078 | processed-signature count, last activity, cumulative SOL flow |
| `creator_funders` | 82,419 / 14,577 | persisted funder relationships; not identity by itself |
| `creator_outgoing_transfers` | 93,648 / 2,954 | persisted later transfers; incomplete coverage |
| `wt_creator_birth_launch` | 95 / 95 | funded-at, launched-at, birth delay and funding signature |
| `wt_watchtower_launches` | 43 / 43 | WATCHTOWER-scoped creator lifecycle and migration delay |
| `wt_walkback_queue` | 6,885 / 3,279 | creator, funder time/mechanism and lineage completion |
| `watchtower_token_attribution` | 1,246 / 599 | attribution only; not used to derive identity |
| `wt_ops_v2_creators` | 1,091 / 1,025 | operation membership and migration time |

`token_analysis.pf_ws_creator` remains the authoritative creator when present. `earliest_tx_creator` is only a row-level fallback, matching existing Discovery behavior. Existing indexes on both creator columns and `creator_tx_ledger.creator_pubkey` are reused.

## Coverage gaps

- No historical balance snapshot proves that a creator emptied after migration.
- `creator_tx_ledger.tx_type` currently contains transfer types, not dependable chain-wide SPL-history coverage.
- `creator_inbound_transfers` and `creator_portfolio` are empty.
- First observation is not automatically wallet genesis. The implementation retains the existing creator-birth evidence quality.

No duplicate persisted identity table or creator-history calculation was introduced.
