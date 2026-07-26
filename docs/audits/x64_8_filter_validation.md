# X64.8 Filter Validation

The client derives each stage from the prior working set:

- `x60BehaviourRows()` filters the launch universe.
- `x60CreatorIdentityRows()` filters those rows by one identity.
- `x60TopologyRows()` consumes identity-filtered rows.
- `x60FundingRows()` consumes topology-filtered rows.
- `x60OperationRows()` consumes funding-filtered rows.

Changing Behaviour clears Identity and all downstream selections. Changing Identity clears Topology, Funding and Operation. Breadcrumb navigation applies the same reset boundary. Empty or stale URL branches are removed by `x60SanitizeSelection()`.

Regression fixtures validate Behaviour -> Identity and Identity -> Topology reduction, zero branches, result counts, and preserved funding/operation filters.
