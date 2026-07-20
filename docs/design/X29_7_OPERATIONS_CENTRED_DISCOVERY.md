# X29.7 — Operations-Centred Discovery: Validation Report

Reorganizes Discovery from a launch-classification dashboard into an operations-investigation platform, per X29.5/X29.6's audits and the user's own reframing during design review: this is not a fixed four-role model, it is a **variable-depth Operational Lineage graph** where Role (Treasury/Subprovider/Creator) is an attribute of a node, not the lineage itself. No attribution, Funding Boundary, Funding Mechanism, Behaviour, Wallet Quality, topology-calculation, launch-detection, or treasury-detection logic changed — this sprint is purely a presentation and navigation reorganization over already-persisted facts.

## Files changed

New:
- [src/ops/operational_lineage.py](../../src/ops/operational_lineage.py) — `roles_for_wallet()`, `build_lineage()`: derives a variable-depth chain from `wt_provisioning_edges` + `wt_watchtower_launches` (the same columns `funding_topology.py` already reads), never a fixed 4-node template
- [src/ops/operations_summary.py](../../src/ops/operations_summary.py) — per-operation role counts + funding-mechanism/-boundary distributions, aggregated from already-persisted facts, zero new classification
- [tests/test_x29_7_operational_lineage.py](../../tests/test_x29_7_operational_lineage.py) — 24 tests

Modified:
- [src/core/operation_dashboard_routes.py](../../src/core/operation_dashboard_routes.py) — three new routes: `/api/ops-v2/known-operations` (list + summaries), `/api/ops-v2/lineage/<wallet>` (variable-depth chain), `/api/ops-v2/roles/<role_type>` (browse by role)
- [templates/discovery.html](../../templates/discovery.html) — landing page now shows **Operations** and **Browse By Role** above the (now demoted) Operational Intelligence panel and Recent Launches; per-entity pages render an **Operational Lineage** chain directly beneath Identity, above every existing intelligence card

## Route naming note

The brief's suggested path `/api/ops-v2/operations` was already claimed by a pre-existing, unrelated route (`api_operations()`, a different `wt_ops_v2`/`operation_uuid`-based concept, present before this sprint). Caught live during verification — the old route was silently winning the collision. Renamed this sprint's new list endpoint to `/api/ops-v2/known-operations` to avoid touching or breaking the existing one.

## Design principle applied: lineage, not fixed roles

Per direct user feedback during design review, this is **not** "four roles, always four cards." `build_lineage()` walks outward from a queried wallet in both directions using only persisted edges, producing however many hops actually exist:
- 3-hop: Treasury → Subprovider → Creator (the common case)
- 2-hop: Treasury → Creator directly (no subprov hop recorded)
- Variable depth generalizes to any future chain shape without a schema or vocabulary change

A structural finding worth flagging explicitly: the brief's illustrative example includes an intermediate "Provisioning Wallet" hop (`HZB2...`) between Subprovider and Creator. Tracing the actual persisted data for the confirmed example showed **this intermediate is not separately recorded anywhere in the schema** — `wt_provisioning_edges`'s `SUBPROV_TO_CREATOR` edge type already collapses that hop (a WSOL wrap-close account is single-use and ephemeral, never persisted as its own wallet identity). `build_lineage()` reflects this honestly: the real chain is 3 nodes (Treasury/Subprovider/Creator), not 4. This module never fabricates a node with no evidence.

## Validation against the confirmed WATCHTOWER example

Live-verified (2026-07-19) via `GET /api/ops-v2/lineage/HTR9U7dkk1eEwmyFyzCzERdy3vr8CM6T8hW5FY1s24gt`:

```
Treasury    9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4   subprovider_count=13
    ↓
Subprovider ANenEukvmpYsyP52LgDsZN6kj3n7igjbJDTCtj4xCAXq   fan_out_count=1, historical_launches=1, mechanisms=[WSOL_WRAP_CLOSE]
    ↓
Creator     HTR9U7dkk1eEwmyFyzCzERdy3vr8CM6T8hW5FY1s24gt
```

Note on the brief's illustrative counts (27 provisioning wallets, 143 historical creators): those numbers came from the user's own separate on-chain investigation, not from what `wt_provisioning_edges`/`wt_watchtower_launches` currently persist for this specific subprovider — this launch is only 4.66 days old (per X29.6's trace) and the subprovider has accumulated 1 recorded launch so far in this corpus snapshot. The module reports exactly what's persisted; it does not, and must not, fabricate the illustrative numbers.

`op_c712641dd45cdf20` is the operation this launch resolves to (via the unchanged `operation_identity.py` treasury-mesh resolver): 1 treasury, 13 subproviders, 13 creators, 13 launches, 100% `WSOL_WRAP_CLOSE`.

Confirmed unchanged (per the brief's explicit requirement):
- **Operational Attribution**: still `CANONICAL_OPERATOR_REACHED` / `KNOWN_OPERATION` (X29.6's trace, re-verified, untouched)
- **Funding Boundary**: `funding_boundary.py` untouched (structural test)
- **Behaviour**: `operational_behaviour_tags.py` untouched (structural test)
- **Funding Mechanism**: `funding_mechanism.py` untouched (structural test)
- **Wallet Quality**: `wallet_quality.py` untouched (structural test)

## Navigation changes

Landing page order, before → after:
```
Before: New Intelligence Today → Operational Intelligence (Topology→Behaviour→Mechanism) → Registry/Feed → Legacy Investigation Queue
After:  Operations (Known Operations) → Browse By Role (Treasuries/Subproviders/Creators) → New Intelligence Today → Operational Intelligence (now "Supporting Intelligence") → Registry/Feed → Legacy Investigation Queue
```

Per-entity page order, before → after:
```
Before: Identity → Attribution → Funding Boundary → Wallet Quality → Leads → Walkback → ...
After:  Identity → Operational Lineage (new) → Attribution → Funding Boundary → Wallet Quality → Leads → Walkback → ...
```

New URL patterns:
- `/discovery?entity=<operation_id>&type=operation` — operation detail page
- `/discovery?browse_role=TREASURY|SUBPROVIDER|CREATOR` — role browse page

## API changes

New:
- `GET /api/ops-v2/known-operations` → `{ok, operations: [...], total_operations}`
- `GET /api/ops-v2/lineage/<wallet>` → `{ok, wallet, primary_role, chain: [{wallet, role, properties}, ...]}`
- `GET /api/ops-v2/roles/<TREASURY|SUBPROVIDER|CREATOR>?limit=N` → `{ok, role, total, wallets: [...]}`

Unchanged: `/api/ops-v2/operations/<operation_id>` (pre-existing X25.4 route, reused as-is), `/api/discovery/search`, `/api/discovery/entity/<id>`, all X29.1–X29.6.1 endpoints.

## Regression summary

`test_x29_7_operational_lineage.py`: 24/24 passed (role derivation from both edge sources, multi-role wallets, variable-depth 2-hop and 3-hop lineage, property attachment to the correct role only, cycle safety, operations summary aggregation, the exact confirmed WATCHTOWER example, zero RPC, and structural guards confirming `funding_topology.py`/`attribution_outcome.py`/`funding_boundary.py`/`wallet_quality.py` were not modified).

Combined with the rest of the X29 family + touched routes files (`test_ops_x21b_routes.py`, `test_ops_x21c_routes.py`, `test_discovery_workspace.py`): **178/178 passed**.

## RPC impact

**Zero.** `operational_lineage.py` and `operations_summary.py` read only already-persisted tables (`wt_provisioning_edges`, `wt_watchtower_launches`, `wt_confirmed_treasuries`, `wt_funding_boundary`); confirmed via structural test asserting no RPC-related strings appear in either module.
