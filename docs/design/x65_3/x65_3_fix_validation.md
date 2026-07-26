# X65.3 — Phase 5: Validate the Proposed Fix

Simulates the proposed SQL change —
`create_tx_signature = COALESCE(incoming_create_tx_signature,
existing_create_tx_signature)` — against every real, logged overwrite
attempt from the live diagnostic (Phase 2), using the exact `existing`
and `incoming` values captured at the moment each attempt occurred.

## Method

For each of the 107 parsed log lines (102 from the primary observation
window plus a handful from the initial deploy pass), the simulation
computes `COALESCE(incoming, existing)` exactly as SQLite would, using
the real captured values — no synthetic or hypothetical data.

## Result: would COALESCE have preserved the signature?

**107 of 107 (100%)** — every single logged attempt had `incoming=NULL`
and a real, non-null `existing` value. `COALESCE(NULL, existing)`
evaluates to `existing` in every case, meaning the proposed fix would
have preserved the original, correctly-captured `create_tx_signature`
in **100% of observed real-world cases**, with zero exceptions.

## Result: would any valid update have been blocked?

**Zero.** Across all 107 observed attempts, `incoming` was **never** a
genuine non-null new signature — the diagnostic's own logging
condition (`existing IS NOT NULL AND incoming IS NULL`) guarantees this
by construction: it only logs when `incoming` is `NULL`, so by
definition every case in this sample has `incoming=NULL`. The proposed
`COALESCE` only ever falls back to `existing` when `incoming` is null
— it never overrides or discards a real, non-null incoming value. This
means the fix cannot, even in principle, block a legitimate new
signature from being written; it only prevents `NULL` from
overwriting something that was already correct.

## Conclusion

The proposed fix is validated against 100% of real, live production
overwrite attempts observed during this task's ~3-hour instrumentation
window: it would have prevented every single one, and would not have
blocked or altered a single legitimate write. No case was found, in
live production data, where the fix's behavior would differ from the
intended, designed outcome.
