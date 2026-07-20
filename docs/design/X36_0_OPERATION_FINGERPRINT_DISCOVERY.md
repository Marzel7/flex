# X36.0 — Operation Fingerprint Discovery

Investigation only. No code changes. Follows [X33.0](X33_0_CANONICAL_MOTIF_DISCOVERY.md),
[X34.0](X34_0_PRIMITIVE_SUFFICIENCY_AUDIT.md), [X35.0](X35_0_PRIMITIVE_GENERALISATION_AUDIT.md).
Primitive library is frozen (Primitive A — identity handoff via WSOL seed-and-close;
Primitive B — bulk/dust capital allocation). No attribution attempted — behavioural
classification and clustering only. All numbers from live SQL against
`database/wt_ops_v2.db`, run 2026-07-20.

## Data-source note (important, discovered during this pass)

Two independent tables carry "treasury" identity, and they are **almost entirely disjoint**:

- `wt_confirmed_treasuries` (58 rows) — the identity-confirmation table, with its own
  native fingerprint fields (`transfer_pct`, `out_sol`, `recipients`, `micro_pings`,
  `method`, `confidence`). This is built from a different evidentiary pipeline (3SIGNAL,
  LAUNCH_CHAIN, human review, etc.), not from `wt_provisioning_edges`.
- `wt_provisioning_edges` (the structural funding-graph table used in X33.0–X35.0).

**Only 6 of the 58 confirmed treasuries appear at all as a `from_wallet` in
`wt_provisioning_edges`.** The remaining 52 confirmed treasuries have zero rows in the
edge table. This means the richest structural fingerprint data (fan-out, bulk/dust ratio,
subprov reuse) is only computable for a small minority of confirmed treasuries — the
edge table is not yet a complete structural record of confirmed WATCHTOWER activity. This
caveat applies to every fingerprint dimension below that depends on `wt_provisioning_edges`.

## Phase 1 — Treasury Fingerprint Catalogue

### Confirmed WATCHTOWER treasuries (native fields, n=58)
From `wt_confirmed_treasuries` directly:

| confidence tier | count | avg transfer_pct | avg out_sol | avg recipients | avg micro_pings |
|---|---|---|---|---|---|
| CERTAIN | 2 | 100.0 | 22,000.0 | 21.0 | 16.5 |
| CONFIRMED | 10 | 99.4 | 5,137.9 | 32.6 | 210.0 |
| STRICT | 38 | (not populated at this tier) | — | — | — |
| LOW/MEDIUM/MANUAL | 9 | — | — | — | — |

Method mix: `LAUNCH_CHAIN` (37), `subprov_funder_trace` (7), `REVIEW_PROMOTED` (4),
`3SIGNAL` (4), `human_review_recovery_safe` (2), `HAND+3SIGNAL` (2), `3SIGNAL+ORIGINAL` (2).

### Confirmed treasuries with structural (edge/launch-table) fingerprints (n=6 — the overlap set)

| treasury (short) | launches | Primitive-A launches | avg subprov funding (SOL) | distinct subprovs | avg fanout→create (s) |
|---|---|---|---|---|---|
| 43PKjr22AFXt… | 4 | 4/4 (100%) | 700.0 | 4 | 2.0 |
| 9hGcxVHFajR4… | 13 | 13/13 (100%) | 723.6 | 13 | — |
| Cgwr5FAa6d39… | 2 | 2/2 (100%) | 800.0 | 2 | 4.0 |
| DchJquEZzM6V… | 15 | 15/15 (100%) | 241.7 | 15 | 3.5 |
| Dtwi1eLMTLaU… | 7 | 7/7 (100%) | 778.6 | 7 | — |
| G2CQewGxgMrr… | 1 | 1/1 (100%) | — | 1 | — |

**Dominant confirmed-WATCHTOWER fingerprint** (from this n=6 structural sample):
100% Primitive A usage, avg subprov capitalization 240–800 SOL, near-1:1 subprov-to-launch
ratio (i.e. one subprov per launch rather than heavy subprov reuse in this particular
sample — differs from X33.0's earlier finding of concentrated fan-out in the largest
subprovs; both are true simultaneously because fan-out concentration is a *subprov-level*
statistic while this is a *treasury-level* view), fanout→create typically 2–4 seconds
(instant mode).

### Non-confirmed treasuries with edge-table presence (n=350, all `TREASURY_TO_SUBPROV` funders not in `wt_confirmed_treasuries`)

Behavioural bucketing by (capital scale × fan-out breadth × dust/bulk mix):

| Capital scale | Fan-out | Mix | Count |
|---|---|---|---|
| DUST_ONLY | NARROW (<3 subprovs) | dust-edges-only | 135 |
| MID_CAPITAL (1–50 SOL avg) | NARROW | bulk-only | 79 |
| DUST_ONLY | NARROW | bulk-only (near-zero bulk amount) | 69 |
| LOW_CAPITAL (0–1 SOL avg) | NARROW | bulk-only | 44 |
| HIGH_CAPITAL (≥50 SOL avg) | NARROW | bulk-only | 13 |
| LOW_CAPITAL | FANNED (≥3 subprovs) | bulk-only | 3 |
| DUST_ONLY | FANNED | dust-only | 2 |
| (five other small combinations) | — | — | 1 each |

**350/350 non-confirmed edge-table treasuries are NARROW (fewer than 3 distinct subprovs)
except 8 total.** This is the single starkest structural difference from confirmed
WATCHTOWER's fan-out pattern (X33.0 Motif 8: top confirmed treasuries reach 13–15 launches
via distinct subprovs each).

## Phase 2 — Behavioural Clusters (identity-blind)

Grouping by the bucket table above, treating each row purely as a behavioural signature:

**Cluster 1 — "Single-shot high-capital"** (13 treasuries, HIGH_CAPITAL/NARROW/BULK_ONLY)
- Every member has exactly 1 edge and exactly 1 distinct subprov. Amounts range 50–58,960
  SOL (one extreme outlier at 58,960 SOL).
- Dominant fingerprint: one large capitalization, no reuse, no fan-out.
- Confidence: HIGH that this is a real, distinct behavioural signature (clean bucket,
  no ambiguity) — but LOW confidence that it represents coordinated operational
  infrastructure rather than a single one-off treasury-to-wallet transfer (could be
  anything from a real subprov seed to an unrelated large payment).

**Cluster 2 — "Dust-only maintenance"** (135 treasuries, DUST_ONLY/NARROW/dust-edges)
- Only ever sends ≤0.002 SOL transfers, never a bulk transfer, to 1–2 recipients.
- Dominant fingerprint: pure top-up/keep-alive behaviour with no visible capitalization
  event in this table — either the bulk event happened outside the edge table's window,
  or this wallet only ever performs maintenance, never initial funding.
- Confidence: MEDIUM — large, clean cluster, but ambiguous whether it's a genuine
  "maintenance-only" operational role or an artifact of the 16-day edge-table window
  missing an earlier bulk event (per X33.0's data-availability caveat).

**Cluster 3 — "Narrow bulk funders" (Mid/Low capital, NARROW)** (192 treasuries combined:
79 MID + 69 near-zero-bulk + 44 LOW)
- One or two bulk-scale transfers to 1–2 subprovs, no fan-out.
- Dominant fingerprint: matches Primitive B's transfer mechanic but not its "many
  recipients from one root" reuse pattern.
- Confidence: MEDIUM-LOW as a distinct operational cluster — this could be many
  unrelated small operators independently reusing the same *mechanism* (which X35.0
  already showed is not WATCHTOWER-exclusive) rather than a single coordinated cluster.

**Cluster 4 — "Fanned, low/mid capital"** (5 treasuries, FANNED with LOW/MID capital)
- Small in count but structurally the closest shape to confirmed WATCHTOWER
  (multiple subprovs funded from one root), just at lower capital scale.
- Confidence: MEDIUM — small n, but this is the most promising cluster for Phase 3
  comparison below.

**Cluster 5 — confirmed WATCHTOWER (n=6 structural sample, n=58 native-fingerprint sample)**
- Reference cluster, not being discovered here — used as the comparison baseline.

## Phase 3 — Similarity to WATCHTOWER

Comparing Cluster 4 (fanned, low/mid capital, n=5) against the confirmed-WATCHTOWER
structural fingerprint (n=6):

| Dimension | WATCHTOWER (n=6) | Cluster 4 (n=5) | Similarity |
|---|---|---|---|
| Primitive A usage | 100% (edge_type restricted to WSOL_WRAP_CLOSE by construction of the launches table) | Edge type is TREASURY_TO_SUBPROV bulk transfers — Primitive A (creator-side wrap-close) not separately confirmed for these 5 in this pass | **Not directly comparable — different edge type measured** |
| Fan-out breadth | avg ~7 subprovs per treasury (range 1–15) | avg ~3.4 subprovs (range 3–5, by cluster definition) | **~45-50% similarity** (same direction, smaller magnitude) |
| Capital scale | avg 240–800 SOL bulk capitalization | avg well under 50 SOL (LOW/MID bucket ceiling) | **~10-20% similarity** (same mechanism, an order of magnitude smaller) |
| Reuse (edges per treasury) | 1–15 launches per treasury | 2–3 edges per treasury (from bucket population) | **~20-30% similarity** |

**Overall similarity estimate: LOW-MODERATE (roughly 25-35%)** — Cluster 4 shares the
*shape* of treasury-driven fan-out (Primitive B applied to multiple subprovs from one
root) but at meaningfully smaller scale on every quantitative axis. This is stated as an
estimate, not a rigorously computed score — the n=5/n=6 sample sizes on both sides are too
small for a defensible precise percentage, and doing so would overstate confidence the
data doesn't support. No cluster in this pass reached the kind of 90%+ multi-dimension
match the spec's example illustrates.

## Phase 4 — Novel Fingerprint Candidates

- **Cluster 1 (single-shot high-capital, n=13)**: quantitatively different from
  WATCHTOWER (no reuse, no fan-out) but not qualitatively different in mechanism — it's
  Primitive B's transfer shape used once. Not a candidate for a new primitive or model;
  more likely either background capital movement or under-observed WATCHTOWER-adjacent
  wallets whose subsequent activity fell outside this table's window. **Quantitative
  difference (scale/frequency), not qualitative.**
- **Cluster 2 (dust-only maintenance, n=135)**: quantitatively different (no bulk event
  visible) — most plausibly an artifact of partial observation (per the recurring
  data-availability caveat across X33–X35) rather than a genuinely dust-only operational
  role, since a subprov cannot function purely on dust top-ups without an initial
  capitalization somewhere. **Flagged as an observation-window gap, not a novel
  fingerprint.**
- **No cluster in this pass showed a qualitatively different mechanism** (e.g., a funding
  pattern that doesn't decompose into Primitive A or B at all). Every cluster differs from
  WATCHTOWER only in scale, breadth, or reuse count — all quantitative axes. Per the
  spec's instruction, none of these clusters qualify as "novel fingerprint" candidates
  requiring a new behavioural model; they qualify only as **lower-confidence or
  differently-scaled instances of the same two-primitive vocabulary.**

## Phase 5 — Fingerprint Stability

| Fingerprint dimension | Supporting launches | Supporting treasuries | Time coverage | Robustness to missing data | Reliability rank |
|---|---|---|---|---|---|
| Primitive A usage rate | 43 (confirmed) + 95 (non-confirmed WSOL_WRAP_CLOSE rows) | 6 confirmed + 15 non-confirmed | 16-day edge window; single window, no cross-month check | Depends entirely on whether wrap-close was walked back — LINK_ONLY rows (139) can't score this dimension at all | **1 (most reliable)** — cleanest binary signal, directly instruction-verified in X34.0 |
| Bulk-vs-dust bimodal split | 383 edges (confirmed-adjacent) | 350 non-confirmed + 6 confirmed | same 16-day window | Sensitive — X35.0 showed the bimodal signature weakens sharply outside confirmed set at low n | **3** — real but amplitude-dependent, degrades with small samples |
| Fan-out breadth (subprov count per treasury) | 1,022 edges total | 356 treasuries | same window | Robust to missing amount data (just needs distinct-recipient counting) but sensitive to the table's 16-day recency (older treasuries may have already exhausted their fan-out before the window opened) | **2** — cheap to compute, structurally meaningful, but window-truncation risk |
| Capital scale (avg bulk SOL) | 236 confirmed-context bulk edges | overlapping with above | same window | Most sensitive to missing data — a treasury observed only during its dust-maintenance phase would misleadingly bucket as DUST_ONLY | **4 (least reliable alone)** — must be paired with fan-out/reuse to mean anything |
| Reuse (edges/launches per treasury over time) | 1,022 edges / 43 launches | 356 / 58 | 16-day window only | Cannot yet assess month-over-month consistency — no historical partition available (same limitation flagged in X33.0 Phase 3) | **Unranked — insufficient longitudinal data** |

## Deliverables Summary

- **Treasury fingerprint catalogue**: built from two source tables (`wt_confirmed_treasuries`
  native fields; `wt_provisioning_edges` structural fields), with an important schema gap
  documented (52/58 confirmed treasuries have no edge-table presence at all).
- **Behavioural clusters**: 5 clusters described above, built without reference to wallet
  identity, vanity prefixes, or prior WATCHTOWER labels — purely from capital-scale ×
  fan-out-breadth × dust/bulk-mix buckets.
- **WATCHTOWER similarity ranking**: only Cluster 4 (fanned, low/mid capital, n=5)
  produced an estimated similarity score (~25-35%, LOW-MODERATE) — every other cluster
  is a poor structural match to confirmed WATCHTOWER's fan-out/capital-scale signature.
- **Novel fingerprint candidates**: none found. All observed clusters differ from
  WATCHTOWER quantitatively (scale, breadth, reuse), not qualitatively (mechanism). No
  cluster requires a new behavioural model.
- **Cluster confidence scores**: Cluster 1 (single-shot) HIGH confidence as a clean bucket,
  LOW confidence as a meaningful operational signal; Cluster 2 (dust-only) MEDIUM
  confidence, likely an observation-window artifact; Clusters 3 MEDIUM-LOW; Cluster 4
  MEDIUM (small n, most WATCHTOWER-like).
- **Recommended canonical fingerprint dimensions** (ranked by this pass's reliability
  assessment): (1) Primitive A usage rate, (2) fan-out breadth (distinct subprovs per
  treasury), (3) bulk-vs-dust bimodal split, (4) capital scale — deliberately ranked with
  capital scale last since it was the most window-sensitive and least reliable alone.
- **Runtime implementation recommendation**: do not attempt automated fingerprint-based
  clustering yet on the current schema — the 16-day-only edge-table window and the
  52/58 confirmed-treasury schema gap mean any runtime clustering built today would be
  training against an incomplete and recency-biased slice. Before implementation: (a)
  backfill `wt_provisioning_edges` for the 52 confirmed treasuries currently missing from
  it, (b) extend the edge table's retention/window so fan-out and reuse can be measured
  across months, not days.

## Answer to the stated success criterion

**Not yet reliably** — coordinated operations can be *partially* fingerprinted using the
frozen primitive library (fan-out breadth and Primitive-A usage rate are the most stable
dimensions found), but no cluster in this pass reached high-confidence similarity to
WATCHTOWER, and the underlying data has two structural limitations (a 16-day observation
window, and 52 of 58 confirmed treasuries missing from the structural edge table) that
would need to be resolved before fingerprint-based detection could be trusted at runtime.
The most promising candidate (Cluster 4, fanned low/mid-capital treasuries) is worth
targeted follow-up, but at n=5 it does not yet constitute a validated fingerprint.
