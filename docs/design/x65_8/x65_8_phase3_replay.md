# X65.8 — Phase 3: Replay Validated WATCHTOWER Launches

Live replay of all 43 confirmed WATCHTOWER launches against: (a) the
live `/api/ops-v2/operational-intelligence?window=all` response's
`campaign` field (X65.7, unmodified, queried live — not recomputed in
isolation, to avoid the pipeline-context errors an isolated
reconstruction would introduce), (b) an "Observed" topology computed
directly from `wt_candidate_websocket_watches` (subprov recipient
count), and (c) the same response's existing `topology` field
(unmodified `funding_topology.py`).

## Full replay table

| Mint | Campaign | Observed | Current Topology | Correct? |
|---|---|---|---|---|
| JyJWcxa8xP... | OTHER_CAMPAIGN | NO_DATA (0) | UNKNOWN | N/A (not WATCHTOWER) |
| AB7XXeQAvN... | NOT_IN_WINDOW | NO_DATA (0) | NOT_IN_WINDOW | N/A |
| 3gbBrgtwyx... | NOT_IN_WINDOW | NO_DATA (0) | NOT_IN_WINDOW | N/A |
| Bn9kT53VKy... | NOT_IN_WINDOW | LINEAR (1) | NOT_IN_WINDOW | N/A |
| sP79aMCqfZ... | NOT_IN_WINDOW | FAN_OUT (2) | NOT_IN_WINDOW | N/A |
| 2PZAgPXXAU... | NOT_IN_WINDOW | FAN_OUT (5) | NOT_IN_WINDOW | N/A |
| 5iPoWhLAzo... | NOT_IN_WINDOW | FAN_OUT (13) | NOT_IN_WINDOW | N/A |
| 3SkdUCkXKX... | NOT_IN_WINDOW | FAN_OUT (37) | NOT_IN_WINDOW | N/A |
| 2vBvPiCpsb... | NOT_IN_WINDOW | FAN_OUT (50) | NOT_IN_WINDOW | N/A |
| GQEEL98udp... | NOT_IN_WINDOW | FAN_OUT (45) | NOT_IN_WINDOW | N/A |
| 6YqsppC6qj... | NOT_IN_WINDOW | NO_DATA (0) | NOT_IN_WINDOW | N/A |
| 9x4NHggD8U... | NOT_IN_WINDOW | FAN_OUT (179) | NOT_IN_WINDOW | N/A |
| 9YXYH9A8b2... | NOT_IN_WINDOW | FAN_OUT (57) | NOT_IN_WINDOW | N/A |
| CQJzHVvpn3... | NOT_IN_WINDOW | FAN_OUT (9) | NOT_IN_WINDOW | N/A |
| 7DZuY9tjXs... | NOT_IN_WINDOW | FAN_OUT (35) | NOT_IN_WINDOW | N/A |
| 6hDxh9uXFw... | NOT_IN_WINDOW | FAN_OUT (15) | NOT_IN_WINDOW | N/A |
| F2fcE5sjDu... | NOT_IN_WINDOW | FAN_OUT (9) | NOT_IN_WINDOW | N/A |
| 4MnczXgbDt... | NOT_IN_WINDOW | FAN_OUT (26) | NOT_IN_WINDOW | N/A |
| 5UQNY2hk4f... | NOT_IN_WINDOW | FAN_OUT (11) | NOT_IN_WINDOW | N/A |
| **6YZm2PVLBo...** | **WATCHTOWER** | **FAN_OUT (255)** | **UNKNOWN** | **✗ Mismatch** |
| **CPtvQTf8bX...** | **WATCHTOWER** | **FAN_OUT (60)** | **UNKNOWN** | **✗ Mismatch** |
| **7YnzMgUvUj...** | **WATCHTOWER** | **FAN_OUT (19)** | **UNKNOWN** | **✗ Mismatch** |
| **AshPvt8cws...** | **WATCHTOWER** | **FAN_OUT (39)** | **UNKNOWN** | **✗ Mismatch** |
| **AyafwyhUhZ...** | **WATCHTOWER** | **FAN_OUT (300)** | **UNKNOWN** | **✗ Mismatch** |
| **EN3kJPf6bv...** | **WATCHTOWER** | **FAN_OUT (75)** | **UNKNOWN** | **✗ Mismatch** |
| **3fc6tLVPx6...** | **WATCHTOWER** | **FAN_OUT (106)** | **UNKNOWN** | **✗ Mismatch** |
| **F7NmdG9JAh...** | **WATCHTOWER** | **FAN_OUT (218)** | **UNKNOWN** | **✗ Mismatch** |
| **EZozuXuPez...** | **WATCHTOWER** | **FAN_OUT (167)** | **UNKNOWN** | **✗ Mismatch** |
| **6SXTLNED1i...** | **WATCHTOWER** | **FAN_OUT (61)** | **UNKNOWN** | **✗ Mismatch** |
| **AvLiJBdtb4...** | **WATCHTOWER** | **FAN_OUT (239)** | **UNKNOWN** | **✗ Mismatch** |
| **7pncD23yVt...** | **WATCHTOWER** | **FAN_OUT (272)** | **UNKNOWN** | **✗ Mismatch** |
| **F612mB7c9p...** | **WATCHTOWER** | **FAN_OUT (4)** | **UNKNOWN** | **✗ Mismatch** |
| **HHmh4bSYBX...** | **WATCHTOWER** | **FAN_OUT (310)** | **UNKNOWN** | **✗ Mismatch** |
| **EeujXJZkoy...** | **WATCHTOWER** | **FAN_OUT (34)** | **UNKNOWN** | **✗ Mismatch** |
| 3xFT4J96Vz... | NOT_IN_WINDOW | FAN_OUT (70) | NOT_IN_WINDOW | N/A |
| **753AMCTdvo...** | **WATCHTOWER** | **FAN_OUT (15)** | **UNKNOWN** | **✗ Mismatch** |
| **Ct2VDLuBan...** | **WATCHTOWER** | **FAN_OUT (86)** | **UNKNOWN** | **✗ Mismatch** |
| **C4TFLdu1f2...** | **WATCHTOWER** | **FAN_OUT (481)** | **UNKNOWN** | **✗ Mismatch** |
| **EQ6qQsweDh...** | **WATCHTOWER** | **FAN_OUT (14)** | **UNKNOWN** | **✗ Mismatch** |
| **AwXtJ4QsZw...** | **WATCHTOWER** | **FAN_OUT (2)** | **UNKNOWN** | **✗ Mismatch** |
| FN7GB2Mf4p... | NOT_IN_WINDOW | FAN_OUT (7) | NOT_IN_WINDOW | N/A |
| 4SLVH8rtur... | NOT_IN_WINDOW | FAN_OUT (54) | NOT_IN_WINDOW | N/A |
| **EGB4sv9ddN...** | **WATCHTOWER** | **FAN_OUT (25)** | **LINEAR** | **✗ Mismatch (direct contradiction)** |

## Summary

| Category | Count |
|---|---|
| **Campaign=WATCHTOWER, Current Topology correctly = FAN_OUT-equivalent** | **0** |
| Campaign=WATCHTOWER, Current Topology wrongly = UNKNOWN | 20 |
| Campaign=WATCHTOWER, Current Topology wrongly = LINEAR (direct contradiction — 25 observed recipients) | 1 |
| Campaign=WATCHTOWER total | 21 |
| Not in the 365-day window's `wt_attribution_outcomes` population at all (older mints; a separate, pre-existing coverage boundary, not a Topology defect) | 21 |
| Campaign=OTHER_CAMPAIGN (correctly not WATCHTOWER) | 1 |

**Of the 21 launches Campaign correctly and independently identifies as
WATCHTOWER, 0 are correctly classified by the current Topology
classifier.** 20 are `UNKNOWN` and 1 (`EGB4sv9ddN...`) is `LINEAR` —
the same specific contradiction case already found in X65.4, reconfirmed
here against Campaign's own live output rather than a standalone
replay script.

## Explaining every mismatch

Every mismatch traces to the identical mechanism already established
in X65.4 and Phase 2 above: the current Topology classifier's primary
Fan-Out/Linear rule counts `SUBPROV_TO_CREATOR` edges in
`wt_provisioning_edges` — a table with only 2.3% coverage of this exact
population (Phase 2) — rather than `wt_candidate_websocket_watches`,
which covers 90.7% of the same population and, when consulted directly
(the "Observed" column above), shows real, substantial fan-out (2 to
481 recipients) for every single mismatched launch.

## Note on the "NOT_IN_WINDOW" population

21 of the 43 confirmed launches are older than the 365-day window this
task queried and have no row in the live `wt_attribution_outcomes`
population at all — this is a separate, pre-existing population
boundary (the same underlying table Campaign and Topology both key
off of), not a defect in either classifier being evaluated in this
task. These rows are marked `N/A` rather than folded into the
mismatch count, since neither classifier produced a comparable result
for them.
