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

Immutable snapshots prove scheduling, isolation, registry coexistence, version
coexistence, and deterministic replay. EP3.2A subsequently completed the frozen
3SW2 corpus at 13/13. EP2.1 corrected freshness ordering: 9/13 creators now verify
fresh, while four bounded histories omit the reference event and remain
unverifiable. This classified Evidence gap does not invalidate the isolation
proof.
