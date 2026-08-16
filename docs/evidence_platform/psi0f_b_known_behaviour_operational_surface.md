# PSI0F-B known-behaviour operational surface

PSI0F-B is a pure fixture-only, default-off descriptive contract. It accepts
synthetic PSI0E aggregate availability and synthetic EB0.4 operational-family
summaries. It never joins PSI0E coverage to an individual operation.

The operation axis is `PLATFORM_OPERATION_ID`; primary role remains the
classification axis, while edge, mechanism and temporal descriptors remain
explicit metadata. `PROPOSED` and `SUPPORTED` nominations are preserved as
non-authoritative behavioural similarity. They never assert operator identity.

The canonical output groups nomination accounting by primary role and carries
the PSI0E evidence availability summary in a separately labelled global
context. `ABSENT_NOT_NEGATIVE`, duplicates, conflicts and unmatched accounting
are preserved without inference or resolution. The contract rejects topology-
only support, unknown roles or nomination states, prohibited identity/ranking/
policy semantics, operation-specific coverage inference and authority drift.

The projection has no file, database, network, service or configuration I/O.
It grants no policy, ranking, attribution, integration, deployment or activation
authority. Applying it to real bundles, publishing a surface, wiring a consumer,
Evidence mode, production activation and EB2 remain separately gated.
