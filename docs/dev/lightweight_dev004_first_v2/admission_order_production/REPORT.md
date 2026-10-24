# DEV004 production migration: HOLD

Engineering source: `c8e0233ecbdbfcc59bb8c6892f3f7dedd8580b30`.

The migration signature is durably published before optional completion lookup. Lookup failure retains pending admission; transitional authority defers dispatch. Mixed-generation binding continues to fail closed. The same transitional handling preserves already-durable births. The existing Core index bootstrap now forwards the accepted historical price-parent profile without changing its default.

Qualification: 42 focused admission/paired-binding tests passed, zero failures. The separate six focused ordering/composition controls and one price-parent/index bootstrap control passed. Prior unrelated accepted matrices were reused. An earlier broad transitional catch failed mixed-binding rejection; the final correction restricts allowed transitional errors and all 42 controls pass.

Runtime gate for the admission correction: PASS. Qualified listener sources installed with preimages preserved; listener restarted to PID 59620. Final source payload hashes all match the frozen runtime plan.

Production migration: HOLD. Final bounded operational reconciliation found at least 12,001 rows in `migration_walkback_handoff` and raised `ROW_LIMIT:migration_walkback_handoff`. Both staged target transactions were rolled back. Required handoff preservation is therefore not qualified; no retirement or paired V2 activation was attempted.

The matched unretired V1 authority was restored ACTIVE. Resolver PID 59912 and Walkback PID 59940 resumed; listener PID 59620 remains running. Durable journals were preserved, including quarantined/exhausted records. The terminal snapshot records 4,183,355,392 free bytes, Core V2 staging 10,645,504 bytes and OPS V2 staging 60,428,288 bytes. Original V1 databases remain authoritative; no deletion, compaction or full V1 copies. No artificial provider requests or dormant services were activated.

Storage design: all new control/fixture processes use a 499,000,000-byte kernel file limit; journal capacity/retention unchanged. Raw provider retention unchanged. This change adds no unbounded growth path. Existing oversized V1 files remain historical source authority and were not copied.

Rollback: no retired V1 reactivation occurred. Fence was withdrawn while both V1 databases remained unretired, and the matched V1 pair was validated before workers resumed. Staged V2 is inactive. Fresh compact matched-pair recovery remains required if a later attempt crosses retirement.

Exact next action: replace the fixed handoff population limit with bounded, durable, indexed continuation and complete coverage verification, preserving pending/completed handoff identities and dependencies; then resume the already-authorized cutover. Do not raise the limit or silently omit rows.
