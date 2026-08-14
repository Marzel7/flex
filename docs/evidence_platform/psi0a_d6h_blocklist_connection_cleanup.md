# PSI0A-D6H blocklist connection-lifecycle repair

PSI0A-D6H closes the residual structural connection leak identified by the
post-index PSI0A-D6G diagnosis. The listener's `_blocklist_write` helper opened
the primary database with `db_connect()` but closed it only after a successful
commit. Any SELECT, JSON encoding, UPDATE, INSERT or commit exception bypassed
that close while the outer asynchronous method correctly failed open.

The repair retains the existing queries, branch behavior, explicit commit,
serializer/lease integration and outer fail-open handling. It adds only a
`finally` backstop around the existing connection-scoped work, guaranteeing one
close after every success, return or exception path. It introduces no retry,
query, schema, index, concurrency or authority change.

Frozen fake-connection tests prove:

- existing-row UPDATE and new-row INSERT behavior is unchanged;
- malformed stored JSON still falls back to an empty token list;
- SELECT, JSON encoding, UPDATE, INSERT and commit failures close exactly once;
- the missing-creator no-op still opens no connection.

This milestone is safe-local only. The running listener has not loaded the
repair. Deployment, restart and post-deployment observation require separate
authorization.
