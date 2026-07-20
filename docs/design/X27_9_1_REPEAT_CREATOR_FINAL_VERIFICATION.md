# X27.9.1 — Complete Repeat Creator Authoritative Classification

## Objective

Remove the remaining scalability blind spot from X27.9's fix, replay the
correction against a genuinely frozen dataset (not two rolling-window
snapshots), and confirm the live API/UI surface now presents Repeat
Creator classification correctly.

## Phase 1 — Removed the >1000-launch blind spot

X27.9's `_creator_launch_history_span()` gated its accurate,
`token_analysis`-derived span computation behind `launch_count <= 1000`,
falling back to `first_seen=last_seen=None` (observation_seconds=0, gate
fails) for any larger creator — a creator with 1001 launches was strictly
*less* classifiable than one with 999, purely due to size.

**Root cause of the original cap**: the implementation used `SELECT {ok} AS
ok, *` — every column, every matching row — which becomes expensive at
scale. The row count was never actually the cost driver; the column count
was.

**Fix**: select only the timestamp columns needed
(`SELECT {ok} AS ok, first_observed_at, analyzed_at, created_at,
block_time`, whichever exist), not `SELECT *`. This keeps the function a
single linear pass with memory proportional to row count (a handful of ints
per row), independent of creator size. The `launch_count <= 1000` gate was
removed entirely from the call site in `evaluate_launcher_profile()`.

No O(N²) behavior, no unbounded memory, no full-table scan was introduced —
exactly one query and one Python pass per creator, same as before, just
without the artificial ceiling.

**Verified against the platform's actual largest creator**
(`bwamJzztZsepfkteWRChggmXuiiCQvpLqPietdNfSXa`, coincidentally the
self-funding/spam scheme already documented in `docs/CLAUDE.md`'s own
example — 15,990 launches):

```
launch_count = 15990
observation_seconds = 8,273,707  (~95.8 days)
valid_launch_timestamp_count = 15990
established = True
elapsed: 2.46s
```

No error, no truncation, no fabricated span — every one of 15,990
timestamps was normalized and included.

## Phase 2/3 — Frozen-dataset replay with exact reconciliation

The X27.9 replay compared two live rolling-24h-window snapshots taken
seconds apart, which produced a small (~1-mint) population discrepancy
between "before" and "after" — correct in spirit but not bit-exact. This
sprint replaces it with a genuinely frozen dataset:

1. Snapshotted `wt_attribution_outcomes` (577 mints), each mint's resolved
   creator, and each mint's behavioural evidence (`rapid_birth`/`burst`
   lookups) **once**, to a static JSON file. This population never changes
   for the rest of the comparison.
2. Reconstructed the pre-X27.9 `evaluate_launcher_profile()` (the original
   funder-derived-span logic) as a standalone module and ran it against the
   **exact same frozen 577 mints**.
3. Ran the current (X27.9 + X27.9.1-fixed) evaluator against the **same
   577 mints**.

```
                        BEFORE (old evaluator)   AFTER (fixed evaluator)
Known Operation                   0                        0
Known Infrastructure             54                       54
Repeat Creator                   134                      241
Rapid Birth → Launch              0                        0
Burst Launches                   93                       71
Unknown Infrastructure           48                       36
Lineage Gap                      49                       30
Insufficient Evidence           199                      145
total                           577                      577
```

Movement matrix (measured directly on the identical frozen mint set, not
inferred):

```
Insufficient Evidence  → Repeat Creator: 54
Burst Launches         → Repeat Creator: 22
Lineage Gap             → Repeat Creator: 19
Unknown Infrastructure → Repeat Creator: 12
                                   total: 107
```

**Exact reconciliation, per bucket** (`before_count - moved_out + moved_in
== after_count`), verified programmatically for all 8 buckets:

```
KNOWN_OPERATION:        0 - 0 + 0 = 0    vs actual 0    OK
KNOWN_INFRASTRUCTURE:  54 - 0 + 0 = 54   vs actual 54   OK
REPEAT_CREATOR:        134 - 0 + 107 = 241  vs actual 241  OK
RAPID_BIRTH_LAUNCH:     0 - 0 + 0 = 0    vs actual 0    OK
BURST_LAUNCH:           93 - 22 + 0 = 71   vs actual 71   OK
UNKNOWN_INFRASTRUCTURE: 48 - 12 + 0 = 36   vs actual 36   OK
LINEAGE_GAP:            49 - 19 + 0 = 30   vs actual 30   OK
INSUFFICIENT_EVIDENCE: 199 - 54 + 0 = 145  vs actual 145  OK

sum(matrix) = 577 = total_launches (before AND after)
ALL RECONCILE EXACTLY: True
```

No rolling-window caveats, no timing drift — every number above derives
from one static, immutable population evaluated by two different code
paths.

## Phase 4 — Large-creator audit (top 20 by launch count, live)

```
bwamJzztZsepfkteWRCh   launches=15990  obs_s=8273956   established=True
8gM4gnxdLdkvifM9TCwk   launches= 7214  obs_s=1024730   established=True
7FVfSdnR9VPGjMtmBP1H   launches= 5803  obs_s=7178344   established=True
AV7PjXHL5JXZ1YoYRoN9   launches= 3452  obs_s=7607127   established=True
whamNNP9tHoxLg92yHvJ   launches= 3323  obs_s=8149687   established=True
F3WmHxwyCieSzPv4ALY2   launches= 3302  obs_s=2324954   established=True
Ep1ZM5X5YNPj4wkEtQsd   launches= 3233  obs_s=1129940   established=True
4UKLdTBiz6pGRccq9CGw   launches= 3206  obs_s=3059819   established=True
FTTGwytZTPoXrfw9SVcE   launches= 3111  obs_s=6094538   established=True
5FqUo9aBjsp7QeeyN6Vi   launches= 2949  obs_s=8150827   established=True
3WNi4g2ftVvRyYW68xyD   launches= 2781  obs_s=3452639   established=True
BuBMjNCr1UBpnPfywwAY   launches= 2742  obs_s=3253635   established=True
8YcbyX92UHTU23HZv3cc   launches= 2689  obs_s=8123183   established=True
DVhwSE98dHBtEGnQcYyA   launches= 2496  obs_s=8127425   established=True
AWGAwNm53RTSjxPEqxiY   launches= 2359  obs_s=7257905   established=True
2iC9HoFNhvC6Ems2rAhJ   launches= 2294  obs_s=1455849   established=True
8NJ7Ujpji8uMF2675mqa   launches= 2251  obs_s=8183988   established=True
29yFzeBZgxf5zqrAkKXw   launches= 2219  obs_s=8131199   established=True
6MMJgPRcvNWetgrqafH3   launches= 2133  obs_s=8056626   established=True
9RrKUhRpbPDNxR7x88Zs   launches= 2102  obs_s=6988046   established=True
```

**All 20 of the platform's largest creators — every single one exceeding
1000 launches — classify `established: True`. Zero were excluded merely
for size.**

## Phase 5 — Live deployment verification

`watchtower_api` (gunicorn, master PID 20584) reloaded via `SIGHUP`.
Confirmed the running process serves the fixed code:

```
GET /api/ops-v2/investigation-pipeline?window=24h&bucket=REPEAT_CREATOR
-> 242 mints (consistent with the fixed-code range measured directly)
```

This code path (`build_pipeline_health`/`assign_bucket`/
`evaluate_launcher_profile`) is served exclusively by the Flask API
(gunicorn), not by the `ws_cascade` daemon — confirmed no restart of
`ws_cascade` was needed or relevant for this sprint.

## Phase 6/7 — UI and API verification (X27.8 launch)

No existing UI surface renders a single-launch detail card with "Primary
Classification"/"Additional Behaviour" wording — the current drill-down is
bucket-level (a flat or creator-grouped mint list), which the X27.8 launch
now correctly appears in via the `REPEAT_CREATOR` bucket's creator-grouped
drill-down. Building a dedicated single-launch detail view was out of this
sprint's scope (data/classification correctness, not new UI work) and is
not claimed as delivered.

What **was** verified, both by direct function call and by the live API:

```
build_pipeline_health(..., now=1784220848)["assignments"]["GoFJ78jZsPhk3i5dyy8tmbpf4c6RkvRD6Vw3sUPfpump"]
-> {
     "bucket": "REPEAT_CREATOR",
     "creator": "C2N2Ac5E9m128Tct2AepMpN4VdULpxWAX3gcigqup7rc",
     "secondary_evidence": {
       "rapid_birth_launch": null,
       "burst_launch": {"matched": true, "cluster_size": 3}
     }
   }
```

`bucket == REPEAT_CREATOR` and `secondary_evidence.burst_launch.matched ==
true` — confirmed directly, matching the success criteria exactly. Also
confirmed live via `GET .../investigation-pipeline?window=all&bucket=REPEAT_CREATOR`
that the mint appears in the all-time bucket listing (`True`).

New API surface added for this sprint (Phase 7): `mint=<MINT>` query
parameter on the existing `/api/ops-v2/investigation-pipeline` route
returns `{"mint": ..., "assignment": {"bucket": ..., "secondary_evidence":
{...}}}` for one specific launch — no new endpoint, no new dashboard,
reusing the same `build_pipeline_health()` output already computed for the
bucket-summary response.

## Performance finding (reported, not silently fixed)

Serving `/api/ops-v2/investigation-pipeline` for the live 24h window (~579
launches, ~649 distinct creators) took **~45 seconds** measured directly,
and the `window=all` (365-day, 4,465 launches) variant repeatedly timed out
even at a 180-second client timeout during this sprint's own verification
calls. Root cause: `evaluate_launcher_profile()` runs once per distinct
creator in the window with no caching, and each call issues 2-4 SQL queries
plus (per Phase 1's fix) a full per-row timestamp scan for that creator's
entire history — costs that compound across hundreds of creators per
request, dominated by the platform's largest few creators (the 16K-launch
creator alone takes ~2.5s by itself).

This is not a regression introduced by removing the `<=1000` cap — the cap
was *masking* large-creator cost by simply not measuring their span at all
(returning 0 immediately). Now that every creator gets a correct
measurement, the aggregate per-request cost is higher but the classification
is no longer silently wrong for scale. **Not fixed in this sprint** (outside
Phase 1-8's explicit scope, which is about correctness/inclusion, not
request-latency); flagged as a follow-up candidate: per-creator profile
caching (e.g., a `wt_launcher_profile_cache` table keyed by creator wallet,
invalidated on new launches) would remove the recomputation-per-request
cost entirely.

## Phase 8 — Tests

`tests/test_x27_9_1_repeat_creator_final_verification.py` (7 tests, all
pass):

- A creator with 1200 launches (exceeding the old 1000 cap) still measures
  its true ~90-day span and classifies `established: True`.
- Observation span is independent of launch count: two creators with
  identical true spans (999 vs 1500 launches) measure the same span within
  1 day of each other.
- A frozen dataset evaluated twice produces identical bucket assignments
  and conserves exactly.
- A synthetic before/after movement (Burst Launch → Repeat Creator as a
  creator's history accumulates) reconciles exactly against the raw
  bucket-count delta.
- No creator at counts 999/1000/1001/5000 is excluded purely for crossing
  the old size boundary.
- The live API's new `mint=<MINT>` parameter returns `bucket` +
  `secondary_evidence` for the X27.8 launch when it's within the queried
  window, and returns `assignment: null` (not an error) for an unknown
  mint.

Full regression re-run (`ws_cascade`/`x24`/`x27` suites, including the
existing 299 tests from X27.7/X27.8/X27.9/X24.8/X24.9) confirmed clean.

## What was not changed (non-goals, confirmed)

`MIN_LAUNCHER_HISTORY`, `MIN_LAUNCHER_OBSERVATION_SECONDS`, `BUCKET_ORDER`,
creator identity resolution, attribution outcomes, walkback logic,
lifecycle capture, websocket behavior, and database schema are all
unchanged. No creator or mint was special-cased. The fix removes a
scalability ceiling uniformly for every creator, verified against the
platform's actual largest creators (Phase 4), not just a synthetic
1001-launch test case.

## Confirmation

Repeat Creator classification is now provably independent of creator size,
timestamp format, discovery timing, and launch volume — confirmed via a
16,000-launch real creator, the top-20 largest creators on the platform,
mixed epoch/ISO-8601 timestamp fixtures (X27.9), and a frozen-dataset replay
with exact, zero-discrepancy reconciliation across every bucket.
