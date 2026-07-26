# X61 Canonical WATCHTOWER Validation

## Conclusion

**Confidence: STRONG**

**Recommendation: Leave as a WATCHTOWER candidate pending human confirmation of the newly discovered treasury `5nTJWT…`.**

The launch has a confirmed WATCHTOWER-like lifecycle and creator-funding transaction: a fresh creator received `0.11213928 SOL` through an atomic WSOL wrap/close two seconds before CREATE, then migrated in the CREATE slot. Those facts establish behavioural and transaction-template similarity only.

The corrected balance-flow walkback recovered a causal capitalisation edge that the first replay incorrectly discarded in favour of newer dust: `5nTJWT…` sent 700 SOL to the zero-balance provisioning wallet `DCyQ…` 130 seconds before creator funding. This surfaces `5nTJWT…` as a new treasury candidate. It is not automatically canonical because human treasury approval remains required.

## Launch

| Fact | Persisted/RPC evidence |
|---|---|
| Creator | `71ftvekAkhanTdJJXdZRLtz7ShkXxdAxhmVmyv2YVSFS` |
| Mint | `CvP9vVUCpoDuMd2jg5qvakFsk8Ht4qQwmKtZTMeUpump` |
| CREATE signature | `4h62xSm9uLpPQP9XEEV5TMLzw6fCghAsmcXe6dDAzkzDQdsPShvjd3zs24duhXz3ene7EMGvgK2gJxK8rJdw5u7Y` |
| CREATE slot/time | `434118208`, `2026-07-20T14:45:28Z` |
| Migration signature | `2begm2bDzSG9ee9knQe4ExpUVFbeNc9sum24EDYxc1xiYsp6JvAbZHZXvgvoYdM6LWhstoh9LwBpKx9fwPP1sDrv` |
| Migration slot/time | `434118208`, `2026-07-20T14:45:28Z` |
| Current topology | `UNKNOWN` |
| Operation assignment | None |
| Operation confidence | None; persisted outcome is `INSUFFICIENT_EVIDENCE` |

The CREATE signature came from `creator_funding_queue` and was independently hydrated by RPC. The migration signature came from `token_analysis` and was independently hydrated by RPC.

## Lifecycle

The creator's complete pre-CREATE signature history contains one transaction:

- Wallet birth/funding: `2026-07-20T14:45:26Z`, signature `NoK7KdV5UuQS9VLJ7YYf1e35Rgj6s1HR54Ht84hKgWhSkMV4DhynGtvSmHkp9pRwPR9XHdrnU7BNm37ETAjRXHq`.
- CREATE: `2026-07-20T14:45:28Z`.
- Migration: `2026-07-20T14:45:28Z`.
- Birth to CREATE: **2 seconds**.
- CREATE to migration: **0 seconds** at block-time resolution.
- Birth to migration: **2 seconds**.

Classifications:

| Classification | Result |
|---|---|
| Quick Birth -> Migration | YES |
| Rapid Birth -> CREATE | YES |
| Migration <5m | YES |

## Creator Funding

The creator-funding transaction is directly observed:

- Fee payer/immediate operational funder: `DCyQJVfAL37WtcwWAmLNeTatRG553WyfDNytQok41tko`.
- Temporary WSOL account: `2VJdDFxDtaRHR2Cnhjt3mCf9YNj9THnE9Tk23FNQLPZh`.
- WSOL owner/close authority: `DkPaXT4ULDurTFq2n7TimsF7KmDhGLSDme2H3XUPrtbK`.
- Close destination: the creator.
- Net creator gain: `0.11213928 SOL`.
- Mechanism: `WSOL_WRAP_CLOSE` with create, transfer, syncNative, and closeAccount observed in the parsed transaction.

This is strong transaction-template evidence. Registry absence is not treated as negative evidence: the purpose of walkback is to discover previously unknown infrastructure.

## Walkback

The defensible operational path is:

`5nTJWTSozPMWR7im9aBCeDE7y22K7ePW3TDToTpP9bGo`

→ 700 SOL, signature `5taAcr…`, slot `434117904`

`DCyQJVfAL37WtcwWAmLNeTatRG553WyfDNytQok41tko`

→ 0.11213928 SOL, fee payer and provisioning transaction source

`DkPaXT4ULDurTFq2n7TimsF7KmDhGLSDme2H3XUPrtbK`

→ WSOL owner/close authority

`71ftvekAkhanTdJJXdZRLtz7ShkXxdAxhmVmyv2YVSFS`

`5nTJWT…` fell from `1,039.810782591 SOL` to `339.810777586 SOL`; `DCyQ…` rose from zero to exactly `700 SOL`. Before creator funding, `DCyQ…` held `700.000001 SOL`; afterward it held `699.88785172 SOL`, while the creator rose from zero to `0.11213928 SOL`. This is causal balance flow, not common ancestry.

The newer `5nT1xr… → DCyQ…` transfer was only `0.000001 SOL`. It is dust and must not outrank the 700 SOL capitalisation merely because it is closer in time. The longer dust ancestry retained in the raw replay is not the operational path.

## Canonical Comparison

| Test | Result |
|---|---|
| New treasury candidate surfaced | YES - `5nTJWT…` |
| Known sub-provider reached | NO |
| Known reservoir/hub reached | NO |
| Transaction-derived provisioning chain | YES |
| Shared migration payer | NO |
| Shared authority | NO |
| Same WSOL creator-funding style | YES |
| Quick Birth -> Migration lifecycle | YES |
| Confirmed WATCHTOWER launch within +/-5 minutes | NO |

CREATE and migration are both paid by the creator itself. The account-close authority is `DkPa…`; no persisted canonical WATCHTOWER overlap was found for it.

## Contradictions

**Contradictions found: NO.**

No unrelated treasury, conflicting operation, incompatible lifecycle, or different transaction template was found. The only remaining limitation is governance: `5nTJWT…` is newly discovered and has not yet received human treasury confirmation.

## Scorecard

| Evidence | Result |
|---|---|
| Treasury discovery | YES - transaction-proven 700 SOL capitalisation |
| Provisioning chain | YES |
| Quick Birth -> Migration | YES |
| Funding lineage | YES |
| Campaign timing | NO |
| Shared infrastructure | NO |
| Shared operational template | YES - treasury capitalisation, provisioning, WSOL close, fresh creator, rapid migration |

Independent positive sources are RPC-parsed balance flow, RPC-exhausted creator history, persisted creator-funding queue evidence, and persisted launch/migration evidence. Together they support **STRONG** WATCHTOWER candidacy. Canonical promotion should occur only when a human confirms `5nTJWT…` as a WATCHTOWER treasury; no automatic registry or attribution mutation was performed.
