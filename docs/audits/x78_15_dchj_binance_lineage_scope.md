# X78.15 — DchJ ↔ Binance lineage verification

Audit date: 7 August 2026

Decision: **C — SAME INHERITED-ROOT CONTAMINATION**

Root policy: **GENERIC_FAIL_CLOSED_REQUIRED**

## Executive finding

None of the 21 session transactions currently used to present an upstream root
for Binance 2 contains its stored root. The root is not a signer, fee payer,
account key, parsed instruction participant, or transfer source/recipient.

| Stored root | Binance sessions | Exact direct | Verified indirect | Different sender | Pre-repair launch bucket |
|---|---:|---:|---:|---:|---:|
| DchJ | 14 | 0 | 0 | 14 | 136 |
| 9hGc | 3 | 0 | 0 | 3 | 49 |
| 4231 | 2 | 0 | 0 | 2 | 19 |
| EFKV | 2 | 0 | 0 | 2 | 0 |

No direct DchJ → Binance relationship has been verified. No chronological
indirect DchJ path to the decoded senders or 5tzF was located in the persisted
transaction-first graph. This is not proof that no external path can ever be
acquired; it means no indirect ancestry is currently evidence-backed.

## Frozen Binance presentation

Before enforcement the Binance 2 profile contained 204 launches, 204 direct
`5tzF → creator` edges, 21 eligible sessions, and these upstream allocations:

- DchJ: 136 launches
- 9hGc: 49 launches
- 4231: 19 launches
- EFKV: 0 launches

The UI rendered the root as “Upstream treasury matched by recorded funding
session” and visually joined `root → 5tzF → creator → launch`.

## DchJ session census and replay

All rows use stored mechanism `WSOL_WRAP_CLOSE`, stored recipient 5tzF, and
state `EXPIRED`. Transaction replay instead found one plain native transfer into
5tzF from the sender shown below.

| Session | Funding time UTC | Stored amount SOL | Actual explicit sender | Classification |
|---:|---|---:|---|---|
| 68723 | 6 Jul 11:32:45 | 1.999993 | DbPg3j…JCwD | DIFFERENT_DIRECT_SENDER |
| 146273 | 6 Jul 14:13:01 | 3.999992 | 664HEm…MeAv | DIFFERENT_DIRECT_SENDER |
| 81916 | 10 Jul 12:52:58 | 7.462620855 | 21wG4F…TTgv | DIFFERENT_DIRECT_SENDER |
| 146272 | 12 Jul 10:02:58 | 3.154052946 | G6xTBP…D8PE | DIFFERENT_DIRECT_SENDER |
| 99848 | 13 Jul 15:02:47 | 1.999992 | DbPg3j…JCwD | DIFFERENT_DIRECT_SENDER |
| 102981 | 13 Jul 17:25:18 | 7.861102768 | 21wG4F…TTgv | DIFFERENT_DIRECT_SENDER |
| 145589 | 17 Jul 15:42:51 | 1.999992 | DbPg3j…JCwD | DIFFERENT_DIRECT_SENDER |
| 179072 | 21 Jul 13:42:55 | 2.121463357 | 7GxPdp…AzyK | DIFFERENT_DIRECT_SENDER |
| 178490 | 22 Jul 01:22:49 | 1.999992 | DbPg3j…JCwD | DIFFERENT_DIRECT_SENDER |
| 180607 | 22 Jul 10:32:56 | 6.361553487 | 21wG4F…TTgv | DIFFERENT_DIRECT_SENDER |
| 180929 | 22 Jul 10:42:55 | 1.999992 | DbPg3j…JCwD | DIFFERENT_DIRECT_SENDER |
| 183982 | 22 Jul 20:02:49 | 3.816455413 | 21wG4F…TTgv | DIFFERENT_DIRECT_SENDER |
| 192821 | 24 Jul 05:32:45 | 1.999992 | DbPg3j…JCwD | DIFFERENT_DIRECT_SENDER |
| 198227 | 25 Jul 01:52:45 | 1.999992 | DbPg3j…JCwD | DIFFERENT_DIRECT_SENDER |

Every decoded transaction had one explicit System Program SOL transfer. The
actual sender was also the signer and fee payer. DchJ was absent entirely.
Decoded signatures and complete instruction payloads are preserved in the
bounded audit cache `/tmp/x7815_results.json` and `/tmp/x7815_tx_cache.json`.

## Remaining roots

- 9hGc: all three stored sessions were explicitly sent by `21wG4F…TTgv`.
- 4231: the two senders were `21wG4F…TTgv` and `2PBYG7…cyHz`.
- EFKV: the two senders were `G2zibS…b14i` and `21wG4F…TTgv`.

All 21 rows share the X78.9–X78.11 failure signature: stored root differs from
the transaction sender. They are inherited-root records, not flattened funding
edges. Root account-key presence, root signatures, and root parsed-instruction
participation were all 0/21.

## Launch allocation audit

`OperationIntelligenceAssembler._infrastructure` grouped each direct
`5tzF → creator` launch edge under the latest eligible preceding session for
5tzF. X78.14 changed which sessions were eligible only for 69SN; it did not
change this latest-session algorithm.

Therefore all 136 DchJ-assigned launches were **TEMPORAL ONLY** with respect to
DchJ:

| Evidence class | Launches |
|---|---:|
| Direct DchJ evidence | 0 |
| Verified indirect DchJ ancestry | 0 |
| Temporal session allocation only | 136 |
| Unresolved | 0 |

The 204 direct `5tzF → creator` observations remain valid and are independent of
the invalid root buckets.

## Representative UI chains

| Displayed example | Root → 5tzF | 5tzF → creator | Creator → launch | What “Funding Tx” proves |
|---|---|---|---|---|
| DchJ → 5tzF → BzBYV → 6BDPH | session-only, unsupported | transaction-proven | persisted launch context | 5tzF → BzBYV only |
| DchJ → 5tzF → 6j8mR → 36fc8 | session-only, unsupported | transaction-proven | persisted launch context | 5tzF → 6j8mR only |
| DchJ → 5tzF → 7Juch → 2SLDC | session-only, unsupported | transaction-proven | persisted launch context | 5tzF → 7Juch only |

The launch-specific recorded upstream senders into 5tzF were respectively
`D6QGct…S8Xe`, `3ZfQki…qX4e`, and `8zHcWu…PGvS`, not DchJ.

## Identity-neutral shadow

The historical session table contained 216,155 rows across 14 stored roots.
The transaction-first replay independently verified 28 exact tuples. Under the
generic rule, 28 remain Tier-1 and 216,127 remain historical context only.

| Root | Raw | Exact verified | Context only |
|---|---:|---:|---:|
| 69SN | 207,121 | 0 | 207,121 |
| DchJ | 3,666 | 16 | 3,650 |
| EFKV | 2,931 | 0 | 2,931 |
| Fkcc | 980 | 7 | 973 |
| Dtwi | 641 | 0 | 641 |
| 4231 | 321 | 1 | 320 |
| 9hGc | 205 | 0 | 205 |
| 43PK | 84 | 0 | 84 |
| 5nTJ | 80 | 3 | 77 |
| 2vBd | 79 | 0 | 79 |
| 9gv9 | 43 | 1 | 42 |
| remaining three roots | 4 | 0 | 4 |

“Context only” is not “disproved.” It means the available evidence does not
satisfy directional Tier-1 lineage.

## WATCHTOWER and infrastructure impact

The correction does not alter WATCHTOWER governance, canonical treasury lists,
operation membership, reconciliation, or resolver output. It removes historical
session-only arrows while retaining the 28 independently exact session edges.

For Binance 2 after enforcement:

- total launches: 204 → 204
- direct 5tzF → creator edges: 204 → 204
- funding paths: 346 → 346
- eligible 5tzF upstream sessions: 21 → 0
- rendered upstream roots: 4 → 0
- launch allocation: four asserted root buckets → 204 upstream-unresolved

This is the required separation: downstream infrastructure membership remains;
unsupported upstream interpretation disappears.

## Implementation

- Tier-1 session eligibility now requires an exact registry match on session ID,
  sender, recipient, and signature for every identity.
- The 28 exact X78.13 replay results seed the verified-edge registry.
- Live transaction-decoded writers register exact edges at session creation.
- Raw sessions and quarantine evidence remain unchanged.
- Infrastructure presentation derives upstream roots only from eligible sessions.

Final verdict: **same inherited-root contamination; generic fail-closed required.**
