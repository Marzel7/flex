# X65.1 — Phase 5: Known-Treasury Matching

Compares each of the 3 distinct upstream treasury candidates found in
Phase 4 against `wt_confirmed_treasuries` — the only authoritative,
already-approved treasury registry in this system. Per the task's
constraint, `KNOWN_TREASURY` is returned **only** when the wallet is
already confirmed there; nothing in this phase performs new
confirmation activity.

## Matching result: all 3 treasury candidates are already confirmed

| Treasury candidate | In `wt_confirmed_treasuries`? | Confirmation method | Confirmation date | Confidence |
|---|---|---|---|---|
| `DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK` | **Yes** | `3SIGNAL` | 2026-06-11 (`confirmed_at=1781164069`) | `CONFIRMED` |
| `9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4` | **Yes** | `subprov_funder_trace` | 2026-06-14 (`confirmed_at=1782144539`) | `MANUAL` |
| `Dtwi1eLMTLaUuCwbztpFEoepBdzhjhjmLoTyAdsR3p3u` | **Yes** | `manual_override` | 2026-07-21 (`confirmed_at=1784632820`) | `MANUAL`, provenance `MANUAL_OVERRIDE_X64_DTWI1ELM` (an explicit human-authorized override from this project's own recent history) |

All 3 return `KNOWN_TREASURY` — none required new confirmation activity
in this phase; this phase is a lookup against pre-existing authority
only.

## Operation linkage per treasury

| Treasury | Operation ID | Operation state | Operation name | WATCHTOWER? |
|---|---|---|---|---|
| `DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK` | `69af7941-34d5-42b8-b426-a6a2b9013712` | `MIGRATED` | *(no display name stored for this operation record — `wt_operation_lifecycle` has no name field)* | No |
| `9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4` | `4135d67d-2b70-407a-be3c-ab47526203ac` | `MIGRATED` | *(same — no name field)* | No |
| `Dtwi1eLMTLaUuCwbztpFEoepBdzhjhjmLoTyAdsR3p3u` | `9868e8dd-69a1-434f-a185-b03fbf8f5487` | `MIGRATED` | *(same — no name field)* | No |

None of these three operation UUIDs match this project's own canonical
`WATCHTOWER_OPERATOR_ID` (the single fixed operator ID used elsewhere
in this codebase, e.g. `creator_identity.py`/`operational_intelligence.py`)
— confirmed by direct comparison. This is consistent with Phase 1's
finding that `is_watchtower=False` for all 19 cohort launches; none of
the 7 `CONFIRMED_SUBPROV` launches newly resolve to WATCHTOWER via this
walkback. They resolve to **other, already-confirmed, non-WATCHTOWER
operations**.

## Full resolution paths (per the task's example format)

**2GuvMWJpfNBXdZQZVGEWLV1Dx8qfiLKHHoDDfe4Apump**:
```
creator 3NyJNH93vBDM7nn1U2geTBmoRwnogFoHmhjJSEY8fNGh
  ← subprov 7atTgmp9D86zA3f4AfFSFb5XWvDX2doNW4RrbYFqyQJw (CONFIRMED_SUBPROV, funded 380s before CREATE)
  ← confirmed treasury DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK (KNOWN_TREASURY, 3SIGNAL, confirmed 2026-06-11)
  → operation 69af7941-34d5-42b8-b426-a6a2b9013712 (MIGRATED, non-WATCHTOWER)
```

**2XmV6Jk6ATzKCnVB15cnPHCCF9o4Kn4PXvVFk6Rppump**:
```
creator Dsm6w4zFsovcGTvqBE1mmQikXePFg6csYfaT9gzriY6R
  ← subprov FLo2pNsAsS4qpZZnPSN2Quf6cEkiej4fJXC3uVrgzU2X (CONFIRMED_SUBPROV, funded 104s before CREATE)
  ← confirmed treasury DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK (KNOWN_TREASURY)
  → operation 69af7941-34d5-42b8-b426-a6a2b9013712 (MIGRATED, non-WATCHTOWER)
```

**3LZL5cXac86U1ti81V8GEA1qoj3HenLfnJMcQo7opump**:
```
creator 96oi3HjrPWGnkPwhZL8uFbUjg9qJgSVjn5nK7oM85uVg
  ← subprov 82Yzf1hMDyLa1Z8uADcxzMHxmmGedwKj6viUReKfTeKJ (CONFIRMED_SUBPROV, funded ~79min before CREATE, WSOL_WRAP_CLOSE)
  ← confirmed treasury 9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4 (KNOWN_TREASURY, subprov_funder_trace, confirmed 2026-06-14)
  → operation 4135d67d-2b70-407a-be3c-ab47526203ac (MIGRATED, non-WATCHTOWER)
```

**3QFvseNX1Fdkc6SZV4AT2BfSDvMUH4xQDY1H7TbPpump**, **GuyE9St1cU54ppHwqD719Q2AHf6AmPha93MEjzv2pump**
(both same treasury, `9hGcxVHF...`, same operation `4135d67d-...`):
```
creator <...>
  ← subprov E33jmbX8TQLDP2m1VUsdfyzQCWZMBXhtB6wzgqXKhe44 / DmoG9vDaYTf8Rd1vb8i6BSKZi5Zuo3ov4FdMmz5aPzSW
  ← confirmed treasury 9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4 (KNOWN_TREASURY)
  → operation 4135d67d-2b70-407a-be3c-ab47526203ac (MIGRATED, non-WATCHTOWER)
```

**HJ1Ry6iJyAqN7jozMTErJHuNA66kpkDkowi7fhCRpump**:
```
creator <...>
  ← subprov DkhL6D3ZEwdDu4RnW4WHJM9ujX2B94UyvxMAL9CCBV4T (CONFIRMED_SUBPROV, funded 122s before CREATE)
  ← confirmed treasury Dtwi1eLMTLaUuCwbztpFEoepBdzhjhjmLoTyAdsR3p3u (KNOWN_TREASURY, manual_override, confirmed 2026-07-21)
  → operation 9868e8dd-69a1-434f-a185-b03fbf8f5487 (MIGRATED, non-WATCHTOWER)
```

**x8NtU6nnYDn1BwMDGg2oFdBuYBevhJ32kqM97FSpump**:
```
creator <...>
  ← subprov 3KJteRqjBJb5ddR5eZgPZ8uwyWriKuUN5j2ALS97rpU2 (CONFIRMED_SUBPROV, funded 298s before CREATE)
  ← confirmed treasury DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK (KNOWN_TREASURY)
  → operation 69af7941-34d5-42b8-b426-a6a2b9013712 (MIGRATED, non-WATCHTOWER)
```

## Flagged discrepancy (not resolved, surfaced per the task's own constraints)

This project's own persistent memory ("Hello program operator linkage")
independently established, via a separate on-chain evidence path (shared
downstream Hello-service payments), that `DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK`,
`9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4`, and
`Dtwi1eLMTLaUuCwbztpFEoepBdzhjhjmLoTyAdsR3p3u` all pay the same
downstream recipient — evidence they belong to the same real-world
operator. Yet in `wt_ops_v2_wallets`, they are linked to **three
distinct operation UUIDs**. This audit does **not** merge or reroot
these operation records — per the task's explicit "Do not automatically
confirm or reroot treasury identities" constraint (read here as
extending to not silently merging operation records either). This
discrepancy is flagged for human review in Phase 9's summary, not acted
on.

## Group B (12 launches)

No known-treasury matching is possible — Phase 3/4 found no
sub-provisioner or treasury candidate for any of these 12. They proceed
to Phase 6 as candidates for `UNRESOLVED`, not `KNOWN_TREASURY` or
`UNKNOWN_TREASURY_CANDIDATE` (there is no candidate wallet at all to
classify).
