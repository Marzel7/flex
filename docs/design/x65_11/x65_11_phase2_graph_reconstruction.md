# X65.11 — Phase 2: Reconstruct Provisioning Graph

For every launch, the observed funding path is reconstructed strictly
from recorded evidence: `wt_attribution_outcomes.evidence_json`
(`funder_wallet`, `treasuries`, `walkback_class`) and
`wt_active_subprov_sessions` (`treasury_wallet`, `funding_mechanism`,
`state`). No edge is inferred or assumed where no row exists.

## Method

For each launch, the recorded chain is: Creator ← direct funder
(`terminal_entity`/`evidence_json.funder_wallet`) ← that funder's own
recorded `wt_active_subprov_sessions.treasury_wallet`, if any. The
`funding_mechanism` column records how the funder→creator (or
funder→sub-provider) transfer was observed.

## Per-launch reconstructed structure

| Mint | Recorded structure | Mechanism | Matches canonical shape? |
|---|---|---|---|
| 5KNDHuNZZc… | Treasury(DchJqu…) → SubProv(Dv34prGm…) → Creator | WSOL_WRAP_CLOSE | Treasury→SubProv→Creator (no intermediate provisioning-wallet row recorded separately — the wrap-close mechanism itself *is* the provisioning-wallet event) |
| 2HBTVUsaor… | Treasury(5nTJWTSo…) → SubProv(8DWH19uh…) → Creator | WSOL_WRAP_CLOSE | Treasury→SubProv→Creator |
| ExL7K9dVVa… | Treasury(69SNcRC8…) → SubProv(8mowmVCE…) → Creator | WSOL_WRAP_CLOSE | Treasury→SubProv→Creator |
| GtpUa2zbVc… | Treasury(4231KLYi…) → SubProv(5SNDBEZL…) → Creator | **PLAIN_TRANSFER** | Treasury→SubProv→Creator, but via a direct SOL transfer, not the wrap-close mechanism |
| 4cVTL5RNa9… | Treasury(69SNcRC8…) → SubProv(BmFdpraQ…) → Creator | WSOL_WRAP_CLOSE | Treasury→SubProv→Creator |
| 3aNojTm74D… | Treasury(69SNcRC8…) → SubProv(5tzFkiKs…) → Creator | WSOL_WRAP_CLOSE | Treasury→SubProv→Creator |
| 5KtNnnPt7x… | Treasury(FkccGTEh…, **unconfirmed**) → SubProv(62meUYzz…) → Creator | **PLAIN_TRANSFER** | Treasury→SubProv→Creator, treasury not yet confirmed |
| 9wvwgFa2Ni… | Treasury(69SNcRC8…) → SubProv(5tzFkiKs…) → Creator | WSOL_WRAP_CLOSE | Treasury→SubProv→Creator |
| 7ri93jDVvo… | Treasury(69SNcRC8…) → SubProv(BmFdpraQ…) → Creator | WSOL_WRAP_CLOSE | Treasury→SubProv→Creator |
| 4FWfPWMRX5… | Treasury(69SNcRC8…) → SubProv(5tzFkiKs…) → Creator | WSOL_WRAP_CLOSE | Treasury→SubProv→Creator |
| 2bFc6R3Wr8… | Treasury(DchJqu…) → SubProv(2EpHmj6C…) → Creator | **PLAIN_TRANSFER** | Treasury→SubProv→Creator |
| EnEgmM4Eb6… | Treasury(FkccGTEh…, **unconfirmed**) → SubProv(CnS6ZtLC…) → Creator | **PLAIN_TRANSFER** | Treasury→SubProv→Creator, treasury not yet confirmed |
| 5ejRBHFabF… | Treasury(69SNcRC8…) → SubProv(BmFdpraQ…) → Creator | WSOL_WRAP_CLOSE | Treasury→SubProv→Creator |
| 3zUqCv6rsq… | Treasury(4231KLYi…) → SubProv(Co2Q6mEk…) → Creator | **PLAIN_TRANSFER** | Treasury→SubProv→Creator |
| 5TW8ARthng… | Treasury(DchJqu…) → SubProv(Dv34prGm…) → Creator | WSOL_WRAP_CLOSE | Treasury→SubProv→Creator |
| 7LxAGkCSxf… | Treasury(69SNcRC8…) → SubProv(5tzFkiKs…) → Creator | WSOL_WRAP_CLOSE | Treasury→SubProv→Creator |
| Ar3vVpZt2x… | Treasury(4231KLYi…) → SubProv(52XHKRHE…) → Creator | **PLAIN_TRANSFER** | Treasury→SubProv→Creator |
| 7CFsJrkPSb… | Treasury(69SNcRC8…) → SubProv(5tzFkiKs…) → Creator | WSOL_WRAP_CLOSE | Treasury→SubProv→Creator |
| CnEgM3tCug… | Treasury(69SNcRC8…) → SubProv(iGdFcQoy…) → Creator | WSOL_WRAP_CLOSE | Treasury→SubProv→Creator |

## Provisioning-wallet layer: not separately observable for this cohort

The canonical model's middle layer — a distinct, single-use
**Provisioning Wallet** sitting between SubProv and Creator (X65.4/X65.6's
wrap-wallet finding) — is **not separately recorded as its own row** for
any of these 19 launches. The evidence available
(`wt_active_subprov_sessions`, `wt_attribution_outcomes.evidence_json`)
records only the two-hop chain (funder→creator, funder's own
treasury_wallet); the wrap-wallet-level detail X65.4 Phase 3A examined
(`wt_candidate_websocket_watches.wrap_wallet`) has **zero rows for any
subprov in this 24-hour cohort** (confirmed directly: `SELECT COUNT(*)
FROM wt_candidate_websocket_watches WHERE subprov_wallet IN (...)` = 0
for all 12 distinct subprovs in this population).

This is reported as an **absence of evidence**, not as a structural
divergence from the canonical model — per the task's explicit
instruction, no edge is inferred. The observed structure for every
launch in this cohort is exactly:

```
Treasury
    ↓
SubProvider
    ↓
Fresh Creator
```

with the middle "Provisioning Wallet" layer un-instrumented for this
specific cohort (a coverage gap in `wt_candidate_websocket_watches` for
these particular subprovs, not a claim that no provisioning wallet
existed on-chain).
