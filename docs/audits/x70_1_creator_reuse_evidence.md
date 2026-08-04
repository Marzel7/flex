# X70.1 Persist Creator Reuse Evidence

## Result

Creator reuse is now projected into immutable Evidence Reconciliation Packages
from existing `wt_provisioning_edges` rows. No RPC, explorer, replay, worker,
raw evidence store, schema migration, attribution assignment, score, threshold,
or resolver-rule change was introduced.

```text
wt_provisioning_edges (read-only)
        ↓
creator + distinct source_mint observations
        ↓
one immutable CreatorReuseEvidence per creator
        ↓
one CREATOR_REUSE_CONTROL evidence item
        ↓
creator_reuse dependency group
```

## Evidence contract

Each observation records:

- creator wallet;
- every distinct observed mint;
- supporting edge IDs per mint;
- funding transaction signatures per mint;
- first and last observation timestamps per mint and in aggregate;
- affected immutable population revision;
- source table and dependency group;
- a content-addressed `cre:…` evidence identifier.

Multiple edges for one creator/mint remain provenance within one observation.
Multiple mints for one creator remain one behavioural evidence source. No mint
or repeated edge creates an independent evidence item.

When fewer than two distinct mints exist, the package retains the explicit
`CREATOR_REUSE_UNAVAILABLE` missing-evidence fact.

## Live validation

| Metric | Before | After |
|---|---:|---:|
| Unresolved Investigation Populations | 259 | 243 |
| Operator Candidates | 0 | 16 |
| Infrastructure | 9 | 9 |
| Rejected | 9 | 9 |
| Review | 4 | 4 |

Twenty-five packages contain reuse observations in total, comprising 37 unique
creator observations. Nine occur in populations with a higher-priority factual
terminal/review disposition; those outcomes remain unchanged.

The exact 16 new Operator Candidates are:

`3uBN`, `68xd`, `6Sv3`, `6tck`, `8Ubp`, `9cDD`, `B94V`, `BDWy`,
`CLK3`, `DhPY`, `DssT`, `Em9h`, `F3Cc`, `FUCK`, `FxxX`, and `Hri2`.

## Named controls

| Control | Result | Reuse observations |
|---|---|---:|
| WATCHTOWER | CONFIRMED_OPERATION | 0 |
| B48k / Dv34 | UNRESOLVED | 0 |
| C7Ha | REVIEW | 0 |
| Known infrastructure | 9 INFRASTRUCTURE | Terminal results unchanged |
| Dust/unsupported controls | 9 REJECTED | Terminal results unchanged |

## Diagnostics and compatibility

The diagnostics census now reports:

- 282 shadow records;
- 205 Legacy/Shadow agreements;
- 77 expected differences;
- zero unexpected differences;
- zero deterministic replay failures;
- 16 Operator Candidates;
- 243 Unresolved populations.

The three Dormant populations strengthened by creator reuse remain explained
Expected Differences. Thirteen former Candidate→Unresolved differences now
agree as Candidate→Operator Candidate.

Existing attribution fields and API schemas remain unchanged. Optional X69.4
reconciliation metadata reflects the new immutable packages, but no production
consumer reads or branches on it.

## Regression evidence

- 68 focused X67/X69/X70, Registry, Discovery, confirmation, Operational
  Intelligence, search, API-compatibility, and UI contract tests passed.
- The projection uses a read-only SQLite connection and `SELECT` only.
- The source contains no RPC or HTTP client.
- No production attribution, Registry, confirmation, lifecycle, UI, or search
  rule was changed.
