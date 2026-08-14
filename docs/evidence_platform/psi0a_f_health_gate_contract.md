# PSI0A-F Production-Shadow Health-Gate Contract

PSI0A-F defines immutable telemetry checkpoints and deterministic `PASS`, `DO_NOT_START`, and `STOP` decisions for a future bounded shadow read. It binds PSI0A-E resource ceilings, the replay-verified PSI0A-D plans, the canonical manifest, and the C16 boundary. It performs no live observation and grants no extraction or activation authority.

Pre-start requires exactly three complete checkpoints spaced 30 seconds apart with a two-second tolerance. Each checkpoint must be no older than 45 seconds and no more than two seconds in the future. Listener PID must remain stable and running; `primary_fd_count` must remain below 8; critical descriptor events must be zero; serializer p99 must remain below 1,000 ms; lock errors must not increase; and database/WAL, write lease, PumpPortal, PumpSwap, ingestion, workers, queues, and services must all be explicitly `HEALTHY`. Missing, stale, incomplete, degraded, or unknown telemetry fails closed.

During an active operation, any non-queue failure stops immediately. Serializer queue depth is sustained only when it is greater than zero in two consecutive checkpoints sampled 30 seconds apart within the same two-second tolerance. A single nonzero sample followed by zero is transient. Two nonzero samples with invalid spacing fail closed because sustained state cannot be interpreted safely.

Decisions expose deterministic `PSI0A_F_*` reason codes and exact replay digests. Retry and degraded-mode bypass are prohibited. PSI0A-F does not authorize a live health observation, PSI0A-G/H, PSI0B, or production activation.
