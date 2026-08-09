# OIP v2.2B — Compact Provenance Migration Validation

## Verdicts

- **Storage:** B — COMPACT PROVENANCE VALIDATED BUT MIGRATION DESIGN INCOMPLETE
- **Acquisition:** HOLD_ACQUISITION
- **Deployment:** NEEDS_ADDITIONAL_SHADOW_VALIDATION

The current-corpus compact representation preserves all 12,398,192 relationships. Count equality is True; digest equality is True; canonical-minus-compact is 0; compact-minus-canonical is 0.

## Disk and storage

Free disk was 22,073,458,688 bytes before and 43,978,657,792 bytes after the sparse prototype, above the 8,589,934,592-byte reserve. Unrelated local storage was manually removed outside the audit while it was running, so this filesystem delta is contaminated and must not be attributed to compact provenance. The audit itself intentionally deleted no canonical OIP/Evidence data. The authoritative saving is the direct canonical-versus-compact SQLite object measurement below. The cleanup census is persisted in the machine-readable summary and every non-current corpus remains review-only.

Canonical provenance consumes 3,816,488,960 bytes. Compact identity maps, bidirectional indexes, and links consume 521,879,552 bytes: a saving of 3,294,609,408 bytes (86.33%). Bytes/link fall from 307.83 to 42.09. Estimated corpus size after verified retirement would be 2,430,513,152 bytes.

## Compatibility

External Primitive and Evidence identities remain authoritative. The compact keys are internal immutable references. The compatibility view returns the same external pair shape and enforces idempotent insertion; the isolated duplicate-write fixture added exactly one relation and was rolled back.

The consumer census found Primitive-to-Evidence reconstruction, a full ordered Discovery load, Evidence-side shadow-corpus access, exact-pair/audit access, and aggregate scans. A reverse compact index is therefore required. The sole canonical application writer is `EvidenceDatabase.write_primitives`; validators and audit tools are readers.

## Remaining gate

This is deliberately a provenance-only prototype, not a second 5.7 GB corpus. Full Primitive replay, pass-two idempotence, Discovery, motif, and relationship validation against a compact-compatible corpus adapter have not run. Production migration is therefore not ready, and acquisition remains held. This is the exact distinction between physical relation validation and application-path validation.

## Acquisition economics

Measured compact storage projects persistent growth of 355,863,201–533,794,801 bytes for 1,000 attempts, 711,726,402–1,067,589,602 for 2,000, 1,779,316,004–2,668,974,006 for 5,000, and 7,717,605,236–11,576,407,854 for the remaining 21,687 dependencies. These are ±20% planning ranges around v2.1G non-provenance growth plus the measured compact bytes/link; transient replay, journal, checkpoint, and validator space remains additional.

## Migration design

Build sidecar identity/link tables in bounded Primitive-key batches with a persisted cursor. Keep canonical reads and writes authoritative during the build. At the cutover boundary, briefly pause writes, apply the final delta transactionally, verify count/digest/bidirectional differences, switch the repository/view, and retain the canonical table for rollback. Canonical retirement requires replay and all downstream equivalence gates and is not authorized here.

No RPC, coverage, production interaction, semantic change, canonical deletion, or historical deletion occurred.
