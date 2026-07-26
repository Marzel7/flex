# X65.4 — Phase 4: Compare Expected vs Actual Classification

For each of the 43 confirmed WATCHTOWER launches, compares the
Phase 3 observed topology (from `wt_candidate_websocket_watches`
replay) against `funding_topology.py`'s actual classification.

## Comparison table

| Launch | Observed | Classified | Correct |
|---|---|---|---|
| JyJWcxa8xP... | NO_DATA | UNKNOWN | N/A (no observed evidence either way) |
| AB7XXeQAvN... | NO_DATA | (not scored — no `wt_attribution_outcomes` row) | N/A |
| 3gbBrgtwyx... | NO_DATA | (not scored) | N/A |
| Bn9kT53VKy... | LINEAR (1) | (not scored) | N/A |
| sP79aMCqfZ... | FAN_OUT (2) | (not scored) | N/A |
| 2PZAgPXXAU... | FAN_OUT (5) | (not scored) | N/A |
| 5iPoWhLAzo... | FAN_OUT (13) | (not scored) | N/A |
| 3SkdUCkXKX... | FAN_OUT (37) | (not scored) | N/A |
| 2vBvPiCpsb... | FAN_OUT (50) | (not scored) | N/A |
| GQEEL98udp... | FAN_OUT (45) | (not scored) | N/A |
| 6YqsppC6qj... | NO_DATA | (not scored) | N/A |
| 9x4NHggD8U... | FAN_OUT (179) | (not scored) | N/A |
| 9YXYH9A8b2... | FAN_OUT (57) | (not scored) | N/A |
| CQJzHVvpn3... | FAN_OUT (9) | **UNKNOWN** | **✗ Mismatch** |
| 7DZuY9tjXs... | FAN_OUT (35) | (not scored) | N/A |
| 6hDxh9uXFw... | FAN_OUT (15) | (not scored) | N/A |
| F2fcE5sjDu... | FAN_OUT (9) | (not scored) | N/A |
| 4MnczXgbDt... | FAN_OUT (26) | (not scored) | N/A |
| 5UQNY2hk4f... | FAN_OUT (11) | (not scored) | N/A |
| 6YZm2PVLBo... | FAN_OUT (255) | **UNKNOWN** | **✗ Mismatch** |
| CPtvQTf8bX... | FAN_OUT (60) | **UNKNOWN** | **✗ Mismatch** |
| 7YnzMgUvUj... | FAN_OUT (19) | **UNKNOWN** | **✗ Mismatch** |
| AshPvt8cws... | FAN_OUT (39) | **UNKNOWN** | **✗ Mismatch** |
| AyafwyhUhZ... | FAN_OUT (300) | **UNKNOWN** | **✗ Mismatch** |
| EN3kJPf6bv... | FAN_OUT (75) | **UNKNOWN** | **✗ Mismatch** |
| 3fc6tLVPx6... | FAN_OUT (106) | **UNKNOWN** | **✗ Mismatch** |
| F7NmdG9JAh... | FAN_OUT (218) | **UNKNOWN** | **✗ Mismatch** |
| EZozuXuPez... | FAN_OUT (167) | **UNKNOWN** | **✗ Mismatch** |
| 6SXTLNED1i... | FAN_OUT (61) | **UNKNOWN** | **✗ Mismatch** |
| AvLiJBdtb4... | FAN_OUT (239) | **UNKNOWN** | **✗ Mismatch** |
| 7pncD23yVt... | FAN_OUT (272) | **UNKNOWN** | **✗ Mismatch** |
| F612mB7c9p... | FAN_OUT (4) | **UNKNOWN** | **✗ Mismatch** |
| HHmh4bSYBX... | FAN_OUT (310) | **UNKNOWN** | **✗ Mismatch** |
| EeujXJZkoy... | FAN_OUT (34) | **UNKNOWN** | **✗ Mismatch** |
| 3xFT4J96Vz... | FAN_OUT (70) | (not scored) | N/A |
| 753AMCTdvo... | FAN_OUT (15) | **UNKNOWN** | **✗ Mismatch** |
| Ct2VDLuBan... | FAN_OUT (86) | **UNKNOWN** | **✗ Mismatch** |
| C4TFLdu1f2... | FAN_OUT (481) | **UNKNOWN** | **✗ Mismatch** |
| EQ6qQsweDh... | FAN_OUT (14) | **UNKNOWN** | **✗ Mismatch** |
| AwXtJ4QsZw... | FAN_OUT (2) | **UNKNOWN** | **✗ Mismatch** |
| FN7GB2Mf4p... | FAN_OUT (7) | (not scored) | N/A |
| 4SLVH8rtur... | FAN_OUT (54) | (not scored) | N/A |
| **EGB4sv9ddN...** | **FAN_OUT (25)** | **LINEAR** | **✗ Mismatch (direct contradiction)** |

"(not scored)" = the mint has no row in `wt_attribution_outcomes`
within the tested window, so `funding_topology.py` never assigns it
any classification at all (it is simply absent from Discovery's
topology view for that window, not wrongly labeled). These are
recorded honestly as N/A rather than forced into a correct/incorrect
judgment the data doesn't support.

## Summary counts (among the 21 launches that DID receive a classification)

| Result | Count |
|---|---|
| Correctly classified `FAN_OUT` | 0 |
| Misclassified `UNKNOWN` (real fan-out, evidence exists, classifier reports Unknown) | 20 |
| Misclassified `LINEAR` (real fan-out, classifier reports Linear) | 1 |
| Correctly classified `LINEAR`/`NO_DATA` (genuinely single-recipient or no evidence) | 0 (none of the classified launches fell into this bucket) |

**0 of the 21 classified, confirmed-fan-out launches were correctly
classified `FAN_OUT`.** Every single one is either wrongly `UNKNOWN`
or, in the starkest case (`EGB4sv9ddN...`, 25 observed recipients),
wrongly `LINEAR`.

## Explaining every mismatch

**The `UNKNOWN` mismatches (20 of 21)**: traced directly to
`wt_provisioning_edges` having **zero** `SUBPROV_TO_CREATOR` rows for
41 of the 43 confirmed subprovs (checked directly: `COUNT(DISTINCT
to_wallet) FROM wt_provisioning_edges WHERE edge_type=
'SUBPROV_TO_CREATOR' AND from_wallet=<subprov>` returns `0` for 42 of
43). This is because `wt_provisioning_edges` is populated exclusively
by the walkback success path (Phase 2), while these 43 launches were
confirmed via the live cascade's own real-time wrap-close detection
(`wt_watchtower_launches`) — a structurally separate write path that
never calls `capture_provisioning_relationship()`. With no sibling-count
evidence at all for the subprov, `classify_topology_for_launch()`
correctly falls through its own logic (Phase 1's decision tree, step
5c) to the walkback-fallback check, and when that also has no evidence
(these launches were confirmed by the cascade directly, not via a
walkback queue resolution), the result is `UNKNOWN` — a logically
consistent outcome **given the classifier's actual inputs**, but a
factually wrong description of the true, well-evidenced operational
topology.

**The `LINEAR` mismatch (`EGB4sv9ddN...`, 1 of 21)**: this mint's
`wt_attribution_outcomes.evidence_json` carries a single-element
`subprovisioners` list (`["ANenEukvmpYsyP52LgDsZN6kj3n7igjbJDTCtj4xCAXq"]`)
from an independent walkback resolution — but that same subprov wallet
has `edge_sibling_count=1` in `wt_provisioning_edges` (this mint is
apparently the only one whose walkback happened to resolve back to it,
so it has exactly 1 recorded creator edge). The classifier therefore
correctly applies its own rule ("`n_siblings == 1` → `LINEAR`") — but
Phase 3's direct replay shows this exact subprov wallet
(`ANenEukvmp...`) produced **25 distinct wrap-close destinations** in
`wt_candidate_websocket_watches`. The classifier's `LINEAR` label is
therefore a direct, demonstrable contradiction of the platform's own
already-recorded evidence for the very same wallet — not a
data-absence gap like the 20 `UNKNOWN` cases, but a genuine wrong
answer produced from an incomplete evidence source.

## Root-level pattern across all mismatches

Every mismatch in this table traces back to the same underlying cause
already identified in Phases 1/2: **the topology classifier only ever
counts a subprov's fan-out via how many distinct *creators* it has
been linked to through `wt_provisioning_edges`/walkback evidence,
never via the subprov's actual outbound wrap-close destination set**
(available in `wt_candidate_websocket_watches` but unused). Since the
walkback-populated edge table is essentially empty for this
cascade-confirmed cohort (42 of 43 subprovs have 0 recorded
`SUBPROV_TO_CREATOR` edges), the classifier has no evidence to work
with for almost this entire population and defaults to `UNKNOWN`,
except in the one case where a single stray walkback edge exists,
producing a confidently-wrong `LINEAR`.
