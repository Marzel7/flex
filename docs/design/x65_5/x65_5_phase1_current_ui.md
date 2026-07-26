# X65.5 — Phase 1: Review Current Discovery UI

Documents every current Discovery dimension: purpose, evidence source,
and whether it is descriptive (an observation about this one launch)
or definitive (a confirmed identity/attribution claim).

## Current classification flow (progressive drill-down)

```
Behaviour Cohort (canonical_behaviour, X65.0)
  ↓
Creator Identity (creator_identity, src/ops/creator_identity.py)
  ↓
Topology (topology, src/ops/funding_topology.py)
  ↓
Funding Origin (equivalent to topology for most populations, X65.1 Phase 1 finding)
  ↓
Operation Attribution (operation_id, wt_ops_v2_wallets via treasury_resolution.py / direct join)
  ↓
Launch Results
```

Each stage in `templates/discovery.html` filters the previous stage's
already-selected population (`TOPO_SELECTION` object, `x60...Rows()`
functions) — a launch's presence in a later stage is conditioned on
its value at every earlier stage, but stages are computed
independently (each dimension's own classifier runs over the whole
population once, per X65.0's design principle: "read-only derivations,
each dimension computed independently").

## Per-dimension review

| Dimension | Purpose | Evidence source | Descriptive or Definitive |
|---|---|---|---|
| **Behaviour Cohort** (`canonical_behaviour`) | Classifies a launch's observed timing/lifecycle pattern (e.g. `QUICK_BIRTH_MIGRATION`, `RAPID_MIGRATION`) | `src/ops/operational_behaviour_tags.py`, `canonical_behaviour_for()` — pure timing-derived rules, mutually exclusive (X65.0) | **Descriptive** — describes what this launch's timeline looked like, makes no claim about who operated it |
| **Creator Identity** (`creator_identity`) | Classifies whether a launch's creator wallet is fresh, a serial deployer, or has an unresolvable history | `src/ops/creator_identity.py`, `enrich_creator_identity()` — reads `token_analysis` history rows, with the `HISTORY_ROW_CAP` pathological-creator guard | **Descriptive** — a fact about the creator wallet's own on-chain history, independent of any operator/attribution claim |
| **Topology** (`topology`) | Classifies the *shape* of the funding graph reaching this launch's creator | `src/ops/funding_topology.py`, `classify_topology_for_launch()` — reads `wt_provisioning_edges`, `wt_active_subprov_sessions`, `watchtower_events`, walkback tables | **Descriptive**, but (per X65.4) currently **incomplete** — models creator-ancestry only, not full subprov fan-out (see X65.4 for the detailed gap) |
| **Funding Origin** | Surfaces where the creator's funding traces back to | Currently drawn from the same evidence as Topology (X65.1 Phase 1 established these are equivalent for the `QUICK_BIRTH_MIGRATION`/`FRESH_CREATOR`/`UNKNOWN` population); more generally sourced from `wt_attribution_outcomes.terminal_entity` + `treasury_resolution.py`'s walkback | **Descriptive** — a lineage fact, not itself a confirmation of operator identity |
| **Treasury Resolution** (`treasury_resolution` panel, X65.1) | Determines whether a launch's upstream treasury is `KNOWN_TREASURY` (already in `wt_confirmed_treasuries`), `UNKNOWN_TREASURY_CANDIDATE`, `NO_SUBPROV`, or `UNRESOLVED` | `src/ops/treasury_resolution.py`, `resolve_treasury_for_cohort()` — pure read-only walkback over `wt_attribution_outcomes`/`wt_active_subprov_sessions`/`wt_confirmed_treasuries` | **Mixed** — `KNOWN_TREASURY` is **definitive** (backed by an already-human/process-confirmed treasury record); `UNKNOWN_TREASURY_CANDIDATE`/`UNRESOLVED` are **descriptive** (a lineage fact, explicitly not auto-confirmed, per X65.1's constraint) |
| **Operation Attribution** (`operation_id`) | Assigns a launch to a specific, named operation UUID, if its resolved treasury is linked to one in `wt_ops_v2_wallets` | `wt_ops_v2_wallets.operation_uuid`, joined only through an already-confirmed treasury (X65.1) | **Definitive** — by design, this stage never guesses; a launch is either linked to a real, pre-existing confirmed operation, or shows `__UNASSIGNED__` |

## Why this fragments launches from the same real-world campaign

Each dimension above is independently correct and evidence-backed —
X65.5's brief itself confirms this, and X65.0-X65.4 each separately
verified their respective dimension's own internal correctness. The
fragmentation problem is a **composition** issue, not a correctness
issue: two launches from the very same operational WATCHTOWER
provisioning cycle (same treasury, same subprov, same wrap-close fan-out)
can land in *different* Topology buckets (per X65.4's finding — one
creator's edge might exist in `wt_provisioning_edges`, giving `LINEAR`
or `FAN_OUT`, while a sibling creator from the identical subprov fan-out
might have no edge at all, giving `UNKNOWN`) and different Operation
Attribution outcomes (one treasury confirmed and linked to an
operation, another newly-observed and therefore `__UNASSIGNED__`) —
even though both launches are, operationally, part of the exact same
campaign. The current UI has no dimension that asks "does this launch
match the *validated WATCHTOWER provisioning architecture itself*,
regardless of how far each individual evidentiary dimension happened
to resolve" — which is exactly the gap Phase 2 addresses.
