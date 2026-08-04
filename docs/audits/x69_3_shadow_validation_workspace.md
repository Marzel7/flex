# X69.3 Shadow Validation Workspace

## Scope

This developer-only workspace makes the X69 shadow pipeline inspectable. It does
not alter attribution, discovery, the Registry, lifecycle, confirmation, search,
normal navigation, or any existing API response.

```text
Persisted evidence (read-only)
        ↓
InvestigationPopulation revision (ipr:…)
        ↓
EvidenceReconciliationPackage (erp:…)
        ↓
DispositionResult (dr:…)
        ├── deterministic replay comparison
        └── legacy projection difference analysis
```

The HTTP workspace is `/diagnostics/reconciliation`. It is unlinked, returns
`404` unless explicitly enabled (or Flask is in debug/testing mode), and accepts
only loopback requests unless `RECONCILIATION_DIAGNOSTICS_TOKEN` is configured
and supplied in `X-Reconciliation-Diagnostics-Token`. Responses are private,
non-cacheable, and marked `noindex`.

## Live shadow census

| Diagnostic | Result |
|---|---:|
| Investigation populations | 281 |
| Total shadow records (including canonical control) | 282 |
| Agreements | 192 |
| Expected differences | 90 |
| Unexpected differences | 0 |
| Infrastructure | 9 |
| Rejected | 9 |
| Review | 4 |
| Operator Candidate | 0 |
| Confirmed Operation | 1 |
| Retired | 0 |
| Unresolved | 259 |
| Deterministic replay failures | 0 |

## Required controls

| Population | Legacy | Shadow | Classification |
|---|---|---|---|
| WATCHTOWER | CONFIRMED | CONFIRMED_OPERATION | Match |
| B48k / Dv34 Family | CONFIRMED | UNRESOLVED | Expected Difference |
| C7Ha Family | EMERGING | REVIEW | Expected Difference |

B48k remains unresolved because its immutable package lacks independent
control-bearing evidence. Legacy confirmation history remains context, not a
substitute for factual control evidence. C7Ha enters review because its package
contains an observed contradiction. Known infrastructure populations resolve
from persisted contradiction evidence; sampled background populations remain
unresolved.

## Replay and performance

Replay rebuilds the package from the selected immutable population revision,
resolves it again, and compares both package and disposition result identifiers.
All 282 records replayed identically.

On the validation host, legacy composition took 9.70 seconds and the complete
developer workspace (including two package/resolution passes for every record)
took 14.79 seconds. Production overhead remains zero because registration does
not build the workspace and normal requests do not invoke it.

## Regression evidence

- X69.0–X69.3 focused suite: 26 tests passed.
- X69.3 workspace acceptance suite: 8 tests passed.
- Existing production consumers do not import the disposition resolver.
- The only existing application change is inert registration of the gated,
  unlinked diagnostics blueprint.
- No database writes, schema changes, new lifecycle states, or API contract
  changes were introduced.
