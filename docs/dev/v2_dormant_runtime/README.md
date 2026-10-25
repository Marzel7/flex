# V2 dormant runtime integration

This source-only bundle preserves the current Flask application, templates and static assets. It reuses the accepted V2 paired-binding guard, retirement-aware shared writer, durable Birdeye budget and completed-window implementations. No service or schedule is installed by this adapter.

Entrypoint: `scripts/flex_v2_dormant.py --manifest ABSOLUTE_PATH --sha256 SHA256 --component api|monitor|bridge`.

- `--qualify`: import/startup validation only; network, bind and worker-thread creation are prohibited. SQLite opens are limited to the declared disposable pair/approved auxiliary path. No credentials are loaded.
- `--plan`: validate the real frozen source/configuration and retained budget without importing the application or activating a service. API/monitor credentials are read only from the declared existing environment file; values never appear in the manifest or result.
- Neither flag: future separately authorized launch. API uses the existing Gunicorn configuration and Flask app; logs go to stdout/stderr for existing Supervisor rotation. Monitor/bridge call the retained service entrypoints. Do not execute this mode during DEV qualification.

The manifest is SHA-bound and strict. Keys: `version`, `source_commit`, `generation`, `environment`, `queue_path`, `budget_path`, `budget_contract_sha256`, `selection_path`, `selection_sha256`, `credential_file`. Production-mode validation requires the exact clean Git commit. Source packages are resolved from that checkout, with no old-worktree fallback. Core/OPS identities and ACTIVE generation are checked before component import; every shared connection/mutation remains paired and retirement-fenced. Missing stores, missing selection, changed inode/generation, unsupported environment or interrupted budget publication fail closed.

`budget_contract_sha256` binds the qualified owner source, not the mutable consumed-budget contents. Restart validates but never recreates, resets or debits the retained ledger. BIRDEYE is exclusive; limits remain 20 global and 4 per token per 60 seconds. No acquisition endpoints, request windows or price-reduction calculations are changed. Completed-window collision verification remains insert-only and preserves conflicting evidence.

The bridge may use the same OPS V2 database for membership outbox and monitor facts only under validated paired authority. Unpaired legacy distinct-store semantics remain unchanged. Monitor checkpoint paths use the existing V2 guard and file budget; the accepted disposable-only profile remains available outside the explicit V2 profile.

Qualification-only mode suppresses startup maintenance/write-service threads and metadata bootstrap. It does not represent browser-visible or full live provider acceptance. Existing source owners remain available for separately authorized schema preparation and normal startup. UI assets are byte-identical to the accepted source.

Storage-producing adapter output is only bounded result JSON on stdout (under existing Supervisor rotation). It creates no archive, queue or identity store. Manifest reads are capped at 128 KiB; selection/credential inputs at 64 KiB; inherited kernel file cap is 499,000,000 bytes. Startup requires the 2 GB floor plus 64 MiB reserve. Existing queue, journal, terminal/checkpoint quotas, seven-day cache/temp cleanup and protected-receipt policy remain unchanged. No absolute WAL or aggregate 5 GiB cap is claimed.

The detailed nonsecret runtime manifests and operational evidence stay local. Source publication does not authorize deployment, provider acquisition or activation of any dormant component.

Fresh compact recovery source is included as `scripts/flex_v2_fresh_pair_recovery.py`; it imports this commit's existing control renderer instead of an installed historical-tools path. It requires the explicit recovery-execution argument. The renderer preserves terminal evidence and binds RPC telemetry to the recovered Core. Current-generation dormant profiles deliberately fail closed after a pair change; regenerate their manifest identities/hashes for the authorized fresh pair before a later activation, retaining the same queues, selection contracts and budget ledgers. Qualification covers that explicit rebind without replaying exhausted jobs.
