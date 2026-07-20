# X26.5 — Attribution Health Metric Audit

Status: Investigation only, per the sprint's own priority framing — root
cause found and reproduced live with real numbers. No code changed yet;
recommendation at the end proposes the smallest fix and asks for a decision
before implementing, since the fix touches a widely-viewed page.

**Headline finding**: the reported symptom ("Known Relay Reached: 11" vs.
"Axiom: 46 launches") is not two widgets counting different *entities*
(outcomes vs. launches vs. wallets) — every tile and every drill-down reads
the exact same table (`wt_attribution_outcomes`), which is genuinely
one-row-per-launch. The mismatch is entirely a **silent, undocumented
time-window and row-cap inconsistency** between the landing tile and its
own drill-down. This is arguably worse than a different-entities bug: the
underlying data model is fine, but the UI has no way for an analyst to know
two numbers on the same page are answering different questions ("outcomes
in the last 24h" vs. "all outcomes of this type, ever").

---

## Where the tiles live

There are actually **three separate widgets** using outcome-type labels
across two different pages, reading two different tables — a second,
independent source of confusion beyond the one originally reported:

| Widget | Template | Table | Time window | Row cap |
|---|---|---|---|---|
| Attribution Health panel (Discovery landing) | `templates/discovery.html:645-647` | `wt_attribution_outcomes` | Last 24h (client-side) | 500 rows, shared across **all** outcome types combined |
| Attribution Health drill-down (per-type click-through) | `templates/discovery.html:761-767`, `renderRelayGrouped` at 740-753 | `wt_attribution_outcomes` | **None** | 500 rows, **per type** |
| Triage workspace summary (Insufficient Evidence / Lineage Gap only) | `templates/discovery.html:696-717` → `src/ops/discovery_triage.py:build_triage_summary()` | `wt_attribution_outcomes` | **None** | **None** |
| Legacy "Lineage Gaps" panel (different page entirely) | `templates/watchtower_operational_intelligence.html:2890, 3117` → `/api/ops/walkback-queue` → `src/core/walkback_queue.py:queue_stats()` | `wt_walkback_queue` (`intelligence_outcome` column, a legacy pre-X19.7 field, not `outcome_type`) | None for the raw `by_outcome` count shown; a separate unused `trend` dict is 24h-windowed | None |

## Exact mechanics

**The tile** (`templates/discovery.html:775, 788, 638, 645-647`):
1. Fetches `/api/ops-v2/attribution-outcomes?limit=500` — this pulls the
   **500 most-recent rows across every outcome_type combined**, ordered
   `completed_at DESC` (`src/core/operation_dashboard_routes.py:8331-8360`).
2. Client-side (`summariseOutcomes`, line 638), filters that already-capped
   set to `completed_at >= now - 86400` (24 hours).
3. Groups the survivors by `outcome_type` and shows each count as a tile
   row.

**The drill-down** (`templates/discovery.html:761-767`):
1. Fetches `/api/ops-v2/attribution-outcomes?limit=500&outcome_type=X` —
   same endpoint, same table, but **type-filtered at the SQL layer** and
   with **no time bound whatsoever**.
2. For `KNOWN_RELAY_REACHED` (and by the same code path,
   `KNOWN_CEX_REACHED`/`KNOWN_BRIDGE_REACHED`/`UNKNOWN_INFRASTRUCTURE`),
   groups the result by `terminal_entity` (the specific wallet/relay
   address) via `renderRelayGrouped`, and shows launch counts per group.

Both numbers are counts of `wt_attribution_outcomes` rows — one row per
mint (`mint TEXT PRIMARY KEY`, confirmed in `src/ops/attribution_outcome.py:50-51`)
— so "outcomes" and "launches" are the same underlying unit here; there is
no distinct-wallet or distinct-operator double-counting. The only variable
that differs is the time window and the cap.

## Reproduced live (2026-07-16, `database/wt_ops_v2.db`)

True all-time counts per `outcome_type`:

| outcome_type | All-time | Last 24h (correct, uncapped) | Tile's actual output (buggy 500-row-shared-cap + 24h) |
|---|---|---|---|
| INSUFFICIENT_EVIDENCE | 2,551 | 279 | 274 |
| LINEAGE_GAP | 620 | 93 | 92 |
| KNOWN_CEX_REACHED | 318 | 57 | 56 |
| UNKNOWN_INFRASTRUCTURE | 193 | 66 | 66 |
| CANONICAL_OPERATOR_REACHED | 73 | 0 | 0 |
| KNOWN_RELAY_REACHED | 71 | 12 | 12 |
| KNOWN_MULTI_TOKEN_CREATOR | 22 | 0 | 0 |

The 500-row shared cap is **not currently truncating anything material** —
the oldest row in the current top-500-by-recency fetch is ~23.7h old, right
at the edge of the 24h window, so the tile's own math (274 vs 279, 92 vs
93, 56 vs 57 — off by ~1-2%) is close to correct today purely by
coincidence of current outcome volume. This is a live landmine, not a
dormant one: the moment total attribution-outcome throughput across all
types exceeds ~500/day, the shared cap will start silently pushing
older-but-still-within-24h rows out of the fetch before the 24h filter is
even applied, and the tile will under-count with no visible indication.

**Drill-down for `KNOWN_RELAY_REACHED`** (all-time, ungated by any window),
grouped by `terminal_entity`:

| Relay wallet | Launches |
|---|---|
| Axiom (`AxiomRXZAq1...`) | 46 |
| `5Q544fKrFo...` (Raydium-family) | 14 |
| `GpMZbSM2Gg...` | 4 |
| `HLnpSz9h2S...` | 3 |
| 4 further wallets | 1 each |
| **Total** | **71**, matching the true uncapped `KNOWN_RELAY_REACHED` count exactly |

This confirms the exact reported scenario: tile shows **12** (last 24h),
drill-down immediately shows **Axiom alone at 46** (all-time) — an analyst
who has no reason to expect the two views to use different windows sees
what looks like an internal contradiction, when in fact both numbers are
individually correct answers to two different, unstated questions.

**Triage summary** (`Insufficient Evidence`/`Lineage Gap`, the two highest-
volume outcome types) shows the same pattern at even greater scale:
`build_triage_summary()` (`src/ops/discovery_triage.py:143-221`) has
**no LIMIT clause and no time filter at all** — `total_terminal_outcomes`
reproduces live as **3,171** (`INSUFFICIENT_EVIDENCE` + `LINEAGE_GAP`
combined, all-time), while the landing tile would show **274 / 92**
respectively for the same two categories in the same session. This is a
roughly **9x** discrepancy on the platform's two highest-volume categories —
a starker version of the same underlying defect, not a separate one.

## A second, independent confusion source: label collision across tables

`templates/watchtower_operational_intelligence.html` (a different
dashboard page, "Watchtower Operational Intelligence") shows a "Lineage
Gaps" figure sourced from `wt_walkback_queue.intelligence_outcome`
(`src/core/walkback_queue.py:queue_stats()`'s `by_outcome` dict) — a
legacy, pre-X19.7 table with its own independent grouping, entirely
unrelated to `wt_attribution_outcomes.outcome_type`. Reproduced live:
`wt_walkback_queue` reports **1,001** rows with
`intelligence_outcome='LINEAGE_GAP'`, vs. **620** all-time in
`wt_attribution_outcomes.outcome_type='LINEAGE_GAP'`, vs. **93** in the
correct last-24h window, vs. **92** as the tile's actual (slightly
buggy) 24h output. Four different numbers, all captioned "Lineage Gap(s)"
somewhere in this platform, sourced from two structurally unrelated tables.
An analyst who has ever seen both dashboards has a fourth reason to
distrust the figure before even opening a drill-down.

## Root cause summary

There is **no entity-mismatch bug** — the sprint's hypothesis ("outcomes
vs. launches vs. infrastructure wallets") is not what's happening here; both
numbers are literally `COUNT(*)` over the same table with the same grain
(one row per launch/mint). The actual defect is:

1. **Undeclared, inconsistent time-windowing** between a landing tile
   (silently 24h-only) and its own drill-down (silently all-time) for the
   same `outcome_type`, with no UI affordance anywhere stating either
   window explicitly on the tile or the drill-down header.
2. **A row cap shared across unrelated types** on the tile's own fetch
   (500 rows split across all seven `outcome_type` values combined) that
   will silently worsen (1) as volume grows, with no error or indication
   when it does.
3. **A second, independent labeling collision** — the same English label
   ("Lineage Gap(s)") is used for two structurally different tables
   (`wt_attribution_outcomes.outcome_type` vs.
   `wt_walkback_queue.intelligence_outcome`) on two different dashboard
   pages, compounding the trust problem beyond what a single time-window
   fix would resolve.

## Recommendation

This is exactly the class of problem the sprint's framing anticipated
("if that's the case, the dashboard needs to make that distinction
explicit rather than leaving analysts to infer it") — the fix is not a
backend correctness fix (the underlying counts are each individually
correct for the query that produced them), it's a **UI honesty fix**:
make the tile and drill-down either (a) agree on the same window, or (b)
visibly disclose that they don't.

Two viable approaches, not yet implemented pending a decision:

- **Option A (minimal, recommended)**: keep the tile's 24h framing (it is
  useful — "what changed in the last day" is a real and different question
  from "how many of these exist ever") but (1) make both the SQL query and
  the fetch explicitly time-bounded (`WHERE completed_at >= ?` pushed to
  the backend, removing the shared-500-row-cap-then-client-filter pattern
  entirely, so the tile is never silently truncated regardless of volume),
  and (2) label the tile explicitly, e.g. "Known Relay Reached (last 24h):
  12" and the drill-down header "Known Relay Reached — all time: 71", so
  the two numbers visibly state which question they answer rather than
  looking like the same question with two different answers.
- **Option B**: add a real "all-time" row/toggle to the tile itself so an
  analyst never has to infer the window mismatch by clicking through.

Separately, the legacy `wt_walkback_queue`-based "Lineage Gaps" panel on
`watchtower_operational_intelligence.html` should either be relabeled to
something that doesn't collide with the canonical `wt_attribution_outcomes`
vocabulary (e.g. "Walkback Queue: Lineage Gap Rows") or deprecated in favor
of the canonical panel, per the existing project memory note that the
canonical X19.7 `wt_attribution_outcomes` model is meant to supersede the
legacy walkback-queue-outcome vocabulary for analyst-facing purposes.

No code was changed in this investigation. Recommend deciding on Option A
vs. B (or a hybrid) before implementation, since this page is
analyst-facing and any wording/behavior change should be verified live
before shipping, consistent with this session's established practice on
Discovery changes.
