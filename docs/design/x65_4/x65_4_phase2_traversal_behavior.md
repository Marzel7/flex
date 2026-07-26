# X65.4 — Phase 2: Verify Graph Traversal (Option A vs Option B)

## Question

For every launch classified `LINEAR` or `MULTI_LEVEL_FAN_OUT`, does the
algorithm:
- **Option A**: only follow Treasury → SubProv → Creator (creator
  ancestry only), or
- **Option B**: expand Treasury → SubProv → {siblings, ..., Creator}
  (full operational fan-out)?

## Answer: Option A is implemented today — confirmed at both the write side and the read side

**Write side** (`src/ops/provisioning_edges.py`,
`capture_provisioning_relationship()`, lines 150-205): a
`SUBPROV_TO_CREATOR` edge is only ever written when the function is
called with a specific, already-known `creator` argument (line 195:
`if subprov and creator:`). This function is called exclusively from
the walkback success path — i.e., from code that is resolving **one
mint's own creator lineage**, working backward from a known creator to
find its funder. There is no code path anywhere in this module, or
anywhere found via a full-file inspection, that queries a subprov
wallet's outbound transfers to discover recipients other than the one
creator already being walked back from.

**Read side** (`src/ops/funding_topology.py`,
`_subprov_sibling_counts()`, lines 58-69): computes
`COUNT(DISTINCT to_wallet)` grouped by `from_wallet` over
`wt_provisioning_edges WHERE edge_type='SUBPROV_TO_CREATOR'`. Because
the write side (above) only ever inserts a row when the `to_wallet` is
a **confirmed creator**, this count structurally can only ever be "how
many different creators has this subprov been recorded funding, across
however many separate mints' walkbacks happened to resolve to it" — it
is mathematically incapable of counting non-creator sibling recipients,
because no edge type exists to record them (schema CHECK constraint,
`provisioning_edges.py:50`, restricts `edge_type` to
`'TREASURY_TO_SUBPROV'`/`'SUBPROV_TO_CREATOR'` only).

**Walkback-fallback path** (`_walkback_topology_evidence()`,
`funding_topology.py:151-191`): reads `wt_walkback_edge_candidates`
(`selection_status='SELECTED'`) and `wt_walkback_queue.termination_reason_json`.
Both of these are, by construction, the **selected** hop-chain for
resolving **one specific mint's** own funder lineage — the walkback
process does not expand sideways to enumerate a parent wallet's other
children as part of a single mint's walk; "fan-out" in this fallback
path is detected only when the **same parent wallet happens to have
been separately selected as the parent hop for more than one other
mint's own independent walkback**, which is again creator-ancestry
counting (across mints), not a single-provisioning-window graph
expansion.

## Explicit confirmation: no sibling expansion exists anywhere in the classifier

A full read of `funding_topology.py` (392 lines, the entire module)
confirms there is no code path that:
- queries a subprov's or wrap-wallet's outbound transaction history
  directly,
- reads `wt_candidate_websocket_watches` (the table that DOES already
  capture every wrap-close destination a subprov has ever produced,
  confirmed in Phase 1 — zero references to this table anywhere in
  `funding_topology.py`),
- or otherwise enumerates any wallet's fan-out within a bounded
  provisioning-window / single-cycle sense at all.

## Conclusion

**Option A (creator-only ancestry walk) is the behaviour implemented
today**, for every classification path in `funding_topology.py` —
`LINEAR`, `FAN_OUT`, and both `MULTI_LEVEL_FAN_OUT` variants. `FAN_OUT`
as currently computed does not mean "this subprov fanned out to
multiple wallets in one provisioning cycle" — it means "this subprov
has been independently linked, via separate walkbacks, to more than
one creator, across however much history exists in
`wt_provisioning_edges`." This is a materially different claim from
the operational reality documented in Phase 1/3 (a subprov's single
wrap-close-fanout cycle routinely produces dozens to hundreds of
candidate wallets, of which only one becomes the confirmed creator).
