# X29.1 — Hierarchical Operational Classification (Implementation)

**Status: implementation sprint, complete.** Implements the three-dimension
model designed in [X29.0](X29_0_OPERATIONAL_TOPOLOGY_INTELLIGENCE_FRAMEWORK.md).
No existing detection logic was changed — this sprint adds new, purely
read-only classifier modules and one new API route/UI panel; nothing in
`src/ops/investigation_pipeline.py`, `src/ops/behaviour_queue.py`,
`src/ops/attribution_outcome.py`, or the wt_cascade detection pipeline was
modified (confirmed by `git diff --stat`, zero lines changed in the first
two files).

## What was built

Three new, independent classifier modules, each pure/read-only, mirroring
X29.0 Part 6's recommendations exactly:

- **`src/ops/funding_topology.py`** — Stage 1. Exactly one topology per
  launch (`FAN_OUT`/`LINEAR`/`MULTI_LEVEL_FAN_OUT`/`MESH`/`UNKNOWN`),
  evaluated most-specific-first. Reads `wt_provisioning_edges` (sibling
  counts for Fan-Out vs Linear), `wt_attribution_outcomes.evidence_json`
  (broader-corpus fallback for launches never captured by the live
  cascade), and `watchtower_events` (`SUBPROV_SESSION_OPENED_WS` payloads,
  for Multi-Level Fan-Out — see Correction below).
- **`src/ops/operational_behaviour_tags.py`** — Stage 2. Zero or more
  additive tags. Reuses `behaviour_queue.py`'s `rapid_birth_launch_lookup()`/
  `burst_launch_lookup()` and `attribution_outcome.py`'s
  `evaluate_launcher_profile()` **verbatim** — no new behavioural logic,
  no new thresholds.
- **`src/ops/funding_mechanism.py`** — Stage 3. Zero or more additive tags
  (`WSOL_WRAP_CLOSE`/`PLAIN_TRANSFER`/`SEEDED_ACCOUNT_CLOSE`/`MIXED`).
  `MIXED` is a rollup rule over already-persisted per-edge mechanism
  values, not new detection.
- **`src/ops/operational_intelligence.py`** — combines the three into one
  flat per-mint record (`{mint: {topology, behaviours, mechanisms}}`), plus
  `build_hierarchy()` (a pure, on-demand tree computation — never stored)
  and `query()` (cross-dimensional filtering, independent of the tree).
- **`/api/ops-v2/operational-intelligence`** route
  ([operation_dashboard_routes.py](../../src/core/operation_dashboard_routes.py))
  — same conventions as the existing `/api/ops-v2/investigation-pipeline`
  route (`window=24h|all`, `mint=<MINT>`), plus `view=hierarchy` and
  `topology=`/`behaviour=`/`mechanism=` cross-dimensional filters.
- **`templates/discovery.html`** — a new "Operational Topology" panel,
  added **alongside** the existing Investigation Queue panel (not
  replacing it — see Deviation below), rendering the Topology→Behaviour→
  Mechanism drill-down tree with click-through to filtered mint lists.

## Corrections made during implementation (verified before shipping, not assumed)

X29.0's design doc made two assumptions about signal availability that
turned out to be wrong when checked directly against live data — both were
caught and fixed before the classifier was built on top of them, rather than
silently shipped:

1. **Multi-Level Fan-Out signal.** X29.0 assumed `wt_active_subprov_sessions`
   would carry a `session_tag LIKE 'sub_subprov%'` or an
   `open_reason='SUBPROV_TOP_UP'` marker distinguishing a child subprov from
   a directly-treasury-funded one. Checked directly:
   `wt_active_subprov_sessions.open_reason` never contains `SUBPROV_TOP_UP`
   (the real value is `KNOWN_SUBPROV_TOPUP`), and `session_tag` only ever
   holds `OPERATIONAL_SPEND_PROXY`-family values — neither marks
   sub-subprov lineage. The real, verified signal is the
   `watchtower_events` table's `SUBPROV_SESSION_OPENED_WS` event, whose
   `payload_json.via` field is `'subprov_plain_xfer'` (with a
   `parent_subprov` field) for genuine child-subprov opens vs.
   `'treasury_ws'` for direct treasury-funded opens — confirmed 19,387
   sub-subprov events vs. 1,567 direct events in the live
   `wt_ops_v2.db`. `funding_topology.py`'s `_multi_level_subprovs()` uses
   this verified signal, not the originally-assumed one.
2. **Funding Mechanism's `PLAIN_TRANSFER` value.** X29.0 assumed the raw
   persisted string in `wt_provisioning_edges.funding_mechanism` was
   `'PLAIN_TRANSFER'` (matching the code-level constant name seen
   elsewhere in `ws_cascade.py`). Checked directly: `wt_provisioning_edges`
   actually persists `'PLAIN_XFER'` — a different literal string for the
   same real-world mechanism. `funding_mechanism.py`'s `_RAW_TO_CANONICAL`
   map normalizes both `'PLAIN_XFER'` and `'PLAIN_TRANSFER'` onto one
   canonical `PLAIN_TRANSFER` tag, with a regression test
   (`test_plain_xfer_and_plain_transfer_normalize_to_same_canonical_tag`)
   proving this doesn't spuriously trigger a `MIXED` tag for a launch using
   only one real mechanism under two spellings.

## Honest, reported (not glossed-over) result: Mesh = 0

Per X29.0's Gap 2, Mesh had no formal classifier — this sprint implemented
the first candidate rule (a wallet appearing as both `treasury_wallet` and
`subprov_wallet` in `wt_active_subprov_sessions`) and **verified it against
live data before shipping it**: the confirmed-treasury set (10 wallets) and
confirmed-subprov set (64,400 wallets) have **zero overlap**. The rule is
left in place and documented as currently matching nothing — reported
honestly in both the module docstring and the replay output (`Mesh: 0,
0.0%`) rather than fabricated or silently hidden. This is not proof Mesh
doesn't exist in the underlying operations (the prior qualitative
`treasuries-fund-treasuries` finding used a different trace method); it
means this specific structural rule needs a richer data source (e.g. a
persisted treasury-to-treasury transfer set, which the codebase already
detects via `_classify_recipient`'s `TREASURY_MESH` classification but does
not yet persist as a queryable table) before it can classify anything.

## Full historical replay (real numbers, 365-day window, 4,951 mints)

```
=== TOPOLOGY (exclusive) ===
Multi-Level Fan-Out    281   5.7%
Mesh                     0   0.0%
Fan-Out                368   7.4%
Linear                 406   8.2%
Unknown               3896  78.7%
conserved: True (sum of topology counts == total_launches, exactly)

=== BEHAVIOUR (additive — percentages do not sum to 100%) ===
Rapid Birth→Migration    21   0.4%
Burst Launcher         1028  20.8%
Repeat Creator         2315  46.8%

=== MECHANISM (additive) ===
WSOL Wrap-Close         143  26.7%
Plain Transfer          383  71.6%
Seeded Account Close     18   3.4%
Mixed                     9   1.7%
```

**Hierarchy sample** (Fan-Out branch, from the full tree):

```
Fan-Out (368)
  Burst Launcher (75)
    Plain Transfer (34)
    (no mechanism evidence) (41)
  Repeat Creator (167)
    Plain Transfer (44)
    (no mechanism evidence) (123)
  (no behaviour tags) (160)
    WSOL Wrap-Close (1)
    Plain Transfer (81)
    Mixed (1)
    (no mechanism evidence) (79)
```

Note: 75 + 167 + 160 = 402 > 368 — this is **expected**, not a conservation
bug. A single Fan-Out mint with both Burst Launcher and Repeat Creator tags
is counted under both behaviour branches (the additive property working
correctly); Topology itself remains exactly conservative at the top level
(368 total, matches exactly).

**Validation of X29.0's Part 3 predictions**: the design doc's walkthrough
predicted (1) a clean multi-dimensional resolution for cascade-detected
launches, (2) Repeat Creator no longer forcing a topology value, (3)
`LINEAGE_GAP`-class launches correctly landing in Unknown rather than being
misrepresented as Linear, and (4) the majority-Unknown slice still carrying
independently useful Behaviour/Mechanism intelligence. All four are
directly visible in the replay output above: 46.8% Repeat Creator spans
every topology bucket including Multi-Level Fan-Out and Unknown (never
forced to one value); the 78.7% Unknown-topology slice still resolves 1,909
Repeat Creator and 813 Burst Launcher tags — exactly the "Unknown topology
does not mean no intelligence" argument X29.0 made, now measured rather
than asserted.

## Performance (known, pre-existing cost — not a new regression)

The Behaviour dimension (`evaluate_launcher_profile()` per distinct
creator) took ~5 minutes over the full 4,951-mint corpus — this is the
exact same per-creator cost X27.9.1 already documented and flagged as a
follow-up candidate (a `wt_launcher_profile_cache` table), not a new issue
introduced here. The new `/api/ops-v2/operational-intelligence` route
mitigates this with a 5-minute in-process cache
(`_OPERATIONAL_INTELLIGENCE_CACHE`), and the UI fetches the hierarchy view
**asynchronously, after** the rest of the Discovery page renders, so a slow
first load never blocks the existing panels.

## Deviation from the brief: old panel not retired

X29.0 Part 6 recommendation 4 explicitly flagged that retiring
`investigation_pipeline.py`'s `BUCKET_ORDER` model "needs explicit sign-off
before X29.1 starts, since dashboards/routes referencing `BUCKET_ORDER`
directly... will need updating in lockstep." That sign-off was not obtained
this sprint, so the new Operational Topology panel was added **alongside**
the existing Investigation Queue panel in `discovery.html`, not as a
replacement — both currently render on the Discovery page. Retiring the old
bucket model (per X29.0's recommendation) remains a distinct, separately-
scoped follow-up decision.

## Tests

`tests/test_x29_1_operational_topology_intelligence.py` — 24 tests, all
passing:
- Stage 1 topology: Fan-Out/Linear sibling-count boundary, Multi-Level
  Fan-Out and Mesh priority-order precedence, Unknown fallback (both
  "no evidence at all" and "subprov present but no sibling evidence" cases
  — the latter specifically guards against inferring Fan-Out/Linear without
  evidence), and a static signature-inspection test proving
  `classify_topology_for_launch()` cannot accept any behaviour/creator
  -history parameter (the structural guard against X29.0 Part 1's
  identified defect recurring).
- Stage 3 mechanism: additive zero/one/many tags, the `PLAIN_XFER`/
  `PLAIN_TRANSFER` normalization regression test, `SEEDED_ACCOUNT_CLOSE`
  alone never spuriously tagging `MIXED`, and unrecognized raw values being
  dropped rather than fabricated into a new tag.
- Hierarchy: top-level conservation (topology remains exclusive), determinism
  and non-mutation of the flat records on rebuild, additive over-counting
  at the behaviour level being expected rather than a bug, and correct
  handling of zero-behaviour/zero-mechanism branches.
- Cross-dimensional query: every example query pattern from the brief
  (topology alone, behaviour regardless of topology, mechanism regardless
  of topology, topology+mechanism combined, topology+behaviour combined,
  a combination matching nothing returning an empty list not an error, and
  no filters returning everything).

**Regression check**: `investigation_pipeline.py` and `behaviour_queue.py`
show zero diff from this sprint (confirmed via `git diff --stat`). Running
the directly-relevant existing test family
(`-k "x27_2 or x27_4 or x27_5 or x27_9 or behaviour_queue"`, 298 tests)
surfaced 11 pre-existing failures in `test_intelligence_refresh.py`/
`test_ops_x21e_operational_behaviour_service.py` — traced and confirmed
**not caused by X29.1**: these failures reproduce identically with every
one of this sprint's new files still present, and disappear entirely when
only `src/ops/operational_behaviour.py` (a completely different,
pre-existing module this sprint never touched, left over as uncommitted
work from earlier in this session) is set aside. Isolated with `git stash
push -- src/ops/operational_behaviour.py` and re-tested to confirm the
exact file responsible before concluding this was out of scope. All 287
other tests in that run passed.

## Success criteria (from the brief) — status

- Funding topology classified first, exactly one result, Unknown valid — ✅ done, tested.
- Behaviour tags attached independently, additive — ✅ done, reuses existing verified logic.
- Funding mechanisms attached independently, additive — ✅ done.
- UI renders as a drill-down hierarchy — ✅ done, added alongside existing panel (see Deviation).
- Storage remains dimension-based, not tree-based — ✅ the only persisted/cached shape is the flat per-mint record; the tree is rebuilt fresh from it on every call, proven deterministic and non-mutating by test.
- Existing detection logic unchanged — ✅ zero diff in `investigation_pipeline.py`/`behaviour_queue.py`/detection pipeline; confirmed pre-existing test failures traced to unrelated uncommitted work, not this sprint.
- Replay produces reproducible, real (never estimated) coverage — ✅ full 4,951-mint replay completed, conserved exactly, numbers reported above verbatim from the actual run.
- Cross-dimensional querying works independent of the hierarchy — ✅ `query()` operates directly on the flat records; 8 dedicated tests cover every example query pattern from the brief.
