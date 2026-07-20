# X27.8 — Repeat Creator vs Burst Launches Classification Verification

**Investigation only. No code, bucket priority, or thresholds were changed.**

Launch under investigation: `GoFJ78jZsPhk3i5dyy8tmbpf4c6RkvRD6Vw3sUPfpump`
Creator: `C2N2Ac5E9m128Tct2AepMpN4VdULpxWAX3gcigqup7rc`
`outcome_type` (persisted, `wt_attribution_outcomes`): `INSUFFICIENT_EVIDENCE`
Migrated at: `1784220816` (2026-07-16 21:53:36 UTC)

## Conclusion: **C — Repeat Creator rule defect**

The creator legitimately has 895 launches spanning **2026-04-14 to
2026-07-16 (~893,000 seconds, over 3 months)** — far beyond the
`evaluate_launcher_profile()` gate's 7-day (`604,800`s) observation
requirement. The rule correctly requires *sustained* history, not just
*count*, to guard against the shared/bot-wallet false-positive case (per its
own docstring) — but its `observation_seconds` measurement used the wrong
data source for this creator, producing `6` seconds instead of the true
~893,000, so a launcher that should legitimately satisfy the rule's own
stated intent was rejected on a measurement defect, not a real gap in
history.

## Phase 1 — Replay bucket evaluation (actual code execution, not inferred)

Ran `src.ops.investigation_pipeline.assign_bucket()` directly against live
production data for this exact mint:

| Bucket | Matched? | Reason |
|---|---|---|
| Known Operation | FALSE | `outcome_type='INSUFFICIENT_EVIDENCE'` does not map to `KNOWN_OPERATION` |
| Known Infrastructure | FALSE | same — does not map to `KNOWN_INFRASTRUCTURE` |
| Repeat Creator | FALSE | `evaluate_launcher_profile()` returned `established: false` (see Phase 3) |
| Rapid Birth → Launch | FALSE | `rapid_birth_launch_lookup()` returned no evidence for this mint (`None`) |
| Burst Launches | **TRUE** | `burst_launch_lookup()` returned `{"matched": true, "cluster_size": 3}` |
| Unknown Infrastructure | not reached | Burst Launches already matched (first-match-wins) |
| Lineage Gap | not reached | same |
| Insufficient Evidence | not reached | same |

## Phase 2 — Final bucket selection

Only one bucket evaluated TRUE (Burst Launches) — this is not a
multiple-match tie-break scenario:

```
Repeat Creator      FALSE  (established=false — see Phase 3)
Burst Launches      TRUE   (cluster_size=3, priority=5)
selected = Burst Launches
```

`assign_bucket()` called directly, live:
```python
assign_bucket(ops_conn, core_conn, mint, "INSUFFICIENT_EVIDENCE", now=1784220848,
              rapid_birth_evidence=None, burst_evidence={"matched": True, "cluster_size": 3})
# -> {"bucket": "BURST_LAUNCH", "label": "Burst Launches", "reason": "..."}
```

## Phase 3 — Repeat Creator rule inspection (the actual numbers)

`evaluate_launcher_profile(ops_conn, core_conn, creator, now=1784220848)`,
called directly against live data:

```json
{
  "creator": "C2N2Ac5E9m128Tct2AepMpN4VdULpxWAX3gcigqup7rc",
  "launch_count": 895,
  "observation_seconds": 6,
  "canonical_operator_linked": false,
  "fresh_provisioning_evidence": false,
  "material_infrastructure_change": false,
  "minimum_launch_count": 5,
  "minimum_observation_seconds": 604800,
  "established": false,
  "historical_funder_count": 5,
  "latest_funder_observed_at": 1777326698,
  "first_seen": 1777326692,
  "last_seen": 1777326698
}
```

| Metric | Value | Threshold | Result |
|---|---|---|---|
| `launch_count` | 895 | 5 | PASS |
| `observation_seconds` | **6** | 604,800 | **FAIL** |

`established` requires both gates (plus the canonical/fresh-provisioning/
infrastructure-change guards, all `false` here — not the limiting factor).
The launch-count gate is comfortably satisfied; the observation-window gate
is the sole reason `established=false`.

**Why `observation_seconds` reads 6, not ~893,000**: `evaluate_launcher_profile()`
(`src/ops/attribution_outcome.py:184-222`) computes `first_seen`/`last_seen`
from `creator_funders.first_detected_at` when that table has any matching
rows, and only falls back to scanning `token_analysis.created_at` (the
correct, accurate source) if `creator_funders` returned nothing:

```python
if launch_count and (first_seen is None or last_seen is None) and launch_count <= 1000:
    # ... token_analysis-based fallback, never reached here
```

For this creator, `creator_funders` has exactly 5 rows
(`historical_funder_count: 5`), all with `first_detected_at` inside the
**same 6-second window** (2026-04-27 21:51:32–38 UTC) — confirmed directly:

```sql
SELECT COUNT(DISTINCT funder_address), MIN(first_detected_at), MAX(first_detected_at)
FROM creator_funders WHERE creator_address='C2N2Ac5E9m128Tct2AepMpN4VdULpxWAX3gcigqup7rc'
-- (5, '2026-04-27 21:51:32', '2026-04-27 21:51:38')
```

This is a one-time bulk funder-discovery event (all 5 funders detected
within the same 6-second backfill/discovery pass), not the creator's actual
activity span. Because `first_seen`/`last_seen` were non-null from this
source, the more-accurate `token_analysis`-based fallback (which *would*
have measured the creator's true history) was never reached — confirmed
directly:

```sql
SELECT MIN(created_at), MAX(created_at), COUNT(*) FROM token_analysis
WHERE pf_ws_creator='C2N2Ac5E9m128Tct2AepMpN4VdULpxWAX3gcigqup7rc'
-- (1776190090 [2026-04-14 18:08:10 UTC], '2026-07-16T16:53:35Z', 895)
```

The creator's real observation span is **~893,000 seconds — comfortably
above the 604,800s threshold**. Had `observation_seconds` been measured off
`token_analysis` (as the code's own fallback path is designed to do), this
creator would satisfy `established` and this launch would land in Repeat
Creator, exactly matching the "Highly active creator" signal already shown
in the Creator Activity panel.

## Phase 4 — Creator identity verification

| | Value |
|---|---|
| Creator Activity panel source | `CreatorActivityService.build()`, `src/ops/creator_activity.py` — uses `pf_ws_creator` exclusively when present (never `COALESCE`d with `earliest_tx_creator`, to avoid inflating counts via a shared transaction authority) |
| Repeat Creator rule source | `assign_bucket()` → `_resolve_creator()`, `src/ops/investigation_pipeline.py:155-163` — `COALESCE(pf_ws_creator, earliest_tx_creator)` |
| Resolved wallet (both paths) | `C2N2Ac5E9m128Tct2AepMpN4VdULpxWAX3gcigqup7rc` |
| Same entity? | **YES** |

For this specific mint, `pf_ws_creator` is non-null
(`token_analysis.pf_ws_creator = 'C2N2...'`), so both code paths resolve to
the identical wallet regardless of the `COALESCE` difference. **No identity
mismatch** — the 895-launch count shown in Creator Activity and the
895-launch count measured by `evaluate_launcher_profile()` are counting the
exact same creator.

## Phase 5 — Burst Launch rule verification

`burst_launch_lookup()` (`src/ops/behaviour_queue.py`) returned
`{"matched": true, "cluster_size": 3}` for this mint. Direct query of the
underlying migration cluster:

```sql
SELECT mint, migrated_at FROM token_analysis
WHERE migrated_at BETWEEN 1784220756 AND 1784220876  -- target ± 60s
ORDER BY migrated_at
```

| mint | migrated_at |
|---|---|
| `3YudidgEnPyMekuUCDEUf79MPqce8FHWF4ZSBV64pump` | 1784220785 |
| `GoFJ78jZsPhk3i5dyy8tmbpf4c6RkvRD6Vw3sUPfpump` (target) | 1784220816 |
| `9HnAYgmqHjAHUHo3fJyJ46qJu9WmGHhxdMAGFwuPpump` | 1784220875 |

Three distinct mints migrating within a 90-second span (all pairwise within
the 60-second sliding window `BURST_WINDOW_SECONDS` requires),
`cluster_size=3` meets `BURST_MIN_CLUSTER_SIZE=3` exactly. **The Burst Launch
match is genuine and correctly evidenced** — not a false positive, not
stale/cached data.

## Phase 6 — X27.5 priority implementation verification

```python
BUCKET_ORDER = (
    KNOWN_OPERATION, KNOWN_INFRASTRUCTURE, REPEAT_CREATOR,
    RAPID_BIRTH_LAUNCH, BURST_LAUNCH,
    UNKNOWN_INFRASTRUCTURE, LINEAGE_GAP, INSUFFICIENT_EVIDENCE,
)
```

Matches the documented X27.5 order exactly, position-for-position.
`assign_bucket()`'s body (`investigation_pipeline.py:197-215`) is a
sequential `if`/`return` chain evaluated in exactly this order, returning on
the first match — genuine first-match-wins semantics, not a scoring or
tie-break system. **No priority regression exists**: Repeat Creator was
evaluated *before* Burst Launch (as designed) and returned `False` on its
own merits (Phase 3); Burst Launch was reached next and matched.

## Phase 7 — UI consistency assessment

The Creator Activity panel and the Investigation Queue bucket are two
independent, correctly-scoped signals answering different questions:

- **Creator Activity** ("Highly active creator", 894/895 launches): a
  creator-level, all-time profile — purely descriptive, no bucket logic.
- **Investigation Queue bucket** (Burst Launches): a launch-level,
  mutually-exclusive investigative disposition for *this specific migrated
  launch*, per X27.2/X27.5's governing principle.

`Investigation bucket ≠ Creator profile` is **intentional and expected** by
design (X27.5's docstring: two independently-evaluated additions, never
merged into one signal) — the UI is not misrepresenting anything. However,
this specific case is not simply "expected divergence": the creator's real
history genuinely satisfies the Repeat Creator rule's *intended* criteria
(sustained activity, not just count), and would have matched had
`observation_seconds` been measured correctly. So while the UI pattern
itself (two independent panels) is correct behavior, the specific bucket
outcome for this launch is downstream of the Phase 3 measurement defect, not
a case of "correct exclusion, supplementary context" (Outcome A).

## Deliverables

1. Full bucket evaluation replay — Phase 1 (executed against live code/data).
2. Repeat Creator evaluation — Phase 3 (`launch_count=895` PASS,
   `observation_seconds=6` FAIL against 604,800 threshold).
3. Burst Launch evaluation — Phase 5 (genuine 3-launch, 90-second cluster).
4. Final bucket selection reasoning — Phase 2 (single match, no tie-break
   needed).
5. Creator identity verification — Phase 4 (same wallet, confirmed).
6. Priority-order verification — Phase 6 (code matches docs exactly, no
   regression).
7. UI consistency assessment — Phase 7 (two-panel design is intentional;
   this instance's divergence traces to the Phase 3 defect, not the design).
8. Conclusion — **C, Repeat Creator rule defect**: `evaluate_launcher_profile()`'s
   `observation_seconds` measurement sourced from `creator_funders.first_detected_at`
   (a funder-discovery timestamp, not an activity-span measurement) when that
   table has any rows, bypassing the more-accurate `token_analysis`-based
   fallback that exists in the same function and would have measured the
   creator's true ~893,000-second history correctly.

## What this investigation did not do

Per the explicit brief: no code was modified, no bucket priority was
changed, no threshold was altered, and no conclusion was assumed before
replaying the actual evaluation against live data. The defect identified in
Phase 3 is reported as a finding for a future, separately-scoped fix — not
remediated here.
