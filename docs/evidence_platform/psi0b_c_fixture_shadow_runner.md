# PSI0B-C Fixture-Only Shadow Runner

PSI0B-C qualifies a dependency-injected execution and immutable bundle boundary without granting production authority. It binds the exact PSI0B-A/B identities but accepts only SQLite files beneath an explicit fixture root. Production paths are rejected.

The runner executes the five frozen PSI0A-D SELECT templates sequentially using URI `mode=ro`, verified `PRAGMA query_only`, a 250 ms lock timeout, explicit read transactions and active progress-handler deadlines. It applies the C16 inclusive rowid parameters, PSI0A-E per-query and total row/byte/query-time/transaction-time/wall-time/connection/memory/temp ceilings and injected PSI0A-F pre-start/active decisions. The supplied output path must replay to the PSI0B-A output fingerprint. Every query unconditionally removes its handler, rolls back and closes. Exceptions and gate failures publish nothing.

Successful fixture results are canonicalized into `run.json`, `accounting.json`, `results.json` and `hashes.json` via an atomic staging-directory rename. Verification rejects missing, extra, altered, noncanonical, authority-changing or accounting-inconsistent content. Retry, pagination, failover, widening, production execution, integration and activation authority are false.

Qualification is frozen/ephemeral only. A later production runner authorization must bind the committed PSI0B-C revision, real paths, fresh health evidence and the exact run preflight; PSI0B-C itself cannot execute production.
