# PSI0B-E7 production-shadow launcher/bootstrap boundary

PSI0B-E7 adds a committed, path-independent bootstrap script and a fail-closed
launcher contract over the existing PSI0B-D runner. The script derives the
repository root from its own committed location before importing `src`, so it
can be invoked from any working directory.

The launcher validates the exact authorization JSON, PSI0B-D contract identity,
committed superseding cohort/preflight replay, run/output lineage, absent output,
and a caller-supplied consumption directory before invoking any observer. It
then requires a replay-valid `PRESTART/PASS` PSI0A-F decision and atomically
creates one authorization-consumption marker before delegating to the existing
executor. Import, validation, preflight, output, or observer failures consume no
authorization and cannot invoke the executor.

The `--bootstrap-check` command performs validation only. It never observes
production, consumes authorization, opens a database, or executes a query.
Production execution still requires a new immutable authorization ID and an
explicitly authorized observer/executor composition.

The boundary does not alter queries, paths, boundaries, ceilings, health gates,
abort semantics, or authority. Integration and activation remain false.
