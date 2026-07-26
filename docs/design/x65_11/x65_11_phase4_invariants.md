# X65.11 — Phase 4: Verify WATCHTOWER Operational Invariants

Per-launch verification of each requested invariant, against live,
directly-queried evidence — no invariant is assumed true from Campaign
membership alone.

## Creator: Fresh vs. Repeat

**19 of 19 (100%) are `FRESH_CREATOR`** — verified directly via
`creator_identity` field (`src/ops/creator_identity.py`), not assumed
from Campaign membership (this is one of Campaign's two mandatory
criteria, but is independently re-verified here rather than taken on
faith).

| Invariant | Count |
|---|---|
| Fresh creator | 19 |
| Repeat creator | 0 |

## Funding: mechanism per launch

| Invariant | Count | Launches |
|---|---|---|
| Account-close funding (WSOL_WRAP_CLOSE) | **13** | 5KNDHuNZZc, 2HBTVUsaor, ExL7K9dVVa, 4cVTL5RNa9, 3aNojTm74D, 9wvwgFa2Ni, 7ri93jDVvo, 4FWfPWMRX5, 5ejRBHFabF, 5TW8ARthng, 7LxAGkCSxf, 7CFsJrkPSb, CnEgM3tCug |
| Plain transfer (PLAIN_TRANSFER) | **6** | GtpUa2zbVc, 5KtNnnPt7x, 2bFc6R3Wr8, EnEgmM4Eb6, 3zUqCv6rsq, Ar3vVpZt2x |
| Other | 0 | — |

(13 WSOL_WRAP_CLOSE + 6 PLAIN_TRANSFER = 19, exactly the cohort size —
every launch has exactly one recorded mechanism, no launch has none or
more than one.)

## Provisioning: single-use vs. reused

**Cannot be verified for this cohort — insufficient evidence, not
assumed either way.** Per Phase 2/3, `wt_candidate_websocket_watches`
(the table that records the wrap-wallet-level single-use/not-reused
facts, per X65.4 Phase 3A's methodology) has **zero rows for any of
the 12 sub-providers in this cohort**. There is no separately-recorded
"provisioning wallet" row distinct from the sub-provider→creator
funding event itself for any of these 19 launches.

| Invariant | Count |
|---|---|
| Single-use provisioning wallet | 0 (verifiable) |
| Provisioning wallet reused | 0 (verifiable) |
| **Unknown** | **19** |

This is reported honestly as a coverage gap, not interpreted as "all
19 are reused" or "all 19 are single-use" — neither claim is supported
by any evidence found.

## SubProvider: multiple vs. single provisioning wallet

Using the same `wt_candidate_websocket_watches`-based measure as
above, this is likewise **Unknown for all 19** at the
provisioning-wallet layer specifically. However, at the
**sub-provider→creator** layer (a distinct, separately-evidenced
question — "has this sub-provider funded more than one creator,"
Phase 3's measure), the picture is mixed:

| Invariant (sub-provider→creator layer, Phase 3 evidence) | Count |
|---|---|
| Multiple creators funded (fan-out evidenced) | 5 sub-providers (covering 12 of 19 launches: 5tzFkiKscXHK×5, BmFdpraQhkiD×3, Dv34prGm2BT7×2, iGdFcQoyR2Mw×1, 8mowmVCEewZ9×1) |
| Single creator funded (no fan-out evidenced at this layer) | 4 sub-providers (covering 4 launches) |
| Unknown (0 creators recorded) | 3 sub-providers (covering 3 launches) |

## Treasury: known vs. unknown

| Invariant | Count | Launches |
|---|---|---|
| Known (confirmed) treasury | **17** | all except the 2 listed below |
| Unknown (not yet confirmed) treasury | **2** | 5KtNnnPt7x (treasury `FkccGTEh6tJe…`), EnEgmM4Eb6 (treasury `FkccGTEh6tJe…`, same wallet) |

Both "unknown treasury" launches share the **same** unconfirmed
treasury wallet (`FkccGTEh6tJe…`) — a single unconfirmed treasury
candidate, not two independent unknowns. Per this project's standing
constraint (never auto-confirm a treasury), this candidate remains
unconfirmed and is reported as such, not treated as evidence of a
process failure.
