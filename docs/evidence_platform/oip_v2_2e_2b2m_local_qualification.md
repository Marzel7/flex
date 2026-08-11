# OIP v2.2E.2B2M local qualification

## Scope

B2M adds a queue-local `observation_required` marker for reviewed migration
jobs. The marker defaults to false, so ordinary creator-funding jobs preserve
the existing cache and reconciliation behavior.

For a marked migration job, the listener persists the marker and does not
discard the job at the creator cache boundary. Both queue consumers preserve
the marker, exempt the row from satisfied-creator reconciliation, and pass it
to `extract_funding_for_new_token`. The extractor then bypasses only the known
creator fast path. It enters the existing acquisition scope with the job's
current mint, retaining the established acquisition and correlation lineage.

## Safety boundary

This change does not enable a production mode, alter service configuration, or
perform provider work during qualification. The marker is set only on the
existing reviewed at-migration enqueue paths. Rows created by all other paths
remain unmarked.

## Local qualification

Fixture-only tests cover the default cache behavior, marker persistence,
satisfied-row reconciliation exemption, worker propagation, fast-path bypass,
and current-mint acquisition-scope lineage. The surrounding creator-funding
queue, concurrency, freshness, fast-path, keyed-deduplication, lifecycle, and
end-to-end worker tests are also part of the bounded regression set.
