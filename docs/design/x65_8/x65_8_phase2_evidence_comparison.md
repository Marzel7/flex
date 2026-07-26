# X65.8 — Phase 2: Compare Evidence Sources

Live measurements against `database/wt_ops_v2.db`, 2026-07-22.

## Raw table sizes

| Table | Row count |
|---|---|
| `wt_provisioning_edges` | 1,550 |
| `wt_candidate_websocket_watches` | 3,053,025 |
| `wt_active_subprov_sessions` | 161,818 |
| `wt_watchtower_launches` | 43 |
| `wt_walkback_edge_candidates` | 1,244 |
| `wt_walkback_queue` | 7,335 |

## Distinct-subprov coverage (raw, whole-table)

| Table | Distinct subprovs covered |
|---|---|
| `wt_provisioning_edges` (`SUBPROV_TO_CREATOR.from_wallet`) | 620 |
| `wt_candidate_websocket_watches` (`subprov_wallet`) | 442 |
| `wt_active_subprov_sessions` (`subprov_wallet`) | 75,623 |

Raw counts alone are misleading: `wt_provisioning_edges` covers more
*distinct subprovs* than `wt_candidate_websocket_watches` despite far
fewer total rows, because it stores one row per unique subprov→creator
pair (deduplicated across all history) while
`wt_candidate_websocket_watches` stores one row per individual
wrap-close/candidate-detection event (many rows per subprov). Raw
coverage breadth is not the right comparison — **coverage of the
specific population Topology needs to classify correctly** is.

## Coverage of the confirmed-WATCHTOWER population specifically (the population that matters)

| Table | Launches covered (of 43 confirmed) | % |
|---|---|---|
| `wt_provisioning_edges` | 1 | 2.3% |
| `wt_candidate_websocket_watches` | 39 | 90.7% |
| Both | 1 | 2.3% |
| Neither | 4 | 9.3% |

This is the decisive finding: for the exact launches Campaign already
correctly identifies as WATCHTOWER,
**`wt_candidate_websocket_watches` covers 39x more of them than
`wt_provisioning_edges` does** (90.7% vs. 2.3%).

## Timing / freshness

| Table | Earliest observation | Latest observation |
|---|---|---|
| `wt_provisioning_edges` | 2026-07-14 (`first_observed_by_flex`/`last_observed_by_flex`) | 2026-07-22 (still being written) |
| `wt_candidate_websocket_watches` | 2026-06-14 | 2026-07-22 (still being written, live) |

Both tables are actively maintained and current — neither is stale or
abandoned. The difference is entirely about **which write path
populates them**, not about one being an old, dead table.

## Completeness comparison matrix

| Dimension | `wt_provisioning_edges` | `wt_candidate_websocket_watches` |
|---|---|---|
| Writer | `capture_provisioning_relationship()` (`src/ops/provisioning_edges.py`), called **only** from the walkback success path, **only** once a creator is already known | `open_candidate_watch()` (`src/core/ws_cascade_store.py`), called from `_handle_subprov_tx()` for **every** wrap-close/candidate destination observed live, whether or not it becomes a confirmed creator |
| What it records | One deduplicated edge per (subprov, creator) pair, ever observed | Every individual wrap-close destination event, including non-creator siblings |
| Can it represent sibling (non-creator) recipients? | **No** — schema CHECK constraint restricts `edge_type` to `TREASURY_TO_SUBPROV`/`SUBPROV_TO_CREATOR` only (confirmed in X65.4 Phase 1) | **Yes** — every candidate wallet a subprov's wrap-close ever targeted is recorded, creator or not |
| Coverage of cascade-confirmed launches | 2.3% (1/43) | 90.7% (39/43) |
| Coverage of walkback-only-resolved launches | Higher (this is its designed population) | Near-zero (X65.4 Phase 5 finding: this table is populated by the live cascade, which walkback-only launches never pass through) |
| Reliability of a positive signal | High — an edge, once recorded, is a real observed subprov→creator funding event | High — a candidate-watch row is a real, live-observed wrap-close destination |
| Reliability of a negative/absent signal | **Low** — absence very often means "walkback never ran for this subprov," not "no fan-out exists" | **Low for walkback-only launches** — absence very often means "this launch never passed through the live cascade," not "no fan-out exists" |

## Overlap

Only **1 of 43** confirmed WATCHTOWER launches has coverage in *both*
tables — the two sources are almost entirely non-overlapping
populations (cascade-confirmed vs. walkback-resolved), not two
redundant views of the same data. This means neither table alone is a
complete evidence source for the full Discovery population — but for
the specific, already-solved problem of classifying **confirmed
WATCHTOWER launches** correctly, `wt_candidate_websocket_watches` is
overwhelmingly the better-covered, more reliable source.

## Conclusion for Phase 5's design

Topology should consume `wt_candidate_websocket_watches` as its
primary fan-out evidence source for launches it covers, retaining
`wt_provisioning_edges`/walkback evidence as a fallback for launches
`wt_candidate_websocket_watches` has no data for (the reverse of
today's priority order) — this directly targets the exact coverage gap
this phase measured, without discarding either source's real,
independently-verifiable evidence.
