# X78.38A1 — Real Helius Pagination Semantics

## Verdict: blocked

A bounded nine-request Shared Transaction Acquisition experiment was run across
three deep production creators. Raw provider responses are frozen as compressed
audit artifacts.

Two overlap continuations behaved as proposed. The third did not: after using
the penultimate signature as the exclusive `before` anchor, Helius returned a
different signature in the same slot before the expected former page boundary.
This means a signature being present later in the response cannot by itself
prove page ordering or continuity, while requiring it to be first rejects a
real provider result.

The tested protocol is consequently not a universal contiguous-history proof.
No provider-safe alternative has been proven, so no full-vs-overlap funding
equivalence corpus was attempted and X78.38B remains skipped.
