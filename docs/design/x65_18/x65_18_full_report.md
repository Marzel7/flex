# X65.18 — Audit the WATCHTOWER Topology "Fan-Out" UI Data Lineage (Full Report)

Read-only source-code audit. No code changes, no database writes, no UI changes.
Scope: the Discovery page (`templates/discovery.html`), the only WATCHTOWER Discovery
surface per the task's title. `templates/watchtower_operational_intelligence.html`'s
own `fanin`/`fanout`/`fanout_count` fields (a separate dashboard, fed by a different
endpoint, `/api/ops-v2/intel/role-scores/<wallet>`) were located during Phase 1's
search but are explicitly out of scope — noted, not traced, since they belong to a
different page than the one this task names.

## Contents

1. [Locate the UI](#phase-1--locate-the-ui)
2. [Trace the Data Flow](#phase-2--trace-the-data-flow)
3. [Identify the Origin](#phase-3--identify-the-origin)
4. [Reconstruct the Calculation](#phase-4--reconstruct-the-calculation)
5. [Validate the Topology Labels](#phase-5--validate-the-topology-labels)
6. [Compare Against X65.17](#phase-6--compare-against-x6517)
7. [Compare Against the WATCHTOWER Operational Model](#phase-7--compare-against-the-watchtower-operational-model)
8. [Documentation](#phase-8--documentation)

---

## Phase 1 — Locate the UI

Exhaustive search of `templates/discovery.html` for Fan-Out/Fanout/Topology/Provisioning
display surfaces found **three genuinely distinct UI elements**, each with its own data
path:

| # | UI element | Location | What it shows |
|---|---|---|---|
| 1 | **Topology distribution card** | `renderTopologyDistribution()`, `discovery.html:1697-1703` | Per-launch topology bucket counts: Multi-Level Fan-Out / Fan-Out / Linear / Unknown, with tooltips |
| 2 | **Campaign fingerprint stats** ("Fan-out observed") | inline in the Campaign-section renderer, `discovery.html:1646-1673` | A boolean-derived percentage: what fraction of WATCHTOWER-campaign launches have `fan_out_observed=true` |
| 3 | **Lineage node "Fan-out" property** | `lineageNodeCard()`, `discovery.html:1189-1190` | A per-SubProvider integer, shown as "N downstream wallets," inside the entity lineage graph view |
| 4 | **Provisioning Activity card** | `provisioningActivityCard()`, `discovery.html:2608-2613` | Two raw global counts: "Observed edges" and "Provisioning sessions" (already correctly labeled, not claiming to be fan-out) |

All four are distinct code paths with distinct data sources — confirmed by tracing
each independently below, not assumed to be the same mechanism.

---

## Phase 2 — Trace the Data Flow

### Element 1: Topology distribution card

```
renderTopologyDistribution() [discovery.html:1697]
    ↑ x60TopologyRows() [discovery.html:1448-1451] -- filters x60CampaignRows() by r.topology
        ↑ x60CampaignRows() [discovery.html:1434] -- filters x60CreatorIdentityRows()
            ↑ (chain of x60...Rows() functions, ultimately reading the flat records array)
                ↑ fetch('/api/ops-v2/operational-intelligence?...') [discovery.html:2388]
                    ↑ api_operational_intelligence() [operation_dashboard_routes.py:8741]
                        ↑ _get_operational_intelligence() → build_operational_intelligence()
                            ↑ build_topology_classification() [funding_topology.py:369]
                                ↑ classify_topology_for_launch() [funding_topology.py, per-launch decision]
                                    ↑ _subprov_sibling_counts() [funding_topology.py:58] reads wt_provisioning_edges
                                    ↑ _subprov_candidate_watch_counts() [funding_topology.py:72] reads wt_candidate_websocket_watches
```

### Element 2: Campaign fingerprint "Fan-out observed" stat

```
fpStat('Fan-out observed', fingerprint.fan_out) [discovery.html:1672]
    ↑ fingerprint.fan_out++ if ev.fan_out_observed [discovery.html:1656]
        ↑ ev = r.campaign_evidence [discovery.html:1648]
            ↑ same operational-intelligence fetch as Element 1, but the campaign_evidence field
                ↑ build_campaign_classification() [campaign_classification.py]
                    ↑ classify_campaign_for_launch() [campaign_classification.py:349]
                        ↑ _fanout_evidence_for_subprovs() [campaign_classification.py:158-184]
                            ↑ reads wt_candidate_websocket_watches ONLY (no wt_provisioning_edges reference anywhere in this function)
```

### Element 3: Lineage node "Fan-out" property

```
lineageNodeCard() [discovery.html:1187-1190]
    ↑ node.properties.fan_out_count
        ↑ fetch('/api/ops-v2/lineage/'+wallet) [discovery.html:1222]
            ↑ api route → build_lineage() [operational_lineage.py:265]
                ↑ _node_properties(role=SUBPROVIDER) [operational_lineage.py:252-257]
                    ↑ _fan_out_count() [operational_lineage.py:130-150]
                        ↑ reads wt_provisioning_edges (SUBPROV_TO_CREATOR) UNION wt_watchtower_launches.subprov_wallet
```

### Element 4: Provisioning Activity card

```
provisioningActivityCard(summary.provisioning_activity) [discovery.html:2636]
    ↑ fetch('/api/ops-v2/discovery-triage/summary') [discovery.html:2627]
        ↑ _provisioning_activity_status() [discovery_triage.py:109-124]
            ↑ SELECT COUNT(*) FROM wt_provisioning_edges (global, unfiltered)
            ↑ SELECT COUNT(*) FROM wt_provisioning_sessions (global, unfiltered)
```

---

## Phase 3 — Identify the Origin

| UI element | Proven origin table(s) | Filtered/global |
|---|---|---|
| Topology distribution card | `wt_provisioning_edges` (fallback path) **and/or** `wt_candidate_websocket_watches` (primary path, consulted first per line 313-327) **and/or** `wt_active_subprov_sessions` (for MULTI_LEVEL_FAN_OUT/MESH) | Per-launch, per-subprov |
| Campaign fingerprint "Fan-out observed" | `wt_candidate_websocket_watches` **only** — confirmed by reading `_fanout_evidence_for_subprovs()`'s full body, which contains zero references to `wt_provisioning_edges` | Per-subprov, aggregated across the WATCHTOWER-campaign cohort |
| Lineage "Fan-out" property | `wt_provisioning_edges` (`SUBPROV_TO_CREATOR`) UNION `wt_watchtower_launches.subprov_wallet` | Per-subprov, single wallet queried |
| Provisioning Activity card | `wt_provisioning_edges` + `wt_provisioning_sessions`, unfiltered `COUNT(*)` | Global, not per-launch or per-subprov |

No UI element traced in this audit sources its number from live computation, a
graph-reconstruction step, `wt_swarm_buys`, or "another source entirely" — every path
terminates at one or more of the four tables named above, each confirmed by direct
citation, not inferred.

---

## Phase 4 — Reconstruct the Calculation

### Element 1 (Topology card): does it measure total recipients, confirmed creators, or something else?

**Both, depending on which evidence source fires for a given subprov — and this is
the single most important nuance this audit establishes.** `classify_topology_for_launch()`
(`funding_topology.py:307-327`) tries `wt_candidate_websocket_watches` (raw,
pre-creator-filter recipient count) **first**; only if that table has zero coverage
for the subprov does it fall back to `wt_provisioning_edges`' sibling count
(confirmed-creator count, per X65.17's proof). The `derived_from` field on every
classification result explicitly records which source fired
(`wt_candidate_websocket_watches_count=N` vs. `wt_provisioning_edges_sibling_count=N`),
so **the correct answer is source-dependent, and the UI does not currently surface
which source produced a given launch's label** — a genuine limitation identified here.

### Element 2 (Campaign fingerprint): confirmed creators or total recipients?

**Total recipients (raw fan-out), unambiguously.** `_fanout_evidence_for_subprovs()`
reads `wt_candidate_websocket_watches` exclusively (`COUNT(*) ... GROUP BY subprov_wallet`,
`campaign_classification.py:179-184`) — every `candidate_wallet` row this table holds
is, per X65.16's established finding, recorded regardless of whether that candidate
ever became a confirmed creator. **This specific metric is correctly named** — it
measures genuine raw fan-out, not confirmed-creator count.

### Element 3 (Lineage "Fan-out" property): confirmed creators or total recipients?

**Confirmed creators only, always.** `_fan_out_count()`'s own docstring
(`operational_lineage.py:131`) states plainly: "Distinct creators this subprov has
funded." Both of its two sources (`wt_provisioning_edges.SUBPROV_TO_CREATOR`,
X65.17-proven creator-only; `wt_watchtower_launches.creator_wallet`, cascade-confirmed
creator column by table definition) are creator-only tables. **This metric is
mislabeled in the UI** — `discovery.html:1190` renders it as "N downstream wallet(s),"
language that implies raw recipient count, when the underlying value is always a
confirmed-creator count.

### Element 4 (Provisioning Activity card): what does it measure?

**A raw table-size health indicator, not a fan-out metric at all.** Already correctly
labeled ("Observed edges" / "Provisioning sessions," not "fan-out"); no correction
needed.

---

## Phase 5 — Validate the Topology Labels

Full decision logic, `classify_topology_for_launch()` (`funding_topology.py`),
evaluated in this exact order (first match wins, per the module's own documented
`TOPOLOGY_ORDER`):

1. **Walkback-derived MULTI_LEVEL_FAN_OUT** (lines ~270-285): if the selected
   walkback chain has depth ≥2 with a branching parent (`upstream_fanout` from
   `_walkback_topology_evidence()`), label MULTI_LEVEL_FAN_OUT,
   `derived_from=selected_walkback_depth=N;upstream_fanout=M`.
2. **No lineage evidence at all** (line 287-288): UNKNOWN,
   `derived_from=no_lineage_evidence`.
3. **Session-lineage MULTI_LEVEL_FAN_OUT** (lines 290-297): if any involved subprov
   is itself recorded as a child of another subprov session
   (`wt_active_subprov_sessions`), label MULTI_LEVEL_FAN_OUT,
   `derived_from=wt_active_subprov_sessions_sub_subprov_lineage`.
4. **MESH** (lines 299-305): if any involved treasury is itself structurally a
   subprov elsewhere, label MESH, `derived_from=treasury_also_subprov_elsewhere`.
5. **FAN_OUT vs. LINEAR, candidate-watch path** (lines 307-327): if
   `wt_candidate_websocket_watches` has coverage for the subprov: **>1 distinct
   candidate wallet → FAN_OUT**; **exactly 1 → LINEAR**. This is checked
   **before** the sibling-count fallback specifically because it covers 90.7% of
   the cascade-confirmed population vs. 2.3% for the fallback (comment,
   line 308-310, citing X65.8 Phase 2's own measurement).
6. **FAN_OUT vs. LINEAR, sibling-count fallback** (lines 328-340): only reached if
   the subprov has zero `wt_candidate_websocket_watches` coverage. **>1 distinct
   confirmed-creator recipient → FAN_OUT**; **exactly 1 → LINEAR**.
7. Further fallbacks (walkback-fanout-based Unknown resolution, not reproduced in
   full here) for subprovs with neither evidence source populated.

**Threshold used throughout: `count > 1` → FAN_OUT/MULTI_LEVEL_FAN_OUT, `count == 1`
→ LINEAR.** No other numeric threshold (e.g., ≥5, ≥10) exists anywhere in this
decision logic — X65.14/X65.15's own use of "≥5" as a Strong-Candidate cutoff was a
threshold **those audits introduced themselves** for a different purpose (partitioning
Campaign launches by evidence strength), not a value read from or matching this
classifier's own FAN_OUT/LINEAR boundary (which is simply >1).

---

## Phase 6 — Compare Against X65.17

X65.17 proved `wt_provisioning_edges` structurally can only ever contain confirmed
creator recipients, never raw fan-out. Applying that finding to each element traced in
this audit:

| UI element | Uses `wt_provisioning_edges`? | Verdict |
|---|---|---|
| Topology card | **Sometimes** (fallback path only, ~2.3%-9.1% of cases per the code's own cited coverage measurements) | **Where this fallback fires, "Fan-Out"/"Linear" labels are technically measuring confirmed-creator count, not raw fan-out** — X65.17 applies directly and the label is imprecise for this subset. Where the primary `wt_candidate_websocket_watches` path fires instead (the large majority), the label is accurate, since that table is genuinely raw fan-out (X65.16-established). |
| Campaign fingerprint "Fan-out observed" | **No** — reads `wt_candidate_websocket_watches` exclusively | **X65.17 does not apply here.** This metric is correctly named; it measures genuine raw recipient fan-out. |
| Lineage "Fan-out" property | **Yes**, always (one of its two union sources, and the other source — `wt_watchtower_launches.creator_wallet` — is equally creator-only by the table's own schema) | **X65.17 applies directly and fully.** The value is always a confirmed-creator count. The UI's own wording ("N downstream wallets") is the least accurate of the four elements audited — it should read "N confirmed creators funded" per X65.17's finding. |
| Provisioning Activity card | Yes, but never labeled "fan-out" | Not applicable — already correctly worded. |

---

## Phase 7 — Compare Against the WATCHTOWER Operational Model

```
Treasury → SubProvider → Many recipient wallets → One creator → Launch
```

- **Campaign fingerprint "Fan-out observed"** is the only element in this audit that
  genuinely represents "many recipient wallets" — the complete, raw SubProvider
  fan-out step of the model, unfiltered by creator status.
- **Lineage "Fan-out" property** represents only "one creator," repeated across
  however many distinct launches this platform has confirmed for that subprov — it
  never reaches the "many recipient wallets" step of the model at all.
- **Topology card** represents a **derived, mixed abstraction**: sometimes the "many
  recipient wallets" step (when `wt_candidate_websocket_watches` fires), sometimes
  only the "one creator" step repeated across launches (when the
  `wt_provisioning_edges` fallback fires) — collapsed into the same visual label
  (FAN_OUT) without the UI distinguishing which evidentiary case produced it.
- **Provisioning Activity card** represents neither step directly — it is a
  system-health indicator (how many edges/sessions exist at all), not a
  representation of any specific launch's or subprov's position in the model.

**None of the four elements represents "complete SubProvider topology" in full** (no
element shows the actual set of recipient wallets alongside their individual
creator/non-creator status) — the closest is the Campaign fingerprint stat, which
correctly reports *whether* fan-out was observed but not the recipient list itself.

---

## Phase 8 — Documentation

| Metric (UI label) | Source table(s) | Calculation | Exact meaning | Limitations | What it is NOT |
|---|---|---|---|---|---|
| **Topology: Fan-Out / Linear / Multi-Level Fan-Out / Unknown** | `wt_candidate_websocket_watches` (primary) or `wt_provisioning_edges` (fallback) or `wt_active_subprov_sessions` (multi-level/mesh) | `count > 1` → FAN_OUT/MULTI_LEVEL; `count == 1` → LINEAR; session-lineage/mesh checks take precedence when they fire | The topology label for one launch, derived from whichever evidence source had coverage for its subprov, in priority order | The UI does not surface which evidence source produced the label; when the fallback source fires, the count is confirmed-creators, not raw recipients | Not a uniform, single-definition metric — its precision depends on evidence-source coverage, invisible to the viewer |
| **Campaign fingerprint: "Fan-out observed"** | `wt_candidate_websocket_watches` only | Boolean: `COUNT(*) > 1` distinct candidate wallets for the subprov | Whether this subprov was observed, by the live cascade, funding more than one raw recipient (creator status irrelevant) | Only covers launches whose subprov was live-cascade-observed; silently `False` (not "unknown") when absent, per the module's own documented convention | Not a total-recipient count — it's a boolean; not scoped to `wt_provisioning_edges` at all |
| **Lineage graph: "Fan-out — N downstream wallets"** | `wt_provisioning_edges` (SUBPROV_TO_CREATOR) ∪ `wt_watchtower_launches.subprov_wallet` | `COUNT(DISTINCT creator wallet)` across both sources | The number of distinct, confirmed token creators this subprov has ever funded | Always a strict undercount of true total fan-out, since non-creator recipients are structurally invisible to both sources (X65.17) | **Not** "downstream wallets" in the general sense — it is specifically "downstream creators"; the current UI wording is imprecise |
| **Provisioning Activity: "Observed edges" / "Provisioning sessions"** | `wt_provisioning_edges`, `wt_provisioning_sessions` | Global, unfiltered `COUNT(*)` | Whether X21B's provisioning-capture pipeline has recorded any facts at all, system-wide | Not scoped to any specific launch, subprov, or treasury — a coarse system-health signal only | Not a fan-out metric of any kind; already correctly worded |

### Corrected terminology recommendation

- **Lineage graph property** (`discovery.html:1190`): change "Fan-out — N downstream
  wallet(s)" to **"Creators funded — N confirmed creator(s)"** — the single clearest,
  lowest-risk correction identified in this audit, since the underlying value is
  always creator-count and the current wording actively implies raw recipient count.
- **Topology card**: no wording change is strictly required (FAN_OUT/LINEAR are the
  classifier's own established names), but consider surfacing `derived_from` in the
  UI (already computed server-side and already present in the API response) so a
  viewer can distinguish a candidate-watch-derived FAN_OUT from a
  provisioning-edges-derived one — closing the one genuine ambiguity this audit found
  in an otherwise correctly-implemented classifier.
- **Campaign fingerprint stat**: no change needed — already correctly represents raw
  fan-out.
- **Provisioning Activity card**: no change needed — already correctly worded as
  "edges"/"sessions," never "fan-out."

### Deliverables

Complete UI→backend→SQL→table lineage for all four Discovery-page elements
displaying fan-out/topology/provisioning concepts (Phase 1-3); reconstructed
calculation for each, establishing that two of the four measure raw recipient
fan-out and two measure confirmed-creator count exclusively (Phase 4); full
topology-classification decision order and thresholds, all `count > 1` boundaries,
with source citations (Phase 5); a direct, element-by-element comparison against
X65.17's proof, finding it applies fully to one element, partially to another
(evidence-source-dependent), and not at all to a third (Phase 6); comparison against
the documented WATCHTOWER operational model, finding no single UI element represents
the complete model (Phase 7); and a full documentation table plus one concrete,
narrowly-scoped wording correction recommendation (Phase 8). No code was changed; no
database writes occurred; no UI was modified.
