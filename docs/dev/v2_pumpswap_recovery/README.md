# PumpSwap connection recovery — DEV qualification

Runtime payload: only `runtime/listener/src/core/pumpfun_curve_listener.py`.
Required installed SHA256 preimage:
`747b2d497e707977de6f9e50ba9c81f9d864ee07c837abf57117be5d7bb44f9a`.

The PumpSwap loop no longer terminates the process after ten transport
failures. Backoff remains 5 seconds, multiplied by 1.5 up to 60 seconds.
Confirmed integer subscription identities reset the failure streak; socket
connection alone, null/bool replies and subscription errors cannot do so.
Connection closures and unsuccessful subscription completion re-enter bounded
backoff. PumpPortal has a separate task and its existing fatal policy is unchanged.

Sanitized diagnostics include a closed exception-type vocabulary, safe errno
and HTTP status, identifiable setup/subscription/receive phase, endpoint slot,
failure count, next delay and recoverability. No URL, raw exception message,
provider credential or payload is serialized by the new diagnostic. DNS snapshots
receive a sanitized exception preserving errno. CONNECT is deliberately retained
when the exception cannot prove a more specific network phase.

Known certificate/URI/subscription-configuration failures mark the channel DOWN
and stop that channel, not the birth task. Existing 401/403 primary-to-public
fallback is preserved. Unknown failures propagate. In particular ENOSPC, schema,
paired-authority and database failures are not transport retry candidates.
No journal/receipt/schema/provider-budget changes or retry resets are made.

Retained production logs prove the explicit PumpSwap ten-failure process exit,
but masked the underlying exception. The original errno/endpoint transport root
cause remains unproven; this correction must not be described as repairing the
provider connection itself or proving lossless historical outage coverage.

Tests extract and execute the actual installed-loop method with synthetic async
connections. No full runtime startup, provider, port or websocket is opened.
Birth continuity is concurrent-task simulation, not deployed acceptance.
Unchanged real journal tests retain durable admission, completion, conflict and
restart authority. No live exhausted population is replayed.

Qualification: 22 new controls and 530 existing migration-completion controls,
all passed. Unrelated listener source outside the new helpers and PumpSwap loop
is byte-identical to the accepted installed source. This is ready only for
read-only deployment preflight. No deployment, instrumentation activation,
service restart or production mutation was performed.

Fallback is the exact installed preimage, retaining terminal-aware journals,
CREATE delivery and birth retries; it still has the old process-exit behaviour.
No database rollback is needed. The existing recovery renderer's source pins
must be reviewed at deployment preflight; do not install any unrelated candidate
instrumentation files or flip optional audit flags.
