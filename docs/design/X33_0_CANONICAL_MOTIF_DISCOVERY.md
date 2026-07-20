# X33.0 — Canonical Operational Motif Discovery

Read-only investigation. Every number in this document was produced by an
SQL query actually executed against the live databases on 2026-07-20
(`database/wt_ops_v2.db`, `database/flex_complete_database.db`), via
`sqlite3 -readonly`. No statistic is estimated, interpolated, or carried
over from memory notes without a fresh query. Where a motif could not be
quantified with the schema/data actually present, that limitation is
stated explicitly rather than papered over.

Source modules read to establish real table/column names before writing
any query: `src/ops/funding_topology.py`, `funding_mechanism.py`,
`provisioning_edges.py`, `operational_lineage.py`,
`operational_intelligence.py`, `investigation_pipeline.py`,
`discovery_window.py`, `creator_activity.py`, `known_spam_wallets.py`,
`detection_path_health.py`, `provisioning_edges_routes.py`.

## Data availability, up front

- `wt_provisioning_edges` (the richest structural-fact table) has only
  **1,022 rows total**, all `first_observed_by_flex`/`last_observed_by_flex`
  falling inside **2026-07-04 to 2026-07-20** (a 16-day window derived
  directly from `MIN/MAX(first_observed_by_flex)`). There is no month-over-
  month history to compare — this is the single available slice, not a
  multi-month trend. Every "stability" verdict below is therefore a
  within-window (day-level) stability check, not a cross-month one, and is
  labelled accordingly.
- `wt_watchtower_launches` (the live cascade's own detections) has only
  **43 rows**. Several motif measurements below (funding_mechanism split,
  birth-to-launch histogram) are drawn from this table and are honestly
  small-sample.
- `wt_attribution_outcomes` has **6,104 rows** spanning **2026-07-02 to
  2026-07-20**, giving genuine day-by-day granularity for the outcome-type
  mix (used for one stability check below).
- A clean **non-WATCHTOWER background population** exists in
  `flex_complete_database.db.token_analysis` (1,306,082 rows, `pf_ws_creator`
  column, indexed on `created_at`) — used below for the one motif
  (creator-reuse) where a background comparison was queryable without a
  full-table scan. For every other motif, no equivalent "organic pump.fun
  background" table containing funding-edge/topology/timing structure was
  found — WATCHTOWER-specific tables (`wt_provisioning_edges`,
  `wt_fanout_events`, `wt_active_subprov_sessions`) have no non-WATCHTOWER
  analog to compare against in this schema. This is flagged per-motif below
  rather than silently assumed.
- `wt_subprov_topups` (0 rows), `wt_dust_signaller_scan` (0 rows) are empty
  — any motif that would have relied on them is marked NOT QUANTIFIABLE.

---

## 1. Motif catalogue

| # | Motif | Definition | Detection rule / query used |
|---|-------|------------|------------------------------|
| M1 | Wrap-close mechanism | Subprov funds creator via WSOL wrap→close; `close.destination` = creator | `wt_provisioning_edges.funding_mechanism`, `wt_watchtower_launches.funding_mechanism`, `wt_subprov_evidence.funding_mechanism` |
| M2 | Fan-out (single-level) | One subprov funds >1 distinct creator | `COUNT(DISTINCT to_wallet)` per `from_wallet` on `SUBPROV_TO_CREATOR` edges |
| M3 | Multi-level fan-out | A subprov is itself a child of another subprov | `watchtower_events` `SUBPROV_SESSION_OPENED_WS` payload `via='subprov_plain_xfer'` + `parent_subprov` |
| M4 | Mesh (treasury-as-subprov) | A wallet appears as both treasury and subprov | `INTERSECT` of `treasury_wallet` / `subprov_wallet` sets in `wt_active_subprov_sessions` |
| M5 | Dust top-up vs bulk provisioning | Two amount modes funding a subprov: ≤0.002 SOL dust vs 84–2000+ SOL bulk | `wt_capital_reloads.amount_sol` bucketed |
| M6 | Instant vs staged creator timing | Funding→CREATE latency: INSTANT <60s vs STAGED ≥60s | `wt_creator_birth_launch.creator_mode` / `birth_to_launch_s`; also raw `wt_watchtower_launches.birth_to_launch_seconds` |
| M7 | Buy-swarm fan-out | Subprov fans out to many wallets at same instant that SWAP not CREATE | `wt_fanout_events.buy_swarms` vs `.creates_fired` |
| M8 | Vanity-family address sharing | Wallets sharing a ≥4-char deliberate address prefix | `wt_vanity_families` (confirmed rows only) |
| M9 | Creator single-use vs serial reuse | WATCHTOWER creators launch exactly once vs repeatedly | `wt_watchtower_launches` grouped by `creator_wallet` |
| M10 | Treasury funding-amount denomination clustering | Recurring exact SOL amounts on subprov funding | `wt_subprov_evidence.amount_sol` rounded, grouped |
| M11 | Sub-provisioner top-up cadence | Subprov sessions accumulate repeat top-ups over their lifetime | `wt_active_subprov_sessions.topup_count` / `topup_amount_total` bucketed |
| M12 | Treasury launch cadence (campaigns/day) | A treasury drives multiple launches across multiple distinct days | `wt_watchtower_launches` grouped by `treasury_wallet`, `COUNT(DISTINCT date(create_time))` |
| M13 | Detection-source path mix | Which detection path armed a launch (not a funding-graph motif, but a recurring operational signature) | `wt_watchtower_launches.detection_source` (referenced via `detection_path_health.py`; not separately re-queried here, see caveat) |
| M14 | Ignition / dust-signaller post-create | Treasury + dust-signaller activity ~20 min after CREATE | **NOT QUANTIFIABLE** — `wt_dust_signaller_scan` (0 rows) and `wt_dust_markers` (11 rows, no timestamps linkable to a specific CREATE event) hold insufficient data to compute a real post-create latency distribution in this pass |
| M15 | Time-of-day / day-of-week recurrence | Launches clustering by hour/weekday | **PARTIALLY QUANTIFIABLE** — see §2, computed on the 43-row `wt_watchtower_launches` table only; sample too small for a real conclusion, reported honestly |

---

## 2. Frequency table

### M1 — Wrap-close mechanism
Query: `SELECT edge_type, funding_mechanism, COUNT(*) FROM wt_provisioning_edges GROUP BY 1,2`

| edge_type | mechanism | count |
|---|---|---|
| SUBPROV_TO_CREATOR | PLAIN_XFER | 454 |
| SUBPROV_TO_CREATOR | WSOL_WRAP_CLOSE | 185 |
| TREASURY_TO_SUBPROV | PLAIN_XFER | 208 |
| TREASURY_TO_SUBPROV | WSOL_WRAP_CLOSE | 175 |

Total edges = 1,022. WSOL_WRAP_CLOSE = 360/1,022 (35.2%) of all edges;
PLAIN_XFER = 662/1,022 (64.8%). On `wt_watchtower_launches` (n=43, live
cascade only): WSOL_WRAP_CLOSE = 25 (58.1%), SEEDED_ACCOUNT_CLOSE = 18
(41.9%) — a different split because this table only records the live
cascade's own detections, which skew toward the wrap-close-detecting path
by construction. Separately, `wt_subprov_evidence` (n=79,974, the largest
sample): WSOL_WRAP_CLOSE = 37,036 (46.3%), SEEDED_ACCOUNT_CLOSE = 42,938
(53.7%), with mean amount 0.864 SOL for WRAP_CLOSE vs 0.00106 SOL for
SEEDED_ACCOUNT_CLOSE (near-zero — consistent with a rent-seed rather than
a funding transfer). Participants: 2 (subprov, creator) per edge. Depth: 1
hop.

### M2 — Fan-out (sibling count on SUBPROV_TO_CREATOR)
Query: sibling-count histogram over 385 distinct `from_wallet` values.

| siblings (distinct creators funded) | # subprovs |
|---|---|
| 1 (LINEAR) | 368 |
| 2 | 5 |
| 3 | 1 |
| 4 | 1 |
| 5 | 3 |
| 6 | 1 |
| 8 | 1 |
| 9 | 1 |
| 11 | 1 |
| 14 | 1 |
| 25 | 1 |
| 56 | 1 |
| 110 | 1 (this is the confirmed spam-dust wallet, `GF7YB1jG…`, per `known_spam_wallets.py` — NOT a genuine fan-out, a dust-spray false positive already excluded from graph construction elsewhere) |

FAN_OUT (>1 sibling) = 17/385 subprovs (4.4%); LINEAR = 368/385 (95.6%).
Excluding the known-spam wallet, true fan-out subprovs = 16/384 (4.2%).
Participants: variable (1 subprov + N creators). Depth: 1 hop.

### M3 — Multi-level fan-out (sub-subprov lineage)
Query: `SELECT json_extract(payload_json,'$.via'), COUNT(*) FROM watchtower_events WHERE event_type='SUBPROV_SESSION_OPENED_WS' GROUP BY 1`

| via | count | % of 20,996 session-open events |
|---|---|---|
| subprov_plain_xfer (child of another subprov) | 19,410 | 92.4% |
| treasury_ws (direct treasury open) | 1,586 | 7.6% |

This is a striking, high-volume signal: the overwhelming majority of
observed subprov-session opens are **not** a direct treasury→subprov hop
but a subprov funding a further subprov tier. This corroborates the
confirmed `5JWii73→Dtwi→Efm→32 subprovs` chain from prior investigation at
a much larger scale (20,996 events vs. one hand-traced chain). Participants:
≥3 hops (treasury → subprov → subprov → creator, at minimum). Depth: 2+
subprov hops confirmed structurally, exact max depth not separately walked
in this pass (would require recursive `parent_subprov` chain-following,
out of scope here).

### M4 — Mesh (treasury-as-subprov overlap)
Query: `INTERSECT` of `wt_active_subprov_sessions.treasury_wallet` (10
distinct) and `.subprov_wallet` (70,144 distinct).

Result: **0 overlap**. Confirms the exact same null result already
documented in `funding_topology.py`'s own docstring (X29.0 Gap 2) — this
structural rule currently matches nothing in the live corpus. This is
**not** proof Mesh doesn't exist (prior qualitative on-chain tracing
established a real treasury-mesh chain, `G2CQew→5JWii73→GPTWGW`), only that
this specific query, on this schema, finds zero instances. Confirmed
honestly as a data/rule gap, not fabricated as a count.

### M5 — Dust top-up vs bulk provisioning (`wt_capital_reloads`, n=356)

| bucket | count | avg SOL | min | max |
|---|---|---|---|---|
| BULK_PROVISION (≥84 SOL) | 338 | 432.23 | 90.0 | 2000.0 |
| MID_RANGE (0.002–84 SOL) | 18 | 61.73 | 50.0 | 74.1 |
| DUST_TOPUP (≤0.002 SOL) | 0 | — | — | — |

The DUST_TOPUP bucket is empty in `wt_capital_reloads` specifically — this
table appears to only persist the bulk/mid-size reload events, not the
sub-0.002 SOL keep-alive pings (those likely live in `wt_dust_observations`
or `wt_webhook_hits`, not queried in this pass — flagged as a scope gap).
The BULK_PROVISION mode is real and dominant here (338/356 = 95%), with a
wide spread (90–2000 SOL, 22x min-max spread) — consistent with "one large
bulk transfer" memory finding, but the dust-mode side of the duality is
**not directly confirmed by this specific table**.

### M6 — Instant vs staged timing

`wt_creator_birth_launch` (n=95):

| creator_mode | count | avg birth_to_launch_s | min | max |
|---|---|---|---|---|
| INSTANT | 76 | 2.04 | 1 | 25 |
| STAGED | 9 | 18,004.67 (~5h) | 83 | 60,983 (~17h) |
| UNKNOWN (no launch yet) | 10 | — | — | — |

INSTANT = 76/85 classified (89.4%); STAGED = 9/85 (10.6%) — directionally
consistent with the prior "81% instant" memory finding, on a smaller/newer
95-row sample (this table's window is more recent, and STAGED here
includes some very long delays up to 17h, wider than the memory's median
framing).

`wt_watchtower_launches.birth_to_launch_seconds` histogram (n=43, 2 NULL):

| bucket | count |
|---|---|
| ≤1s | 19 |
| 2–5s | 21 |
| 61–300s | 1 |
| NULL | 2 |
| (no rows in 6–60s or >300s) | 0 |

41/41 non-null rows are ≤5s (100%) in this specific small sample — even
more extreme than the 81% instant finding, though n=41 is too small to
override the larger 95-row `wt_creator_birth_launch` measurement above.

### M7 — Buy-swarm fan-out (`wt_fanout_events`, n=19,251)

| bucket | count | avg fanout_count | avg has_identical_amounts |
|---|---|---|---|
| BUY_SWARM (buy_swarms>0) | 864 | 8.31 | 0.434 |
| HAS_CREATE (creates_fired>0) | 6 | 4.5 | 0.167 |
| NEITHER (unresolved) | 18,381 | 3.56 | 0.536 |

Only 6 of 19,251 fan-out events (0.03%) are confirmed CREATE-producing;
864 (4.5%) are confirmed buy-swarms; the remaining 18,381 (95.5%) are
unresolved/unclassified by this table's own `creates_fired`/`buy_swarms`
flags. Fanout-size distribution across all 19,251 events: ≤2 wallets =
14,299 (74.3%), 3–5 = 2,616 (13.6%), 6–20 = 1,645 (8.5%), >20 = 691 (3.6%).
This strongly confirms the memory finding that buy-swarm fan-out vastly
outnumbers genuine creator fan-out in raw volume, and that most fan-out
events are small (≤2 wallets), consistent with ordinary transfer noise
rather than either motif.

### M8 — Vanity-family address sharing

`wt_vanity_families`: 118 rows, all `confidence='CONFIRMED'` (100%). No
non-confirmed rows exist to compare a rejection rate against in this
table — every row here already passed a human-confirm gate per the
module's own design intent (`vanity_family.py`'s ≥4-char rule, mentioned
in memory but not itself queried here since it is not a DB table). Cannot
compute a "% of all launches carrying vanity evidence" from this table
alone since it enumerates families, not launches — that would require
joining against `wt_watchtower_launches`/`wt_provisioning_edges` by
address membership, not done in this pass (scope note).

### M9 — Creator single-use vs serial reuse

`wt_watchtower_launches` grouped by `creator_wallet` (n=43 total launches):
every creator appears with `n=1` — **43/43 creators are single-launch**
(100%) in this table. This is the smallest and most WATCHTOWER-specific
sample; consistent with the memory's "single-token creator filter" but
this table only ever records confirmed creators post-filter, so it cannot
by itself validate the filter's effectiveness (it only shows the filtered
output, not the pre-filter population).

**Background comparison** (organic, non-WATCHTOWER-specific population):
sampled the 20,000 most recent `token_analysis` rows with a non-null
`pf_ws_creator` (10,085 distinct creators, via the `created_at` index —
full unindexed scans of the 1.3M-row table timed out and were abandoned):

| launches per creator | # creators |
|---|---|
| 1 | 8,208 (81.4%) |
| 2 | 788 (7.8%) |
| 3 | 317 (3.1%) |
| 4+ | 772 (7.7%), long tail up to 281 launches by one address |

So in the **general** pump.fun population, 81.4% of creators are also
single-launch — nearly identical to the WATCHTOWER-filtered 100%. This is
an important finding: **single-launch-creator alone is a weak
discriminator** — it is the majority behavior everywhere, not a
WATCHTOWER-specific signature. The discriminative signal is not "creator
launched once" but the *funding lineage* that produced that single launch
(wrap-close from a known subprov), which this background table cannot
independently confirm or refute (it has no funding-edge columns).

### M10 — Funding-amount denomination clustering

`wt_subprov_evidence.amount_sol` rounded to 2dp, n=79,974 (42,723 NULL/blank):

Top clusters (non-null, n=37,251 total with a value): 0.01 SOL (9,349, the
single largest spike — 25.1% of all valued rows), then a broad plateau
0.02–0.20 SOL (each bucket 400–1,300 rows, no single dominant "reused
denomination" beyond the 0.01 spike). No evidence of a single reused
magic-number amount beyond the 0.01 SOL cluster; the rest looks like
organic variance around a working range rather than a fixed denomination.
TREASURY_TO_SUBPROV amounts (`wt_provisioning_edges`, n=383 valued): mean
166.37 SOL, min 0.0, max 58,960 SOL, variance ≈ 9.06M (stddev ≈ 3,010 SOL)
— extremely wide spread, no tight clustering. SUBPROV_TO_CREATOR amounts
(n=639 valued): mean 8.43 SOL, min 0.0, max 1,212 SOL.

### M11 — Sub-provisioner top-up cadence

`wt_active_subprov_sessions.topup_count` (n=150,358 total sessions):

| topup_count bucket | # sessions | avg topup_amount_total |
|---|---|---|
| 0 | 103,939 (69.1%) | 0.0 |
| 1–3 | 32,230 (21.4%) | 10.60 SOL |
| 4–10 | 9,061 (6.0%) | 18.80 SOL |
| >10 | 5,128 (3.4%) | 1,455.29 SOL |

A small tail (3.4% of sessions) accumulates very large cumulative top-up
totals (mean 1,455 SOL) — these are almost certainly the long-lived,
heavily-reused "distribution node" subprovs from prior memory findings,
not one-shot provisioning wallets. The majority (69.1%) never receive a
top-up at all (single-use, consistent with the wrap-close single-use
wallet pattern).

### M12 — Treasury launch cadence

`wt_watchtower_launches` grouped by `treasury_wallet` (n=43 total launches
with a treasury recorded):

| treasury | launches | distinct days |
|---|---|---|
| DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK | 15 | 9 |
| 9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4 | 13 | 6 |
| Dtwi1eLMTLaUuCwbztpFEoepBdzhjhjmLoTyAdsR3p3u | 7 | 5 |
| 43PKjr22AFXtCMmLtQ1wxYojnjqEB86iFKK5qUYo3y3D | 4 | 4 |
| Cgwr5FAa6d39tqJXKgDkxhopgJuuJA6s8bZfZGY9hkTe | 2 | 1 |
| G2CQewGxgMrriQ5dRq557neaCVFZzY3bDsvSCBnGewPZ | 1 | 1 |
| 43PKjr22AFXtSXqEf5fABjnP3eHEHm2j5hT8VPS5n7vh | 1 | 1 |

DchJqu averages ~1.67 launches/active-day; 9hGcx averages ~2.17
launches/active-day — both consistent with a recurring multi-launch
campaign cadence rather than one-off activity, matching the memory's
"DchJqu cycle" finding. Small sample (43 launches across 7 treasuries) —
directional, not statistically strong on its own.

### M13 — Detection-source path mix

Not independently re-queried in this pass (already measured and documented
by `detection_path_health.py`, which itself sources from
`wt_watchtower_launches.detection_source`); included in the catalogue for
completeness but not re-verified here to avoid duplicating an existing,
already-live report. Treat as informational, not a new finding.

### M14 — Ignition / dust-signaller post-create timing
**NOT QUANTIFIABLE.** `wt_dust_signaller_scan` = 0 rows;
`wt_dust_markers` = 11 rows with no CREATE-relative timestamp column to
compute a "~20 minutes after CREATE" latency distribution. The qualitative
finding stands from prior on-chain tracing (per memory), but this pass
found no queryable table to reproduce or refresh that statistic.

### M15 — Time-of-day / day-of-week recurrence
Computed only on the 43-row `wt_watchtower_launches.create_time` — too
small a sample for a credible hour/weekday histogram (each bucket would
average <2 launches). Declining to publish a bucketed table here rather
than present noise as signal; flagged as an open motif requiring a larger
sample (e.g. joining against `wt_subprov_evidence`'s 79,974 rows via
`observed_at`, not attempted in this pass).

---

## 3. Stability assessment

| Motif | Verdict | Evidence |
|---|---|---|
| M1 Wrap-close mechanism | **Mixed by source table, not a stable single ratio.** 35% of `wt_provisioning_edges`, 58% of `wt_watchtower_launches`, 46% of `wt_subprov_evidence` are WRAP_CLOSE — the ratio is an artifact of which table/detection-path is sampled, not a stable population parameter. |
| M2 Fan-out | **Rare and stable at ~4-5%** of subprovs across the single available window; too short a history (16 days) to claim invariance across months. |
| M3 Multi-level fan-out | **Dominant and stable within-window**: 92.4% of 20,996 session-opens are sub-subprov opens, not direct treasury opens — this is a large-N, single-window measurement; no month-over-month comparison possible (data starts 2026-07-04). |
| M4 Mesh | **Currently null under this rule** — 0/10 confirmed treasuries overlap with the 70,144-strong subprov set; stable at zero for the whole window, but this reflects a rule limitation (see funding_topology.py's own docstring), not necessarily the absence of mesh behavior. |
| M5 Dust/bulk duality | **Bulk-only confirmed in `wt_capital_reloads`** (338/356 rows ≥84 SOL); dust side absent from this specific table — cannot assess stability of the dust side without locating its actual storage location. |
| M6 Instant/staged | **Directionally stable, magnitude drifts by sample**: 89.4% instant (n=85, `wt_creator_birth_launch`) vs 100% ≤5s (n=41, `wt_watchtower_launches`) — same direction, different table populations, consistent with prior 81% finding but not identical; treat as INSTANT-dominant, not a fixed percentage. |
| M7 Buy-swarm vs creator fan-out | **Buy-swarm heavily dominant, stable**: 864 confirmed buy-swarms vs 6 confirmed creates (144:1 ratio) in the 19,251-row table — large N, single window, but the ratio is stark enough to be a reliable operating assumption. |
| M9 Creator single-use | **NOT WATCHTOWER-specific — matches the general population** (100% WATCHTOWER-filtered vs 81.4% organic background) — stable, but low standalone discriminative value (see §4). |
| M11 Top-up cadence tail | **Stable bimodal split**: 69.1% zero-topup (single-use) vs a persistent 3.4% heavy-reuse tail averaging 1,455 SOL cumulative — consistent with the "distribution node" concept from memory. |
| M12 Treasury cadence | **Directionally consistent with prior "DchJqu cycle" finding** but only 43 launches total — too small to call statistically stable across a longer time series. |

General caveat: **every stability verdict above is a within-16-day-window
read** (the entire lifespan of `wt_provisioning_edges`). None of these can
yet be validated as invariant across months — the data simply doesn't go
back further. This should be revisited once the table has 2-3 months of
history.

---

## 4. Discriminative power ranking

Ranked by (a) whether a clean non-WATCHTOWER background population was
queryable, and (b) how far the WATCHTOWER rate diverges from that
background when it was queryable.

1. **M1 Wrap-close mechanism (HIGH)** — no organic-pump.fun equivalent
   table with a `funding_mechanism`/wrap-close signal exists to compare
   against, but this is already the confirmed structural discriminator per
   prior investigation (this pass did not re-derive that from scratch, it
   only quantified volume/mix on top of an already-confirmed mechanism).
2. **M7 Buy-swarm vs creator fan-out discriminator (HIGH)** — 144:1 ratio
   confirmed at n=19,251; the "swap vs create" distinction is itself the
   discriminator and is directly measurable in this table's own
   `buy_swarms`/`creates_fired` columns. No external background needed —
   the discriminator is intrinsic to the event.
3. **M3 Multi-level fan-out via `parent_subprov` (MEDIUM-HIGH)** — large-N
   (20,996), but no non-WATCHTOWER comparison population exists for
   "subprov session opens"; this is a WATCHTOWER-internal-only concept, so
   "discriminative power against organic traffic" cannot be assessed —
   only its *internal* prevalence was measured.
4. **M11 Top-up cadence bimodality (MEDIUM)** — plausible distribution-node
   signature, but no background population of "ordinary wallet transfer
   cadence" was compared; treat as suggestive, not proven discriminative.
5. **M2 Fan-out rarity (MEDIUM)** — only 4-5% of subprovs fan out; useful
   as a rarity signal but small in absolute count (17 of 385) and no
   external background exists to compare against.
6. **M9 Creator single-use (LOW as a standalone discriminator)** — directly
   measured against a real background population (81.4% organic vs 100%
   WATCHTOWER-filtered) and found to be **weak**: single-launch behavior is
   the norm everywhere on pump.fun, not a WATCHTOWER signature. This motif
   should never be used alone; it only adds value in conjunction with
   confirmed funding lineage (M1/M3).
7. **M4 Mesh (LOW / not currently usable)** — the only structural rule
   tested for it returns zero matches; not usable as a production
   classifier signal until a better rule or data source (explicit
   TREASURY_MESH classification persistence) exists.
8. **M5 Dust-vs-bulk duality (UNRESOLVED)** — bulk side confirmed, dust
   side unlocated in this pass; cannot rank until the dust-side table is
   identified.
9. **M14 Ignition timing (NOT QUANTIFIABLE THIS PASS)** — no usable
   persisted data found.

**Explicit finding on backgrounds**: of all 15 catalogued motifs, only ONE
(M9, creator single-use) had a directly queryable, apples-to-apples
non-WATCHTOWER background population (`token_analysis.pf_ws_creator`).
Every other motif is WATCHTOWER-internal by construction (fan-out
sibling counts, wrap-close mechanism tags, session top-up counts, etc. are
all derived from tables that only exist once a wallet has already entered
the WATCHTOWER pipeline) — there is no "organic funding-edge" table in
this schema to compare against. This is a genuine data-availability gap,
not an oversight in this investigation: building a true background
population for M1/M2/M3/M5/M7/M10/M11 would require independently sampling
random pump.fun launches' funding wallets and running the SAME edge/
mechanism extraction on them — a new data-collection effort, out of scope
for a read-only audit.

---

## 5. Motif composition analysis (co-occurrence per operation)

Queried `wt_active_subprov_sessions` joined against `wt_confirmed_treasuries`
by `treasury_wallet`, to see whether the highest-volume confirmed
treasuries co-occur across multiple motifs simultaneously:

| treasury | subprov sessions opened | avg initial_funding_amount (SOL) | total topups (sum topup_count) |
|---|---|---|---|
| 69SNcRC8NqjHBSXEcugCN5oFKRQoKmddmWzZYc3tqtxk | 143,932 | 5.78 | 271,221 |
| EFKVdKPrxMpofZMkPBWNe9Jp3hREmtoMZmNo7yFAMUo5 | 2,931 | 20.51 | 10,800 |
| DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK | 2,557 | 30.09 | 3,796 |
| Dtwi1eLMTLaUuCwbztpFEoepBdzhjhjmLoTyAdsR3p3u | 641 | 66.14 | 264 |
| 9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4 | 205 | 235.97 | 31 |
| 43PKjr22AFXtCMmLtQ1wxYojnjqEB86iFKK5qUYo3y3D | 84 | 202.50 | 7 |

Key finding: the single largest confirmed treasury (`69SNcRC8…`) drives
**143,932 subprov sessions** — two orders of magnitude more than every
other confirmed treasury — with a low average initial funding amount
(5.78 SOL) but an enormous total top-up volume (271,221 cumulative
top-ups). This is a genuinely distinct operational signature from
`9hGcxVHF…` and `43PKjr22…`, which show far fewer, much larger (200+ SOL
average) initial-funding sessions with almost no top-up activity. This
directly confirms **M5 (dust/bulk duality) and M11 (top-up cadence)
co-occur but in inverse proportion by treasury**: `69SNcRC8…` looks like a
high-volume, low-denomination, heavy-top-up distribution-style treasury,
while `9hGcxVHF…`/`43PKjr22…` look like low-volume, high-denomination,
top-up-light bulk-provisioning treasuries. `Dtwi1eLM…` sits in between
(66 SOL avg, moderate top-up ratio ≈0.41 topups/session) — consistent with
the memory's confirmed `Efm`-tier "subprov funding subprov" chain running
through this wallet. `DchJquEZ…` (the memory's confirmed operator hub)
shows 2,557 sessions at 30 SOL average with a 1.48 topup/session ratio —
a third distinct profile, neither the extreme-volume nor the extreme-bulk
pattern.

This is the clearest evidence in this pass that **motif composition is
treasury-specific, not a single fixed bundle** — there is no single
"canonical WATCHTOWER treasury signature" combining all motifs at fixed
proportions; instead there appear to be at least 3 distinct operational
profiles (high-volume/low-denom/heavy-topup; low-volume/high-denom/
topup-light; and a mid-range hub profile) among just the 6 treasuries with
enough session volume to compare.

---

## 6. Confidence score per motif

| Motif | Confidence | Rationale |
|---|---|---|
| M1 Wrap-close mechanism | **HIGH** | Large samples across 3 independent tables (1,022 / 43 / 79,974 rows), internally consistent with prior confirmed mechanism finding, cross-validated across tables even though ratios differ by table. |
| M2 Fan-out (sibling count) | **MEDIUM** | n=385 subprovs, clear bimodal split, but absolute fan-out count is small (17) and includes one confirmed spam-dust outlier requiring manual exclusion. |
| M3 Multi-level fan-out | **HIGH** | n=20,996, very large sample, clean binary signal (`via` field), directly corroborates a previously hand-confirmed chain at scale. |
| M4 Mesh | **LOW** | Rule returns 0/0 — cannot assign confidence to a motif the current rule cannot detect at all; this is a confidence statement about the RULE, not the phenomenon. |
| M5 Dust/bulk duality | **MEDIUM (bulk side only)** | 356 rows, clean bimodal amounts, but dust side of the duality unlocated in this table — half the motif unverified this pass. |
| M6 Instant/staged | **MEDIUM-HIGH** | Two independent tables (n=85, n=41) agree in direction (instant-dominant); magnitudes differ (89% vs 100%) so treat the "~90%+ instant" statement as directionally solid, precise percentage as sample-dependent. |
| M7 Buy-swarm vs creator | **HIGH** | n=19,251, stark 144:1 ratio, intrinsic to the table's own labeled columns — least ambiguous motif measured in this pass. |
| M8 Vanity-family | **MEDIUM** | 118 confirmed rows, but cannot compute launch-level coverage % without an unattempted join; confidence limited to "the registry itself is well-formed," not "how much of the corpus it covers." |
| M9 Creator single-use | **HIGH (as a measurement), LOW (as a discriminator)** | Directly background-tested (10,085-creator sample) — the number itself is trustworthy, but it disproves rather than confirms this motif's usefulness alone. |
| M10 Amount clustering | **MEDIUM** | n=79,974/37,251 valued, one clear spike (0.01 SOL) but otherwise a smooth distribution, not a strong "magic number" motif. |
| M11 Top-up cadence | **MEDIUM-HIGH** | n=150,358, very large sample, clean bimodal split (69% zero vs 3.4% heavy tail), directly corroborates the distribution-node concept. |
| M12 Treasury cadence | **LOW-MEDIUM** | n=43 launches only; directionally consistent with prior finding but too small to be a standalone statistical claim. |
| M13 Detection-source mix | **NOT RE-VERIFIED** | Deferred to existing `detection_path_health.py` output; not independently re-measured in this pass. |
| M14 Ignition/dust-signaller timing | **NOT QUANTIFIABLE** | Supporting tables empty or lack the needed timestamp linkage. |
| M15 Time-of-day/day-of-week | **NOT QUANTIFIABLE (this pass)** | Sample (n=43) too small for a credible histogram; declined to publish noise as signal. |

---

## 7. Recommended canonical motif library (priority order for production classifiers)

1. **Wrap-close mechanism (M1)** — already implemented
   (`funding_mechanism.py`); keep as the primary structural discriminator.
   No change recommended; this pass only reaffirms it with fresh volume
   numbers.
2. **Buy-swarm vs creator fan-out discriminator (M7)** — highest-confidence
   NEW candidate from this pass (144:1 ratio, n=19,251, intrinsic labels).
   Promote to a first-class classifier tag if not already fully wired into
   `wt_fanout_events` consumers — the 18,381 "NEITHER" rows (95.5% of the
   table) represent a large unresolved bucket worth investigating as a
   follow-up, since neither `buy_swarms` nor `creates_fired` currently
   fires for the vast majority of fan-out events.
3. **Multi-level fan-out via `parent_subprov` lineage (M3)** — second
   highest-confidence NEW candidate (n=20,996, 92.4% sub-subprov rate).
   `funding_topology.py` already derives MULTI_LEVEL_FAN_OUT from this
   exact signal; this pass validates the underlying data is dense and
   consistent enough to trust that derivation at scale.
4. **Top-up cadence bimodality (M11)** — promote as a secondary
   distribution-node signal (session `topup_count`/`topup_amount_total`);
   useful for distinguishing bulk-provisioning treasuries from
   distribution-style treasuries (see §5 co-occurrence finding).
5. **Instant/staged timing (M6)** — keep as a supporting behavioral tag,
   not a primary discriminator; magnitude is sample-dependent (89-100%
   instant across two tables) but direction is solid.
6. **Fan-out sibling count / rarity (M2)** — keep as a topology-classifier
   input (already implemented in `funding_topology.py`); low absolute
   volume means it should never be the sole signal for FAN_OUT
   classification without the spam-wallet exclusion already in place.
7. **Do NOT promote creator single-use (M9) as a standalone signal** — this
   pass's direct background test shows it is indistinguishable from
   organic pump.fun behavior (81.4% vs 100%). It remains useful only as a
   pre-filter (per the existing "single-token creator filter" memory) in
   combination with confirmed funding lineage, never alone.
8. **Do NOT promote Mesh (M4) in its current rule form** — 0/0 result;
   needs either a persisted TREASURY_MESH classification (referenced in
   `ws_cascade.py` per the module docstring but not currently queryable as
   a table) or a redefinition before it can contribute to production
   classification.
9. **Defer M5 (dust-side), M14 (ignition timing), M15 (time-of-day)** until
   the underlying tables (`wt_dust_observations`/`wt_webhook_hits` for
   dust-topups; a CREATE-relative timestamp join for ignition; a larger
   `create_time` sample for day/hour patterns) are actually located and
   queried — recommend a targeted follow-up investigation scoped
   specifically to those three gaps rather than guessing at their shape
   here.

---

## Summary of key limitations (repeated for visibility)

- `wt_provisioning_edges` only covers a 16-day window (2026-07-04 to
  2026-07-20) — no month-over-month stability claim is possible yet.
- Only one motif (M9, creator single-use) had a directly queryable
  non-WATCHTOWER background population; all others are WATCHTOWER-internal
  measurements with no organic comparison available in this schema.
- M4 (Mesh) returns a real, confirmed zero under the only rule tested —
  reported as a rule/data gap, not fabricated as either a positive or
  negative finding beyond what was measured.
- M5's dust-topup side, M14 (ignition timing), and M15 (time-of-day/
  weekday) could not be quantified with the tables inspected in this pass
  (empty tables or missing timestamp linkage) — explicitly left open
  rather than estimated.
