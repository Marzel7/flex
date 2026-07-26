# X65.6 — Phase 7: Discovery Mock-up (integrated into the existing page)

Text mock-up of the proposed layout at `http://localhost:5002/discovery?window=7d`,
showing Campaign inserted into the existing numbered cascade
(see the companion visual mock-up artifact for a rendered version).
Numbers are illustrative (same caveat as X65.5 Phase 7 — a broader,
mixed population would show a richer real distribution once
implemented).

```
Discovery Cohort Report
Investigate progressively from behaviour through creator identity,
operational pattern, topology, funding origin and operation attribution.

Current Selection
┌─────────────────┬─────────────────┬──────────────────┬───────────┬─────────┬───────────┐
│ Behaviour        │ Creator Identity│ Operational       │ Topology  │ Funding │ Operation │
│ QUICK_BIRTH_     │ FRESH_CREATOR   │ Pattern           │ All       │ All     │ All       │
│ MIGRATION        │                 │ (not yet selected)│ topologies│ origins │ operations│
└─────────────────┴─────────────────┴──────────────────┴───────────┴─────────┴───────────┘

1. Behaviour Cohort                              [existing, unchanged]
   [ QUICK_BIRTH_MIGRATION  412 ]  [ RAPID_MIGRATION  1,203 ]  ...

2. Creator Identity                              [existing, unchanged]
   [ FRESH_CREATOR  358 ]  [ SERIAL_DEPLOYER  40 ]  [ UNKNOWN_CREATOR_IDENTITY  14 ]

3. Campaign                           [NEW — this task]
   Classifying 358 FRESH_CREATOR launches by validated provisioning
   fingerprint (independent of treasury resolution).

   [ WATCHTOWER Provisioning   58 ]   [ Other Campaign   211 ]   [ Unknown Campaign   89 ]

   ── selecting "WATCHTOWER Provisioning" reveals: ──

   Treasury breakdown (58)
   ┌────────────────────────────────────┐
   │ Confirmed Treasury      31          │
   │ Probable Treasury        8          │
   │ New Treasury             11         │
   │ Unknown Treasury         8          │
   └────────────────────────────────────┘

   Confidence breakdown (58)
   ┌────────────────────────────────────┐
   │ High         46                     │
   │ Medium        8                     │
   │ Baseline      4                     │
   └────────────────────────────────────┘

4. Topology                                      [existing, unchanged, renumbered 3→4]
   Classifying 58 WATCHTOWER Provisioning launches.
   [ SubProv Fan-Out  ... ]  [ Linear  ... ]  [ Unknown  ... ]
   (Topology terminology per X65.5 Phase 6; underlying classifier unchanged,
    known coverage gap tracked separately in X65.4 — not fixed by this task)

5. Funding Origin                                [existing, unchanged, renumbered 4→5]
6. Operation Attribution                         [existing, unchanged, renumbered 5→6]

Launch Results — 58 launches
┌────────────────┬───────────┬─────────────┬──────────────┬──────────┐
│ Token           │ Treasury  │ Topology     │ Operational  │ Confidence│
│                 │           │              │ Pattern      │           │
├────────────────┼───────────┼─────────────┼──────────────┼──────────┤
│ ABC…            │ Unknown   │ SubProv     │ WATCHTOWER   │ High      │
│                 │           │ Fan-Out     │ Provisioning │           │
│ XYZ…            │ Confirmed │ SubProv     │ WATCHTOWER   │ High      │
│                 │           │ Fan-Out     │ Provisioning │           │
│ DEF…            │ New       │ SubProv     │ WATCHTOWER   │ Medium    │
│                 │           │ Fan-Out     │ Provisioning │           │
└────────────────┴───────────┴─────────────┴──────────────┴──────────┘
Every row remains fully explorable via every existing Discovery field —
no column removed, Campaign added as one more column.
```

## Key differences from X65.5's mock-up

- X65.5 proposed a **separate top-level tab/entry point** ("Behaviour
  Cohorts" vs. "★ WATCHTOWER Provisioning") outside the existing
  cascade.
- X65.6 instead integrates Campaign **as stage 3 inside the
  same existing cascade**, per this task's explicit objective
  ("rather than creating a separate page or workflow"). Selecting
  `WATCHTOWER Provisioning` at stage 3 produces the same
  Treasury/Confidence breakdown X65.5 designed, but as an in-place
  expansion of the stage-3 card rather than a separately-tabbed view —
  a smaller, more conservative UI change that fits entirely within
  the page's existing information architecture.

See the companion visual artifact for a rendered version of this same
layout, styled to match the existing Discovery page's dark
operational-console visual language.
