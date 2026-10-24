# DEV004 Full Direct Compact-V2 Disposable Rehearsal

**PASS — one direct-source import qualified; stop.** Production deployment/cutover was not performed or authorized.

Source/evidence composition at execution `fd7f4670ba51bfb75d263049f4b993d6ccfbe91c`. Frozen runtime remains byte-for-byte unchanged from4b871217. Starting handoff7c7304b1; immutable plan and source identities are in plan.json. Rehearsal controls only were added; no runtime/source deployment.

## Direct migration and integrity

Exactly **one** import ran from read-only, indexed V1 selectors into new compact Core/OPS databases. The complete accepted population is **28,462 records** (including15 separately accepted retained-reference records);29previously documented synthetic controls remain outside operational selection. Six Byzantine closure cases are included. Original selected data remains field-exact, including generated-column values. No V1 backup, clone or large historical snapshot.

The derived typed sidecar adds1,171rows, for **29,633 target rows**. Foreign keys remained ON; deferred checks were validated before commit. Both databases pass quick_check and full foreign_key_check. Every original and derived row was verified after restart against frozen PK/row-SHA256 oracles. The archived oracles contain keys/hashes, not a source database payload.

All **11 actual installed-reader outputs** match the frozen V1 outputs: treasury/SubProvider recognition, Watchtower route, creator classification, attribution outcome, alternative parents/quarantine, price facts/observations/reduction and creator funding history. All10known operations retained. In both V2 and rollback:10UNKNOWN_INFRASTRUCTURE/MEDIUM outcome conflicts and5PENDINGoutbox admissions remain intact;180confirmed-match,111identity-event,104membership-commit,14funding-signature and762NONE mappings retain their distinctions. No promotion or counterevidence suppression. Canonical price/provenance is exact;0.1% ATH valuation tolerance unchanged. Accepted unrelated matrices were reused, not rerun.

## Restart

A fresh interpreter forbids **all non-disposable SQLite access**, validates all29,633row digests, FK/quickcheck and11reader outputs, and imports the actual listener, resolver and Walkback database-binding entrypoints. The matched DEV pair is accepted. No daemon, subscription or provider is started.

The first child verification exposed a harness-only API assumption: Walkback exposes OPS_DB_PATH rather than resolve_ops_db_path. The harness was corrected to use the actual interface. Verification resumed on the **same imported pair**, with source database access forbidden and zero additional imports. Original diagnostic and resume authority are preserved; no runtime correction was needed.

## Rollback and restoration

An actual managed staged price change was rolled back, restoring its original value. The existing retirement owner permanently retired only the imported **DEV** pair. A fresh matched rollback pair was provisioned from the already compact V2 output, never from production V1. Existing paired-binding seals/authority validated the new generation and retired predecessor pair; a stale DEV writer was rejected.

A second fresh interpreter verified **all29,633rows and11reader outputs** on the rollback pair, with zero FK violations, quick_check=ok and all three database bindings correct. The original DEV pair stays retired; production V1 was never sealed, fenced, retired, rebound or replaced. This is tested compact-DEV restoration, not a claim of an executed production rollback.

## Size and storage

| Measurement | Bytes |
|---|---:|
| Core V2 | 4,833,280 |
| OPS V2 | 12,783,616 |
| Combined compact pair | 17,616,896 |
| Sampled peak additional allocation, including compact rollback pair/oracles | 41,721,856 |
| Largest observed file | 12,783,616 |
| Free immediately before direct import | 3,758,845,952 |
| Free after verification/rollback | 3,678,900,224 |

Combined pair **17.62MB /16.80MiB**. The compact rollback pair duplicates only this new small output. The6.40MBrow-oracle metadata is bounded; compressed audit archive is2,296,569bytes. The41.72MBsampled peak covers the disposable execution root; the2.30MBcompressed published oracle archive and small repository/git metadata are additional and excluded from that fixture measurement. It is not a guaranteed instantaneous high-water mark. All feature-created files are constrained by64MiB OS limits, below500,000,000bytes; total fixture allocation checked against256MiB. Disk floor checks used2GB plus64MiB reserve during work and256MiB initial reserve. Lowest retained free-space sample `3678900224`bytes exceeds the floor.

Filesystem free change is `-79945728`bytes; it is not equated with the41.72MBfixture allocation because other processes and filesystem accounting can change concurrently. No storage cleanup, VACUUM, checkpoint, historical deletion or source copying occurred. The measured initial selected-seed footprint does not establish a lifetime5GiBcap or operational growth bound.

## Authority and boundaries

Production mutations0; provider calls0; production restarts0; realV1copies0; direct imports1. BIRDEYE authority and budgets unchanged; credentials not read. Existing journal/acquisition/retry qualifications are reused and their production state is not reset. External compact-history/mapping authorities remain under their accepted contracts; they were not replaced with V1 bulk.

Core/OPS V1 were read through independent read-only snapshots. This is not an atomic/quiesced production generation, final live queue reconciliation or production cutover acceptance. Runtime deployment status QUALIFIED_NOT_DEPLOYED. No new source features, infrastructure, providers or historical research.

**NEXT_ACTION: NONE — requested disposable rehearsal complete.** Preserve V1. Any production transition requires separate authorization and the applicable final authority/reconciliation checks.
