# PSI0B-E13 database/WAL metadata gate

PSI0B-E13 replaces the PSI0B-E11 aggregate database/WAL existence check with a canonical per-database filesystem classifier. It is a telemetry-only contract amendment and grants no extraction, integration, or activation authority.

For each bound `main` and `ops` database, every prestart and active checkpoint records the database and WAL path, existence, filesystem type, inode, size, and nanosecond modification time, together with a canonical component digest. The database must exist as the expected regular file. Its WAL is healthy either when absent (a valid quiescent SQLite state) or when present as a regular file with a nonnegative size.

The observer fails closed before a source open on a missing or nonregular database, a present nonregular WAL, an unknown path, malformed component metadata, or any stat/identity collection exception. Symlinks are classified with `lstat` and are never accepted as regular database or WAL files. WAL creation, removal, recreation, and inode rotation are recorded provenance changes but do not independently fail health while the database remains valid.

The change preserves the E11 Supervisor, stable-PID, descriptor, critical-event, serializer, lock-error, queue, authoritative lease, RELEASE_PENDING, feed, ingestion, freshness, and service gates; the E12 entrypoint and five-query execution controls are unchanged.

Qualification is recorded in `docs/audits/psi0b_e13_database_wal_metadata_gate_qualification.json`.
