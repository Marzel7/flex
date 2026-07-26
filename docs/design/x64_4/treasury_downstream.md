# X64.4 — Phase 4: Downstream Infrastructure of `4231KLYi…`

Every wallet observed interacting with `4231KLYi…` in either direction,
per the 15 `watchtower_events` rows and the corroborating tables in
`treasury_timeline.md`.

| Wallet | First seen (UTC) | Last seen (UTC) | Role (relative to `4231KLYi…`) | Observations | Classification |
|---|---|---|---|---|---|
| `9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4` | 2026-07-16T11:51:55 | 2026-07-16T13:36:28 | **Upstream funder** (`treasury_wallet` on both `wt_active_subprov_sessions` rows for `4231KLYi…`'s first window) | 2 (funding sessions) + 1 (`wt_webhook_hits`) | **Treasury** — independently confirmed in `wt_confirmed_treasuries`, `confirmed_at=1782144539` (2026-06-22T16:08:59), `method=subprov_funder_trace`, `confidence=MANUAL` |
| `DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK` | 2026-07-19T11:37:42 (this wallet's relation to `4231KLYi…`; the wallet itself is far older, see caveat) | same | **Upstream funder** (`treasury_wallet` on the reactivation session) | 1 | **Treasury** — independently confirmed, `confirmed_at=1781164069` (2026-06-11T07:47:49), `method=3SIGNAL`, `confidence=CONFIRMED`, `provenance=CONFIRMED_SEED`. Per prior project memory, `DchJqu…` is a recurring wallet already linked to a known Hello-payment operator cluster — independent corroboration this is pre-existing, previously-catalogued infrastructure, not something new. |
| `82Yzf1hMDyLa1Z8uADcxzMHxmmGedwKj6viUReKfTeKJ` | 2026-07-16T13:06:47 | 2026-07-16T13:36:58 | **Downstream** — session opened by `4231KLYi…`, then wrap-close-fanout target (806.9946 SOL) | 3 (session, fanout, `wt_candidate_websocket_watches`) | **Unknown / provisional** — present in `wt_discovered_subprovs` as `first_creator=4231KLYi…`, `treasury=9hGcxVHF…`, `state=PROVISIONAL_SUBPROV`, `confidence=0.28` — i.e. this system's own discovery layer already treats `82Yzf1hM…` as a *creator*-role wallet funded by `4231KLYi…`, not as further subprov infrastructure |
| `56m5gW58qe47DxgqyA9two6KePvtme2KZzcasDmx4V7u` | 2026-07-16T13:06:47 | 2026-07-16T13:36:58 | **Downstream** — same pattern as `82Yzf1hM…` (session, fanout, 128.2345 SOL) | 3 | **Unknown / provisional** — also in `wt_discovered_subprovs`, `first_creator=4231KLYi…`, `treasury=9hGcxVHF…`, `state=PROVISIONAL_SUBPROV`, `confidence=0.28` |
| `7uJw44kvyNjog514Zq2dz2tQ6w45By1ju5WZaZWrpF8e` | 2026-07-16T13:05:46 | same | Downstream — WS monitoring session opened, no further activity recorded | 1 | **Unknown** — no fanout/wrap-close/discovery-table entry beyond the initial monitor open |
| `E33jmbX8TQLDP2m1VUsdfyzQCWZMBXhtB6wzgqXKhe44` | 2026-07-16T13:05:46 | same | Downstream — same as above | 1 | **Unknown** |
| `4NWNq7bBaYv44xDFe5e6YY8Hwxe7xipJv99s4DhFsXVH` | 2026-07-16T13:06:16 | 2026-07-16T13:36:28 | Downstream monitor open, then itself appears as the counterparty on `4231KLYi…`'s second `SUBPROV_SESSION_OPENED_WS` event (`4231KLYi… ← 4NWNq7bB…`) | 2 | **Unknown** — the second event's directionality (`4NWNq7bB…` as `related_wallet` on a session opened BY `4231KLYi…`) does not by itself establish a role beyond "observed counterparty" |
| `2x2PVxG9oaSdHFPcURR8rN49F2r8721NK4M6VE4QUY3s` | 2026-07-16T13:06:16 | same | Downstream monitor open | 1 | **Unknown** |
| `FkccGTEh6tJe7FGg3hk1dMsz67FDKr5aMh6CWYnTu1f8` | 2026-07-19T11:37:42 | same | Downstream — counterparty on the reactivation session-open event | 1 | **Unknown** |

## Summary counts

- **1 treasury pair** confirmed upstream of `4231KLYi…` across its two
  activity windows: `9hGcxVHF…` (window 1) and `DchJqu…` (window 2) —
  **both already `wt_confirmed_treasuries` rows, both confirmed well
  before `4231KLYi…`'s own first observed activity** (see
  `treasury_age.md`).
- **2 downstream wallets** (`82Yzf1hM…`, `56m5gW58…`) show a genuine
  wrap-close fanout FROM `4231KLYi…`, and are independently catalogued in
  `wt_discovered_subprovs` as `PROVISIONAL_SUBPROV` with `4231KLYi…`
  recorded as their `first_creator` — i.e. this system's own prior
  discovery layer already models `4231KLYi…` as the funding source one
  level above these two, consistent with a subprov role, not a treasury
  role.
- **5 further wallets** (`7uJw44kv…`, `E33jmbX8…`, `4NWNq7bB…`,
  `2x2PVxG9…`, `FkccGTEh…`) appear only as WS-monitoring session-open
  counterparties, with no fanout, discovery-table, or confirmed-treasury
  evidence beyond that single observation — genuinely unclassifiable from
  stored data, not evidence of anything further.

## Role determination for `4231KLYi…` itself

Across every table that records a role for this wallet
(`wt_active_subprov_sessions.subprov_wallet`,
`wt_temp_provision_candidates.wallet` with `treasury=9hGcxVHF…`,
`wt_subprov_sig_cursor.subprov_wallet`,
`wt_subprov_sig_retry.subprov_wallet`), **`4231KLYi…` is consistently
recorded in the SUBPROV role, never as a `treasury` column value or a
`wt_confirmed_treasuries` row.** This is a direct, five-table-consistent
contradiction of treating it as "the treasury" in isolation — the
system's own existing data model already places it one hop downstream of
two separately-confirmed treasuries.
