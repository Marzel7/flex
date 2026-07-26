# X64.2 — Phase 2: Disposable Wallet Analysis

All data read-only from `database/wt_ops_v2.db` and
`database/flex_complete_database.db`. Lifetime/inbound/outbound counts are
bounded to what is directly stored (`wt_walkback_queue`,
`wt_walkback_edge_candidates`) — no RPC was issued to independently verify
on-chain balance history.

## Per-wallet table

| wallet | appearances (lifetime) | distinct creators | distinct mints | first seen (UTC) | last seen (UTC) | amount (SOL) | classification |
|---|---|---|---|---|---|---|---|
| `Dbvr7ktCbxqJJv3gDtAuK9AjXBsJuqBAh8sCsandLfQz` | 2 | 1 | 2 | 2026-07-19T21:29:30 | 2026-07-20T09:15:11 | 0.001994 (2nd) | **Possibly reusable** |
| `Di9Jpx8BS8mr8SAMvA4NZQP3VaishnWfsHTUEWT1h51r` | 1 | 1 | 1 | 2026-07-08T16:21:01 | same | 0.169894 | **Unknown (anomalous timing)** |
| `FZFM6roR47EjDKSr4HJ5DDKfo5q7at2quDd9bQGAmwun` | 1 | 1 | 1 | 2026-07-20T05:23:30 | same | 0.010699 | Single-use |
| `F6hrtsQbYDgaJpGWVoJQ9J2bGGnMPV1FKZurTYrwQAvz` | 1 | 1 | 1 | 2026-07-20T09:21:00 | same | 0.001984 | Single-use |
| `CQYN8HpSjEKoASxfanQsTK7oXzcia4PneBTt6gJQHixM` | 1 | 1 | 1 | 2026-07-20T06:47:51 | same | 0.00289 | Single-use |
| `9gTRxKUiGmNH92M2cDL4S7Gy9N7Npcom5M6E2q7HueBJ` | 1 | 1 | 1 | 2026-07-20T09:21:08(≈) | same | 0.025211 | Single-use |
| `GxyGhyQKvc1csUrzwB4xtnUv3wG5xV2ChXTGAp2VQE1h` | 1 | 1 | 1 | 2026-07-20T11:55:41 | same | 0.003994 | Single-use |
| `G9dYo6spsEvL2FMq5KRfcJu9XSa9KN9n7CmWj3FYZyFN` | 1 | 1 | 1 | 2026-07-20T12:26:00 | same | 0.648304 | Single-use |
| `HXMUxU94Zs2hGHW6r4odBiCTMxkzjV7YGJHAMYdTPFRY` | 1 | 1 | 1 | 2026-07-20T13:33:20 | same | 1.112039 | Single-use |
| `AU3CFDUayhZ9Zykcpsg3aYLBTAp4ESfaS8tJHjgZN83i` | 1 | 1 | 1 | 2026-07-20T14:31:55 | same | 0.000404 | Single-use |
| `DCyQJVfAL37WtcwWAmLNeTatRG553WyfDNytQok41tko` | 1 | 1 | 1 | 2026-07-20T14:45:26 | same | 0.112139 | Single-use |
| `Q6rUf193CuSzQ1nNN7Gjs3T5CwvzLbqJGo5kFA7ThBW` | 1 | 1 | 1 | 2026-07-20T14:45:43 | same | 0.022086 | Single-use |
| `7gkGAKgr158j5NTg1uHgLcxiN92orvLEdtyPzTbHuucK` | 1 | 1 | 1 | 2026-07-20T14:47:50 | same | 0.008144 | Single-use |
| `HBoQ8iQX6xpz9BMMoy8EPbipGQz45fT7SK86VNTmfGpJ` | 1 | 1 | 1 | 2026-07-20T14:54:23 | same | 0.088967 | Single-use |
| `DuXAsBkoYHVre8eEjW4YyytRJmbZbimCAyAJ8EKJ1cF4` | 1 | 1 | 1 | 2026-07-20T16:43:27 | same | 0.003007 | Single-use |
| `Dq54F75j5Va9iq7SLnZ24fdfUKjFsa65NipgARQMnAyZ` | 1 | 1 | 1 | 2026-07-20T16:57:54 | same | 0.023086 | Single-use |
| `2rxo9N5g4sDQFjDp5PEtB7qu5wk7zLHLSuzm5EsXj3gc` | 1 | 1 | 1 | 2026-07-20T17:16:08 | same | 1.774155 | Single-use |
| `GCzbZ4sam2Z6RNF1YwiEqKnigS4mEn9Lafdw2wsbjQXo` | 1 | 1 | 1 | 2026-07-20T18:23:17 | same | 0.179105 | Single-use |

## Total lifetime / total SOL moved / inbound / outbound

These fields require either RPC (full on-chain balance history) or a
pre-existing DB record of the wallet's full transaction set. Neither is
available for any of these 18 wallets — **`wt_walkback_edge_candidates`
has zero rows for all 18 wallets** (verified directly, see
`x64_2_treasury_emergence.md` §Phase 4), meaning no hop2 walk ever
persisted any inbound-funding evidence for them. "Total SOL moved" and
"inbound/outbound count" are therefore **not derivable from stored
evidence** for any of the 18 — reported honestly as a gap, not estimated.
The only quantities genuinely known per wallet are the single outbound
leg captured by the walkback itself (creator funding amount, above) and,
for the recurring wallet, its second known outbound leg.

## Classification and reasoning

**Single-use (16 of 18)**: exactly one appearance anywhere in
`wt_walkback_queue`'s full history (not window-limited), one creator, one
mint. This is the expected, default shape for a disposable
sub-provisioner under the X62 primitive — a wallet created, used once to
fund a creator via WSOL_WRAP_CLOSE, and never seen again in this dataset.

**Possibly reusable (1 — `Dbvr7ktCbxqJJv3gDtAuK9AjXBsJuqBAh8sCsandLfQz`)**:
2 appearances, but both for the **same single creator**
(`B1cJJMstShf6oGhJ1bmBMK1XBjjr4n58kWHKYUNWygbL`), ~11.75 hours apart.
Classified "possibly reusable" rather than "reusable" because the
recurrence is scoped to one creator, not cross-creator infrastructure
reuse — this is consistent with a creator re-using their own known-good
disposable-funding wallet across two of their own launches, not
necessarily a shared operator tool serving multiple creators. Confirmed
via direct join: no third appearance, no other creator touches it.

**Unknown / anomalous timing (1 — `Di9Jpx8BS8mr8SAMvA4NZQP3VaishnWfsHTUEWT1h51r`)**:
Single appearance by wallet-reuse count, but flagged separately because
its `funder_block_time` (2026-07-08T16:21:01) precedes the token's own
`created_at` (2026-07-20T14:22:13) by **~11.9 days** — the funding
transaction the walkback selected is not a plausible immediate
pre-launch funding event. Every other wallet in this set funded its
creator within hours (at most) of that creator's own token CREATE.
This is not treated as evidence of anything operationally interesting;
it is flagged as a probable weak/mismatched hop1 selection (see
`falsification.md`) and excluded from the timing-cluster and treasury-
emergence analysis in the other documents.
