# X65.11 — Phase 5: Compare With Canonical WATCHTOWER Model

Canonical model:
```
Treasury
    ↓
SubProvider
    ├── Provisioning Wallet A
    ├── Provisioning Wallet B
    ├── Provisioning Wallet C
    └── Provisioning Wallet X
               ↓
          Fresh Creator
```

Each launch is classified `Fully matches` / `Partially matches` /
`Diverges` / `Insufficient evidence`, per the task's explicit
instruction not to assume divergence is an error.

## Per-launch classification

| Mint | Classification | Reasoning |
|---|---|---|
| 5KNDHuNZZc… | **Partially matches** | Treasury→SubProv→Creator chain fully confirmed (WSOL_WRAP_CLOSE, confirmed treasury); the SubProv's own multi-branch fan-out is independently evidenced (18 creators, Phase 3), but the specific Provisioning-Wallet layer for THIS launch is not separately recorded (Phase 2) |
| 2HBTVUsaor… | **Partially matches** | Treasury→SubProv→Creator confirmed; SubProv (`8DWH19uhVTaz…`) shows only 1 recorded creator (Phase 3) — no independent fan-out evidence either confirming or contradicting the canonical multi-branch shape for this specific SubProv |
| ExL7K9dVVa… | **Partially matches** | Treasury→SubProv→Creator confirmed; SubProv (`8mowmVCEewZ9…`) shows 8 creators funded (fan-out evidenced) — consistent with canonical shape at the SubProv layer, but the Provisioning-Wallet layer itself unrecorded |
| GtpUa2zbVc… | **Diverges (mechanism)** | Treasury→SubProv→Creator confirmed, but via PLAIN_TRANSFER, not the wrap-close mechanism the canonical model's "Provisioning Wallet" concept was built around (X65.4's wrap-wallet finding). SubProv (`5SNDBEZLHtQX…`) shows 0 recorded creators — no fan-out evidence at all |
| 4cVTL5RNa9… | **Partially matches** | Treasury→SubProv→Creator confirmed (WSOL_WRAP_CLOSE); SubProv (`BmFdpraQhkiD…`) shows 33 creators funded — strong fan-out evidence at the SubProv layer |
| 3aNojTm74D… | **Partially matches** | Same reasoning as above; SubProv (`5tzFkiKscXHK…`) shows 68 creators funded — the strongest fan-out evidence in this cohort |
| 5KtNnnPt7x… | **Diverges (mechanism + treasury)** | PLAIN_TRANSFER, not wrap-close; treasury (`FkccGTEh6tJe…`) not yet confirmed; SubProv shows only 1 recorded creator |
| 9wvwgFa2Ni… | **Partially matches** | Same SubProv as 3aNojTm74D (`5tzFkiKscXHK…`, 68 creators) — strong fan-out evidence |
| 7ri93jDVvo… | **Partially matches** | Same SubProv as 4cVTL5RNa9 (`BmFdpraQhkiD…`, 33 creators) |
| 4FWfPWMRX5… | **Partially matches** | Same SubProv as 3aNojTm74D/9wvwgFa2Ni (`5tzFkiKscXHK…`, 68 creators) |
| 2bFc6R3Wr8… | **Diverges (mechanism)** | PLAIN_TRANSFER; SubProv (`2EpHmj6CLGQJ…`) shows exactly 1 recorded creator — no fan-out evidence |
| EnEgmM4Eb6… | **Diverges (mechanism + treasury)** | PLAIN_TRANSFER; treasury (`FkccGTEh6tJe…`) not yet confirmed (same unconfirmed treasury as 5KtNnnPt7x); SubProv shows exactly 1 recorded creator |
| 5ejRBHFabF… | **Partially matches** | Same SubProv as 4cVTL5RNa9/7ri93jDVvo (`BmFdpraQhkiD…`, 33 creators) |
| 3zUqCv6rsq… | **Diverges (mechanism)** | PLAIN_TRANSFER; SubProv (`Co2Q6mEkB7iG…`) shows 0 recorded creators — no fan-out evidence at all |
| 5TW8ARthng… | **Partially matches** | Same SubProv as 5KNDHuNZZc (`Dv34prGm2BT7…`, 18 creators) |
| 7LxAGkCSxf… | **Partially matches** | Same SubProv as 3aNojTm74D/9wvwgFa2Ni/4FWfPWMRX5 (`5tzFkiKscXHK…`, 68 creators) |
| Ar3vVpZt2x… | **Diverges (mechanism)** | PLAIN_TRANSFER; SubProv (`52XHKRHELcqz…`) shows 0 recorded creators |
| 7CFsJrkPSb… | **Partially matches** | Same SubProv as 3aNojTm74D et al. (`5tzFkiKscXHK…`, 68 creators) |
| CnEgM3tCug… | **Partially matches** | Treasury→SubProv→Creator confirmed (WSOL_WRAP_CLOSE); SubProv (`iGdFcQoyR2Mw…`) shows 10 creators funded |

## Summary

| Classification | Count | % |
|---|---|---|
| Fully matches canonical topology | 0 | 0% |
| Partially matches | 13 | 68% |
| Diverges | 6 | 32% |
| Insufficient evidence | 0 | 0% |

**Zero launches "fully match"** the canonical topology as literally
drawn (with an explicitly-recorded multi-branch Provisioning Wallet
layer) — because, per Phase 2, that specific evidence layer
(`wt_candidate_websocket_watches` wrap-wallet/candidate-wallet rows) is
entirely absent for this cohort's sub-providers, regardless of whether
real fan-out exists at the sub-provider→creator layer. This is treated
as an evidence-coverage fact, not as proof the canonical shape is
wrong — 13 of 19 launches (68%) show real, independently-recorded
fan-out at the sub-provider layer (Phase 3) and are classified
"Partially matches," not "Diverges," because the recorded evidence is
consistent with (not contradictory to) the canonical shape.

## Individual divergence explanations

The 6 "Diverges" launches all diverge for the **same reason**: their
recorded funding mechanism is `PLAIN_TRANSFER`, not the wrap-close
mechanism the canonical model's provisioning-wallet concept
(X65.4/X65.6) was specifically built around, **and** their sub-provider
shows no recorded creator-fan-out evidence at all (0 or 1 creator).
This is not necessarily an error or a "bad" launch — per this
project's own prior finding (memory:
`distribution-funding-two-mode.md`, `treasuries-fund-treasuries.md`),
PLAIN_TRANSFER is a recognized, legitimate alternative provisioning
mechanism used alongside wrap-close, not a defect. Two of these six
(`5KtNnnPt7x…`, `EnEgmM4Eb6…`) additionally share an unconfirmed
treasury, which is reported as a separate, additional divergence
dimension for those two specifically — not conflated with the
mechanism divergence shared by all 6.
