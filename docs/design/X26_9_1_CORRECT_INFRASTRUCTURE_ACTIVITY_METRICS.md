# X26.9.1 — Correct Infrastructure Activity Metrics in Discovery

Status: Implemented, tested, live-verified against the real Axiom launch.
No detection, attribution, walkback, operation identity, or schema logic
changed — this is a presentation-layer correction confined to
`src/ops/operational_behaviour.py`.

---

## Confirmed defect

Per X26.9's audit, `wt_discovered_subprovs.creator_count` for Axiom
(`AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk`) is `2` — a value produced
exclusively by the old `promote_recurring_funders()` path
(`walkback_worker.py`), scoped to `COUNT(DISTINCT creator) FROM
wt_walkback_queue WHERE funder_wallet=? AND intelligence_outcome=
'NO_ATTRIBUTION_FOUND'`. This is neither a live count nor an all-time
count — it is a frozen historical subset from before X26.3 added the
infrastructure-exclusion check that now prevents this path from ever
touching Axiom's row again.

## Implementation

**1. New lookup, `_infrastructure_activity_facts()`** (`operational_behaviour.py`):
called once in `build()`, only when `funder_role` is
`REJECTED_INFRASTRUCTURE`/`OTHER_REJECTED`, using the ops connection
already open inside `build()`'s `try` block (so `funder_role` resolution
was moved earlier, into that same block, to share the connection):

```python
attributed_launch_count:
  SELECT COUNT(DISTINCT mint) FROM wt_attribution_outcomes WHERE terminal_entity=?

observed_creator_count:
  SELECT COUNT(DISTINCT creator) FROM wt_walkback_queue
  WHERE (funder_wallet=? OR subprov=?) AND creator IS NOT NULL
```

Returns `None` for `VALID_SUBPROVISIONER`/`UNRESOLVED_FUNDER` — these
metrics are specific to a rejected/infrastructure role; a genuine
sub-provisioner is completely untouched and keeps using
`wt_discovered_subprovs.creator_count` exactly as before.

**2. Returned as explicit, dedicated fields.** `build()`'s response now
includes a new top-level key `infrastructure_activity`:
```json
{"attributed_launch_count": 46, "observed_creator_count": 23,
 "coverage_note": "Reflects launches/creators present in the persisted
 attribution and walkback datasets, not an exhaustive chain-wide total."}
```
`None` for any non-rejected role. Never overloaded onto `creator_count`.

**3/4. Behaviour Summary rewired.** For `REJECTED_INFRASTRUCTURE`, renders
exactly:
```
Funding source: Axiom · reviewed infrastructure
Launches attributed here: 46
Distinct creators observed: 23
```
`wt_discovered_subprovs.creator_count` is never read for this role branch
at all (the prior `subprov_facts.get("creator_count")` lookup is now
gated behind `is_valid`, i.e. `funder_role == VALID_SUBPROVISIONER`).

**5. Infrastructure Pattern rewired** identically — the prior
`"Infrastructure wallet (Axiom) funded N observed creators"` line (sourced
from `wt_discovered_subprovs.creator_count`) is replaced by two lines
sourced from the new metrics:
```
Infrastructure wallet (Axiom): 46 launches attributed here     (source: wt_attribution_outcomes.terminal_entity)
Infrastructure wallet (Axiom): 23 distinct creators observed   (source: wt_walkback_queue)
```

**6. Genuine `VALID_SUBPROVISIONER` wallets unaffected.** Verified live
(`Hk6AxTQZyK7zsPfQLmgGdw8t9nzaD3zDeRjduNHGxbXF`, `state=PROVISIONAL_SUBPROV`)
— still renders `"Sub-provisioner has funded 16 creators"` exactly as
before, `infrastructure_activity: null`.

**7. Implementation-source parenthetical text removed** from analyst-facing
prose in `_build_behaviour_summary` — `"(per wt_discovered_subprovs)"` no
longer appears anywhere in the Behaviour Summary section (it was present
in the pre-X26.9.1 wording for both valid and rejected roles; both were
stripped). Note: the `"source"` field on each `infrastructure_pattern`
entry is a separate, structured metadata field (not embedded in the
displayed label text) and was intentionally left as-is — it's a machine-
readable citation field, not prose, and the brief's forbidden phrases are
specifically about parenthetical text *within* the wording.

**8. Coverage documented explicitly** via the new `coverage_note` field on
`infrastructure_activity`, stating the counts reflect what's present in
the persisted attribution/walkback datasets, not an exhaustive
chain-wide total.

## Tests

`tests/test_x26_9_1_infrastructure_activity_metrics.py` — 14 tests, all
passing:
- `test_axiom_displays_46_attributed_launches_and_23_observed_creators` —
  exact fixture reproducing 46 mints/23 creators via both
  `wt_attribution_outcomes` and a `wt_walkback_queue` OR-union across
  `funder_wallet`/`subprov` columns.
- `test_axiom_never_displays_stale_creator_count_of_2` — asserts the old
  phrasing is gone and 46/23 are present.
- `test_infrastructure_metrics_not_read_from_discovered_subprovs_creator_count`
  — sets `wt_discovered_subprovs.creator_count=999` and confirms the
  displayed figures are still the correct 46/23, proving the new metrics
  are structurally independent of that column.
- `test_genuine_subprovisioner_still_displays_creator_count_normally`.
- `test_cex_bridge_relay_wallets_use_same_infrastructure_aggregation`
  (parametrized over CEX/BRIDGE/RELAY, not just Axiom).
- `test_duplicate_walkback_rows_do_not_double_count_creators` — same
  creator across 3 walkback rows counts as 1 distinct creator, 3 distinct
  mints.
- `test_duplicate_attribution_rows_cannot_double_count_mints`.
- `test_no_database_mutation` (SHA-256 before/after on both DBs).
- `test_role_resolution_unchanged`, `test_coverage_note_present_and_explicit`,
  `test_no_implementation_source_text_in_behaviour_summary`.

Also fixed one pre-existing X26.8 test
(`test_axiom_creator_funding_count_still_visible_with_neutral_wording`)
whose premise ("the real creator_count is preserved as a number") is
superseded by this sprint's correction — updated to assert
`infrastructure_activity is None` for its minimal fixture (no attribution/
walkback rows) and that no `wt_discovered_subprovs` source text leaks into
the wording.

**Full regression**: 117/117 passing across this new suite plus
`test_x26_8_reject_state_aware_operational_behaviour.py`,
`test_x26_7_evidence_presentation_refresh.py`,
`test_x26_6_1_reject_state_aware_provenance.py`,
`test_discovery_workspace.py`, `test_x26_2_1_attribution_gate_fix.py`,
`test_x26_3_subprov_infrastructure_exclusion.py`,
`test_x26_5_1_attribution_health_window_integrity.py`,
`test_ops_x20_6_discovery_prioritisation.py`, and the pre-existing
`test_ops_x21e_operational_behaviour_rendering.py`.

## Live verification

Restarted `watchtower_api`, fetched the real Axiom launch
(`2GTswvgFNGucLwrUMvttVshy28C5bmjgsuQZ4eVcpump`). Confirmed
`operational_behaviour` now returns:

```
behaviour_summary:
  "Creator funding observed via PLAIN_XFER"
  "Funding source: Axiom · reviewed infrastructure"
  "Launches attributed here: 46"
  "Distinct creators observed: 23"
  "Historical funding relationship recorded (provisioning session exists; funder is not a valid sub-provisioner)"
infrastructure_activity:
  {"attributed_launch_count": 46, "observed_creator_count": 23, "coverage_note": "..."}
```

No occurrence of "2 creator-funding observations" or "funded 2 observed
creators" anywhere in the response. Also confirmed unchanged in the same
response: `attribution_outcome.stop_reason` = "Attribution boundary
reached. Known infrastructure boundary: Axiom."; `canonical_identity:
null`; `operation_identity: null`. Re-fetched the genuine sub-provisioner
comparison case — unchanged. `git status --porcelain -- database/*.db`
empty — no DB mutation.

## Confirmation

- No file under `src/core/` (detection), `src/ops/attribution_outcome.py`,
  walkback, or operation identity was touched.
- No schema migration issued — both new metrics are computed via `SELECT`
  against already-existing tables/columns.
- Only `src/ops/operational_behaviour.py` (implementation) and two test
  files were modified.
