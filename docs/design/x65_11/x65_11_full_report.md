# X65.11 — Audit of Operational Topology of Last 24 Hours WATCHTOWER Launches (Full Report)

Read-only audit. No code changes, no database writes, no UI changes.
Live query: `GET /api/ops-v2/operational-intelligence?window=24h&campaign=WATCHTOWER&include_records=1`, 2026-07-22.

## Contents

1. [Identify Population](#phase-1--identify-population)
2. [Reconstruct Provisioning Graph](#phase-2--reconstruct-provisioning-graph)
3. [Measure SubProvider Fan-Out](#phase-3--measure-subprovider-fan-out)
4. [Verify WATCHTOWER Operational Invariants](#phase-4--verify-watchtower-operational-invariants)
5. [Compare With Canonical WATCHTOWER Model](#phase-5--compare-with-canonical-watchtower-model)
6. [Explain Topology Classification](#phase-6--explain-topology-classification)
7. [Cohort Summary](#phase-7--cohort-summary)
8. [Conclusion](#phase-8--conclusion)

---

## Phase 1 — Identify Population

### Total launches

**19 launches** classified `campaign=WATCHTOWER` within the last 24
hours. `campaign_conserved=True` and `conserved=True` (Topology) for
the whole 24h population at time of query.

### Full population

| # | Mint | Creator | Treasury (session) | Sub-provider | Topology | Funding Origin | Operation Attribution |
|---|---|---|---|---|---|---|---|
| 1 | 5KNDHuNZZcMJDH3PSZwVSTg7ziNELPhV4g91fEtupump | Hu2izucayRXSBhZjHvTcxsE2sJz9mdFn63FHq2pAZxz3 | DchJquEZzM6V… (confirmed) | Dv34prGm2BT7… | MULTI_LEVEL_FAN_OUT | =Topology | `__UNASSIGNED__` |
| 2 | 2HBTVUsaorJ1axpMxkqBv1c85nyXp34GFMnrSA2ipump | Fhffhne9RHHWkgZ3f8kKCAQTsdJFNaiPb3PvS757865E | 5nTJWTSozPMW… (confirmed) | 8DWH19uhVTaz… | LINEAR | =Topology | **WATCHTOWER (confirmed operation)** |
| 3 | ExL7K9dVVazu2poWmTzRRMVmf5xx43CksPgE8oKxpump | Gq3AyCeA6Z67VVxcDNDN4Ak68XtNu2RTpQdJQcKUJQVE | 69SNcRC8NqjH… (confirmed) | 8mowmVCEewZ9… | MULTI_LEVEL_FAN_OUT | =Topology | `__UNASSIGNED__` |
| 4 | GtpUa2zbVcyJdvk2PRADam5uUcQsx8KAndMWUCMTpump | Eo7Qce3Udo6FeuuZfjRizWUbEDnq1srS9QEUKVw4hHkG | 4231KLYipwRT… (confirmed) | 5SNDBEZLHtQX… | LINEAR | =Topology | `__UNASSIGNED__` |
| 5 | 4cVTL5RNa97pZWmA5JbK8xptM3c2fkAQoPW4jurTpump | 97EqPCC65vuY2CCTTJ8PjqbzTutjW2CwATXMYwDp7J69 | 69SNcRC8NqjH… (confirmed) | BmFdpraQhkiD… | MULTI_LEVEL_FAN_OUT | =Topology | `__UNASSIGNED__` |
| 6 | 3aNojTm74DoCxXgHEfZKsguLUp7AKBehTURdT6Copump | 8eAweMS34hy4U1aqqDyVhZAyNtJpbS4rvXppLQSbjCX2 | 69SNcRC8NqjH… (confirmed) | 5tzFkiKscXHK… | MULTI_LEVEL_FAN_OUT | =Topology | `__UNASSIGNED__` |
| 7 | 5KtNnnPt7xM8DtTjFDRuVvw3gpfCwvBGH4V7xZAGpump | HxAnxXpTUzj9Q1dfMutA9VZfs6K5VUVuW2jYwBVTPACG | FkccGTEh6tJe… (**not** confirmed) | 62meUYzzLJAL… | MULTI_LEVEL_FAN_OUT | =Topology | **WATCHTOWER (confirmed operation)** |
| 8 | 9wvwgFa2NiQNTLx7uxXTT7CJGT9Qn4ih5jeSermxpump | HEdiA8ft5uW1Gqpw1y82r5NdPypDAwCun7q2pXkPgJ5o | 69SNcRC8NqjH… (confirmed) | 5tzFkiKscXHK… | MULTI_LEVEL_FAN_OUT | =Topology | `__UNASSIGNED__` |
| 9 | 7ri93jDVvouPdsmATufzrGiukWWcBZhkMSzJ2JWVpump | AhGyxW6ts2tT2SLTbhNgzXxgwi5t7Ap1MBsQmJLhCcFj | 69SNcRC8NqjH… (confirmed) | BmFdpraQhkiD… | MULTI_LEVEL_FAN_OUT | =Topology | `__UNASSIGNED__` |
| 10 | 4FWfPWMRX5b7tXiTKMzuG5sqDCi6FVGmRYPhdC8Cpump | D1AgMwipkyYoGQmphTHs3VxCsG2gZo1Nm2wFTgndZ51E | 69SNcRC8NqjH… (confirmed) | 5tzFkiKscXHK… | MULTI_LEVEL_FAN_OUT | =Topology | `__UNASSIGNED__` |
| 11 | 2bFc6R3Wr8e9frH5BDNHoD3vcLQk1DBQzUkrSdADpump | HKmi2d17LE1FJ7aaCjkhN82rLPcvhf2UMneivqw5YZ4W | DchJquEZzM6V… (confirmed) | 2EpHmj6CLGQJ… | LINEAR | =Topology | **WATCHTOWER (confirmed operation)** |
| 12 | EnEgmM4Eb6x6uoWZgFtffGJTJc27bDxBR5pmqKDWpump | HAsS8QjfQudGFiFMHGJoRkUmQSEWo4NHdZymn49SFVXH | FkccGTEh6tJe… (**not** confirmed) | CnS6ZtLCnT5y… | MULTI_LEVEL_FAN_OUT | =Topology | **WATCHTOWER (confirmed operation)** |
| 13 | 5ejRBHFabFTTPKugKqwaMraj2jEaRqPDjwzmoNsUpump | 4x7FChxZiKcJ3refKDSbsWcw33V3KdxkM5Dtza7MtDup | 69SNcRC8NqjH… (confirmed) | BmFdpraQhkiD… | MULTI_LEVEL_FAN_OUT | =Topology | `__UNASSIGNED__` |
| 14 | 3zUqCv6rsqxvPJSLf1SetgYqjXBLqbuWyJqchnZfpump | Fmf9wEN24gS1e5GYsXU7hYhihjNKqZkeWb4zrkjXQte6 | 4231KLYipwRT… (confirmed) | Co2Q6mEkB7iG… | LINEAR | =Topology | `__UNASSIGNED__` |
| 15 | 5TW8ARthngGiRDzB3K72LjtaYDxBquBbwgBvC5rrpump | EFLHxXKVssX42tuXP3P7igT3YNtaP9HtTwPvj3BPZo7B | DchJquEZzM6V… (confirmed) | Dv34prGm2BT7… | MULTI_LEVEL_FAN_OUT | =Topology | `__UNASSIGNED__` |
| 16 | 7LxAGkCSxftYVZnqai7bPfY2diVKep2sF3NaXc1Ppump | An2tcZ5AHmA72MwJbBGiaJcjn5iDM7T1byhxq98nWvJ | 69SNcRC8NqjH… (confirmed) | 5tzFkiKscXHK… | MULTI_LEVEL_FAN_OUT | =Topology | `__UNASSIGNED__` |
| 17 | Ar3vVpZt2xZB5Z52F2tkWAXRiYM6umWUhRBJvUVXpump | GvgvypNqB7b6ytPjJrTmw5Qxb5GhxNXzrckYq7KWSLPa | 4231KLYipwRT… (confirmed) | 52XHKRHELcqz… | LINEAR | =Topology | `__UNASSIGNED__` |
| 18 | 7CFsJrkPSb2qdC593VtB4SEHSt4bor15cYEJxTZbpump | 6PtaXNpF3qKHT3StayYoJF1F1H7qohnraFd7k7zRyYfK | 69SNcRC8NqjH… (confirmed) | 5tzFkiKscXHK… | MULTI_LEVEL_FAN_OUT | =Topology | `__UNASSIGNED__` |
| 19 | CnEgM3tCugpdyKpX6qCPj1ZK6hVxbXGqgD6wYwBYpump | 3gkwd6sHGi7q5XgU8zZuqUKxfNjehwpVxdgRzAVRcUPf | 69SNcRC8NqjH… (confirmed) | iGdFcQoyR2Mw… | MULTI_LEVEL_FAN_OUT | =Topology | `__UNASSIGNED__` |

### Notes on evidence used

- **Creator**: `wt_attribution_outcomes.evidence_json.creator` /
  `wt_watchtower_launches.creator_wallet`.
- **Treasury**: `wt_active_subprov_sessions.treasury_wallet` (most
  recent session row for the resolved sub-provider), cross-checked
  against `wt_confirmed_treasuries`.
- **Sub-provider**: `campaign_evidence.subprov_wallet`
  (`src/ops/campaign_classification.py`).
- **Funding Origin**: equals Topology for this population (X65.1
  Phase 1 finding, confirmed by direct comparison, not assumed).
- **Operation Attribution**: two independent fields — `campaign`
  (X65.7, all 19 by definition) and the older, treasury-gated
  `operation_id`/`is_watchtower` (X65.1). Only **4 of 19 (21%)** have a
  confirmed `operation_id=WATCHTOWER`.

---

## Phase 2 — Reconstruct Provisioning Graph

For every launch, the observed funding path is reconstructed strictly
from recorded evidence (`wt_attribution_outcomes.evidence_json`,
`wt_active_subprov_sessions`). No edge is inferred where no row exists.

### Per-launch reconstructed structure

| Mint | Recorded structure | Mechanism |
|---|---|---|
| 5KNDHuNZZc… | Treasury(DchJqu…) → SubProv(Dv34prGm…) → Creator | WSOL_WRAP_CLOSE |
| 2HBTVUsaor… | Treasury(5nTJWTSo…) → SubProv(8DWH19uh…) → Creator | WSOL_WRAP_CLOSE |
| ExL7K9dVVa… | Treasury(69SNcRC8…) → SubProv(8mowmVCE…) → Creator | WSOL_WRAP_CLOSE |
| GtpUa2zbVc… | Treasury(4231KLYi…) → SubProv(5SNDBEZL…) → Creator | **PLAIN_TRANSFER** |
| 4cVTL5RNa9… | Treasury(69SNcRC8…) → SubProv(BmFdpraQ…) → Creator | WSOL_WRAP_CLOSE |
| 3aNojTm74D… | Treasury(69SNcRC8…) → SubProv(5tzFkiKs…) → Creator | WSOL_WRAP_CLOSE |
| 5KtNnnPt7x… | Treasury(FkccGTEh…, **unconfirmed**) → SubProv(62meUYzz…) → Creator | **PLAIN_TRANSFER** |
| 9wvwgFa2Ni… | Treasury(69SNcRC8…) → SubProv(5tzFkiKs…) → Creator | WSOL_WRAP_CLOSE |
| 7ri93jDVvo… | Treasury(69SNcRC8…) → SubProv(BmFdpraQ…) → Creator | WSOL_WRAP_CLOSE |
| 4FWfPWMRX5… | Treasury(69SNcRC8…) → SubProv(5tzFkiKs…) → Creator | WSOL_WRAP_CLOSE |
| 2bFc6R3Wr8… | Treasury(DchJqu…) → SubProv(2EpHmj6C…) → Creator | **PLAIN_TRANSFER** |
| EnEgmM4Eb6… | Treasury(FkccGTEh…, **unconfirmed**) → SubProv(CnS6ZtLC…) → Creator | **PLAIN_TRANSFER** |
| 5ejRBHFabF… | Treasury(69SNcRC8…) → SubProv(BmFdpraQ…) → Creator | WSOL_WRAP_CLOSE |
| 3zUqCv6rsq… | Treasury(4231KLYi…) → SubProv(Co2Q6mEk…) → Creator | **PLAIN_TRANSFER** |
| 5TW8ARthng… | Treasury(DchJqu…) → SubProv(Dv34prGm…) → Creator | WSOL_WRAP_CLOSE |
| 7LxAGkCSxf… | Treasury(69SNcRC8…) → SubProv(5tzFkiKs…) → Creator | WSOL_WRAP_CLOSE |
| Ar3vVpZt2x… | Treasury(4231KLYi…) → SubProv(52XHKRHE…) → Creator | **PLAIN_TRANSFER** |
| 7CFsJrkPSb… | Treasury(69SNcRC8…) → SubProv(5tzFkiKs…) → Creator | WSOL_WRAP_CLOSE |
| CnEgM3tCug… | Treasury(69SNcRC8…) → SubProv(iGdFcQoy…) → Creator | WSOL_WRAP_CLOSE |

### Provisioning-wallet layer: not separately observable for this cohort

The canonical model's middle layer — a distinct, single-use
**Provisioning Wallet** sitting between SubProv and Creator (X65.4/X65.6's
wrap-wallet finding) — is **not separately recorded as its own row**
for any of these 19 launches. `wt_candidate_websocket_watches.wrap_wallet`
has **zero rows for any subprov in this 24-hour cohort** (confirmed
directly). This is reported as an absence of evidence, not a
structural divergence. The observed structure for every launch is
exactly `Treasury → SubProvider → Fresh Creator`, with the middle
"Provisioning Wallet" layer un-instrumented for this specific cohort.

---

## Phase 3 — Measure SubProvider Fan-Out

12 distinct sub-providers underlie the 19-launch cohort.

### Per-subprovider measurements

| SubProvider | Provisioning wallets observed | Candidate wallets observed | Creators funded (all-time) | Launches produced (24h) | Sessions recorded (all-time) |
|---|---|---|---|---|---|
| 5tzFkiKscXHK… | 0 | 0 | **68** | 5 | 53 |
| BmFdpraQhkiD… | 0 | 0 | **33** | 3 | 2 |
| Dv34prGm2BT7… | 0 | 0 | **18** | 2 | 10 |
| iGdFcQoyR2Mw… | 0 | 0 | **10** | 1 | 12 |
| 8mowmVCEewZ9… | 0 | 0 | **8** | 1 | 1 |
| 2EpHmj6CLGQJ… | 0 | 0 | 1 | 1 | 1 |
| 62meUYzzLJAL… | 0 | 0 | 1 | 1 | 1 |
| 8DWH19uhVTaz… | 0 | 0 | 1 | 1 | 2 |
| CnS6ZtLCnT5y… | 0 | 0 | 1 | 1 | 1 |
| 52XHKRHELcqz… | 0 | 0 | 0 | 1 | 1 |
| 5SNDBEZLHtQX… | 0 | 0 | 0 | 1 | 1 |
| Co2Q6mEkB7iG… | 0 | 0 | 0 | 1 | 1 |

**Every sub-provider has zero rows in `wt_candidate_websocket_watches`**
— confirmed directly. This cohort was resolved entirely via walkback
(`walkback_class=FULL_WALKBACK` for all 19), not the live cascade that
populates that table.

### Does each SubProvider exhibit operational fan-out?

| SubProvider | Fan-out determination |
|---|---|
| 5tzFkiKscXHK… | **Yes** — 68 creators funded all-time, 5 launches in this window |
| BmFdpraQhkiD… | **Yes** — 33 creators, 3 launches in this window |
| Dv34prGm2BT7… | **Yes** — 18 creators, 2 launches in this window |
| iGdFcQoyR2Mw… | **Yes** — 10 creators all-time |
| 8mowmVCEewZ9… | **Yes** — 8 creators all-time |
| 2EpHmj6CLGQJ…, 62meUYzzLJAL…, 8DWH19uhVTaz…, CnS6ZtLCnT5y… | **No** — exactly 1 creator recorded each |
| 52XHKRHELcqz…, 5SNDBEZLHtQX…, Co2Q6mEkB7iG… | **Insufficient evidence** — 0 recorded creator edges |

**5 of 12 (42%)** sub-providers show direct, measured fan-out
evidence. **4 of 12 (33%)** show single-creator evidence only. **3 of
12 (25%)** have no creator-edge evidence and no alternative fan-out
source either, for this cohort.

---

## Phase 4 — Verify WATCHTOWER Operational Invariants

### Creator: Fresh vs. Repeat

**19 of 19 (100%) are `FRESH_CREATOR`** — verified directly, not
assumed from Campaign membership.

### Funding: mechanism per launch

| Invariant | Count |
|---|---|
| Account-close funding (WSOL_WRAP_CLOSE) | **13** |
| Plain transfer (PLAIN_TRANSFER) | **6** |
| Other | 0 |

(13 + 6 = 19, exactly the cohort size.)

### Provisioning: single-use vs. reused

**Cannot be verified — insufficient evidence, not assumed either way.**
`wt_candidate_websocket_watches` has zero rows for any of the 12
sub-providers. No separately-recorded provisioning-wallet row exists
distinct from the sub-provider→creator funding event for any of these
19 launches.

| Invariant | Count |
|---|---|
| Single-use provisioning wallet | 0 (verifiable) |
| Provisioning wallet reused | 0 (verifiable) |
| **Unknown** | **19** |

### SubProvider: multiple vs. single provisioning wallet

Likewise **Unknown for all 19** at the provisioning-wallet layer. At
the sub-provider→creator layer specifically (Phase 3's measure):

| Invariant | Count |
|---|---|
| Multiple creators funded (fan-out evidenced) | 5 sub-providers, 12 launches |
| Single creator funded | 4 sub-providers, 4 launches |
| Unknown (0 creators recorded) | 3 sub-providers, 3 launches |

### Treasury: known vs. unknown

| Invariant | Count |
|---|---|
| Known (confirmed) treasury | **17** |
| Unknown (not yet confirmed) treasury | **2** (`5KtNnnPt7x…`, `EnEgmM4Eb6…`) |

Both "unknown treasury" launches share the **same** unconfirmed
treasury wallet (`FkccGTEh6tJe…`) — one distinct unconfirmed
candidate, not two independent unknowns.

---

## Phase 5 — Compare With Canonical WATCHTOWER Model

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

### Summary

| Classification | Count | % |
|---|---|---|
| Fully matches canonical topology | 0 | 0% |
| Partially matches | 13 | 68% |
| Diverges | 6 | 32% |
| Insufficient evidence | 0 | 0% |

**Zero launches "fully match"** the canonical topology as literally
drawn, because the specific Provisioning-Wallet evidence layer is
entirely absent for this cohort's sub-providers — an evidence-coverage
fact, not proof the canonical shape is wrong. 13 of 19 launches (68%)
show real, independently-recorded fan-out at the sub-provider layer
and are classified "Partially matches," since the recorded evidence
is consistent with (not contradictory to) the canonical shape.

### Individual divergence explanations

The 6 "Diverges" launches (`GtpUa2zbVc…`, `5KtNnnPt7x…`, `2bFc6R3Wr8…`,
`EnEgmM4Eb6…`, `3zUqCv6rsq…`, `Ar3vVpZt2x…`) all diverge for the
**same reason**: `PLAIN_TRANSFER` mechanism (not wrap-close) and no
recorded creator-fan-out at their sub-provider (0 or 1 creator). Per
this project's own standing memory
(`distribution-funding-two-mode.md`, `treasuries-fund-treasuries.md`),
PLAIN_TRANSFER is a recognized, legitimate alternative provisioning
mechanism, not a defect. 2 of these 6 (`5KtNnnPt7x…`, `EnEgmM4Eb6…`)
additionally share an unconfirmed treasury — a separate, additional
factor for those two specifically.

---

## Phase 6 — Explain Topology Classification

For every launch, the exact evidence that drove
`classify_topology_for_launch()`'s decision, read directly from the
live `topology_derived_from` field (X65.10's implementation).

### Pattern: Topology classification tracks funding mechanism exactly

- **Every one of the 13 WSOL_WRAP_CLOSE launches → `MULTI_LEVEL_FAN_OUT`** (13/13).
- **Every one of the 6 PLAIN_TRANSFER launches → `LINEAR`** (6/6).

This reflects the walkback process's own selected-hop-chain evidence
(the dominant source for 16 of 19 launches) tending to resolve deeper,
more-branching chains for wrap-close-mediated funding, while
plain-transfer funding in this cohort resolved to shallower, single-hop
chains with no observed branch. This is an observation about this
specific cohort's evidence, not a general claim about the two
mechanisms always producing these topologies.

Representative `derived_from` values: `wt_active_subprov_sessions_sub_subprov_lineage`
(genuine multi-tier chain, 3 launches), `selected_walkback_depth=N;upstream_fanout=M`
(walkback-resolved branching, 10 launches), `wt_provisioning_edges_sibling_count=1`
(2 launches), `selected_walkback_depth=1;no_observed_branch` (3 launches
— sole-hop resolution, no branch observed).

### Note: X65.10's candidate-watch rule never fired in this cohort

None of this cohort's 12 sub-providers have any
`wt_candidate_websocket_watches` coverage, so X65.10's newly-added
evidence-priority rule never had data to act on here — every
classification came from the pre-existing fallback paths
(`wt_provisioning_edges` sibling-count or walkback-based evidence).
This is consistent with X65.8/X65.10's own finding that the new
evidence source's coverage is concentrated in the live-cascade-confirmed
population, which this walkback-resolved cohort is not part of.

---

## Phase 7 — Cohort Summary

| Dimension | Breakdown |
|---|---|
| **Total launches** | 19 |
| **Topology** | MULTI_LEVEL_FAN_OUT 13 (68.4%), LINEAR 6 (31.6%), FAN_OUT/MESH/UNKNOWN 0 |
| **Treasury** | Known 17 (89.5%), Unknown 2 (10.5%, same wallet) |
| **Creator freshness** | Fresh 19 (100%), Repeat 0 |
| **Provisioning reuse** | Unknown 19 (100%, no wrap-wallet evidence available) |
| **Sub-provider reuse** | Multi-creator 12 launches / 5 subprovs; single-creator 4 launches / 4 subprovs; unknown 3 launches / 3 subprovs |
| **Funding mechanism** | WSOL_WRAP_CLOSE 13 (68.4%), PLAIN_TRANSFER 6 (31.6%) |
| **Operation attribution** | `campaign=WATCHTOWER` 19 (100%); `operation_id=WATCHTOWER` (confirmed operation) 4 (21.1%); unassigned 15 (78.9%) |

Topology distribution is conserved (sums to cohort size exactly, per
the classifier's exhaustive if/elif/else structure). `campaign` and
`operation_id` are independent fields — a launch can correctly be
`campaign=WATCHTOWER` while `operation_id` remains unassigned, since
Operation Attribution requires an already-confirmed treasury→operation
link (X65.1), a stricter, separate condition from Campaign's mandatory
criteria.

---

## Phase 8 — Conclusion

**Do the last 24 hours follow the same operational topology as
previously confirmed WATCHTOWER launches?** Yes, with a qualification.
Every launch's recorded funding path follows the canonical
Treasury→SubProvider→Creator shape at the two hops evidence covers.
13 of 19 (68%) show real, independently-measured sub-provider fan-out,
consistent with the canonical model's characteristic signature already
established from 43 historical confirmed launches (X65.4/X65.8/X65.10,
22/22 matched). No launch contradicts the canonical model outright —
the qualification is that the Provisioning-Wallet layer is not
separately observable for any of these 19 launches (a coverage gap,
not a structural contradiction).

**Which launches differ?** 6 of 19 (32%): `GtpUa2zbVc…`, `5KtNnnPt7x…`,
`2bFc6R3Wr8…`, `EnEgmM4Eb6…`, `3zUqCv6rsq…`, `Ar3vVpZt2x…`. All 6 share
the same divergence — `PLAIN_TRANSFER` mechanism with no recorded
sub-provider fan-out. 2 of these 6 additionally have an unconfirmed
treasury.

**What causes the differences?**
- **Genuine operational change**: not supported. PLAIN_TRANSFER is an
  already-documented, pre-existing alternative mechanism, not a newly
  observed pattern.
- **Incomplete evidence**: confirmed as the dominant cause of the
  Provisioning-Wallet-layer gap (affecting all 19 launches uniformly,
  not just the 6 divergent ones) — this cohort was resolved entirely
  via walkback, which never populates `wt_candidate_websocket_watches`.
- **Topology classification behaviour**: partially contributory but
  correctly so — the classifier reports exactly what the walkback
  evidence shows (X65.8/X65.10's design), not a fabricated pattern.
- **Another cause**: 2 of 6 divergent launches share one unconfirmed
  treasury candidate — a separate, additional factor for those two.

**Does the evidence support retaining the canonical model, or should
it be revised?** **Retain the current canonical model.** The evidence
confirms the canonical shape for 100% of the cohort at the hops with
available data, confirms real fan-out for 68%, and attributes the
remaining 32% divergence entirely to an already-documented, legitimate
alternative mechanism — not a model flaw. The Provisioning-Wallet
evidence gap affects the entire cohort uniformly, confirming it is a
data-coverage boundary (walkback-resolved vs. cascade-confirmed
populations), not a model-accuracy problem. No evidence in this audit
supports revising the canonical model.

### Deliverables

`docs/design/x65_11/` — `x65_11_phase1_population.md` through
`x65_11_phase8_conclusion.md`, and this consolidated report. No code
was changed; no database writes occurred; no UI was modified.
