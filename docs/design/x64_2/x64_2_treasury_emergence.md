# X64.2 — Treasury Emergence Audit: Master Report

Read-only, zero-RPC audit of the 18 disposable sub-provisioners surfaced
in [X64_1_24H_DISCOVERY_REPORT.md](../x64/X64_1_24H_DISCOVERY_REPORT.md).
No production data was changed. Companion documents: `creator_graph.md`,
`disposable_wallet_analysis.md`, `candidate_treasuries.md`,
`cluster_report.md`, `falsification.md`.

## Phase 1 — Infrastructure Census

| Mint | Creator | Disposable sub-provider | Funding signature | Amount (SOL) | Mechanism | Block time (UTC) | Evidence | Discovery state | Treasury state |
|---|---|---|---|---|---|---|---|---|---|
| `HTog7L8RFmgvza1hGg6hWnQncxeViedNyy6zPUwNpump` | `FpJ1LUmGzcqpbduH1p4WfTMm72enuZYeV1NS1Jg8TG6f` | `Di9Jpx8BS8mr8SAMvA4NZQP3VaishnWfsHTUEWT1h51r` | `4mr1DTgXK5R…SuyFgFot` | 0.169894 | WSOL_WRAP_CLOSE | 2026-07-08T16:21:01 ⚠ | MECHANISM_ONLY | none | none |
| `AGumPoj6jUXMsJv1s9iuXa7uiWj18gBSXuM4bLVQpump` | `B1cJJMstShf6oGhJ1bmBMK1XBjjr4n58kWHKYUNWygbL` | `Dbvr7ktCbxqJJv3gDtAuK9AjXBsJuqBAh8sCsandLfQz` | `4HFvMiXafg…9fVtdaaNpeBkuP5` | 0.001994 | WSOL_WRAP_CLOSE | 2026-07-19T01:58:30 | MECHANISM_ONLY | none | none |
| `61BtvdXLEWT52BBsGh6qrsuwoGUcE3cuuS3EC8Mjpump` | `DUvwaBotjogEZ6YV11WG72GXfSNzLmj5CQM9ua7hMwVA` | `FZFM6roR47EjDKSr4HJ5DDKfo5q7at2quDd9bQGAmwun` | `2VbLnaWpHx…v2gZ3upz5` | 0.010699 | WSOL_WRAP_CLOSE | 2026-07-19T21:02:44 | MECHANISM_ONLY | none | none |
| `A9TJYUgpN4krvqjTAqHEoqe3KLjEm4tSgp957ykcpump` | `ZQwAjVgxsQL4zgjhyjVmo9b3fkWaVTC3m4NqrHW8eDh` | `F6hrtsQbYDgaJpGWVoJQ9J2bGGnMPV1FKZurTYrwQAvz` | `3GHsJM1858…dyJWfHYL5R` | 0.001984 | WSOL_WRAP_CLOSE | 2026-07-20T05:23:30 | MECHANISM_ONLY | none | none |
| `51bLwxUw4993Be342Z2BNhAYc7ZmQ1T4GWP8bcYNnHtu` | `3NxXWkmJqT9KPg2sYPtGhHDffmHZ6f1e3afnstmz7DJU` | `CQYN8HpSjEKoASxfanQsTK7oXzcia4PneBTt6gJQHixM` | `2tSsFijKWS…r2tsLRL` | 0.00289 | WSOL_WRAP_CLOSE | 2026-07-20T06:47:51 | MECHANISM_ONLY | none | none |
| `F74webejVVTfPxXxGvSSpfu6vwhES5FkMqH5irP1pump` | `Ebzrp6LSBohCjBdfM3xM1Ahxr7ZxT9QfGTrbwfHD1oVR` | `9gTRxKUiGmNH92M2cDL4S7Gy9N7Npcom5M6E2q7HueBJ` | `2y2CfMM7yM…9cxveL33K2u` | 0.025211 | WSOL_WRAP_CLOSE | 2026-07-20T09:21:00(≈) | MECHANISM_ONLY | none | none |
| `3uJNC2pJESYdGBPfrxnwyk7ULXjqqhsXoxu49wp2pump` | `B1cJJMstShf6oGhJ1bmBMK1XBjjr4n58kWHKYUNWygbL` | `GxyGhyQKvc1csUrzwB4xtnUv3wG5xV2ChXTGAp2VQE1h` | `2CBiViAQw4…54SbQaom4` | 0.003994 | WSOL_WRAP_CLOSE | 2026-07-20T11:55:41 | MECHANISM_ONLY | none | none |
| `F8dWKhaKAbP91xwGKyQr11sGarUR5MairFKfcC8vpump` | `FmmrPt6NxZALAE4muP1Jd9Mzneu6G8CndhPKbx6cSNnF` | `G9dYo6spsEvL2FMq5KRfcJu9XSa9KN9n7CmWj3FYZyFN` | `4haiw7pxdq…BJRcnbDFGcF` | 0.648304 | WSOL_WRAP_CLOSE | 2026-07-20T12:26:00 | MECHANISM_ONLY | none | none |
| `HHcXBLbnuSWdYigNgiYDmPhuwwRzTCB73CmyJ8M7pump` | `7nxHcmxbaM4FC2SxdABWzEWhxtsSU8WX7JXGZdaAwizS` | `HXMUxU94Zs2hGHW6r4odBiCTMxkzjV7YGJHAMYdTPFRY` | `4zj9xLziFV…6DmjzJAFboNw` | 1.112039 | WSOL_WRAP_CLOSE | 2026-07-20T13:33:20 | MECHANISM_ONLY | none | none |
| `Q3WvTW8drUVbQLkRr7m9LBTYJoJrmftJQgUsXwQpump` | `5Cf9Fu8gRhBjwwU64dtSVki3aMewwraRxreR2JcmgnWo` | `AU3CFDUayhZ9Zykcpsg3aYLBTAp4ESfaS8tJHjgZN83i` | `4orCsw9Kih…qojynPND8pSgX` | 0.000404 | WSOL_WRAP_CLOSE | 2026-07-20T14:31:55 | MECHANISM_ONLY | none | none |
| `CvP9vVUCpoDuMd2jg5qvakFsk8Ht4qQwmKtZTMeUpump` | `71ftvekAkhanTdJJXdZRLtz7ShkXxdAxhmVmyv2YVSFS` | `DCyQJVfAL37WtcwWAmLNeTatRG553WyfDNytQok41tko` | `NoK7KdV5Uu…ETAjRXHq` | 0.112139 | WSOL_WRAP_CLOSE | 2026-07-20T14:45:26 | MECHANISM_ONLY | PROVISION_CANDIDATE (post-X64 fix) | none |
| `9NqjcpGCBc4vZ57gwjpQjU8J9NqPUKo21jwmWDQZpump` | `utJ3CPNT6zHiaQvr356vURiQVQ3GhobWUDbRMUybHww` | `Q6rUf193CuSzQ1nNN7Gjs3T5CwvzLbqJGo5kFA7ThBW` | `2K3sN69aXz…57atUtktH9r` | 0.022086 | WSOL_WRAP_CLOSE | 2026-07-20T14:45:43 | MECHANISM_ONLY | none | none |
| `DxRJpsVNs8NLwSyjaz3zVFViSRWGgQQxKT1wwCy5pump` | `56dQSiMeu8FX2gAADbLEXhfSv63k3SuLJg9YJrLs9G3c` | `7gkGAKgr158j5NTg1uHgLcxiN92orvLEdtyPzTbHuucK` | `2QefWGFMtR…kSnpJqCTTFWEo` | 0.008144 | WSOL_WRAP_CLOSE | 2026-07-20T14:47:50 | MECHANISM_ONLY | none | none |
| `EXn2aNztPQBQNrdKCg3HnAtuxFZ6eEnfuMJD2y7tpump` | `CfBoFQ3tRrKhhjoXiocdVFJ9WkCzQNwbEK8uBZ6vRnrR` | `HBoQ8iQX6xpz9BMMoy8EPbipGQz45fT7SK86VNTmfGpJ` | `4448GJ4k72…gmLHvDkPjrZ` | 0.088967 | WSOL_WRAP_CLOSE | 2026-07-20T14:54:23 | MECHANISM_ONLY | none | none |
| `9rvQ2wcqU5uRvS97JbdwHmUokiCV796T3SGoREUgpump` | `GtT43AzJwU9ZaaGahoHuHbxisHSuAx8X7ASVGb3HgMuj` | `DuXAsBkoYHVre8eEjW4YyytRJmbZbimCAyAJ8EKJ1cF4` | `45RbaBiHnR…KfrZC14E9BuyU` | 0.003007 | WSOL_WRAP_CLOSE | 2026-07-20T16:43:27 | MECHANISM_ONLY | none | none |
| `6UXXyzvnysCjqz2pDpgZmyLmrERCTEY4kPQ6dQGapump` | `7R35RBFbo1J9PXa4GowoqdavxPWuRGJ4syzyL4K27jn3` | `Dq54F75j5Va9iq7SLnZ24fdfUKjFsa65NipgARQMnAyZ` | `563KwmMFyz…XqBrn4` | 0.023086 | WSOL_WRAP_CLOSE | 2026-07-20T16:57:54 | MECHANISM_ONLY | none | none |
| `8D9ncyi7Jd8ozajg4aewiDMaPR42czdZCSMf5nWDeBZW` | `7hmGyLvVgjiZf2uMRAMWwvATKfgswtxF1SUYWhaT3sE2` | `2rxo9N5g4sDQFjDp5PEtB7qu5wk7zLHLSuzm5EsXj3gc` | `5XfM7uwkSg…9GV71fUqmx` | 1.774155 | WSOL_WRAP_CLOSE | 2026-07-20T17:16:08 | MECHANISM_ONLY | none | none |
| `8wpoG9gbG7mz2Fy75oXqd6i6ytto6FbX4UMJfVgApump` | `A4gzZinixyRUutKZeBBsM9LBJgk3oPzCs9wyacE6nbyK` | `GCzbZ4sam2Z6RNF1YwiEqKnigS4mEn9Lafdw2wsbjQXo` | `3JstKYefAk…UoAfHZ2Ng` | 0.179105 | WSOL_WRAP_CLOSE | 2026-07-20T18:23:17 | MECHANISM_ONLY | none | none |

⚠ = flagged timing anomaly, see Phase 2 and `falsification.md` §6.
"Discovery state" reflects `wt_discovered_subprovs` as of this audit; the
`CvP9vVUC…` row now shows `PROVISION_CANDIDATE` as a direct, expected
consequence of the already-implemented X64 fix (not a new action taken by
this audit). All 18 mints show `treasury state = none` — confirmed
against `wt_confirmed_treasuries`, zero matches.

**All 18 rows: MECHANISM_ONLY evidence** (zero have a matching strict
`wt_wrap_close_candidates` row) — consistent with X64.1's finding.

## Phase 2 — Disposable Wallet Analysis

See [disposable_wallet_analysis.md](disposable_wallet_analysis.md).
Summary: **16 single-use, 1 possibly-reusable (same-creator reuse only),
1 unknown/anomalous-timing**. "Total SOL moved" / inbound-outbound counts
are **not derivable from stored evidence** for any of the 18 — zero rows
exist in `wt_walkback_edge_candidates` for any of these wallets.

## Phase 3 — Creator Relationships

See [creator_graph.md](creator_graph.md). Summary: 17 distinct creators,
one of which (`B1cJJMstShf…`) appears twice within this dataset using two
different disposable wallets. **No evidence connects any two DISTINCT
creator addresses** — no shared wallet, amount, or vanity prefix across
different creators.

## Phase 4 — Upstream Convergence (primary objective)

**This is the central finding of the audit: it cannot be answered from
stored evidence.** `wt_walkback_edge_candidates` — the table that would
record any hop2 (upstream-of-disposable-wallet) evidence — has **zero
rows for all 18 disposable wallets**, confirmed via direct per-wallet
query. This is a direct, mechanical consequence of the exact code path
that produced these 18 rows in the first place: each one reached the
`FULL_WALKBACK` branch's terminal `else` specifically because
`_find_with_evidence(hop1, ...)` (the hop2 search) returned nothing —
`hop2` was falsy, so `_capture_provisioning_facts` (which would have
written to `wt_walkback_edge_candidates`) was never reached for any of
them (see `src/core/walkback_worker.py`'s `FULL_WALKBACK` branch,
confirmed in the X64 audit).

Consequently:
- **Identical upstream wallets**: cannot be checked — no upstream data
  exists for any of the 18 to compare.
- **Repeated unresolved wallets**: same — none stored.
- **Common intermediate wallets**: same.
- **Repeated funding signatures**: checked directly at the hop1 level —
  all 18 `funder_sig` values are distinct; no signature reuse.
- **Common inbound source**: cannot be checked (same underlying gap).
- **Repeated funding paths**: cannot be checked (same gap).

This is reported honestly as **absence of evidence, not evidence of
absence** — it does not support "no shared treasury exists," it means
"the question cannot currently be answered without RPC." See
`falsification.md`'s "Where RPC would genuinely be required" section —
this is the single highest-value follow-up identified by this audit.

## Phase 5 — Timing Analysis

See [cluster_report.md](cluster_report.md). Summary: one real 4-launch,
9-minute density spike (14:45:26–14:54:23, 2026-07-20), zero shared
evidence among those 4 rows beyond timing. No coordinated wave, no
sequential-creation pattern, no synchronized multi-creator launch found.

## Phase 6 — Funding Fingerprints

- **Amounts**: all 18 distinct, ranging 0.000404–1.774155 SOL, no
  round-number or repeated-value clustering.
- **Mechanism**: uniformly `WSOL_WRAP_CLOSE` across all 18 — this is the
  selection criterion itself, not a discovered fingerprint.
- **Residual balances**: not derivable without RPC (would require
  re-fetching each transaction's post-balance state beyond what
  `funder_amount_sol` already captures).
- **Transaction size / signature characteristics**: not analyzed beyond
  signature string uniqueness (confirmed: 18 distinct signatures, no
  reuse); byte-level signature/transaction-shape comparison would require
  RPC re-fetch and was not performed.
- **Conclusion**: no evidence of common operational tooling beyond the
  shared `WSOL_WRAP_CLOSE` mechanism itself, which is the dataset's
  selection criterion, not an independently discovered signal.

## Phase 7 — Behaviour Correlation

| Marker | Finding |
|---|---|
| Rapid birth→migration | 17/18 migrated in 1-2 seconds; 1 outlier (`F74webej…`, 542s ≈ 9 minutes) |
| Migration timing | All 18 confirmed `migrated` in `token_analysis.lifecycle_stage` |
| Trader swarm / sweep timing / dust observations / wash trading | **Not evaluated** — would require querying `wt_behaviour_queue`/swarm-detection tables not joined in this pass; flagged as a gap, not a negative finding |
| Known behavioural queue hits | Not checked against `operational_behaviour_tags.py`'s classification output in this pass — flagged as a gap |
| Creator history | All 17 creators show a single token each in `token_analysis` for this dataset's mints (repeat-creator count already covered in Phase 3); broader creator history (other tokens outside this 18-set) was not separately queried |
| Provision cadence | See `creator_graph.md` — only computable for the one repeat creator, and irregular (not fixed-interval) |

**No treasury attribution was required or attempted for this phase**, per
instruction — markers are reported as observed, not scored toward
confirmation.

## Phase 8/9 — Hidden Treasury Discovery & Ranking

See [candidate_treasuries.md](candidate_treasuries.md). **No family
grouping could be constructed from stored evidence** — every shared-link
check (wallet, amount, vanity prefix, existing infrastructure table)
returned zero cross-creator matches. The only candidate evaluated (the
18-launch cohort as a single treasury) scores **Low confidence**.

## Phase 10 — Falsification

See [falsification.md](falsification.md). The two apparent patterns
(the timing density spike and the uniform rapid-migration signature) are
both explicitly tested and rejected as coincidental / already-expected
base-rate behaviour rather than treasury evidence. The `HTog7L8R…` timing
anomaly is attributed to a probable walkback mis-selection, not treasury
dormancy. The single genuinely open question (upstream convergence) is
named as requiring RPC, not resolved by inference.

---

## Executive Summary

**1. How many independent infrastructure families exist within the 18
launches?**
**18** — on current stored evidence, each launch is an isolated
observation with no cross-launch link except one same-creator wallet
reuse (which does not constitute a family, since it involves only one
creator's own two launches, not multiple creators/wallets converging).

**2. How many appear operationally related?**
**None conclusively.** One creator (`B1cJJMstShf…`) relates to itself
across 2 of its own launches — not a multi-party operational relationship.
No two distinct creators show any shared evidence.

**3. Did multiple disposable wallets converge on the same unresolved
upstream?**
**Cannot be determined from stored evidence.** Zero hop2/upstream rows
exist for any of the 18 wallets in `wt_walkback_edge_candidates`. This is
the audit's central open question, not a "no."

**4. Did multiple creators appear to belong to the same operator?**
**No.** Checked exhaustively for shared disposable wallets, shared
funding amounts, and shared vanity/ground address prefixes across all 17
distinct creators — zero matches found between any two different
creators.

**5. Did any funding fingerprints repeat?**
**No.** All 18 funding amounts are distinct (no round-number or repeated
clustering), all 18 signatures are distinct, and the only shared
mechanism (`WSOL_WRAP_CLOSE`) is the dataset's own selection criterion,
not a discovered pattern.

**6. Did any cluster exhibit WATCHTOWER behavioural characteristics
despite lacking treasury attribution?**
**Yes, at the individual-launch level, but not as a cluster-specific
signal.** 17/18 show the rapid (1-2s) create→migrate signature that is
the generic WATCHTOWER/X62 handoff behaviour — expected given the
selection criteria, not additional evidence of a shared treasury behind
these specific 18 launches.

**7. Is there evidence that WATCHTOWER has rotated to one or more
previously unseen treasuries?**

**Weak evidence.**

Support: 18 genuine `WSOL_WRAP_CLOSE`-funded, rapid-migrating launches
were surfaced in a 40-hour window, all currently unattributed to any
known treasury/subprov — consistent with *either* a new, not-yet-observed
treasury operating disposable infrastructure at low volume, *or* 17-18
independent creators/operators each separately using the same
publicly-observable WATCHTOWER handoff mechanism (a known, previously
documented pattern in this system, not exclusive to any one treasury).
The data cannot currently distinguish between these two explanations,
because the one test that could (upstream convergence, Phase 4) has no
stored evidence to draw on — every disposable wallet's own funding
origin is completely unknown in this database. Nothing in Phases 1-9 rises
above "weak": no shared wallets across creators, no shared amounts, no
existing infrastructure links, and the one timing cluster and one
funding-amount near-match both failed falsification as coincidental.

**Recommendation**: this constitutes credible grounds to run the specific,
narrow RPC investigation identified in `falsification.md` (hop2 walks on
the 18 disposable wallets, prioritizing `B1cJJMstShf…`'s two wallets and
the `HTog7L8R…` anomaly) before drawing any stronger conclusion — not
grounds for treasury promotion, which remains unsupported by current
evidence.

## Success-criteria answers

- **Are these 18 isolated launches or part of larger infrastructure?**
  On current stored evidence: isolated. The question is open pending an
  upstream (hop2) RPC investigation this audit could not perform.
- **Do they reveal one or more previously unseen treasury families?**
  No family is established by stored evidence; at most, a single
  Low-confidence candidate (the cohort as a whole) was scored and does
  not meet a promotion bar.
- **Which wallets deserve promotion to active treasury investigation?**
  None deserve *treasury* promotion. All 18 disposable wallets already
  qualify (independent of this audit) for the discovery-lead treatment
  X64 provides; `B1cJJMstShf…`'s two wallets specifically deserve
  priority for a hop2 RPC follow-up given the same-creator reuse pattern.
- **Which clusters should be monitored going forward?** The 4-launch,
  9-minute density window (14:45:26–14:54:23, 2026-07-20) and the
  `B1cJJMstShf…` creator (any future launches reusing either of its two
  known disposable wallets, or a third new one, would upgrade its
  classification from "possibly reusable" toward "reusable").
- **Is there credible evidence of a new WATCHTOWER treasury lineage
  emerging?** Weak evidence only — insufficient for promotion, sufficient
  to justify the specific, narrow RPC follow-up named above.
