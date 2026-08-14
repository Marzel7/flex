# PSI0A-D Exact Future-Query and Query-Plan Contract

PSI0A-D freezes five deterministic, single-statement future shadow-read templates over the PSI0A manifest surfaces. Every template has an inclusive `rowid` upper boundary and explicit result-row ceiling. Cohort-bound sources receive a caller-supplied canonical JSON mint list; evidence is restricted to an explicit fact family.

Qualification invokes only `EXPLAIN QUERY PLAN` through SQLite URI `mode=ro` connections with verified `PRAGMA query_only`, a 250 ms lock timeout, an active deadline of at most two seconds, and unconditional progress-handler removal and connection closure. It records planner detail, selected indexes, relation scans and temporary structures. It never executes a SELECT template or reads an evidence row.

The contract binds the canonical PSI0A capture manifest and immutable PSI0A-C16 boundary digests. It grants neither extraction nor activation authority. Resource ceilings beyond the per-template row parameter remain PSI0A-E work, and PSI0B remains separately authorized.

## D2A immutable identity rebind

PSI0A-D2A supersedes contract digest `5d35498800f5c3d65ab972fd3fa593ca768eeaf79be2e2813a9c0d7192d718df`, which was bound to the abbreviated engineering revision `0ab2e8e7`. The rebound contract binds the same five templates byte-for-byte to engineering revision `d0bd5f1d2f0d95cc6026d681bb4c3ee1a619165f`, producing digest `8ba0259d356c3fd6300f22dbf08b6ca3ea96fd836f94221d4f2499949de4577c`.

The canonical manifest, C16 boundary, parameters, limits, read-only enforcement, deadlines, cleanup behavior and false extraction/activation grants are unchanged. A fixed template digest fails closed if any query surface changes. The rebind is safe-local only and does not authorize production plan inspection.
