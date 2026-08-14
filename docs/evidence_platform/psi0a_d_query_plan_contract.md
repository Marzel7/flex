# PSI0A-D Exact Future-Query and Query-Plan Contract

PSI0A-D freezes five deterministic, single-statement future shadow-read templates over the PSI0A manifest surfaces. Every template has an inclusive `rowid` upper boundary and explicit result-row ceiling. Cohort-bound sources receive a caller-supplied canonical JSON mint list; evidence is restricted to an explicit fact family.

Qualification invokes only `EXPLAIN QUERY PLAN` through SQLite URI `mode=ro` connections with verified `PRAGMA query_only`, a 250 ms lock timeout, an active deadline of at most two seconds, and unconditional progress-handler removal and connection closure. It records planner detail, selected indexes, relation scans and temporary structures. It never executes a SELECT template or reads an evidence row.

The contract binds the canonical PSI0A capture manifest and immutable PSI0A-C16 boundary digests. It grants neither extraction nor activation authority. Resource ceilings beyond the per-template row parameter remain PSI0A-E work, and PSI0B remains separately authorized.
