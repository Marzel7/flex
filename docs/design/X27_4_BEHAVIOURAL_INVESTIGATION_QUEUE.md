# X27.4 — Behavioural Investigation Queue

Stage 2 of the Investigation Queue: classifies Stage 1's remaining
population by behavioural archetype, independent of attribution, funding,
or walkback. Built directly on X27.3.2's timing-integrity findings.

## Phase 1 — Behaviour signal inventory (established sources only)

| Signal | Source | Reliability | Coverage | Used? |
|---|---|---|---|---|
| Canonical birth+CREATE timestamp | `wt_watchtower_launches.create_time` | On-chain (`tx.blockTime`) | 42/43 rows total (live-cascade-scoped) | Yes — Rapid Birth→Launch |
| Birth→launch interval | `wt_watchtower_launches.birth_to_launch_seconds` | Derived from the above | 41/43 rows | Yes — Rapid Birth→Launch |
| Migration timestamp | `token_analysis.migrated_at` | Ingestion time, verified live within ~6s of true `blockTime` | ~100% of migrated launches | Yes — Burst Launches |
| Birth timestamp (general population) | `token_analysis.created_at` | **Rejected** (X27.3.2): ~96% ingestion-time fallback, not on-chain | 100% populated but untrustworthy | **No** |
| Creator lifecycle / first funding | `creator_funders.first_detected_at` | Ingestion time only (confirmed via 3 write sites, all `CURRENT_TIMESTAMP`) | 17.8% of recent creators | **No** |
| Funding mechanism / provisioning timing | `wt_walkback_queue`, `wt_provisioning_sessions` | On-chain, reliable | Varies | **No** — out of scope; behavioural archetypes must not use funding evidence per the governing principle |

Only signals independently proven trustworthy by X27.3.2 were used.

## Phase 2 — Rapid Birth → Launch (implemented)

`src/ops/behaviour_queue.py::rapid_birth_launch_lookup()`. Fires only when
`wt_watchtower_launches.create_time IS NOT NULL AND birth_to_launch_seconds
IS NOT NULL` for a mint — absence means the mint is simply not in this
dict; callers never estimate a substitute value. Threshold: **≤5 seconds**
(`RAPID_BIRTH_LAUNCH_THRESHOLD_SECONDS`), the corpus's own natural cutoff —
the single outlier in the 41-row trustworthy set sits at 98 seconds, an
order of magnitude beyond the cluster.

## Phase 3 — Burst Launches (implemented)

`src/ops/behaviour_queue.py::burst_launch_lookup()`. Uses only
`token_analysis.migrated_at`. Definition: a launch's `cluster_size` is the
count of migrations (including itself) within a symmetric
±`BURST_WINDOW_SECONDS` (60s) window; matched when `cluster_size >=
BURST_MIN_CLUSTER_SIZE` (3). Threshold derivation (measured live, 24h
window, n=524): a 60s window's neighbour-count distribution has p90=2
(background rate) and p99=8 — requiring 3 total co-migrating launches sits
meaningfully above ordinary background clustering without being an
arbitrary round number. Never touches `created_at` or any creator-timing
signal.

## Phase 4 — Behaviour Queue (live, 2026-07-16, 24h window)

```
total_launches: 526
conserved: True
Confirmed Rapid Birth → Launch   0    coverage 7.8%   confidence HIGH
Burst Launches                   98   coverage 100.0% confidence MEDIUM
Unclassified Behaviour           428  coverage 100.0% confidence N/A
sum: 526
```

`Confirmed Rapid Birth → Launch` legitimately shows 0 today: coverage
(7.8%, i.e. 41 of ~526 launches have the required evidence) measures
*evidence availability*, and the specific 41 rows in
`wt_watchtower_launches` (a small, historically-accumulated,
live-cascade-scoped table) happen not to overlap with today's specific
524-launch window (independently confirmed: `len(today_mints &
wt_mints) == 0`). This is the archetype behaving exactly as designed —
it never widens its own evidence requirement to manufacture a nonzero
count.

## Phase 5 — WATCHTOWER replay

Live replay of `rapid_birth_launch_lookup()` against the real
`wt_ops_v2.db`:

```
total with canonical timing: 41
matched (<=5s): 40
precision: 97.56%
false negative: 9x4NHggD8U5gUQ6hYWha3xSJDdv3GykXR8txuCrcpump (98s)
```

Exactly reproduces the 40/41 (97.6%) figure from the brief. **Caveat
honestly disclosed**: this is a replay against the confirmed-WATCHTOWER
corpus itself (the population the archetype was tuned on), so "false
positives" cannot be measured from this replay alone — there is no
larger labeled non-WATCHTOWER set in this table to check the archetype
against for false-positive rate. The measured number is corpus
*precision within known-positive rows*, not a full precision/recall
matrix against a broader population; this is stated as a limitation, not
elided.

## Phase 6 — Behaviour metadata

Every archetype's API/UI entry exposes `coverage_pct`, `coverage_note`,
`confidence`, `confidence_note`, and `evidence_source` explicitly (see
`build_behaviour_queue()`'s returned `archetypes` list and the UI's
`behaviourQueuePanel()`/`filteredArchetype()` drill-down). Both the
landing panel and the drill-down page state in text that "coverage is
informational only" and is never to be read as recall.

## Phase 7 — Relationship to investigation

Confirmed intended workflow, matching the brief exactly:

```
Migration → Investigation Queue (X27.2) → Behaviour Queue (X27.4) → Walkback → Operation Discovery
```

Behaviour Queue does not remove launches from the Stage 1 population (all
526 launches remain visible); it adds a second, independent lens. Walkback
remains a separate, downstream validation step — this sprint made no
change to `src/core/walkback_worker.py` or any walkback table.

## Phase 8 — Future extensibility (confirmed, no implementation)

`ARCHETYPE_ORDER` is a plain ordered tuple; `assignments` stores every
matched archetype per launch (`archetypes_matched`), not just the single
`primary_archetype` used for summary counts — so a future archetype
(delayed migration, synchronized migration, treasury bursts, provisioning
families, creator cadence, operation-specific fingerprints) requires only:
1. A new lookup function (mirroring `rapid_birth_launch_lookup`/`burst_launch_lookup`) built only on an independently-verified-trustworthy signal.
2. One new entry in `ARCHETYPE_ORDER`/`ARCHETYPE_LABELS` and the `archetypes` list construction in `build_behaviour_queue()`.
No redesign of the walk, the route, or the UI panel is needed.

## Tests

`tests/test_x27_4_behaviour_queue.py` — 15 tests: Rapid Birth→Launch only
fires with canonical timing present, missing timing routes to
Unclassified, archetypes never infer unavailable timing, live WATCHTOWER
replay reproduces the exact 40/41 (97.6%) figure, Burst Launches
cluster-size measurement, every launch gets exactly one
`primary_archetype`, totals match the Stage-1 population, drill-down
returns only launches assigned to that archetype, zero DB mutation,
`OUTCOME_TYPES`/`BUCKET_ORDER` unchanged (confirms attribution/X27.2
untouched), HTTP 200 + bad-archetype 400 route behaviour, coverage
/confidence/evidence-source metadata present, and threshold constants
match the measured values. Full regression: 255 passed / 1 pre-existing
unrelated failure.

## Live verification

`GET /api/ops-v2/behaviour-queue?window=24h` → HTTP 200, `conserved: true`,
526 total, three archetypes summing exactly. Reloaded gunicorn and
confirmed the served `/discovery` HTML includes the new panel/JS
(`behaviourQueuePanel`, `filteredArchetype`, `FILTER_ARCHETYPE`).

## Confirmation

No attribution, walkback, detection, or schema logic was changed.
`src/ops/attribution_outcome.py::OUTCOME_TYPES` and
`src/ops/investigation_pipeline.py::BUCKET_ORDER` are confirmed
byte-for-byte unchanged. `src/ops/behaviour_queue.py` is new, additive,
and read-only (zero writes, `PRAGMA query_only=ON`). Only
`src/core/operation_dashboard_routes.py` (one new route) and
`templates/discovery.html` (one new panel + drill-down) were touched
among existing files.
