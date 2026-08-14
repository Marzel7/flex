# PSI0A-E Production-Shadow Resource-Ceiling Contract

PSI0A-E binds the closed PSI0A-D five-query surface and its replay-verified production plan evidence to immutable hard resource ceilings. It is a safe-local contract only and grants neither extraction nor activation authority.

The future shadow operation is limited to 5,000 rows per query and 25,000 rows total. Canonical output is limited to 8 MiB per ordinary query, 16 MiB for snapshots and 48 MiB total. Each query has a five-second active deadline and six-second maximum transaction lifetime; the whole operation has a 30-second wall deadline. Exactly five connections may be opened, only one concurrently. Process RSS growth is limited to 128 MiB and SQLite temporary work to 32 MiB.

Pagination, retry, failover, adaptive widening and limit changes are prohibited. Every query must produce exact row, byte, duration, transaction and temporary-work accounting. Global connection, wall-time, memory and temporary-space accounting must also reconcile exactly. Violations produce deterministic `PSI0A_E_*` reason codes.

## Snapshot temporary ordering

The replay-verified `snapshot_selected_cohort` plan selects `idx_tps_mint_captured` and uses a temporary B-tree only for the rightmost `snapshot_id` ordering component. PSI0A-E permits that specific bounded ordering under all of these simultaneous limits: at most 5,000 rows, 16 MiB canonical output, five query seconds, six transaction seconds and 32 MiB temporary work. No other query receives temporary-work authority. Exceeding or failing to account for any limit fails closed.

PSI0A-E performs no production access and does not authorize PSI0A-F, PSI0A-H, PSI0B or activation.
