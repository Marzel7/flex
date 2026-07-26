# X64.1 — 24-Hour WATCHTOWER Discovery Report (Read-Only, Zero-RPC)

Pure intelligence assessment. No database row was modified, no RPC call
was made, no transaction was decoded, no walkback was rerun. Every number
below comes from a direct read-only `sqlite3` query against
`database/wt_ops_v2.db`, run 2026-07-21.

## Time window

- **cutoff resolution**: `now_utc - timedelta(hours=24)`, resolved once.
- **now_utc**: `2026-07-21T09:03:02`
- **start (cutoff) UTC**: `2026-07-20T09:03:02` (epoch `1784534582`)
- **end UTC**: `2026-07-21T09:03:02`
- Column used: `wt_walkback_queue.completed_at` (the worker's actual
  completion timestamp — not `enqueued_at`/launch time, per the task's
  explicit instruction).

## Report Summary

| Metric | Value |
|---|---|
| Rows scanned (all `status='complete'` in window) | 343 |
| Rows matching X64 pattern | 18 |
| Distinct creators | 17 |
| Distinct mints | 18 |
| Distinct disposable wallets (`funder_wallet`) | 18 |
| STRICT evidence | 0 |
| MECHANISM_ONLY evidence | 18 |
| Already known discovery leads (`wt_discovered_subprovs`) | 0 |
| Already rejected | 0 |
| Already confirmed (treasury/attribution/launch) | 0 |
| Recurring disposable wallets | 1 |
| Connected to existing WATCHTOWER infrastructure | 0 |
| Already WATCHTOWER confirmed | 0 |

Mechanism distribution: all 18 rows are `WSOL_WRAP_CLOSE`. Zero rows in
this window carry `SEEDED_ACCOUNT_CLOSE`.

## Evidence classification method (zero-RPC, as specified)

For each candidate, `wt_wrap_close_candidates` was joined on
`creator = <candidate creator> AND close_destination = <candidate
creator>`. A match would indicate a prior, already-stored strict
`closeAccount.destination == creator` confirmation (from either the live
WS-detection path or a prior walkback's own
`_store_close_destination_evidence` write) → `STRICT`. No match →
`MECHANISM_ONLY`. **Result: 0 of 18 candidates have a matching
`wt_wrap_close_candidates` row — all 18 classify as `MECHANISM_ONLY`.**
This is consistent with the earlier X64 audit's finding for the traced
mint `CvP9vV…`, itself one of these 18 rows.

## Existing Infrastructure Lookup (all four, zero matches)

Direct DB-only joins, no inference:
- `wt_discovered_subprovs` (any state, including `REJECTED*`) — **0 of 18
  wallets present**.
- `wt_confirmed_treasuries` — **0 of 18 wallets present**.
- `wt_treasury_review` — **0 of 18 wallets present**.
- `watchtower_token_attribution` — **0 of 18 mints present**.
- `wt_watchtower_launches` (the authoritative WATCHTOWER-confirmed launch
  record) — **0 of 18 mints present**.

None of the 18 candidates connect, through any existing stored lineage, to
any known treasury, accepted sub-provider, treasury-review entry, or
confirmed attribution/launch record.

## Recurrence Analysis

Recurrence was checked against the wallet's full history in
`wt_walkback_queue` (not window-limited), so a wallet's earlier,
outside-window appearance is still counted.

**Exactly one recurring disposable wallet found:**

| wallet | appearances | distinct creators | distinct mints | first seen (UTC) | last seen (UTC) |
|---|---|---|---|---|---|
| `Dbvr7ktCbxqJJv3gDtAuK9AjXBsJuqBAh8sCsandLfQz` | 2 | 1 | 2 | 2026-07-19T21:29:30 | 2026-07-20T09:15:11 |

Both appearances belong to the same creator,
`B1cJJMstShf6oGhJ1bmBMK1XBjjr4n58kWHKYUNWygbL`, across two mints
(`87RGBzxbheCo5H4zJjVxAkQh2VA4AZxiekd6dGmopump` — the earlier,
outside-window one — and `AGumPoj6jUXMsJv1s9iuXa7uiWj18gBSXuM4bLVQpump` —
inside window). Both prior rows are also `NO_ATTRIBUTION_FOUND` with
`subprov IS NULL` — i.e. this recurrence was itself never surfaced before,
consistent with the pre-fix behavior across both occurrences.

**Notable creator-level observation** (not a recurring-wallet case, but
adjacent): the same creator, `B1cJJMstShf6oGhJ1bmBMK1XBjjr4n58kWHKYUNWygbL`,
also appears in this 24h window a third time via mint
`3uJNC2pJESYdGBPfrxnwyk7ULXjqqhsXoxu49wp2pump`, funded by a **different**
disposable wallet, `GxyGhyQKvc1csUrzwB4xtnUv3wG5xV2ChXTGAp2VQE1h`. So this
single creator used two distinct disposable sub-provisioners across three
launches in ~36 hours — a pattern visible only because this report groups
by wallet AND separately reports per-creator activity; neither
`GxyGhyQKvc1c…` nor `Dbvr7ktCbxq…` individually appears more than twice.

No other wallet in the 18-row set repeats.

## WATCHTOWER Connection Analysis — category breakdown

- **Category A (new disposable provisioning lead)**: **17 wallets** — not
  previously present anywhere, no known treasury, no confirmed
  attribution. (18 candidates minus the 1 wallet that qualifies instead
  under Category B below, since it already appeared once before this
  window — still a *new* lead in the sense that it was never surfaced as
  one, but categorized under B per the recurrence criterion.)
- **Category B (recurring disposable infrastructure)**: **1 wallet** —
  `Dbvr7ktCbxqJJv3gDtAuK9AjXBsJuqBAh8sCsandLfQz` (2 appearances, 1
  creator, 2 mints, per the recurrence table above).
- **Category C (connected to existing WATCHTOWER infrastructure)**: **0
  wallets** — confirmed via all four infrastructure-lookup joins above,
  all empty.
- **Category D (already WATCHTOWER confirmed)**: **0 tokens** — confirmed
  via the `wt_watchtower_launches` join above (empty) and by explicitly
  NOT treating `WSOL_WRAP_CLOSE`/`LINEAGE_GAP`/`PROVISION_CANDIDATE` as
  confirmation, per the task's instruction.

---

## Section 1 — Newly Surfaced Disposable Provisioning Leads

All 18 candidates (completion time shown as UTC ISO; funding amount in
SOL; existing discovery state confirmed empty for every row via the
lookups above):

| mint | creator | disposable wallet | mechanism | evidence | amount (SOL) | completed (UTC) | existing discovery state |
|---|---|---|---|---|---|---|---|
| `8wpoG9gbG7mz2Fy75oXqd6i6ytto6FbX4UMJfVgApump` | `A4gzZinixyRUutKZeBBsM9LBJgk3oPzCs9wyacE6nbyK` | `GCzbZ4sam2Z6RNF1YwiEqKnigS4mEn9Lafdw2wsbjQXo` | WSOL_WRAP_CLOSE | MECHANISM_ONLY | 0.179105 | 2026-07-21T08:26:40 | none |
| `8D9ncyi7Jd8ozajg4aewiDMaPR42czdZCSMf5nWDeBZW` | `7hmGyLvVgjiZf2uMRAMWwvATKfgswtxF1SUYWhaT3sE2` | `2rxo9N5g4sDQFjDp5PEtB7qu5wk7zLHLSuzm5EsXj3gc` | WSOL_WRAP_CLOSE | MECHANISM_ONLY | 1.774155 | 2026-07-21T07:19:36 | none |
| `6UXXyzvnysCjqz2pDpgZmyLmrERCTEY4kPQ6dQGapump` | `7R35RBFbo1J9PXa4GowoqdavxPWuRGJ4syzyL4K27jn3` | `Dq54F75j5Va9iq7SLnZ24fdfUKjFsa65NipgARQMnAyZ` | WSOL_WRAP_CLOSE | MECHANISM_ONLY | 0.023086 | 2026-07-21T06:59:31 | none |
| `9rvQ2wcqU5uRvS97JbdwHmUokiCV796T3SGoREUgpump` | `GtT43AzJwU9ZaaGahoHuHbxisHSuAx8X7ASVGb3HgMuj` | `DuXAsBkoYHVre8eEjW4YyytRJmbZbimCAyAJ8EKJ1cF4` | WSOL_WRAP_CLOSE | MECHANISM_ONLY | 0.003007 | 2026-07-21T06:47:06 | none |
| `DxRJpsVNs8NLwSyjaz3zVFViSRWGgQQxKT1wwCy5pump` | `56dQSiMeu8FX2gAADbLEXhfSv63k3SuLJg9YJrLs9G3c` | `7gkGAKgr158j5NTg1uHgLcxiN92orvLEdtyPzTbHuucK` | WSOL_WRAP_CLOSE | MECHANISM_ONLY | 0.008144 | 2026-07-21T05:46:43 | none |
| `EXn2aNztPQBQNrdKCg3HnAtuxFZ6eEnfuMJD2y7tpump` | `CfBoFQ3tRrKhhjoXiocdVFJ9WkCzQNwbEK8uBZ6vRnrR` | `HBoQ8iQX6xpz9BMMoy8EPbipGQz45fT7SK86VNTmfGpJ` | WSOL_WRAP_CLOSE | MECHANISM_ONLY | 0.088967 | 2026-07-21T04:58:49 | none |
| `61BtvdXLEWT52BBsGh6qrsuwoGUcE3cuuS3EC8Mjpump` | `DUvwaBotjogEZ6YV11WG72GXfSNzLmj5CQM9ua7hMwVA` | `FZFM6roR47EjDKSr4HJ5DDKfo5q7at2quDd9bQGAmwun` | WSOL_WRAP_CLOSE | MECHANISM_ONLY | 0.010699 | 2026-07-21T04:58:00 | none |
| `9NqjcpGCBc4vZ57gwjpQjU8J9NqPUKo21jwmWDQZpump` | `utJ3CPNT6zHiaQvr356vURiQVQ3GhobWUDbRMUybHww` | `Q6rUf193CuSzQ1nNN7Gjs3T5CwvzLbqJGo5kFA7ThBW` | WSOL_WRAP_CLOSE | MECHANISM_ONLY | 0.022086 | 2026-07-21T04:47:11 | none |
| `CvP9vVUCpoDuMd2jg5qvakFsk8Ht4qQwmKtZTMeUpump` | `71ftvekAkhanTdJJXdZRLtz7ShkXxdAxhmVmyv2YVSFS` | `DCyQJVfAL37WtcwWAmLNeTatRG553WyfDNytQok41tko` | WSOL_WRAP_CLOSE | MECHANISM_ONLY | 0.112139 | 2026-07-21T04:46:21 | none |
| `Q3WvTW8drUVbQLkRr7m9LBTYJoJrmftJQgUsXwQpump` | `5Cf9Fu8gRhBjwwU64dtSVki3aMewwraRxreR2JcmgnWo` | `AU3CFDUayhZ9Zykcpsg3aYLBTAp4ESfaS8tJHjgZN83i` | WSOL_WRAP_CLOSE | MECHANISM_ONLY | 0.000404 | 2026-07-21T04:38:35 | none |
| `HTog7L8RFmgvza1hGg6hWnQncxeViedNyy6zPUwNpump` | `FpJ1LUmGzcqpbduH1p4WfTMm72enuZYeV1NS1Jg8TG6f` | `Di9Jpx8BS8mr8SAMvA4NZQP3VaishnWfsHTUEWT1h51r` | WSOL_WRAP_CLOSE | MECHANISM_ONLY | 0.169894 | 2026-07-21T04:22:51 | none |
| `HHcXBLbnuSWdYigNgiYDmPhuwwRzTCB73CmyJ8M7pump` | `7nxHcmxbaM4FC2SxdABWzEWhxtsSU8WX7JXGZdaAwizS` | `HXMUxU94Zs2hGHW6r4odBiCTMxkzjV7YGJHAMYdTPFRY` | WSOL_WRAP_CLOSE | MECHANISM_ONLY | 1.112039 | 2026-07-21T03:33:56 | none |
| `A9TJYUgpN4krvqjTAqHEoqe3KLjEm4tSgp957ykcpump` | `ZQwAjVgxsQL4zgjhyjVmo9b3fkWaVTC3m4NqrHW8eDh` | `F6hrtsQbYDgaJpGWVoJQ9J2bGGnMPV1FKZurTYrwQAvz` | WSOL_WRAP_CLOSE | MECHANISM_ONLY | 0.001984 | 2026-07-21T02:45:05 | none |
| `F8dWKhaKAbP91xwGKyQr11sGarUR5MairFKfcC8vpump` | `FmmrPt6NxZALAE4muP1Jd9Mzneu6G8CndhPKbx6cSNnF` | `G9dYo6spsEvL2FMq5KRfcJu9XSa9KN9n7CmWj3FYZyFN` | WSOL_WRAP_CLOSE | MECHANISM_ONLY | 0.648304 | 2026-07-21T02:31:19 | none |
| `3uJNC2pJESYdGBPfrxnwyk7ULXjqqhsXoxu49wp2pump` | `B1cJJMstShf6oGhJ1bmBMK1XBjjr4n58kWHKYUNWygbL` | `GxyGhyQKvc1csUrzwB4xtnUv3wG5xV2ChXTGAp2VQE1h` | WSOL_WRAP_CLOSE | MECHANISM_ONLY | 0.003994 | 2026-07-21T01:58:02 | none |
| `F74webejVVTfPxXxGvSSpfu6vwhES5FkMqH5irP1pump` | `Ebzrp6LSBohCjBdfM3xM1Ahxr7ZxT9QfGTrbwfHD1oVR` | `9gTRxKUiGmNH92M2cDL4S7Gy9N7Npcom5M6E2q7HueBJ` | WSOL_WRAP_CLOSE | MECHANISM_ONLY | 0.025211 | 2026-07-20T23:35:40 | none |
| `AGumPoj6jUXMsJv1s9iuXa7uiWj18gBSXuM4bLVQpump` | `B1cJJMstShf6oGhJ1bmBMK1XBjjr4n58kWHKYUNWygbL` | `Dbvr7ktCbxqJJv3gDtAuK9AjXBsJuqBAh8sCsandLfQz` | WSOL_WRAP_CLOSE | MECHANISM_ONLY | 0.001994 | 2026-07-20T23:15:11 | none (2nd appearance of this wallet — see Section 2) |
| `51bLwxUw4993Be342Z2BNhAYc7ZmQ1T4GWP8bcYNnHtu` | `3NxXWkmJqT9KPg2sYPtGhHDffmHZ6f1e3afnstmz7DJU` | `CQYN8HpSjEKoASxfanQsTK7oXzcia4PneBTt6gJQHixM` | WSOL_WRAP_CLOSE | MECHANISM_ONLY | 0.00289 | 2026-07-20T09:18:16 | none |

## Section 2 — Recurring Disposable Infrastructure

| wallet | creator count | mint count | first seen (UTC) | last seen (UTC) | STRICT | MECHANISM_ONLY | current discovery status |
|---|---|---|---|---|---|---|---|
| `Dbvr7ktCbxqJJv3gDtAuK9AjXBsJuqBAh8sCsandLfQz` | 1 | 2 | 2026-07-19T21:29:30 | 2026-07-20T09:15:11 | 0 | 2 | not present in `wt_discovered_subprovs` — no discovery lead exists for it anywhere, before or after this window |

No other wallet in the 18-row window set repeats. Ranking (by creators,
then mints, then recency) is trivial with only one entry.

## Section 3 — Existing WATCHTOWER Connections

**None.** Zero wallets from this window's 18-row set matched any of the
four infrastructure tables (`wt_discovered_subprovs`,
`wt_confirmed_treasuries`, `wt_treasury_review`,
`watchtower_token_attribution`). No rows to report.

## Section 4 — Already Confirmed WATCHTOWER Tokens

**None.** Zero of the 18 mints appear in `wt_watchtower_launches` (the
authoritative confirmed-launch record), and per instruction,
`WSOL_WRAP_CLOSE`/`LINEAGE_GAP`/`PROVISION_CANDIDATE` were not treated as
confirmation anywhere in this analysis. No rows to report.

---

## Executive Summary

**1. How many additional disposable provisioning wallets would X64
surface in the last 24 hours?**
**18** distinct disposable wallets, across 17 distinct creators and 18
distinct mints.

**2. How many of those are STRICT vs. MECHANISM_ONLY?**
**0 STRICT, 18 MECHANISM_ONLY.** Not one of the 18 candidates has a
matching strict `closeAccount.destination == creator` record already
stored in `wt_wrap_close_candidates` — every one of them would be
surfaced purely on the `_detect_mechanism`-level `WSOL_WRAP_CLOSE`
classification.

**3. How many recur across multiple creators?**
**Zero** wallets recur across *multiple creators* in this window. Exactly
**one** wallet (`Dbvr7ktCbxqJJv3gDtAuK9AjXBsJuqBAh8sCsandLfQz`) recurs, but
across two mints funded by the *same single creator* — not cross-creator
recurrence. (Separately, one creator, `B1cJJMstShf…`, used two *different*
disposable wallets across three launches in this window — a creator-level
pattern, not a wallet-level recurrence.)

**4. Did any connect to existing WATCHTOWER infrastructure?**
**No.** All four infrastructure-lookup joins
(`wt_discovered_subprovs`, `wt_confirmed_treasuries`, `wt_treasury_review`,
`watchtower_token_attribution`) returned zero matches for all 18 wallets
and all 18 mints.

**5. Did any become immediately WATCHTOWER confirmed from existing stored
lineage?**

No additional WATCHTOWER confirmations exist in the last 24 hours using
existing stored lineage.

However, X64 would surface 18 additional disposable provisioning leads
for analyst review.
