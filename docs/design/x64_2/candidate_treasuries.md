# X64.2 — Phase 8/9: Hidden Treasury Discovery & Candidate Ranking

## Phase 8 — Attempted family grouping

A "family" requires at least one piece of *shared* stored evidence linking
two or more of the 18 launches beyond mere co-membership in this dataset
(shared disposable wallet across creators, shared unresolved upstream,
shared funding amount, shared vanity prefix, or a direct DB-confirmed
infrastructure link). Checked exhaustively:

- **Shared disposable wallet across DIFFERENT creators**: zero instances
  (the only recurring wallet, `Dbvr7ktCbxq…`, recurs under a single
  creator — see `creator_graph.md`).
- **Shared unresolved upstream (hop2)**: **not determinable at all** —
  `wt_walkback_edge_candidates` has zero rows for all 18 disposable
  wallets (see `x64_2_treasury_emergence.md` Phase 4). There is no stored
  hop2 evidence of any kind for this cohort, so "did multiple disposable
  wallets converge on the same unresolved upstream" cannot be answered
  from stored data — it is an open gap, not a negative finding.
- **Shared funding amount**: zero instances (18 distinct amounts).
- **Shared vanity/ground prefix**: zero instances, checked across both
  creator and disposable-wallet address sets (first 8 characters).
- **Shared infrastructure-table connection**: zero instances (Phase 3 of
  the master report — none of the 18 wallets/mints appear in
  `wt_discovered_subprovs`, `wt_confirmed_treasuries`, `wt_treasury_review`,
  or `watchtower_token_attribution`).

**Result: no family grouping can be constructed from stored evidence.**
Every one of the 18 launches is, on current data, an isolated single-hop
observation: `Unknown upstream (undetermined) → Disposable N → Creator N
→ Mint N`, with no cross-launch edge connecting any two of them except
the one same-creator wallet-reuse case already documented.

If a "family" is defined loosely as "shares nothing but co-occurrence in
a 40-hour window," then trivially all 18 form one group — but that
definition has no discriminating power (see `falsification.md`) and is
explicitly not treated as a family here.

## Phase 9 — Candidate treasury ranking

Given Phase 8's result, there is only one candidate worth scoring: the
proposition that **this 18-launch set as a whole represents a single
emerging treasury**. It is scored below and rejected. No sub-groups
exist to score individually because no sub-group has any shared evidence.

### Candidate: "Single treasury behind the 18-launch cohort"

| Signal | Present? | Detail |
|---|---|---|
| Shared disposable wallets (cross-creator) | No | 0 of 18 |
| Shared upstreams | Undetermined | 0 hop2 rows exist for any wallet — cannot confirm or deny |
| Creator overlap | Minimal | 1 of 17 creators self-repeats; 0 cross-creator links |
| Timing overlap | Weak | One 4-launch/9-minute density spike, otherwise evenly spread over 40h |
| Funding similarity | None | 0 shared amounts, no round-number clustering |
| Behaviour similarity | Partial | 17/18 share the generic rapid-migration (1-2s) signature — common to WATCHTOWER broadly, not distinctive to this cohort specifically |
| Existing WATCHTOWER markers | None | 0 of 18 connect to any confirmed treasury/subprov/attribution table |
| Recurrence | Minimal | 1 wallet reused, by its own creator only |

**Confidence: Low.**

**Explanation of score**: the only two signals present at all —
rapid-migration timing and one same-creator wallet reuse — are both
individually weak. Rapid (1-2s) migration is the generic X62/WATCHTOWER
handoff signature shared by essentially all `WSOL_WRAP_CLOSE`-funded
launches in this system (per prior session memory: 81% of confirmed
WATCHTOWER launches are "instant," so this is expected base-rate
behaviour for ANY mechanism-qualifying launch, not a distinguishing mark
of a specific treasury). One creator reusing its own disposable wallet
across two of its own launches is normal single-operator behaviour and
does not by itself imply a *treasury* (an upstream capital source funding
*multiple distinct sub-provisioners/creators*) — it is exactly as
consistent with one independent creator managing their own funding wallet
as it is with a larger operation. Every signal that would move this
above Low (shared upstream, shared wallet across different creators,
shared funding amount, any existing infrastructure link) is **absent**,
not merely weak.

No other candidate treasury groupings were found to score — Phase 8
produced no sub-clusters.
