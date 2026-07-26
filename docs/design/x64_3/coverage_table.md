# X64.3 — Phase 1: Located Existing WATCHTOWER Records

All queries read-only against `database/wt_ops_v2.db` (ops DB) and
`database/flex_complete_database.db` (live DB), 2026-07-21. Table column
names verified via `.schema` before querying (several tables use
non-obvious column names — e.g. `wt_treasury_review.creator_wallet`/
`token_mint`, not `creator`/`mint` — a mismatch that recurs across this
audit; see `lookup_path_analysis.md`).

## Case 1 — creator `7nxHcmxbaM4FC2SxdABWzEWhxtsSU8WX7JXGZdaAwizS`, mint
`HHcXBLbnuSWdYigNgiYDmPhuwwRzTCB73CmyJ8M7pump`, user-asserted treasury
`4231KLYipwRTmFdQ6ZBa1H4Jf3EpfF62Gzg6DWHWvhPZ` (user-supplied chain:
Creator ← `7WbkFQAbQt8toHLHFxjdYZp6XSHr4hTLjPZSCuDYkiDj` (PROV) ←
`HXMUxU94Zs2hGHW6r4odBiCTMxkzjV7YGJHAMYdTPFRY` (SUB PROV) ← `4231KLYi…`
(TREASURY))

| Table | Match found | Key | Detail |
|---|---|---|---|
| `watchtower_token_attribution` | **No** | — | zero rows for this mint/creator |
| `wt_confirmed_treasuries` | **No** | — | `4231KLYi…` is not present as a `treasury` row |
| `wt_discovered_subprovs` | Partial, unrelated | `subprov` PK | `4231KLYi…` appears **as `first_creator`** on two rows (`82Yzf1hM…`, `56m5gW58…`, both `treasury=9hGcxVHF…`) — a completely different lineage, not connected to this case's creator or mint |
| `wt_treasury_review` | **No** | — | zero rows for `4231KLYi…`, `HXMUxU94…`, or `7WbkFQAb…` |
| `wt_walkback_queue` | **Yes** | `mint` PK | 1 row: `mint=HHcXBLbnuSWdYigNgiYDmPhuwwRzTCB73CmyJ8M7pump`, `creator=7nxHcmxb…`, `subprov=NULL`, `treasury=NULL`, `funder_wallet=HXMUxU94…`, `funding_mechanism=WSOL_WRAP_CLOSE`, `intelligence_outcome=NO_ATTRIBUTION_FOUND`, `completed_at=1784554436` |
| `wt_candidate_scores` | Table not found in `wt_ops_v2.db` | — | see `lookup_path_analysis.md` — this table lives in the live DB (`flex_complete_database.db`), not the ops DB; checked there, zero rows for this creator |
| `wt_detected_creates` | Table not found in `wt_ops_v2.db` | — | same; checked in live DB, zero rows for this mint |
| `token_analysis` (live DB) | **Yes** | `mint` PK | row exists, `watchtower_related=0`, `watchtower_evidence_json` empty |
| `watchtower_events` (ops DB) | **Yes, but not connecting** | `wallet_address`/`related_wallet` | `4231KLYi…` and `HXMUxU94…` each appear in `watchtower_events` rows (see below), but **never together, and never referencing this creator or mint** |

**`watchtower_events` detail for `4231KLYi…`** (chronological): 15 rows,
`SUBPROV_SESSION_OPENED_WS`/`WRAP_CLOSE_FANOUT_DETECTED`/
`SELF_CLOSE_IGNORED`/`CANDIDATE_WATCH_EXPIRED` events from
2026-07-08T15:16:48 through 2026-07-19T18:57:42 (epoch
1784205408–1784461062), involving wallets `9hGcxVHFajR4…`,
`7uJw44kvyNjog…`, `E33jmbX8TQLDP…`, `4NWNq7bBaYv44…`, `2x2PVxG9oaSdH…`,
`82Yzf1hMDyLa1…`, `56m5gW58qe47D…`, `FkccGTEh6tJe7…` — **none of these
are `7nxHcmxb…`, `HHcXBLbn…`, `HXMUxU94…`, or `7WbkFQAb…`.**

**`HXMUxU94…` (the wallet this audit's own X64.2 data already recorded as
hop1 for this mint) appearing anywhere else**: checked across all 101
tables in `wt_ops_v2.db` by column-name pattern match — present only in
`wt_walkback_queue` (the same row above) and `wt_attribution_outcomes`
(the materialized `INSUFFICIENT_EVIDENCE`/`NO_ATTRIBUTION_FOUND` mirror
of that same row). **Zero rows anywhere connect `HXMUxU94…` to
`7WbkFQAb…` or to `4231KLYi…`.**

**Conclusion for Case 1**: the specific 3-hop chain the user supplied
(`Creator → 7WbkFQAb (PROV) → HXMUxU94 (SUB PROV) → 4231KLYi (TREASURY)`)
is **not present, in whole or in any part beyond the already-known
Creator↔HXMUxU94 hop1 leg, in any table in either database.** The
`7WbkFQAb…` intermediate wallet does not appear anywhere in either
database at all — zero rows, any table, any column, in this audit's
search. This chain, if correct, is not currently derivable from stored
evidence; it must rest on RPC/on-chain knowledge outside what has been
persisted (see Phase 2 and Phase 6).

## Case 2 — creator `71ftvekAkhanTdJJXdZRLtz7ShkXxdAxhmVmyv2YVSFS`, mint
`CvP9vVUCpoDuMd2jg5qvakFsk8Ht4qQwmKtZTMeUpump`, treasury
`5nTJWTSozPMWR7im9aBCeDE7y22K7ePW3TDToTpP9bGo`

| Table | Match found | Key | Detail |
|---|---|---|---|
| `watchtower_token_attribution` | **No** | — | zero rows for this mint |
| `wt_confirmed_treasuries` | **Yes** | `treasury` PK | `treasury=5nTJWTSozPMWR7im9aBCeDE7y22K7ePW3TDToTpP9bGo`, `method=human_review_recovery_safe`, `confidence=HIGH`, `confirmed_at=1784589622` (2026-07-20T23:20:22 UTC), `provenance=APPROVED_NO_WEBHOOK` |
| `wt_discovered_subprovs` | **Yes, but not this creator's hop1** | `subprov` PK | `subprov=4MEbMFxWsswFUyKqEsx4QbZkD7psYF2L8gkAErSBbTwD`, `treasury=5nTJWTSoz…`, `first_creator=4wJUb7uPiqw8doaarkJksDjaNqvEqJXzsDTrVzAvujYg` — **a different creator entirely**, not `71ftvekA…` |
| `wt_treasury_review` | **No** | — | zero rows for `5nTJWTSoz…` (already promoted past review, consistent with `wt_confirmed_treasuries` presence) |
| `wt_walkback_queue` | **Yes** | `mint` PK | 1 row: `mint=CvP9vVUCpoDuMd2jg5qvakFsk8Ht4qQwmKtZTMeUpump`, `creator=71ftvekA…`, `subprov=NULL`, `treasury=NULL`, `funder_wallet=DCyQJVfAL37…`, `funding_mechanism=WSOL_WRAP_CLOSE`, `intelligence_outcome=NO_ATTRIBUTION_FOUND` — **correction**: an earlier draft of this audit stated `subprov=DCyQJVfAL37…`/`outcome=LINEAGE_GAP` for this row, reflecting the X64 code fix's *test-fixture* output, not this live row's actual current state. The X64 fix only changes behavior for *future* walkback runs; per X64's own explicit "no historical rows modified" constraint, this already-`complete` row was never reprocessed and still shows its original `NO_ATTRIBUTION_FOUND`/`subprov=NULL` values. Verified directly: `SELECT status,intelligence_outcome,subprov,treasury FROM wt_walkback_queue WHERE mint='CvP9vVUCpoDuMd2jg5qvakFsk8Ht4qQwmKtZTMeUpump'` → `complete, NO_ATTRIBUTION_FOUND, NULL, NULL`, `updated_at` unchanged since original completion (`1784558781`). |
| `token_analysis` (live DB) | **Yes** | `mint` PK | row exists, `watchtower_related=0` |
| `watchtower_events` (ops DB) | **Yes, but not connecting** | `wallet_address`/`related_wallet` | `5nTJWTSoz…` has 20+ rows from 2026-07-20T23:21:42 onward — `TREASURY_WEBSOCKET_OPENED` self-events, then `SUBPROV_SESSION_OPENED_WS`/`CAPITAL_RELOAD`/`CDC_REGISTERED`/`TEMP_CANDIDATE_PROMOTED` events naming subprovs `4MEbMFxWs…`, `5xBzetUS2Bs2…`, `G2fr9ikcVgtm…` — **none of these is `DCyQJVfAL37…` (the hop1 wallet already on record for this creator), and no event references `71ftvekA…` or `CvP9vVUC…`** |

**Conclusion for Case 2**: `5nTJWTSoz…` is genuinely, verifiably a
**confirmed treasury** as of 2026-07-20T23:20:22 — this part of the user's
claim is fully supported by stored evidence. However, **there is no
stored evidence anywhere in either database connecting this treasury to
creator `71ftvekA…`, mint `CvP9vVUC…`, or disposable wallet
`DCyQJVfAL37…`.** The treasury's own recorded downstream activity
(`watchtower_events`) names three different subprov wallets, none of
which is `DCyQJVfAL37…`. If this creator/mint genuinely traces to this
treasury, that link is not currently recorded in the database — it would
require either an unresolved hop2 walk (Phase 4/6) or an out-of-band
confirmation not yet written back into any table this audit could find.
