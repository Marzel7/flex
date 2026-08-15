# PSI0B-E8 durable observer-failure provenance

PSI0B-E8 extends the committed E7 launcher without changing its authorization,
query, health-gate, resource-ceiling, cleanup, or authority semantics. A caller
supplies one new empty observer-attempt directory. Before invoking the observer,
the launcher creates an append-only canonical ledger binding the exact
authorization, preflight, E7 launcher, and E8 provenance-contract identities.

The injected observer records each checkpoint attempt before accepting or
rejecting it. Each record binds the checkpoint sequence, Supervisor identities,
descriptor count, serializer snapshot digest, lock-error baseline, queue depth,
write-lease state, RELEASE_PENDING metadata digest, database/WAL state, feed and
ingestion states, and a named gate reason where applicable. Every transition is
flushed and fsynced. The terminal record preserves the exact exception type and
message or the replay-valid health decision.

An observer exception still returns `PSI0B_E7_OBSERVER_BOOTSTRAP_FAILED`, and a
non-passing decision still returns `PSI0B_E7_PRESTART_DO_NOT_START`. Both paths
produce a replayable provenance bundle before returning, consume no execution
authorization, open no source database, invoke no executor, and publish no
shadow output. Only a complete `PRESTART/PASS` terminal record permits E7's
existing atomic authorization-consumption boundary.

The E8 contract digest is
`64f24fa687f23b5b11ffbeed15cebe1e0bf3d1dd4ea71d46b24cfca0f5d342ca`.
It grants no extraction, integration, or activation authority.
