# PSI0A-C High-Water and Read Boundary

PSI0A-C binds every allow-listed relation to a non-negative cursor upper bound and, where applicable, an event-time upper bound. Capture uses one explicit read transaction per database, URI `mode=ro`, verified `PRAGMA query_only`, a 250 ms lock timeout, aggregate `MAX` reads only, and unconditional rollback/close. No evidence rows are materialized.

Future PSI0B queries must use both the recorded cursor bound and any recorded event bound. Inserts after capture cannot enter the frozen cohort, and an extraction must keep its bounded read transactions open only within the separately qualified PSI0A-E duration. PSI0A-C grants neither extraction nor activation authority.

Declared numeric affinity is insufficient: a runtime aggregate that is not a non-negative SQLite integer fails closed with `PSI0A_C_NON_INTEGER_HIGH_WATER`. The contract never coerces textual timestamps into an unreviewed ordering policy.
