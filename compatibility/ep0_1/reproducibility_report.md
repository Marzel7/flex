# EP0.1 engineering reproducibility report

## Safety boundary

- Production databases are opened read-only.
- No schema setup is imported or executed.
- No RPC or HTTP requests are issued.
- No worker is started.
- No feature flag is enabled.
- No volatile generator timestamp enters a fixture.
- Ordering is primary-key order, falling back to declared column order.
- JSON is UTF-8, compact and key-sorted.
- Every generated payload is SHA-256 addressed by the manifest.

## Determinism procedure

1. Use the same immutable main and operations database snapshots.
2. Supply the same explicit snapshot timestamp, or preserve snapshot mtimes.
3. Run the generator into two empty output directories.
4. Compare all files byte for byte.
5. Compare `compatibility_manifest.json` SHA-256 values.

The release is reproducible only when the directory comparison is empty.

## Production behaviour

EP0.1 creates compatibility material only. It does not change attribution,
reconciliation, discovery, walkback, funding, governance, API or UI behaviour.

## Performance baseline boundary

The manifest captures stable queue counts, table counts and database sizes.
Serializer latency and throughput are retained as reviewed operational
observations because they are process-local and time-varying. At the initial
audit the listener serializer reported 146.5 writes/minute, 0 ms p50 wait,
0.8 ms p99 wait, 0.73 ms average commit and 3.23 ms p99 commit. These values
must not become golden semantic fixtures.

CPU, memory, restart rate, RPC volume and WAL growth require a bounded sampling
window. They must be attached to the release review with its start/end times;
they are not fabricated when no sampler is available.
