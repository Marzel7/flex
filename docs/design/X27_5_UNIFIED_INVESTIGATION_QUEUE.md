# X27.5 — Unify Behavioural Archetypes into the Investigation Queue

Merges X27.4's standalone Behaviour Queue into X27.2's mutually-exclusive
Investigation Queue. One migrated launch, one investigation path, one
visible bucket.

## Phase 1 — Overlap audit (live, 2026-07-17, 24h window)

```
pipeline total: 580   behaviour total: 585

Burst Launches (133 matched launches) by investigation bucket:
  Known Infrastructure     16
  Repeat Creator           34
  Insufficient Evidence    50
  Lineage Gap              17
  Unknown Infrastructure   16
```

Confirmed the exact defect the brief describes: the 133 launches matching
`BURST_LAUNCH` were spread across **all five** investigation buckets
simultaneously — a launch already resolved as `Known Infrastructure` was
*also* being counted in the separate Behaviour Queue's Burst Launches row,
forcing an analyst to mentally reconcile two panels to know what still
needed investigation.

## Phase 2 — Canonical unified priority

```
1. Known Operation
2. Known Infrastructure
3. Repeat Creator
4. Rapid Birth → Launch      (X27.5 — merged from behaviour_queue.py)
5. Burst Launches            (X27.5 — merged from behaviour_queue.py)
6. Unknown Infrastructure
7. Lineage Gap
8. Insufficient Evidence
```

Matches the brief's recommended order exactly. Behavioural archetypes sit
between Repeat Creator (strongest identity-based resolution) and Unknown
Infrastructure (weakest attribution-based signal) — a launch's behavioural
fingerprint is treated as a stronger investigative signal than "attribution
simply ran out of evidence," but weaker than an already-resolved identity.

## Phase 3/4 — Exclusive assignment implementation

`src/ops/investigation_pipeline.py`:
- `BUCKET_ORDER` extended with `RAPID_BIRTH_LAUNCH`/`BURST_LAUNCH` at the
  correct priority position (a plain ordered tuple — no branching-logic
  rewrite).
- `assign_bucket()` extended with two new checks, evaluated after
  `REPEAT_CREATOR` and before the `UNKNOWN_INFRASTRUCTURE`/`LINEAGE_GAP`/
  `INSUFFICIENT_EVIDENCE` fallback. Both checks consume pre-computed
  evidence dicts (`rapid_birth_evidence`, `burst_evidence`) rather than
  recomputing behavioural logic themselves.
- `build_pipeline_health()` now calls `src.ops.behaviour_queue`'s
  `rapid_birth_launch_lookup()`/`burst_launch_lookup()` once, up front —
  the **same, unmodified** functions X27.4 built and validated (97.6%
  corpus precision, ≥3-launches/60s burst threshold) — so there is exactly
  one place in the codebase that decides whether a launch exhibits either
  behavioural archetype. No detection or evidence logic was duplicated or
  changed.
- `src/core/operation_dashboard_routes.py`'s standalone
  `GET /api/ops-v2/behaviour-queue` route was deleted entirely (confirmed
  404 live and by test). The existing `GET /api/ops-v2/investigation
  -pipeline` route required no changes beyond its docstring — it already
  worked generically for any bucket in `BUCKET_ORDER`.

## Phase 5 — Conservation proof (live)

```
total: 578   conserved: True
Known Operation             0
Known Infrastructure       64
Repeat Creators            127
Rapid Birth → Launch        0
Burst Launches              83
Unknown Infrastructure     49
Lineage Gap                52
Insufficient Evidence      203
sum: 578
```

`sum(bucket counts) == total_launches` exactly, live, on the real 24h
population — no duplication, no omissions. Burst Launches dropped from
133 (double-counted across 5 buckets) to 83 (its true exclusive share)
once Known Infrastructure/Repeat Creator correctly claimed their higher
-priority launches first.

## Phase 6 — Drill-down integrity (live)

```python
seen = set()
for bucket in BUCKET_ORDER:
    mints = launches_in_bucket(pipeline, bucket)
    overlap = seen & set(mints)   # == 0 for every bucket, every run
    seen |= set(mints)
assert len(seen) == pipeline["total_launches"]   # exact match
```

Verified live: zero cross-bucket overlap for all 8 buckets; `BURST_LAUNCH`
drill-down (`?bucket=BURST_LAUNCH`) returns only launches whose
`primary_bucket` is exactly `BURST_LAUNCH` — never a launch also present
in `KNOWN_INFRASTRUCTURE` or any other bucket.

## Phase 7 — UI

`templates/discovery.html`:
- `behaviourQueuePanel()`, `filteredArchetype()`, `FILTER_ARCHETYPE`, and
  the `/api/ops-v2/behaviour-queue` fetch were removed entirely.
- `healthPanel()` (the single remaining panel, "Investigation Queue")
  extended `BUCKET_COLOURS` for the two new buckets; its title/description
  and drill-down mechanism (`?bucket=<ID>`) required no other changes,
  since it already rendered generically off the `buckets` array.
- Dead CSS (`.dw-behaviour-row`, `.dw-behaviour-meta`, `.dw-queue-stack`)
  removed.
- Confirmed via `node --check` that the inline script still parses
  cleanly after the removal.

## Phase 8 — Future extensibility (confirmed, no implementation)

`BUCKET_ORDER` remains a plain ordered tuple and `assign_bucket()` remains
a single generic first-match walk. Adding a future archetype (Coordinated
Migration, Treasury Burst, Shared Provisioning, Creator Recycling,
Migration Waves) requires only:
1. A new evidence-lookup function (mirroring `rapid_birth_launch_lookup`/
   `burst_launch_lookup`), built on an independently-verified-trustworthy
   signal.
2. One new `BUCKET_ORDER`/`BUCKET_LABELS`/`BUCKET_REASONS` entry and one
   new predicate branch in `assign_bucket()`, at the desired priority
   position.

No second dashboard is created — the archetype simply becomes another row
in the same panel, exactly as this sprint demonstrates for Rapid Birth →
Launch and Burst Launches.

## Tests

15 new tests (`tests/test_x27_5_unified_investigation_queue.py`): bucket
order includes behavioural archetypes at the correct priority, every
launch gets exactly one bucket with behaviour merged in, Rapid Birth/Burst
correctly consume from lower-priority buckets, Known Infrastructure/Repeat
Creator still win over behavioural matches (the exact double-counting
scenario this sprint fixes), zero overlap across all 8 buckets including
behavioural ones, drill-down returns only launches assigned to that
bucket, classification is deterministic, zero DB mutation,
`OUTCOME_TYPES` unchanged, the standalone Behaviour Queue module's lookup
functions remain importable (proving no logic was duplicated), the old
`/api/ops-v2/behaviour-queue` route returns 404, the unified route returns
200 with both behavioural buckets present, and the served HTML no longer
references the removed panel/JS. Two pre-existing X27.2 tests were updated
(fixture needed `token_analysis.migrated_at` + `wt_watchtower_launches`
tables, since the unified pipeline now always computes behavioural
evidence); two pre-existing X27.4 tests were updated to reflect the
route's removal and the extended `BUCKET_ORDER` (X27.4's own module
-level tests — archetype lookups, precision replay, coverage metadata —
are all unaffected and still pass, since `behaviour_queue.py` itself was
not modified). Full regression: 274 passed / 1 pre-existing unrelated
failure (confirmed independent of this work via `git stash` in an earlier
sprint).

## Live verification

`GET /api/ops-v2/investigation-pipeline?window=24h` → HTTP 200,
`conserved: true`, 577 total, 8 buckets summing exactly, both behavioural
buckets present. `GET /api/ops-v2/behaviour-queue` → HTTP 404 (confirmed
removed). Reloaded gunicorn and confirmed the served `/discovery` page
shows "Rapid Birth → Launch" inside the single unified panel with no
`behaviourQueuePanel`/`behaviour-queue` references remaining.

## Confirmation

No attribution, detection, or walkback logic was changed.
`src/ops/attribution_outcome.py::OUTCOME_TYPES` is confirmed byte-for-byte
unchanged. `src/ops/behaviour_queue.py`'s evidence-computation functions
(`rapid_birth_launch_lookup`, `burst_launch_lookup`,
`RAPID_BIRTH_LAUNCH_THRESHOLD_SECONDS`, `BURST_WINDOW_SECONDS`,
`BURST_MIN_CLUSTER_SIZE`) are unmodified — X27.5 only changed how their
output is *consumed* (by `investigation_pipeline.py`'s priority walk,
instead of a second, standalone dashboard). No database schema was
touched.
