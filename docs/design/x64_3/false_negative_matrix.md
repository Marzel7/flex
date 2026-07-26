# X64.3 — Phase 3/4/5: Coverage Matrix, Full Recheck, False-Negative Count

## Phase 3 — Coverage matrix for the two known-confirmed cases

| Lookup | Case 1 (`7nxHcmxb…`/`HHcXBLbn…`) performed by X64.2? | Case 2 (`71ftvekA…`/`CvP9vVUC…`) performed by X64.2? |
|---|---|---|
| Mint | Yes (`watchtower_token_attribution.mint` join) | Yes (same) |
| Creator | No | No |
| Disposable wallet (hop1) | Yes (`wt_discovered_subprovs.subprov` join) | Yes (same) |
| Treasury | Yes, but wrong direction (see below) | Yes, but wrong direction |
| Attribution table | Yes (`watchtower_token_attribution`) | Yes (same) |
| Candidate table (`wt_candidate_scores`) | No — table not in `wt_ops_v2.db` at all | No — same |

**Explaining every "No"**:
- **Creator**: X64.2's four joins never included a direct `creator`-keyed
  lookup against any table (not `watchtower_events.wallet_address`, not
  `wt_creator_launches.creator_wallet`, not `wt_candidate_scores`). Both
  creators were never independently searched for by their own address —
  only their launch's `mint` and `funder_wallet` were checked.
- **Treasury (direction)**: the `wt_confirmed_treasuries` join was run,
  but only as "is this launch's `funder_wallet` itself a `treasury`
  row" — never as "does any ALREADY-confirmed treasury's own recorded
  activity include this launch's creator or hop1 wallet." The forward
  check correctly returned "no" (a disposable subprov is not itself a
  treasury); the reverse check that would be needed to catch Case 2 was
  never run.
- **Candidate table**: `wt_candidate_scores` and `wt_detected_creates`
  live exclusively in `database/flex_complete_database.db`. X64.2's
  entire "Existing Infrastructure Lookup" phase ran only against
  `database/wt_ops_v2.db` — this table was never in scope, not filtered
  out or joined incorrectly, simply never queried at all.

## Phase 4 — Full 18-launch recheck

Re-ran the attribution lookup for all 18 launches (exact mint list from
X64.1/X64.2) against every table located in Phase 1, including
`watchtower_events`, `wt_candidate_scores`, `wt_detected_creates`,
`wt_creator_launches`, and both databases. No RPC used; all from stored
data.

| Mint | Confirmed WT | Prev. attributed | Treasury known | Creator known (any table) | Disposable known | Status |
|---|---|---|---|---|---|---|
| `HHcXBLbnuSWdYigNgiYDmPhuwwRzTCB73CmyJ8M7pump` | **User-asserted, not DB-confirmed** | No | No (in DB) | No | Yes (hop1 only) | See note ¹ |
| `CvP9vVUCpoDuMd2jg5qvakFsk8Ht4qQwmKtZTMeUpump` | **User-asserted, not DB-confirmed** | No | Treasury itself exists in DB, but not linked to this mint | No | Yes (hop1 only) | See note ² |
| `AGumPoj6jUXMsJv1s9iuXa7uiWj18gBSXuM4bLVQpump` | No | No | No | No | Yes (recurring, same creator as `3uJNC2pJ…`) | Unresolved |
| `3uJNC2pJESYdGBPfrxnwyk7ULXjqqhsXoxu49wp2pump` | No | No | No | No | Yes | Unresolved |
| `61BtvdXLEWT52BBsGh6qrsuwoGUcE3cuuS3EC8Mjpump` | No | No | No | No | Yes | Unresolved |
| `A9TJYUgpN4krvqjTAqHEoqe3KLjEm4tSgp957ykcpump` | No | No | No | No | Yes | Unresolved |
| `51bLwxUw4993Be342Z2BNhAYc7ZmQ1T4GWP8bcYNnHtu` | No | No | No | No | Yes | Unresolved |
| `F74webejVVTfPxXxGvSSpfu6vwhES5FkMqH5irP1pump` | No | No | No | No | Yes | Unresolved |
| `F8dWKhaKAbP91xwGKyQr11sGarUR5MairFKfcC8vpump` | No | No | No | No | Yes | Unresolved |
| `9NqjcpGCBc4vZ57gwjpQjU8J9NqPUKo21jwmWDQZpump` | No | No | No | No | Yes | Unresolved |
| `DxRJpsVNs8NLwSyjaz3zVFViSRWGgQQxKT1wwCy5pump` | No | No | No | No | Yes | Unresolved |
| `EXn2aNztPQBQNrdKCg3HnAtuxFZ6eEnfuMJD2y7tpump` | No | No | No | No | Yes | Unresolved |
| `9rvQ2wcqU5uRvS97JbdwHmUokiCV796T3SGoREUgpump` | No | No | No | No | Yes | Unresolved |
| `6UXXyzvnysCjqz2pDpgZmyLmrERCTEY4kPQ6dQGapump` | No | No | No | No | Yes | Unresolved |
| `8D9ncyi7Jd8ozajg4aewiDMaPR42czdZCSMf5nWDeBZW` | No | No | No | No | Yes | Unresolved |
| `8wpoG9gbG7mz2Fy75oXqd6i6ytto6FbX4UMJfVgApump` | No | No | No | No | Yes | Unresolved |
| `Q3WvTW8drUVbQLkRr7m9LBTYJoJrmftJQgUsXwQpump` | No | No | No | No | Yes | Unresolved |
| `HTog7L8RFmgvza1hGg6hWnQncxeViedNyy6zPUwNpump` | No | No | No | No | Yes (anomalous timing, see X64.2) | Unresolved, flagged anomaly |

¹ **Case 1**: the user-supplied treasury `4231KLYi…` and intermediate
wallet `7WbkFQAb…` are **not present in any queryable table** connecting
them to this mint/creator (confirmed exhaustively in `coverage_table.md`
Phase 1). This audit cannot mark this row "confirmed WATCHTOWER" from
stored evidence — doing so would mean asserting a database fact that does
not exist. The user's assertion may well be correct (via RPC/on-chain
verification not reflected in these tables), but it is **not currently a
database-confirmed fact**, and this audit's job is to report what the
database shows, not to accept an external claim as if it were a stored
record.

² **Case 2**: `5nTJWTSoz…` **is** a genuinely `wt_confirmed_treasuries`
row — this part is a real database fact, independently verified. But
**no stored row anywhere links it to `71ftvekA…`, `CvP9vVUC…`, or
`DCyQJVfAL37…`** — its own recorded downstream wallets
(`4MEbMFxWs…`, `5xBzetUS2Bs2…`, `G2fr9ikcVgtm…`) do not include this
creator's disposable wallet. Same conclusion as Case 1: this audit cannot
mark this row "confirmed WATCHTOWER" from stored evidence alone.

## Phase 5 — Quantifying false negatives

**Original X64.2**: Confirmed WT = **0** (of 18).

**Corrected, using every located attribution source (Phase 1-4)**:
Confirmed WT (from stored database evidence alone) = **still 0** of 18.

**False negatives introduced by X64.2's incomplete lookup scope**:
**0, as measured by database evidence.** The scope gap identified in
`lookup_path_analysis.md` (never touching `flex_complete_database.db`,
`watchtower_events`, or a reverse treasury→downstream join) is real and
should be fixed — but running the corrected, wider lookup against all 18
launches **did not surface any additional stored connections to a
confirmed treasury**, for these two cases or any of the other 16. Both
of the user's asserted confirmations rely on information (the
`7WbkFQAb…` hop for Case 1; any `DCyQJVfAL37…`↔`5nTJWTSoz…` link for
Case 2) that is not recorded in the database at all — no lookup, however
complete, run against these two databases could have found it, because
it isn't there to find.

**This is an important distinction the audit must be explicit about**:
"the lookup was incomplete" (true, and now documented) is a different
finding from "the lookup, if complete, would have found these two
confirmations" (not supported — even the corrected, exhaustive lookup in
this audit did not find them). Both statements matter and neither should
be dropped in favor of the other.

List of every false negative recovered by the corrected lookup: **none**.
