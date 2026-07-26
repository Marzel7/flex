# X64.8 Creator Identity Design

Every launch resolves to exactly one category in this precedence order:

1. **Unknown**: creator unresolved or no creator launch exists in `token_analysis`.
2. **Fresh Creator**: exactly one observed launch and creator age at CREATE is 0-86,400 seconds.
3. **Single-use Creator**: exactly one observed launch, but fresh-wallet timing is absent or older than 24 hours.
4. **Dormant / Reactivated**: multiple observed launches and the prior-launch gap exceeds 30 days.
5. **Returning Creator**: multiple launches and the prior-launch gap exceeds 7 days but not 30 days.
6. **Repeat Creator**: multiple launches without a returning/dormant gap, including a multi-launch history whose current launch has no comparable prior timestamp.

The boundaries reuse the project's existing 24-hour birth threshold and existing 7-day/30-day Discovery windows. Timestamps are normalized through the existing dual ISO/epoch parser. Missing age never becomes Fresh; missing gaps never become Returning or Dormant. Future launches establish repeat identity but never rewrite historical timestamps or attribution.

SQL uses indexed `pf_ws_creator IN (...)`, then indexed `earliest_tx_creator IN (...)` only where `pf_ws_creator` is empty. Ledger metrics use `creator_pubkey IN (...) GROUP BY creator_pubkey`. Queries are chunked to remain below SQLite parameter limits.
