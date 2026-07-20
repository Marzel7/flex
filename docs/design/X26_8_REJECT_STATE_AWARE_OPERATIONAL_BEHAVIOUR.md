# X26.8 — Reject-State-Aware Operational Behaviour

Status: Implemented, tested, live-verified against the real Axiom launch.
No detection, walkback, attribution, operation identity, Launch Profile,
or schema logic changed — this is a presentation-layer correction confined
to `src/ops/operational_behaviour.py`.

**Governing invariant enforced**: a wallet's canonical `wt_discovered_subprovs
.state` (with the reviewed infrastructure registry as an additional,
registry-wins check) now determines the funder's role *before* any
Behaviour wording is built, and every section defers to that role.

---

## Phase 1 — Reproduction and trace

Live data for the primary case, confirmed directly against
`database/wt_ops_v2.db`:

| Table | Row |
|---|---|
| `wt_discovered_subprovs` | `subprov=Axiom..., state=REJECTED_INFRASTRUCTURE, rejected_reason=KNOWN_INFRASTRUCTURE_REGISTRY_MATCH, creator_count=2, funding_mechanism=PLAIN_XFER` |
| `wt_walkback_queue` | `mint=2GTswvgF..., creator=GdRSPexhx..., funder_wallet=Axiom..., funding_mechanism=PLAIN_XFER` |
| `wt_attribution_outcomes` | `outcome_type=KNOWN_RELAY_REACHED, terminal_entity=Axiom..., terminal_entity_type=AUTOMATION, stop_reason="...Known infrastructure boundary: Axiom."` |
| `wt_provisioning_sessions` | a real row exists for this mint (`subprov_to_creator_mechanism=PLAIN_XFER`, no wrap-close/seeded-account-close mechanism at all) |
| `wt_provisioning_edges` | one `SUBPROV_TO_CREATOR` edge, `observation_count=1`, `funding_mechanism=PLAIN_XFER` |

Called `OperationalBehaviourService.build()` directly against this data
before any fix — reproduced exactly the reported defect:

```
"Sub-provisioner funded creator via PLAIN_XFER"
"Sub-provisioner has funded 2 creators (per wt_discovered_subprovs)"
"Walkback completed successfully (provisioning session recorded)"
```
plus, in `infrastructure_pattern`:
```
"Sub-provisioner funded 2 creators"
"First time this exact sub-provisioner→creator funding path was observed"
```

Traced each line to its exact source:
- "Sub-provisioner funded creator via {mechanism}" —
  `_build_behaviour_summary()`, from `wt_provisioning_edges
  .funding_mechanism` on the SUBPROV_TO_CREATOR edge — real data, wrong role
  label.
- "Sub-provisioner has funded N creators" — same function, from
  `wt_discovered_subprovs.creator_count` — real count, wrong role label.
- "Sub-provisioner funded N creators" (Infrastructure Pattern) —
  `_build_infrastructure_pattern()`, same source column.
- "First time this exact sub-provisioner→creator funding path was
  observed" — same function, from `wt_provisioning_edges.observation_count
  == 1` — real observation, wrong role label baked into the sentence.
- "Walkback completed successfully (provisioning session recorded)" —
  same function, from a bare `wt_provisioning_sessions` row-exists check —
  a genuinely operation-agnostic session (confirmed live: the session's own
  `subprov_to_creator_mechanism` is `PLAIN_XFER`, never a verified
  provisioning mechanism) being read as if it proved a valid role.

None of these lines came from stale data — every underlying value was
correct and current. The defect was entirely in the wording layer treating
`wt_discovered_subprovs`/`wt_provisioning_edges`/`wt_provisioning_sessions`
row *existence* as proof of the sub-provisioner *role*, without ever
checking `state`.

## Phase 2 — Canonical-role resolution

Added `_resolve_funder_role()` (`operational_behaviour.py`), called once at
the top of `build()` before any wording function runs:

```python
def _resolve_funder_role(subprov_facts, subprov) -> str:
    if subprov and is_known_account(subprov):      # registry wins even over
        return ROLE_REJECTED_INFRASTRUCTURE          # a stale non-REJECTED state
    if subprov_facts is None:
        return ROLE_UNRESOLVED_FUNDER
    state = str(subprov_facts.get("state") or "").upper()
    if state.startswith("REJECTED_INFRASTRUCTURE") or state == "REJECTED_INFRASTRUCTURE":
        return ROLE_REJECTED_INFRASTRUCTURE
    if state.startswith("REJECTED"):
        return ROLE_OTHER_REJECTED
    return ROLE_VALID_SUBPROVISIONER
```

Four roles, exactly as specified: `VALID_SUBPROVISIONER`,
`REJECTED_INFRASTRUCTURE`, `OTHER_REJECTED`, `UNRESOLVED_FUNDER`.
`wt_discovered_subprovs.state` is authoritative when a row exists; the
reviewed infrastructure registry (`src/utils/infra_mapping.py
.is_known_account()` — the same registry X26.3's exclusion logic already
trusts) is checked independently so a known infrastructure wallet can
never resolve to `VALID_SUBPROVISIONER` even if its own row hasn't (yet)
been marked `REJECTED*`. No new schema field was added — `state` and
`rejected_reason` already existed on `wt_discovered_subprovs` (X26.3); the
only schema-adjacent change was widening `_subprov_facts()`'s own `SELECT`
to also fetch `state`/`rejected_reason`, which it previously omitted.

## Phase 3 — Behaviour Summary corrected

| Condition | Before | After (role ≠ VALID) |
|---|---|---|
| Block-time ordering | "Creator funded after sub-provisioner..." | "Creator funded after the upstream funding wallet..." |
| Funding mechanism | "Sub-provisioner funded creator via {mechanism}" | "Creator funding observed via {mechanism}" |
| Creator count, REJECTED_INFRASTRUCTURE | "Sub-provisioner has funded N creators (per wt_discovered_subprovs)" | "Funding source: {name} · reviewed infrastructure (N creator-funding observation(s) recorded, per wt_discovered_subprovs)" |
| Creator count, OTHER_REJECTED | same | "Funding source is excluded from sub-provisioner classification (N creator-funding observation(s) recorded, per wt_discovered_subprovs)" |
| Session exists | "Walkback completed successfully (provisioning session recorded)" | "Historical funding relationship recorded (provisioning session exists; funder is not a valid sub-provisioner)" |

Words "sub-provisioner", "provisioning wallet", "confirmed lineage", and
"WATCHTOWER" never appear in any rejected-role rendering (verified by
test). The infrastructure display name (e.g. "Axiom") comes from a new
`_infrastructure_label()` helper using the existing
`src/utils/infra_mapping.get_funder_label()` lookup — no new registry, no
invented name.

## Phase 4 — Infrastructure Pattern corrected

| Condition | Before | After (role ≠ VALID) |
|---|---|---|
| Creator count | "Sub-provisioner funded N creators" | REJECTED_INFRASTRUCTURE: "Infrastructure wallet ({name}) funded N observed creators"; OTHER_REJECTED: "N creator-funding observation(s) recorded from this excluded funding source" |
| observation_count == 1 | "First time this exact sub-provisioner→creator funding path was observed" | "First observation of this exact funding relationship" |
| observation_count > 1 | "This sub-provisioner→creator funding path observed N times" | "This exact funding relationship observed N times" |
| Wrap-close mechanism | "Wrap-close creator funding" | unchanged — this line only ever fires for a genuine `WSOL_WRAP_CLOSE` mechanism, which by definition means a real provisioning signature exists; left as-is since it is itself evidence, not a role claim |

Every count remains visible — nothing was hidden, only the role
attribution attached to the count was corrected.

## Phase 5 — Provisioning-session wording

Addressed as part of Phase 3's table above. Confirmed live that
`wt_provisioning_sessions` genuinely is operation-agnostic: the Axiom
session's own `subprov_to_creator_mechanism` field is `PLAIN_XFER`, not a
verified provisioning mechanism, proving the brief's concern was
concretely correct, not hypothetical. Session-existence wording is now
neutral ("Historical funding relationship recorded...") whenever the
funder's role isn't `VALID_SUBPROVISIONER`; the original "Walkback
completed successfully" wording is preserved for genuine sub-provisioners.

## Phase 6 — Operational Consistency

`Repeated treasury` and `Full provisioning sequence recorded` — the two
signals that only make sense relative to a valid provisioning role — now
render `"Not applicable"` instead of `"Not observed"`/`"Not yet available"`
when `funder_role != VALID_SUBPROVISIONER`, per the brief's explicit
distinction ("Do not show Not observed for a concept that was not
applicable"). `Infrastructure reuse`, `Creator funding structure
(wrap-close)`, and `Observed timing` remain meaningful regardless of role
(a rejected infrastructure wallet's funding mechanism, hub-reuse status,
and timing are all still real, checkable facts) and are unchanged.

## Phase 7 — Missing Evidence

Reclassified per the brief's three-way distinction:

| Concept | Old behaviour | New behaviour |
|---|---|---|
| Repeated treasury / repeated provisioning edges / multiple launches / provisioning hub reuse | Always evaluated as MISSING if below threshold, regardless of role | Only evaluated at all when `funder_role == VALID_SUBPROVISIONER`; for a rejected funder, collapsed into a single explicit NOT APPLICABLE line naming why |
| Observed timing history | MISSING when absent | Unchanged — timing absence is a real, role-independent gap (NOT YET AVAILABLE in spirit, kept as its own line since it was already correctly role-agnostic) |

For `REJECTED_INFRASTRUCTURE`: `"Sub-provisioner recurrence: not applicable
— funding source is reviewed infrastructure"`. For `OTHER_REJECTED`:
`"...excluded from sub-provisioner classification"`. For
`UNRESOLVED_FUNDER`: nothing is asserted either way (no funder identity
has been resolved yet, so neither MISSING nor NOT APPLICABLE is
warranted).

## Phase 8 — Registry consistency invariant

Added `assert_no_infrastructure_subprovisioner_conflict(attribution_outcome,
operational_behaviour)` in `operational_behaviour.py` — a standalone,
importable defensive helper (not wired into the hot request path, since
Attribution Outcome and Operational Behaviour are built by independent
service classes and this is a cross-cutting sanity check rather than a
per-request gate). It raises `AssertionError` if the same wallet
(`attribution_outcome.terminal_entity` matching
`operational_behaviour.entities.subprov`) is both a known infrastructure
boundary (`terminal_entity_type` in
`{INFRASTRUCTURE,AUTOMATION,RELAY,CEX,CUSTODY,BRIDGE}`) and produces any
`"Sub-provisioner "`-prefixed wording. Covered by two dedicated tests
(passes for the fixed wording, raises for the old defective wording) plus
verified live against the real Axiom API response.

## Phase 9 — Evidence preservation

No table in this module is ever written — both database connections are
opened `mode=ro` with `PRAGMA query_only=ON` (`_connect()`,
`operational_behaviour.py:42-46`), and every `conn.execute()` call in the
file is a `SELECT` (verified via grep: zero `INSERT`/`UPDATE`/`DELETE`/
`.commit()` anywhere). `wt_discovered_subprovs`, `wt_provisioning_edges`,
`wt_provisioning_sessions`, and `wt_walkback_queue` are read-only inputs
to this change; historical counts, timestamps, and funding signatures are
all still surfaced (just with corrected role wording), never deleted.

## Phase 10 — Tests

`tests/test_x26_8_reject_state_aware_operational_behaviour.py` — 23 tests,
all passing:
- Role resolution: `REJECTED_INFRASTRUCTURE`/`OTHER_REJECTED`/
  `VALID_SUBPROVISIONER`/`UNRESOLVED_FUNDER` states, plus the
  registry-wins-over-stale-state case.
- Axiom live-fixture reproduction (using the real mint/wallet/creator
  addresses): no "Sub-provisioner funded creator", no "Sub-provisioner has
  funded N creators", no "Sub-provisioner funded N creators", no
  "sub-provisioner→creator" path wording; Axiom is named as reviewed
  infrastructure; `PLAIN_XFER` mechanism remains visible; the real
  creator-funding count remains visible with neutral wording; the
  historical funding edge remains visible.
- Genuine `PROVISIONAL_SUBPROV`/`PROVISION_CANDIDATE` cases retain the
  original role-specific wording exactly.
- `REJECTED_NON_PROVISIONING` also receives neutral wording.
- Operational Consistency: provisioning-specific rows are `Not applicable`
  for a rejected funder, unchanged for a valid one.
- Missing Evidence: not-applicable sub-provisioner concepts are never
  reported as MISSING for a rejected funder; unchanged for a valid one.
- The response-level invariant (Phase 8) both passes for correct wording
  and raises for the old defective wording.
- No database mutation (SHA-256 before/after on both ops and core
  fixture DBs).

**Full regression**: 103/103 passing across this new suite plus
`test_x26_7_evidence_presentation_refresh.py`,
`test_x26_6_1_reject_state_aware_provenance.py`,
`test_discovery_workspace.py`, `test_x26_2_1_attribution_gate_fix.py`,
`test_x26_3_subprov_infrastructure_exclusion.py`,
`test_x26_5_1_attribution_health_window_integrity.py`,
`test_ops_x20_6_discovery_prioritisation.py`, and the pre-existing
`test_ops_x21e_operational_behaviour_rendering.py` (7/7 passing —
confirms the wording branch is additive and doesn't disturb existing
valid-sub-provisioner rendering tests).

## Phase 11 — Live verification

Restarted `watchtower_api`, then fetched the real Axiom launch
(`2GTswvgFNGucLwrUMvttVshy28C5bmjgsuQZ4eVcpump`) via
`/api/discovery/entity/<mint>?type=token`. Confirmed `operational_behaviour`
now returns exactly:

```
behaviour_summary:
  "Creator funding observed via PLAIN_XFER"
  "Funding source: Axiom · reviewed infrastructure (2 creator-funding observations recorded, per wt_discovered_subprovs)"
  "Historical funding relationship recorded (provisioning session exists; funder is not a valid sub-provisioner)"
infrastructure_pattern:
  "Infrastructure wallet (Axiom) funded 2 observed creators"
  "First observation of this exact funding relationship"
operational_consistency:
  Repeated treasury: Not applicable
  Full provisioning sequence recorded: Not applicable
missing_evidence:
  "Sub-provisioner recurrence: not applicable — funding source is reviewed infrastructure"
  "Observed timing history"
```

None of the forbidden phrases ("Sub-provisioner funded creator",
"Sub-provisioner has funded 2 creators", "Sub-provisioner funded 2
creators", "sub-provisioner→creator") appear anywhere in the live
response.

Also confirmed in the same response:
- `attribution_outcome.stop_reason` unchanged: "Attribution boundary
  reached. Known infrastructure boundary: Axiom." (`outcome_type
  =KNOWN_RELAY_REACHED`, `terminal_entity_type=AUTOMATION`).
- `canonical_identity: null` — no canonical operator inferred.
- `operation_identity: null` — no treasury/operation inferred.
- Ran `assert_no_infrastructure_subprovisioner_conflict()` directly against
  this live response — passes (no conflict).
- Re-fetched a genuine sub-provisioner
  (`Hk6AxTQZyK7zsPfQLmgGdw8t9nzaD3zDeRjduNHGxbXF`,
  `state=PROVISIONAL_SUBPROV`) — `operational_behaviour` unchanged
  ("Sub-provisioner has funded 16 creators (per wt_discovered_subprovs)"),
  confirming genuine sub-provisioner launches are unaffected.
- `git status --porcelain -- database/*.db` empty — no DB mutation.

## Deliverables

- **Complete trace of every corrected Behaviour line** — Phase 1 table.
- **Role-resolution rule** — Phase 2, `_resolve_funder_role()`.
- **Before/after wording, valid vs. rejected** — Phases 3-4 tables.
- **Test summary** — Phase 10, 23 new tests + 103/103 full regression.
- **Live Axiom verification** — Phase 11.
- **Confirmation raw evidence preserved** — Phase 9 (read-only connections,
  zero writes in the module).
- **Confirmation no classification/detection logic changed** — only
  `src/ops/operational_behaviour.py` was modified; no file under
  `src/core/` (detection), `src/ops/attribution_outcome.py`, walkback, or
  operation identity was touched; no schema migration issued.
