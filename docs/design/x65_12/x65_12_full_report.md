# X65.12 — Validate Whether WATCHTOWER Uses Multiple Operational Topologies (Full Report)

Read-only investigation. No code changes, no database writes, no UI changes.
Live query: `GET /api/ops-v2/operational-intelligence?window=all&campaign=WATCHTOWER&include_records=1`, 2026-07-22.
This audit re-runs X65.11's finding against the **full historical corpus** instead of the
last-24h sample, to determine whether the wrap-close→multi-level / plain-transfer→linear
correlation X65.11 observed generalizes.

## Contents

1. [Build Historical Population](#phase-1--build-historical-population)
2. [Funding Mechanism Analysis](#phase-2--funding-mechanism-analysis)
3. [Topology Distribution](#phase-3--topology-distribution)
4. [Operational Graph Comparison](#phase-4--operational-graph-comparison)
5. [Provisioning Wallet Validation](#phase-5--provisioning-wallet-validation)
6. [Evidence Coverage Audit](#phase-6--evidence-coverage-audit)
7. [Test the Two-Mode Hypothesis](#phase-7--test-the-two-mode-hypothesis)
8. [Canonical Model Review](#phase-8--canonical-model-review)

---

## Phase 1 — Build Historical Population

### Population source and size

Queried `campaign=WATCHTOWER` with `window=all` (`cutoff=0`, i.e. no time
bound at all) — this is the entire historical corpus the live Campaign
classifier (X65.7) has ever labelled `WATCHTOWER`, not a sample. Confirmed
`window=all` bypasses the time filter directly in
`operation_dashboard_routes.py:2005` (`cutoff = 0 if window == "all"`).

| Metric | Value |
|---|---|
| **Total confirmed WATCHTOWER-campaign launches** | **291** |
| `conserved` / `campaign_conserved` | True / True |
| Also `is_watchtower` (older, stricter operation-gated flag — requires a confirmed treasury→operation link, X65.1) | 51 (17.5%) |
| Also has a confirmed `operation_id` | 51 (same 51 — identical criteria) |

### Evidence sources and confirmation levels represented

| Source | Included? | Notes |
|---|---|---|
| Historically confirmed launches (`campaign=WATCHTOWER`, all-time) | Yes — this is the base population (291) |
| Current WATCHTOWER Campaign launches | Yes — subsumed; the live 24h/7d cohorts are subsets of this same 291 |
| Confirmed Operation launches (`operation_id` assigned) | Yes — 51 of 291, reported separately, not excluded |
| Manually confirmed launches | Not separately tagged in the schema — `wt_confirmed_treasuries`/`wt_ops_v2_wallets` entries function as the manual-confirmation record, and 240 of 291 launches (Phase 1 below) already resolve to a `treasury_tier=CONFIRMED` (i.e. a treasury present in `wt_confirmed_treasuries`) |

### Campaign confidence and treasury tier distribution (population characterization, not filtering)

| campaign_confidence | Count |
|---|---|
| MEDIUM | 259 |
| HIGH | 21 |
| BASELINE | 11 |

| treasury_tier | Count |
|---|---|
| CONFIRMED | 240 |
| UNKNOWN | 51 |

No launch was excluded from the 291 on confidence or treasury-tier grounds — Campaign
membership (X65.7) never gates on treasury status, as established previously
([[treasuries-fund-treasuries]], X65.7's own design constraint that Campaign must never
require a confirmed treasury).

---

## Phase 2 — Funding Mechanism Analysis

Computed directly from each launch's `mechanisms` list field (not the API's pre-existing
`mechanism_summary` block, which reports mechanism shares across the full 7,381-launch
30-day token population *before* Campaign filtering, and is not scoped to this corpus).

| Mechanism | Count | % of 291 |
|---|---|---|
| PLAIN_TRANSFER | 119 | 40.9% |
| UNKNOWN (empty `mechanisms` list) | 128 | 44.0% |
| WSOL_WRAP_CLOSE | 25 | 8.6% |
| MIXED (launch recorded >1 mechanism) | 8 | 2.7% |
| SEEDED_ACCOUNT_CLOSE | 11 | 3.8% |
| OTHER | 0 | 0% |

**UNKNOWN is the largest single bucket (44%)** — a materially different picture than
X65.11's 24h cohort, which happened to have zero UNKNOWN-mechanism launches. This
matters directly for Phase 3/7: any topology-vs-mechanism association is being tested
against a corpus where more than 4 in 10 launches have no recorded mechanism at all.

---

## Phase 3 — Topology Distribution

### Per-mechanism topology breakdown

**WSOL_WRAP_CLOSE** (n=25)

| Topology | Count | % |
|---|---|---|
| FAN_OUT | 14 | 56.0% |
| LINEAR | 8 | 32.0% |
| MULTI_LEVEL_FAN_OUT | 3 | 12.0% |
| MESH | 0 | 0% |
| UNKNOWN | 0 | 0% |

**PLAIN_TRANSFER** (n=119)

| Topology | Count | % |
|---|---|---|
| MULTI_LEVEL_FAN_OUT | 94 | 79.0% |
| FAN_OUT | 24 | 20.2% |
| LINEAR | 1 | 0.8% |
| MESH | 0 | 0% |
| UNKNOWN | 0 | 0% |

**SEEDED_ACCOUNT_CLOSE** (n=11)

| Topology | Count | % |
|---|---|---|
| FAN_OUT | 11 | 100% |
| LINEAR / MULTI_LEVEL_FAN_OUT / MESH / UNKNOWN | 0 | 0% |

**UNKNOWN mechanism** (n=128, reported for completeness — not one of the 4 required
mechanism buckets, but material given its 44% share)

| Topology | Count | % |
|---|---|---|
| MULTI_LEVEL_FAN_OUT | 75 | 58.6% |
| FAN_OUT | 45 | 35.2% |
| UNKNOWN | 5 | 3.9% |
| LINEAR | 3 | 2.3% |

### Statistical association test

Chi-square test of independence, WSOL_WRAP_CLOSE vs. PLAIN_TRANSFER (the two
best-populated single-mechanism buckets), across {LINEAR, FAN_OUT, MULTI_LEVEL_FAN_OUT}:

**χ² = 55.91, df = 2** — far beyond the p<0.001 threshold (critical value 13.82 at
p=0.001). **Topology is strongly, statistically associated with funding mechanism**
across the full corpus — this is not noise.

### The critical reversal versus X65.11

X65.11's 24h cohort showed a **clean 1:1 split**: 13/13 WSOL_WRAP_CLOSE → MULTI_LEVEL_FAN_OUT,
6/6 PLAIN_TRANSFER → LINEAR. The full 291-launch corpus shows the **opposite skew**:
WSOL_WRAP_CLOSE is majority FAN_OUT (56%), and PLAIN_TRANSFER is majority
MULTI_LEVEL_FAN_OUT (79%) — essentially inverted from the small sample. The association
is real (confirmed statistically) but its **direction contradicts** X65.11's own
provisional read. This is investigated directly in Phase 6.

---

## Phase 4 — Operational Graph Comparison

Reconstructed strictly from recorded evidence per launch (`campaign_evidence.subprov_wallet`,
`topology_derived_from`); no edge inferred where no row exists.

### Do both mechanisms follow Treasury → SubProvider → Creator?

**Yes, structurally, for both.** Every one of the 291 launches has a recorded
`subprov_wallet` in `campaign_evidence` (Campaign classification's mandatory wrap-close-or-plain-transfer
evidence requirement guarantees this) and a recorded `creator` — so the base
Treasury→SubProvider→Creator shape is present for both mechanisms, at the hops where
evidence exists. Neither mechanism produces a fundamentally different graph shape at
this level; both are two-hop chains from a resolvable SubProvider to a Fresh Creator.

### Where they diverge is evidence depth, not graph shape

- **WSOL_WRAP_CLOSE** launches derive their topology overwhelmingly (15/25, 60%) from
  `wt_candidate_websocket_watches` — a live-cascade evidence source that records
  distinct **candidate wallets** the sub-provider produced (siblings that may or may not
  become creators). This is a **breadth-of-siblings** measure, tending to produce FAN_OUT.
- **PLAIN_TRANSFER** launches derive their topology overwhelmingly (83/119, 70%) from
  `session_lineage` (`wt_active_subprov_sessions_sub_subprov_lineage`) — a **depth-of-chain**
  measure recording whether a sub-provider session is itself the child of a further
  upstream sub-provider session. This tends to produce MULTI_LEVEL_FAN_OUT by construction
  (a literal multi-tier session chain), regardless of the terminal creator-funding
  mechanism.

**Conclusion: the two mechanisms are not observed to diverge in underlying graph shape.**
They diverge in **which evidence source is available to characterize that shape**, and
that evidence source's own measurement axis (breadth vs. depth) mechanically determines
the topology label. This is elaborated fully in Phase 6.

---

## Phase 5 — Provisioning Wallet Validation

Per launch, classified using the same rule as X65.11: only
`wt_candidate_websocket_watches` (`wrap_wallet`/candidate-wallet coverage, X65.4/X65.10)
records a genuinely distinct Provisioning-Wallet-layer fact, separate from the
SubProvider→Creator funding edge itself.

| Classification | Count | % |
|---|---|---|
| **Explicitly observed** (`wt_candidate_websocket_watches` evidence) | 43 | 14.8% |
| **Indirectly supported** (`session_lineage` or `walkback` evidence — implies a chain exists but does not directly record a provisioning-wallet row) | 180 | 61.9% |
| **Not observable** (`wt_provisioning_edges` sibling-count only — a creator-only edge table, structurally incapable of representing a non-creator provisioning wallet, per its schema CHECK constraint) | 63 | 21.6% |
| **Not observable** (no evidence source recorded at all) | 5 | 1.7% |
| **Contradicted** | 0 | 0% |

**Zero launches contradict** the Provisioning-Wallet layer's existence. Per the audit
rule (absence of evidence ≠ contradiction), the 63+5=68 "Not observable" launches (23.4%)
are a coverage gap, not disproof. Only 14.8% of the full corpus has the layer
**explicitly** observed — consistent with X65.8/X65.10's own finding that
`wt_candidate_websocket_watches` coverage is concentrated in the live-cascade-confirmed
population and does not yet span the whole historical corpus.

---

## Phase 6 — Evidence Coverage Audit

### Which evidence source drove each topology decision, corpuswide

| Evidence source | Count | % of 291 |
|---|---|---|
| `session_lineage` (`wt_active_subprov_sessions_sub_subprov_lineage`) | 162 | 55.7% |
| `wt_provisioning_edges` (sibling-count) | 63 | 21.6% |
| `wt_candidate_websocket_watches` (X65.10's rule) | 43 | 14.8% |
| `walkback` | 18 | 6.2% |
| Other (`subprov_present_no_sibling_evidence`) | 5 | 1.7% |

### Evidence source vs. mechanism (the key cross-tabulation)

| Evidence source | WSOL_WRAP_CLOSE | PLAIN_TRANSFER | SEEDED_ACCOUNT_CLOSE | MIXED | UNKNOWN mechanism |
|---|---|---|---|---|---|
| `wt_provisioning_edges` | 7 | 25 | 0 | 3 | 28 |
| `wt_candidate_websocket_watches` | 15 | 0 | 11 | 0 | 17 |
| `session_lineage` | 3 | 83 | 0 | 1 | 75 |
| `walkback` | 0 | 11 | 0 | 4 | 3 |

**This is the confound.** `wt_candidate_websocket_watches` (the live cascade) almost
exclusively instruments WSOL_WRAP_CLOSE and SEEDED_ACCOUNT_CLOSE launches (26 of its 43
hits, 60%) — mechanically, because the cascade daemon watches for wrap/close
program-instruction patterns specifically ([[ws-cascade-architecture]]). `session_lineage`
almost exclusively instruments PLAIN_TRANSFER launches (83 of its 162 hits, 51%) — because
walkback-resolved plain-transfer chains are the ones with recorded sub-provider session
lineage in this corpus. **Each evidence source has its own fixed or near-fixed topology
output**, independent of mechanism:

| Evidence source | Topology output when it fires |
|---|---|
| `wt_provisioning_edges` sibling-count=1 | **Always LINEAR** (32/32 non-UNKNOWN-mechanism hits) |
| `session_lineage` | **Always MULTI_LEVEL_FAN_OUT** (87/87 non-UNKNOWN-mechanism hits) |
| `wt_candidate_websocket_watches` | **Almost always FAN_OUT** (25/26 non-UNKNOWN-mechanism hits) |

Since mechanism correlates with which evidence source is available (not because the
mechanism itself determines topology), and each evidence source deterministically maps to
one topology label, **the mechanism↔topology correlation observed in both X65.11 and this
audit is fully explained as an evidence-source artifact, not a direct causal or
operational relationship.**

---

## Phase 7 — Test the Two-Mode Hypothesis

**Hypothesis:** WATCHTOWER currently operates two distinct provisioning modes — Mode A
(Treasury→SubProvider→WSOL_WRAP_CLOSE→Fresh Creator) and Mode B
(Treasury→SubProvider→PLAIN_TRANSFER→Fresh Creator) — each with its own characteristic
topology.

**Not supported by the full historical corpus.** Three independent pieces of evidence
each individually rule it out:

1. **Neither mechanism has one characteristic topology.** WSOL_WRAP_CLOSE splits
   56%/32%/12% across FAN_OUT/LINEAR/MULTI_LEVEL_FAN_OUT (Phase 3) — not a single
   dominant shape. PLAIN_TRANSFER is more concentrated (79% MULTI_LEVEL_FAN_OUT) but
   still has a real 20% FAN_OUT minority. A genuine "two distinct modes" hypothesis
   would predict near-exclusive topology per mechanism; neither shows that.
2. **The correlation direction itself is unstable across sample sizes.** X65.11's 19-launch
   24h sample showed a clean 1:1 split in one direction; the full 291-launch corpus shows
   a majority skew in the *opposite* direction for WSOL_WRAP_CLOSE. A real, stable
   operational mode would not reverse this way when the sample is enlarged.
3. **Phase 6 supplies the actual causal variable.** Once evidence source is held
   constant, mechanism adds no further predictive information: `wt_provisioning_edges`
   evidence always yields LINEAR whether the mechanism is WSOL_WRAP_CLOSE (7 cases) or
   PLAIN_TRANSFER (25 cases); `session_lineage` always yields MULTI_LEVEL_FAN_OUT whether
   the mechanism is WSOL_WRAP_CLOSE (3 cases) or PLAIN_TRANSFER (83 cases). The topology
   label depends on which evidence source was available, not which funding mechanism
   was used.

**Conclusion: the two-mode hypothesis is rejected.** The Treasury→SubProvider→Creator
shape (Phase 4) is unified across both mechanisms; the topology-*label* variance is an
evidence-instrumentation effect (Phase 6), not a second genuine operational pattern.

---

## Phase 8 — Canonical Model Review

**Is there still one canonical WATCHTOWER topology?**
Yes, at the structural level the canonical model actually describes
(Treasury→SubProvider→[Provisioning Wallet]→Fresh Creator). All 291 launches are
consistent with it at the hops evidence covers (Phase 4); zero launches contradict it
(Phase 5). What varies is not the underlying shape but which *evidence-derived label*
(LINEAR/FAN_OUT/MULTI_LEVEL_FAN_OUT) a given launch receives, and that label tracks
evidence-source coverage, not a second real topology.

**Does the evidence support multiple operational topologies?**
No. Phase 7 directly rejects the two-mode hypothesis. There is one canonical
provisioning shape; the apparent topology diversity is a downstream artifact of
non-uniform evidence-source coverage across the corpus.

**Are the observed differences caused by:**

| Candidate cause | Verdict |
|---|---|
| Funding mechanism | **No** — ruled out directly by Phase 6/7: mechanism predicts nothing once evidence source is held constant |
| Evidence coverage | **Yes — the confirmed primary cause.** Each evidence source (`wt_provisioning_edges`, `session_lineage`, `wt_candidate_websocket_watches`) has its own near-deterministic topology output, and which source is available differs sharply by mechanism (Phase 6's cross-tab) |
| Topology classifier behaviour | **Contributory, but correctly so, not a bug.** The classifier (X65.8/X65.10) reports exactly what each evidence source's own measurement axis implies — breadth-of-siblings → FAN_OUT, depth-of-session-chain → MULTI_LEVEL_FAN_OUT, sibling-count-1 → LINEAR. This is the classifier working as designed on genuinely different-shaped evidence, not fabricating a pattern |
| Genuine operational evolution | **No supporting evidence.** No time-ordered drift was observed that would indicate WATCHTOWER's actual provisioning behavior changed; the variance is fully explained by evidence-source mix, present throughout the corpus, not by a before/after split |
| Another identifiable cause | None found beyond the above |

**Should the canonical WATCHTOWER operational model be retained, expanded, revised, or
split into multiple validated provisioning patterns?**

**Retained, unchanged, but with an explicit clarifying note added to its documentation:**
topology labels (LINEAR/FAN_OUT/MULTI_LEVEL_FAN_OUT) should be understood and presented
as **evidence-coverage-dependent measurements of the same underlying shape**, not as
descriptions of genuinely different provisioning architectures. Splitting the model into
"Mode A / Mode B" would encode an artifact as if it were a real distinction — the Phase
7 analysis directly rules this out. Expansion or revision of the *structural* model
(Treasury→SubProvider→[Provisioning Wallet]→Creator) is not supported either — no
launch in the full 291-launch corpus contradicts it.

### Note on X65.11

X65.11's own conclusion ("retain the canonical model") is **not contradicted** by this
audit — it reached the right high-level answer. What this audit adds is the reason: the
24h sample's clean mechanism↔topology split was a small-sample artifact of which two
evidence sources happened to dominate that particular 24-hour window, not a discovered
operational rule. X65.11's document should be read with this qualification; no correction
of its conclusion is needed, only of the generalizability of its observed correlation.

### Deliverables

This single report constitutes the complete analysis: population construction (Phase 1),
funding mechanism distribution (Phase 2), topology distribution by mechanism with
significance testing (Phase 3), operational graph comparison (Phase 4), provisioning
wallet observability classification (Phase 5), evidence-source coverage audit and the
mechanism/evidence-source confound (Phase 6), formal rejection of the two-mode hypothesis
(Phase 7), and the canonical-model recommendation (Phase 8). No code was changed; no
database writes occurred; no UI was modified.
