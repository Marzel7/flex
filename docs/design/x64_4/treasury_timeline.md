# X64.4 — Phase 2: Complete Chronological Event Timeline for `4231KLYi…`

Wallet: `4231KLYipwRTmFdQ6ZBa1H4Jf3EpfF62Gzg6DWHWvhPZ`. All 15
`watchtower_events` rows (both `wallet_address` and `related_wallet`
match), cross-referenced against `wt_active_subprov_sessions`,
`wt_temp_provision_candidates`, `wt_subprov_evidence`,
`wt_candidate_websocket_watches`, `wt_webhook_hits`,
`wt_subprov_sig_cursor`, `wt_subprov_sig_retry`. Chronological, `source`
column omitted (uniformly `ws_cascade` for every row).

| Timestamp (UTC) | Epoch | Event type | Wallet / related | Classification |
|---|---|---|---|---|
| 2026-07-16T11:51:55 | 1784202715 | (funding tx, recorded via `wt_active_subprov_sessions.funding_time`/`wt_webhook_hits`) | `9hGcxVHF…` → `4231KLYi…`, 1.078342247 SOL, WSOL_WRAP_CLOSE, sig `4od7LR2K…` | **Treasury activation** (this is the actual on-chain funding event that armed `4231KLYi…` as a subprov session — recorded 3 seconds before the WS cascade's own session-open event, consistent with tx-then-detection ordering) |
| 2026-07-16T12:36:48 | 1784205408 | `SUBPROV_SESSION_OPENED_WS` | `4231KLYi…` ← `9hGcxVHF…` | Provisioning (session open, treasury=`9hGcxVHF…`) |
| 2026-07-16T13:05:46 | 1784207146 | `SUBPROV_SESSION_OPENED_WS` | `7uJw44kv…` ← `4231KLYi…` | Monitoring (WS watch opened on a candidate `4231KLYi…` had just interacted with) |
| 2026-07-16T13:05:46 | 1784207146 | `SUBPROV_SESSION_OPENED_WS` | `E33jmbX8…` ← `4231KLYi…` | Monitoring |
| 2026-07-16T13:06:16 | 1784207176 | `SUBPROV_SESSION_OPENED_WS` | `4NWNq7bB…` ← `4231KLYi…` | Monitoring |
| 2026-07-16T13:06:16 | 1784207176 | `SUBPROV_SESSION_OPENED_WS` | `2x2PVxG9…` ← `4231KLYi…` | Monitoring |
| 2026-07-16T13:06:47 | 1784207207 | `SUBPROV_SESSION_OPENED_WS` | `82Yzf1hM…` ← `4231KLYi…` | Monitoring |
| 2026-07-16T13:06:47 | 1784207207 | `SELF_CLOSE_IGNORED` | `4231KLYi…` (self) | Other (defensive filter — `4231KLYi…` closing its own account ignored as non-evidence, per the system's self-close exclusion rule) |
| 2026-07-16T13:06:47 | 1784207207 | `SUBPROV_SESSION_OPENED_WS` | `56m5gW58…` ← `4231KLYi…` | Monitoring |
| 2026-07-16T13:07:17 | 1784207237 | `SELF_CLOSE_IGNORED` | `4231KLYi…` (self) | Other |
| 2026-07-16T13:36:28 | 1784208988 | `SUBPROV_SESSION_OPENED_WS` | `4231KLYi…` ← `4NWNq7bB…` | Provisioning (a second, later funding-adjacent session-open on `4231KLYi…` itself, per `wt_active_subprov_sessions` row `137399`: `9hGcxVHF…` → `4231KLYi…`, 1.060592717 SOL, `funding_time=1784207110`, `state=CONTINUING_OPERATION`) |
| 2026-07-16T13:36:58 | 1784209018 | `WRAP_CLOSE_FANOUT_DETECTED` | `82Yzf1hM…` ← `4231KLYi…` | **Wrap-close** (confirmed via `wt_subprov_evidence` id `80204`: `4231KLYi…` → creator-role wallet `82Yzf1hM…`, 806.9946 SOL — note: `wt_subprov_evidence.creator_wallet` here is `4231KLYi…` itself, i.e. this table's schema names the FUNDING SOURCE as `creator_wallet` in this row's context; see caveat below) |
| 2026-07-16T13:36:58 | 1784209018 | `WRAP_CLOSE_FANOUT_DETECTED` | `56m5gW58…` ← `4231KLYi…` | Wrap-close (`wt_subprov_evidence` id `80205`, 128.2345 SOL) |
| 2026-07-16T14:07:16 | 1784210836 | `CANDIDATE_WATCH_EXPIRED` | `4231KLYi…` (self) | Monitoring (session/candidate TTL expiry) |
| 2026-07-16T14:07:16 | 1784210836 | `CANDIDATE_WATCH_EXPIRED` | `4231KLYi…` (self) | Monitoring (duplicate/second expiry event, same second) |
| 2026-07-19T11:37:42 | 1784461062 | `SUBPROV_SESSION_OPENED_WS` | `4231KLYi…` ← `FkccGTEh…` | Provisioning — **reactivation**, per `wt_active_subprov_sessions` row `160226`: `treasury_wallet=DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK`, `funding_amount=1.0` SOL, `funding_time=2026-07-18T12:42:42` (1784378562), `open_reason=SUBPROV_REACTIVATED` |

## Important caveat on `wt_subprov_evidence`'s column naming

`wt_subprov_evidence.creator_wallet` is populated with `4231KLYi…` in
both matching rows — this column name is misleading in this context: per
the table's own schema (`subprov`, `wrap_close_sig`, `creator_wallet`,
`amount_sol`), this table is designed to record "subprov X funded
creator Y via wrap-close signature Z," and here `subprov=82Yzf1hM…`/
`56m5gW58…` while `creator_wallet=4231KLYi…` — meaning **in this specific
observation, `4231KLYi…` is recorded as the funding recipient
(creator-role), not the funding source**, for these two rows. This is
the opposite direction from `wt_active_subprov_sessions`'s framing
(where `4231KLYi…` is the `subprov_wallet`, receiving from
`treasury_wallet=9hGcxVHF…`). Both are consistent with a single coherent
picture: `4231KLYi…` received capital from `9hGcxVHF…`/`DchJqu…`
(upstream), then itself fanned that capital out toward `82Yzf1hM…` and
`56m5gW58…` (downstream) via wrap-close — i.e. `4231KLYi…` occupies the
**subprov** role in a `treasury → 4231KLYi (subprov) → further wallets`
chain, not the treasury role itself. See `treasury_downstream.md` for
the full role classification.

## Two distinct operational windows

1. **2026-07-16T11:51:55 → 2026-07-16T14:07:16** (~2h15m): the dense
   cluster — funding, session-open, five downstream monitoring opens, two
   wrap-close-fanout detections, two self-close-ignored events, two watch
   expiries. This is `4231KLYi…`'s primary observed activity burst.
- **2026-07-19T11:37:42** (~3 days later): a single reactivation event,
  funded by a *different* upstream treasury (`DchJqu…` rather than
  `9hGcxVHF…`), per `wt_active_subprov_sessions` row `160226`.

No other event exists for this wallet beyond these 15 rows / two windows,
in any table searched.
