# X64.3 — Phase 2: Reconstructing X64.2's Actual Lookup Logic

## What X64.2 actually queried

Re-reading `x64_2_treasury_emergence.md` and its underlying `sqlite3`
commands (all documented inline in that report's Phase 1/3/8 sections),
X64.2's "Existing Infrastructure Lookup" (its own Phase 3) ran exactly
four joins, **all scoped to `database/wt_ops_v2.db` only**:

```sql
-- against wt_discovered_subprovs.subprov
-- against wt_confirmed_treasuries.treasury
-- against wt_treasury_review.treasury
-- against watchtower_token_attribution.mint
```

Each was joined **only on the 18 launches' own `funder_wallet` (hop1) or
`mint` values** — i.e. "does this exact disposable wallet already have a
lead row" and "does this exact mint already have an attribution row."

## Why Case 1 and Case 2 were both missed — the general cause

**X64.2 never queried `watchtower_events`, `wt_candidate_scores`,
`wt_detected_creates`, `wt_creator_launches`, or any table in
`database/flex_complete_database.db` (the live/production DB) at all.**
Every one of X64.2's four lookups ran against `wt_ops_v2.db` exclusively.
This is a real, confirmed scope gap — not a filtering bug or a join-key
typo within the queries it did run (those queries are logically correct
for what they check).

Additionally, and specific to each case:

### Case 1 (`7nxHcmxb…` / `HHcXBLbn…` / claimed treasury `4231KLYi…`)
Even had `watchtower_events` been queried, it would **not** have
surfaced this connection — `4231KLYi…`'s own event rows never reference
`7nxHcmxb…`, `HHcXBLbn…`, or `HXMUxU94…` (Phase 1, `coverage_table.md`).
The specific intermediate wallet the user supplied,
`7WbkFQAb…`, does not appear in ANY table in either database — it cannot
be found by any lookup this audit is capable of running, because it was
never persisted anywhere. **This is not a lookup-logic failure — it is a
genuine absence of stored evidence for this specific link**, distinct
from Case 2 below.

### Case 2 (`71ftvekA…` / `CvP9vVUC…` / treasury `5nTJWTSoz…`)
`5nTJWTSoz…` **is** present in `wt_confirmed_treasuries` — X64.2's own
Phase 3 lookup #2 (`wt_confirmed_treasuries.treasury`) directly targeted
this exact table. The reason it still wasn't surfaced: **X64.2 joined
`wt_confirmed_treasuries.treasury` against the 18 launches'
`funder_wallet` (i.e. hop1, `DCyQJVfAL37…`) — never against `treasury`
itself as a *candidate answer to search for downstream*.** In other
words, the join asked "is this launch's hop1 wallet ITSELF a confirmed
treasury" (correctly answering "no" for `DCyQJVfAL37…`, which is a
disposable subprov, not a treasury) — it never asked "does some OTHER
already-confirmed treasury's own recorded downstream activity
(`watchtower_events`, or any other lineage table) include this launch's
hop1 wallet or creator." That second question is exactly what would be
needed to catch Case 2, and X64.2 never asked it. This is a **join-key/
question-scope gap**, not a table X64.2 failed to touch — it touched the
right table but asked the wrong direction of question.

Confirmed independently in this audit's own Phase 1: `5nTJWTSoz…`'s
`watchtower_events` rows name three specific downstream subprov wallets
(`4MEbMFxWs…`, `5xBzetUS2Bs2…`, `G2fr9ikcVgtm…`) — **`DCyQJVfAL37…` is not
among them.** So even a corrected "reverse" lookup (treasury → its known
downstream wallets → does that set include any of the 18 launches' hop1
wallets) would **still not have found this connection** from currently
stored data. If Case 2 is genuinely correct, the link between
`5nTJWTSoz…` and `DCyQJVfAL37…`/`71ftvekA…` is not recorded anywhere in
either database at all — same underlying situation as Case 1's missing
`7WbkFQAb…` hop, just discovered via a different missing piece.

## Per-lookup disposition

| Lookup | Queried by X64.2? | Result if it had been run correctly |
|---|---|---|
| `funder_wallet` (hop1) → `wt_discovered_subprovs.subprov` | Yes | Correctly "no match" for both cases — hop1 wallets genuinely aren't independently-discovered subprovs |
| `funder_wallet` (hop1) → `wt_confirmed_treasuries.treasury` | Yes | Correctly "no match" — hop1 wallets are disposable subprovs, not treasuries themselves |
| `treasury` (reverse: known treasuries' downstream) → hop1 set | **No** | Would still be "no match" for Case 2 as shown above — the gap isn't only that this reverse join was skipped, it's that the underlying downstream-linkage data doesn't exist for this specific pair |
| `mint` → `watchtower_token_attribution.mint` | Yes | Correctly "no match" — genuinely empty for both mints |
| `creator`/`mint`/`treasury` → `watchtower_events` | **No** | Would have found `4231KLYi…` and `5nTJWTSoz…` activity, but not connected to either case's creator/mint (see above) |
| `creator`/`mint` → `wt_candidate_scores`/`wt_detected_creates` (live DB) | **No** | Confirmed in this audit: both empty for all 7 relevant wallets/mints — would not have changed the outcome |
| `creator`/`mint` → `wt_creator_launches` (live DB) | **No** | Confirmed in this audit: empty for both cases |
| `creator`/`mint` → `token_analysis.watchtower_related`/`watchtower_evidence_json` (live DB) | **No** | Confirmed in this audit: both mints show `watchtower_related=0`, empty evidence JSON — would not have surfaced anything |

## Bottom line

X64.2's scope gap (never touching the live DB or `watchtower_events`) is
real and should be fixed for future audits of this kind — but **fixing
it alone would not have surfaced either of the two cases the user
supplied**, because the specific linking evidence (`7WbkFQAb…` for Case
1; any `DCyQJVfAL37…`↔`5nTJWTSoz…` link for Case 2) does not exist in
any queryable table in either database. Both cases, if genuinely
confirmed WATCHTOWER, rest on knowledge — most plausibly direct
on-chain/RPC verification — that has not yet been written back into any
table this system's read-only audits can see.
