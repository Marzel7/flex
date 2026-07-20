# X26.10 — Unify Terminal Infrastructure Behaviour

Status: Implemented, tested, live-verified against the real Binance/CEX
and Axiom launches. No detection, walkback, attribution, operation
identity, or schema logic changed — a presentation-layer correction
confined to `src/ops/operational_behaviour.py` plus one small,
read-only addition in `src/discovery/service.py`.

---

## Phase 1 — Behavioural role resolution audit (decision tree)

Traced `_resolve_funder_role()` (`operational_behaviour.py`) and its
inputs, confirmed unchanged by this sprint:

```
_resolve_funder_role(subprov_facts, subprov):
  if subprov and is_known_account(subprov):     # registry check WINS
      return REJECTED_INFRASTRUCTURE
  if subprov_facts is None:
      return UNRESOLVED_FUNDER
  state = subprov_facts.state
  if state startswith REJECTED_INFRASTRUCTURE:
      return REJECTED_INFRASTRUCTURE
  if state startswith REJECTED:
      return OTHER_REJECTED
  return VALID_SUBPROVISIONER
```

This decision tree was already correct and role-agnostic across CEX,
automation, bridge, relay, and custody — `is_known_account()` checks
`INFRASTRUCTURE_ACCOUNTS`, `CEX_ACCOUNTS`, and `CUSTOM_ACCOUNTS` uniformly.
**The role-resolution logic was never the bug.** The actual defect was
entirely upstream: whether `subprov` (the address the role resolver needs)
ever reached this function at all, and — a second, compounding bug found
during this sprint's own cross-type consistency check (Phase 6) — whether
the wording-builder functions correctly used that address once it arrived.

## Phase 2 — Inventory of reviewed terminal infrastructure

| Category | Registry source | `outcome_type` | `terminal_entity_type` |
|---|---|---|---|
| CEX | `CEX_ACCOUNTS` | `KNOWN_CEX_REACHED` | `CEX` |
| Automation | `INFRASTRUCTURE_ACCOUNTS`, `category=automation` | `KNOWN_RELAY_REACHED` | `AUTOMATION` |
| Bridge | `INFRASTRUCTURE_ACCOUNTS`, `category=bridge` | `KNOWN_BRIDGE_REACHED` | `BRIDGE` |
| Relay | `INFRASTRUCTURE_ACCOUNTS`, `category=relay` | `KNOWN_RELAY_REACHED` | `RELAY` |
| Custody | `INFRASTRUCTURE_ACCOUNTS`, `category=custody` | `KNOWN_RELAY_REACHED` | `CUSTODY` |
| Platform / Protocol / System | `INFRASTRUCTURE_ACCOUNTS`, other categories | `KNOWN_RELAY_REACHED` | `PLATFORM`/`PROTOCOL`/`SYSTEM` |

Confirmed via `_boundary()` (`src/ops/attribution_outcome.py:265-318`):
only `KNOWN_CEX_REACHED`, `KNOWN_BRIDGE_REACHED`, and `KNOWN_RELAY_REACHED`
exist as `outcome_type` values — every other registry category (automation,
custody, platform, protocol, system) collapses into `KNOWN_RELAY_REACHED`
at the outcome-type level, with the real subtype distinction carried in
`terminal_entity_type` instead. This is why X26.10's presentation model
keys off `terminal_entity_type`, not `outcome_type` — the latter is too
coarse to distinguish "automation" from "custody," but the former isn't.
All are confirmed legitimate terminal boundaries — Discovery Result and
Identity wording already handled all of these correctly (this sprint
found no defect there); the gap was isolated entirely to Operational
Behaviour.

## Phase 3 — `TERMINAL_INFRASTRUCTURE` presentation model

Introduced as a purely presentation-side concept — **not** a database
state, **not** an attribution concept, exists only inside
`OperationalBehaviourService`:

- Reuses the existing `ROLE_REJECTED_INFRASTRUCTURE` role (no new role
  constant was needed — the brief's `TERMINAL_INFRASTRUCTURE` concept maps
  1:1 onto the role X26.8 already introduced).
- New `_SUBTYPE_PHRASES` map + `_terminal_infrastructure_label()` classmethod
  (`operational_behaviour.py`) resolves `"{Name} · reviewed {phrase}"`
  directly from `CEX_ACCOUNTS`/`INFRASTRUCTURE_ACCOUNTS`'s own `category`
  field — the same registry X26.3's exclusion logic and X26.8's role
  resolution already trust, no new/duplicate classification.
- Subtype phrases: `CEX → "exchange"`, `AUTOMATION → "automation
  infrastructure"`, `BRIDGE → "bridge"`, `RELAY → "relay"`, `CUSTODY →
  "custody infrastructure"`, plus `PLATFORM`/`PROTOCOL`/`SYSTEM` for
  completeness, with an implicit fallback (`"infrastructure"`) for any
  future category not yet in the map (Phase 7).

## Phase 4/5 — Unified rendering, found and fixed two real bugs

**Bug 1 (the originally reported gap): `_entity()` never resolves a
CEX/bridge/relay wallet as `subprov` when it's only recorded in
`wt_walkback_queue.funder_wallet` or `.treasury`.** `subprov` in
`src/discovery/service.py` is derived only from
`wt_watchtower_launches.subprov_wallet` / `wt_token_lifecycle.subprov` /
`watchtower_token_attribution.matched_subprov` /
`wt_walkback_queue.subprov` — never `.funder_wallet`. Reproduced live:
mint `7hZSYroo8CkdZ1xJDKCaxvxLYtD9JeEWUjUxmi8Qpump`'s
`wt_walkback_queue` row has `subprov=NULL`, `funder_wallet=<Binance
address>` — so `OperationalBehaviourService.build()` was called with
`subprov=None`, and the entire Operational Behaviour section rendered
empty despite `attribution_outcome.terminal_entity` correctly identifying
the CEX boundary.

  **Fix**: added `terminal_infrastructure: Optional[str] = None` parameter
  to `build()`. `src/discovery/service.py` now computes it from
  `attribution_outcome.terminal_entity` whenever
  `attribution_outcome.terminal_entity_type` is one of the reviewed
  terminal classes (`CEX`, `AUTOMATION`, `RELAY`, `BRIDGE`, `CUSTODY`,
  `PLATFORM`, `PROTOCOL`, `SYSTEM`, `INFRASTRUCTURE`) — explicitly
  excluding `CANONICAL_OPERATOR`/`CREATOR`/`UNKNOWN` so this never fires
  for a genuine operator or creator termination. Inside `build()`, this
  only ever **fills the gap** (`if not subprov and
  terminal_infrastructure: subprov = terminal_infrastructure`) — a real,
  already-resolved sub-provisioner address is never overridden.

**Bug 2 (found during Phase 6's cross-type consistency check, not in the
original report): even when `subprov` WAS correctly passed in,
`_build_behaviour_summary()` and `_build_infrastructure_pattern()` each
discarded it and re-derived their own local `subprov` from
`subprov_facts.get("subprov")`** — which is `None` whenever no
`wt_discovered_subprovs` row exists for that wallet (true for many
registry-only CEX/bridge/custody wallets that were never themselves a
sub-provisioner candidate). This silently broke the subtype-label lookup
even after Bug 1's fix, for any wallet with zero subprov history.
Reproduced live: a deBridge bridge-vault address and a Fireblocks custody
address both fell back to the generic "Funding source is reviewed
infrastructure" instead of naming the wallet and its subtype.

  **Fix**: both functions now accept `subprov` as an explicit parameter
  (`subprov = subprov or (subprov_facts or {}).get("subprov")`), preferring
  the real resolved address over the (possibly-absent) `wt_discovered_subprovs`
  row.

Together, these two fixes give every reviewed terminal class the same
three-line evidence model:
```
Funding source: {Name} · reviewed {subtype}
Launches attributed here: N
Distinct creators observed: N
```
using the exact same canonical sources X26.9.1 introduced
(`wt_attribution_outcomes.terminal_entity` / `wt_walkback_queue`
funder_wallet-or-subprov union).

Per Phase 5's explicit requirement, `Repeated treasury` and `Full
provisioning sequence recorded` render `"Not applicable"` for every
reviewed class (already correct from X26.8, unchanged by this sprint),
and no reviewed-class wording ever mentions "sub-provisioner," "creator
recurrence," or "treasury confirmation."

## Phase 6 — Cross-type consistency, verified live

| Wallet | Category | Behaviour Summary (live/reproduced) |
|---|---|---|
| Axiom | automation | `Funding source: Axiom · reviewed automation infrastructure` |
| Binance 2 | CEX | `Funding source: Binance 2 · reviewed exchange` |
| deBridge Bridge Vault | bridge | `Funding source: deBridge Bridge Vault · reviewed bridge` |
| Relay.link Solver | relay | (already had subprov history; renders via the standard path) |
| Fireblocks Custody | custody | `Funding source: Fireblocks Custody · reviewed custody infrastructure` |

All five produce structurally identical output — same three-line
`behaviour_summary` shape, same `infrastructure_pattern` shape, same
`operational_consistency`/`missing_evidence` treatment — differing only in
the subtype phrase, exactly per the success criterion.

## Phase 7 — Future-proofing assessment

Confirmed: adding a new reviewed infrastructure type requires **only** a
registry entry (`INFRASTRUCTURE_ACCOUNTS[addr] = {"name": ..., "category":
"new_category", ...}`) — no new rendering branch. If `"new_category"`
isn't yet in `_SUBTYPE_PHRASES`, `_terminal_infrastructure_label()` falls
back to the generic `"reviewed infrastructure"` phrase (via
`_SUBTYPE_PHRASES.get(category, "infrastructure")`) rather than erroring
or omitting the wallet — the wallet's activity metrics and role-neutral
treatment still render correctly immediately; only the subtype phrase
would read generically until `_SUBTYPE_PHRASES` is updated with the new
category's phrase, a one-line addition.

## Phase 8 — Tests

`tests/test_x26_10_unified_terminal_infrastructure.py` — 16 tests, all
passing:
- `test_terminal_infrastructure_param_fills_gap_when_subprov_unresolved` —
  the exact Bug 1 scenario, `subprov=None` + `terminal_infrastructure=CEX_WALLET`.
- `test_terminal_infrastructure_never_overrides_real_subprov` — a genuine
  sub-provisioner is never overridden by a different `terminal_infrastructure`
  value.
- `test_cross_type_structural_consistency` — parametrized over
  Axiom/CEX/bridge/relay/custody, asserting identical structure (three
  required summary-line prefixes, subtype phrase present, no
  sub-provisioner leakage, `Not applicable` consistency rows, no "missing"
  language) for every class.
- `test_infrastructure_metrics_identical_shape_across_all_reviewed_types` —
  same `infrastructure_activity` key set across all five classes.
- `test_creator_count_never_leaks_back_in` — parametrized, sets
  `wt_discovered_subprovs.creator_count=9999` and confirms it never
  appears anywhere in the output for Axiom/CEX/bridge.
- `test_genuine_subprovisioner_unaffected`.
- `test_unresolved_funder_not_treated_as_infrastructure`,
  `test_role_resolution_still_correct_for_registry_wallets`.
- `test_discovery_attribution_outcome_field_unmodified` — full end-to-end
  test through `DiscoveryService.resolve()` (not just
  `OperationalBehaviourService` directly), confirming the real Discovery
  entry point produces the fix, `attribution_outcome`/`canonical_identity`/
  `operation_identity` are unaffected, and no DB mutation occurs.
- `test_no_database_mutation` (SHA-256 before/after).

**Full regression**: 116/116 passing across this new suite plus
`test_x26_9_1_infrastructure_activity_metrics.py`,
`test_x26_8_reject_state_aware_operational_behaviour.py`,
`test_x26_7_evidence_presentation_refresh.py`,
`test_x26_6_1_reject_state_aware_provenance.py`,
`test_discovery_workspace.py`, `test_x26_2_1_attribution_gate_fix.py`,
`test_x26_5_1_attribution_health_window_integrity.py`,
`test_ops_x20_6_discovery_prioritisation.py`, and the pre-existing
`test_ops_x21e_operational_behaviour_rendering.py`. One pre-existing
X26.9.1 test assertion was updated (`"reviewed infrastructure"` →
`"reviewed automation infrastructure"` for Axiom) to reflect the intended
subtype-specific wording improvement.

## Live verification

Restarted `watchtower_api`, fetched two real mints via
`/api/discovery/entity/<mint>?type=token`:

- **`7hZSYroo8CkdZ1xJDKCaxvxLYtD9JeEWUjUxmi8Qpump`** (Binance/CEX boundary,
  previously completely empty Operational Behaviour) now returns:
  ```
  Funding source: Binance 2 · reviewed exchange
  Launches attributed here: 154
  Distinct creators observed: 113
  ```
  `attribution_outcome.outcome_type=KNOWN_CEX_REACHED`,
  `terminal_entity_type=CEX`; `canonical_identity: null`;
  `operation_identity: null` — all unaffected.
- **`2GTswvgFNGucLwrUMvttVshy28C5bmjgsuQZ4eVcpump`** (Axiom, X26.9.1's
  original case) now shows the upgraded subtype phrase: `Funding source:
  Axiom · reviewed automation infrastructure` (previously the generic
  `"reviewed infrastructure"`), with all other figures unchanged (46
  launches, 23 creators).
- `git status --porcelain -- database/*.db` empty — no DB mutation.

## Confirmation that only Operational Behaviour changed

- `src/ops/operational_behaviour.py` — the only file with substantive
  logic changes (new `_SUBTYPE_PHRASES`/`_terminal_infrastructure_label()`,
  `terminal_infrastructure` parameter, `subprov` parameter threading fix).
- `src/discovery/service.py` — one small, read-only addition (computing
  `terminal_infrastructure` from an already-fetched `attribution_outcome`
  dict and passing it through); no change to how `attribution_outcome`
  itself is fetched, computed, or rendered elsewhere on the page.
- No file under `src/core/` (detection), `src/ops/attribution_outcome.py`
  (attribution derivation itself, as opposed to reading its already-stored
  output), walkback, or operation identity was touched.
- No schema migration — no new column, no new table.
