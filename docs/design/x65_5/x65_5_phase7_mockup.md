# X65.5 — Phase 7: UI Mock-up

Illustrative mock-up (numbers are representative, not a live query —
the actual 43-launch confirmed-WATCHTOWER cohort is 42
Confirmed-Treasury/1-Other today, too narrow a mix to usefully
illustrate all four treasury tiers; a broader population including
Baseline-confidence, non-treasury-confirmed launches would produce a
richer real distribution once the bucket is implemented).

## Top-level Discovery entry point (new, alongside existing Behaviour Cohort view)

```
┌─────────────────────────────────────────────────────────────┐
│  Discovery                                                   │
│  ┌───────────────────┐  ┌───────────────────────────────┐   │
│  │ Behaviour Cohorts  │  │ ★ WATCHTOWER Provisioning (58)│   │
│  │ (existing view)    │  │ (new canonical bucket)        │   │
│  └───────────────────┘  └───────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

## WATCHTOWER Provisioning bucket — expanded card

```
WATCHTOWER Provisioning (58)
  Launches whose creator was funded through a single-use provisioning
  wallet within an observable SubProv fan-out — the validated
  WATCHTOWER architecture (X65.4), independent of treasury confirmation.

  Treasury confidence
  ┌──────────────────────────────────────────────┐
  │ Confirmed Treasury      ████████████  31      │
  │ Probable Treasury       ██              3      │
  │ New Treasury            █████          12      │
  │ Unknown Treasury        ██████         15      │  (click any row to filter)
  └──────────────────────────────────────────────┘

  Operational fingerprint (across all 58)
  ┌──────────────────────────────────────────────┐
  │ Fresh Creators           58 / 58  (100%)       │
  │ Fan-Out Observed         54 / 58  (93%)        │
  │ Single-use Provisioner   58 / 58  (100%)       │
  │ Provisioner Not Reused   58 / 58  (100%)       │
  └──────────────────────────────────────────────┘

  Operational confidence
  ┌──────────────────────────────────────────────┐
  │ High       ████████████████████  46           │
  │ Medium     ███                     8           │
  │ Baseline   █                       4           │
  └──────────────────────────────────────────────┘

  [ View all 58 launches ]
```

## Selecting the bucket — launch list (every existing dimension preserved)

```
WATCHTOWER Provisioning › All launches (58)

┌────────────┬──────────┬───────────┬────────────┬──────────┬────────────┬──────────┬──────────────┐
│ Mint       │ Creator  │ Topology  │ Treasury   │ Treasury │ Funding    │ Operation│ Op. Confidence│
│            │ Identity │           │ Tier       │          │ Origin     │          │              │
├────────────┼──────────┼───────────┼────────────┼──────────┼────────────┼──────────┼──────────────┤
│ 3fc6tLVPx6…│ FRESH_   │ SubProv   │ Confirmed  │ DchJqu…  │ DchJqu…    │ 69af79…  │ High         │
│            │ CREATOR  │ Fan-Out   │            │          │            │          │              │
├────────────┼──────────┼───────────┼────────────┼──────────┼────────────┼──────────┼──────────────┤
│ EGB4sv9ddN…│ FRESH_   │ Linear*   │ Confirmed  │ 9hGcxV…  │ 9hGcxV…    │ 4135d6…  │ High         │
│            │ CREATOR  │ (*known   │            │          │            │          │(fan-out       │
│            │          │ Topology  │            │          │            │          │ independently │
│            │          │ gap, X65.4)│           │          │            │          │ confirmed)    │
├────────────┼──────────┼───────────┼────────────┼──────────┼────────────┼──────────┼──────────────┤
│ B3Fq8SqBts…│ FRESH_   │ Unknown   │ Unknown    │ —        │ Unknown    │ __UNASS…│ Baseline      │
│            │ CREATOR  │           │ Treasury   │          │            │          │              │
└────────────┴──────────┴───────────┴────────────┴──────────┴────────────┴──────────┴──────────────┘

  Every column above already exists in Discovery today — the bucket
  adds no new column, only a new grouping and an Operational
  Confidence badge alongside the existing per-dimension values.
```

## Key interaction notes

- Clicking a Treasury-tier row (e.g. "New Treasury") filters the
  launch list to just that 12-launch subset, without leaving the
  WATCHTOWER Provisioning context — consistent with how existing
  Discovery dimensions already filter (X65.0/X65.1 precedent).
- The `EGB4sv9ddN...` example row above deliberately illustrates
  Phase 5's non-goal: this launch's existing `Topology` field still
  correctly (per today's classifier) shows `Linear`, even though this
  bucket's own, independent fan-out check (Phase 3, via
  `wt_candidate_websocket_watches`) found real fan-out — the row shows
  both facts side by side rather than silently overriding one with the
  other, with a small annotation explaining the discrepancy links back
  to the known X65.4 gap.
- The bucket's own summary card numbers (Fresh Creators, Fan-Out
  Observed, etc.) are the same per-launch facts also visible in the
  expanded row view — no hidden computation, only aggregation.
