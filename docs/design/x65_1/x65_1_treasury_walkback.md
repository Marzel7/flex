# X65.1 — Phase 4: SubProv-to-Treasury Walkback

For the 7 `CONFIRMED_SUBPROV` launches from Phase 3, walks upstream one
more hop to identify each sub-provisioner's own funding source (the
treasury candidate). Bounded to the task's default maximum depth: **2
funding hops upstream from creator** (creator ← subprov ← treasury).

## Bridging-depth check (before accepting depth 2 as final)

Before treating `treasury_wallet` as terminal, checked whether any of
the 3 distinct treasury wallets found is itself a `subprov_wallet` in
`wt_active_subprov_sessions` (i.e., itself funded by a further upstream
wallet — which would require extending walkback depth per the task's
"only increase depth where there is clear bridging or relay evidence"
instruction).

**Result: zero bridging evidence.** None of the 3 treasury wallets
(`DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK`,
`9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4`,
`Dtwi1eLMTLaUuCwbztpFEoepBdzhjhjmLoTyAdsR3p3u`) appears as a
`subprov_wallet` anywhere. Depth 2 is confirmed as the correct, terminal
walkback depth for this cohort — no depth increase was needed or
performed.

## Per-launch upstream candidate detail

| Field | 2GuvMWJp... | 2XmV6Jk6... | 3LZL5cXa... | 3QFvseNX... | GuyE9St1... | HJ1Ry6iJ... | x8NtU6nn... |
|---|---|---|---|---|---|---|---|
| Creator | `3NyJNH93vBDM7nn1U2geTBmoRwnogFoHmhjJSEY8fNGh` | `Dsm6w4zFsovcGTvqBE1mmQikXePFg6csYfaT9gzriY6R` | `96oi3HjrPWGnkPwhZL8uFbUjg9qJgSVjn5nK7oM85uVg` | (see Phase 3) | (see Phase 3) | (see Phase 3) | (see Phase 3) |
| Sub-provisioner (hop 1) | `7atTgmp9D86z...` | `FLo2pNsAsS4q...` | `82Yzf1hMDyLa...` | `E33jmbX8TQLD...` | `DmoG9vDaYTf8...` | `DkhL6D3ZEwdD...` | `3KJteRqjBJb5...` |
| Treasury candidate (hop 2) | `DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK` | `DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK` | `9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4` | `9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4` | `9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4` | `Dtwi1eLMTLaUuCwbztpFEoepBdzhjhjmLoTyAdsR3p3u` | `DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK` |
| Hop depth | 2 | 2 | 2 | 2 | 2 | 2 | 2 |
| Relationship type | TREASURY_TO_SUBPROV (persisted, `wt_active_subprov_sessions.treasury_wallet`) | same | same | same | same | same | same |
| Funded subprov before launch? | Yes — 380s before CREATE | Yes — 104s before | Yes — 4,719s before | Yes — 344s before | Yes — 281s before | Yes — 122s before | Yes — 298s before |

## Treasury-scale history (per treasury candidate, not per launch — each treasury serves multiple of the 7 launches)

| Treasury | Distinct sub-provisioners funded (all-time) | Total funding_amount (all-time, SOL) | Treasury-scale? |
|---|---|---|---|
| `DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK` | 2,245 | 80,384.6 | Yes — by a wide margin, this is one of the larger treasury wallets tracked in this system |
| `9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4` | 178 | 48,512.6 | Yes |
| `Dtwi1eLMTLaUuCwbztpFEoepBdzhjhjmLoTyAdsR3p3u` | 578 | 42,476.6 | Yes |

All three treasury candidates fund hundreds to thousands of distinct
sub-provisioners with tens of thousands of SOL in aggregate — this is
unambiguous treasury-scale activity, not a coincidental one-off large
transfer (directly addressing the task's explicit prohibition: "Do not
choose the largest historical funder without proving that the transfer
belongs to the relevant provisioning lineage" — here, the specific
`funding_signature` tying each sub-provisioner to its specific launch's
own timing window is what proves the lineage, not the treasury's size
alone).

## Links to other known sub-provisioners (already-established fact, not newly discovered)

All three treasuries were already known, treasury-scale wallets *before*
this task (per `wt_confirmed_treasuries`, confirmed 2026-06-11 through
2026-07-21) — this walkback did not discover new treasuries, it
connected 7 already-unattributed launches to already-confirmed
treasury infrastructure via a cross-reference that previously wasn't
being checked (Phase 2's finding).

## Links to a confirmed operation

All three treasuries are already linked to a `wt_ops_v2_wallets` row
with `role='TREASURY'` (Phase 2): `9hGcxVHF...` →
`4135d67d-2b70-407a-be3c-ab47526203ac`, `DchJquEZ...` →
`69af7941-34d5-42b8-b426-a6a2b9013712`, `Dtwi1eLM...` →
`9868e8dd-69a1-434f-a185-b03fbf8f5487`.

## Group B (12 launches) — no walkback possible

Since Phase 3 classified all 12 Group-B funders `UNRESOLVED` (zero
existing evidence of a sub-provisioner relationship at all), there is
no hop-2 candidate to walk to. Per the task's instruction ("Do not
choose the largest historical funder without proving that the transfer
belongs to the relevant provisioning lineage"), no speculative treasury
candidate is proposed for these 12 — walkback correctly terminates at
hop 1 with no further evidence, rather than guessing.
