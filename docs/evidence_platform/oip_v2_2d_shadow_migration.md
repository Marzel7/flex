# OIP v2.2D — Full Shadow Compact Provenance Migration Rehearsal

## Verdicts

- **Shadow Migration:** B — MIGRATION CONTROL WORKS BUT OPERATIONAL ISSUE REMAINS
- **Production Migration:** NEEDS_ADDITIONAL_SHADOW_WORK
- **Acquisition:** HOLD_ACQUISITION
- **Canonical Retirement:** KEEP_CANONICAL_FOR_ROLLBACK

The complete frozen shadow corpus was built with 141 durable checkpoints and resumed after interruption. The first control switch, compact reads/writes, rollback reconciliation, atomic rollback, canonical writes after rollback, catch-up, and second compact cutover were exercised without RPC or production interaction.

Final relation count: **12,398,217**. External digest: `e076e5d27a04dc82d3063289064e37f6a1919be813eaada6d54080dc3ef19b8b`. Canonical-minus-compact: **0**. Compact-minus-canonical: **0**. Second writer pause: **574014.874 ms** (operational limit: **30000 ms**).

The migration controls are correct, but production migration is not ready because full equivalence validation ran inside the writer pause. Move exhaustive validation before the pause and retain only a bounded final delta/count check inside it, then repeat the shadow cutover rehearsal.

Canonical provenance remains retained. Production migration and 5K acquisition remain separately authorized actions. The machine-readable report contains controls, rollback proof, compact soak measurements, storage, crash recovery, runbook, and stop conditions.
