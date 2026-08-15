# PSI0B-E9 committed production telemetry observer

PSI0B-E9 supplies the previously missing committed composition between the
PSI0A-F health contract and PSI0B-E8 observer provenance. Supervisor aggregate
status is captured without `check=True`; return codes 0 and 3 are accepted only
when stdout is complete and every required named service is deterministically
parsed as `RUNNING` with a positive PID. Intentionally stopped unrelated
services therefore cannot mask valid required-service evidence.

Each checkpoint attempt is written to the E8 append-only fsynced ledger before
its gate result is raised. The checkpoint includes Supervisor return code and
stdout/stderr digests, required service identities, descriptor count,
serializer identity and freshness, lock-error baseline, queue depth,
authoritative write-lease state, RELEASE_PENDING fingerprint, database/WAL and
feed/ingestion states, and the named failure reason.

The production dependency factory reads only Supervisor status, process
descriptor metadata, telemetry/log files, and database/WAL filesystem metadata.
It does not open SQLite, invoke a provider, mutate production, or grant
extraction, integration, or activation authority.

Contract digest:
`01dedd635c763ecd3642604b91d0b607d1bc49532ca288fecd55dc1158a8de5e`.
