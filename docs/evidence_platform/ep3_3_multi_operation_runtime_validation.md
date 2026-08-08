# EP3.3 — Multi-Operation Runtime Validation

EP3.3 adds orchestration around the frozen runtime. Each parallel job owns its
`OperationRuntime`, immutable snapshot, registries, and isolated append-only
runtime store. The scheduler shares no detector, topology, lifecycle, presentation,
or governance state.

WATCHTOWER and 3SW2 execute concurrently in either submission order and produce
identical independent output identities. WATCHTOWER retains its funding-source
topology; 3SW2 retains its controller-to-creator-to-launch topology. Each store
contains only its own contract outputs.

Shared immutable Evidence and Primitive objects are safe inputs, but neither
contract can access the other's snapshot or outputs. Duplicate evaluations of the
same contract version in one schedule fail closed.

## Validation boundary

Synthetic immutable snapshots prove scheduling, isolation, registry coexistence,
version coexistence, and deterministic replay. Historical 3SW2 corpus parity
remains blocked at 0/13 as documented by EP3.2. Therefore EP3.3 validates the
runtime isolation property but does not remove the EP3.2 data-readiness gate.
