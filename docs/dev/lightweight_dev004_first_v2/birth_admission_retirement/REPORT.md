# DEV004 Durable Birth Admission and V1 Retirement

RESULT: PASS — both requested safety conditions independently qualify in isolated DEV. Code remains QUALIFIED_NOT_DEPLOYED; no production activation or paired transfer.

SOURCE_REVISION: 91d3975e77236f601586b1bd8af86544cc97b9de

Objective completed from verified7b9eb450. FLEX_STABLE_V2 remains2d825bbcf27f802bd0bfcef90cedeba43cc968eb with manifestf0d3d933109f3572023af677729feef4db6e432657c7b7095b5bae05f2821dc7. The six installed writer/connection files freshly match the accepted DEV preimages exactly. Package-specific accepted corrections were patched in place, not replaced with another package's version. BIRDEYE authority, provider budgets, attribution predicates and price logic unchanged.

## Independent durable birth admission

The existing birth fallback JSONL file now supports a bounded versioned BirthJournal using the already-qualified migration journal's file locking, atomic publication and file/directory fsync. Explicit opt-in DEV004_DURABLE_BIRTH_JOURNAL_ENABLED=1 requires an absolute stable BIRTH_JOURNAL_PATH and existing compatible/explicitly initialized state. No automatic production initialization, relocation or deletion.

PumpPortal compact birth fields are durably admitted before receipt acknowledgement or canonical persistence. The identity matches the accepted compact receipt identity; original source, creator, signature and reception time are retained. The actual frame test proves durable admission before receipt processing while V1 is retired. Such births stay recoverably pending without a V1 retry insert or consuming an attempt while retirement blocks canonical writes. Failure before durable admission never produces a persistence acknowledgement.

Bounded replay uses the existing canonical birth persistence interface and exact creator/CREATE readback. A crash after canonical commit leaves a retained record; restart readback ACKs it without replaying the canonical write. Conflicts become retained CORE_AUTHORITY_CONFLICT quarantine. Duplicate admissions do not reset attempts/time, and ACK/quarantine/exhausted records are never evicted. New journal attempts use the accepted bounded journal's3-attempt ceiling; existing legacy Core queue counters/policies are not reset or rewritten. Generic non-PumpPortal fallbacks remain unchanged. Legacy bulk import refuses versioned records rather than deleting a partially consumed file.

## Persistent retirement fence

The existing database_write_service now provides explicit retire_database(...,generation=...) for one database. It takes the existing write lane, obtains SQLite BEGIN IMMEDIATE to drain native transactions, publishes at most2048bytes of immutable .retired metadata with file and directory synchronization, then releases its reservation/lease. There is no unretire or paired-switch API. A crash leaving partial/ambiguous metadata still blocks writers; it is never silently recreated or removed. Failed drain before publication does not falsely declare retirement.

New lease acquisition checks retirement both before and after flock. Listener, creator-resolution and Walkback connection wrappers check stale statements/cursors/scripts and pre-commit boundaries, rolling back rejected uncommitted work. Service-owned helper contexts cannot prematurely commit. Cached SQL and process restart do not revive V1. Unreadable metadata fails closed.

Registered native OPS/recovery connections retain their accepted lane exemption, but now have execution-time SQLite progress protection so retirement between the initial check and engine execution cannot permit an autocommit afterward. Existing caller progress/timeout callbacks are chained/restored. Tests explicitly force that race after the precheck across all3packages and verify no committed row. Normal managed transactions retain the existing write-lane ownership rules.

This is a fence for the existing managed Flex writer interfaces and registered native OPS factories, not OS access control for arbitrary unmodified SQLite clients. No SQLite files were replaced under open handles. No claim that two independent stores now form an atomic paired generation.

## Verification

148 unique provider-free tests PASS:66new birth/retirement controls,63accepted signature-first migration controls,19existing writer/autocommit/priority controls. Coverage includes actual PumpPortal frame ordering, pending retries, conflicts, duplicate identities, partial writes/fsync failure, crash after canonical commit, real child-process exits/reopen, active writer/lease drain, lease release, stale cursors/commits, SQL spelling/cache, all3runtime packages, service transaction atomicity and native execution races. Flag-off valid/malformed optional numeric paths preserve existing persistence behaviour.

One existing writer test initially failed solely because the isolated package scaffold omitted ws_cascade_store's storage-module import. Its fixture import path was corrected and only that test rerun: PASS. No Cascade scheduler/service was started. Original and retry JUnit artifacts are retained rather than hiding the initial failure. Optional background diagnostic reaper startup is omitted in isolated module loaders; actual writer/fence/SQLite/journal semantics are exercised. Native SQLite audit checks restrict new focused tests to disposable paths; network calls are blocked and no credentials read.

Unchanged100creator/616Watchtower/26Deep/11frozen, price, Walkback and discovery evidence reused. No broad historical audit or unrelated tests.

## Storage and limits

Each journal:4096byte records,2000000byte published file, one2000000byte staging file,512records,20record batch. Both journals together require at most about8MB for published+staging content, plus fixed lock/retirement metadata and filesystem overhead. Admission checks the existing2GB floor plus staging reserve; this is not a reservation against concurrent unrelated disk consumers. Exhaustion/ambiguous state refuses admission; no cleanup/disposition authority added.

Maximum observed fixture file20480bytes, total fixture allocation lengths900927bytes. Qualification RLIMIT_FSIZE16000000bytes. Marker publication is capped2048bytes. No large DB copies, raw provider payload retention or new unbounded growth path. No absolute fsync/lease-duration or production WAL ceiling is claimed. Retirement uses a transaction reservation/rollback without modifying canonical rows; actual admitted journal records remain retained.

## Remaining scope / next action

No remaining blocker for these two DEV acceptance conditions. The previous paired-transfer report's birth-dependence and stale-managed-writer findings are resolved by this source candidate, not by changing production. Final state reconciliation, paired Core/OPS rebind, exclusive generation activation and rollback rehearsal remain unexecuted and separately authorized work. Retirement markers are permanent authority metadata; deletion/reactivation is not authorized by this correction.

NEXT_ACTION: NONE for this correction — qualification complete. Await separate paired-transfer authorization; do not rebind, retire production stores, deploy code or enable journal flags.

RUNTIME_DEPLOYMENT_GATE=QUALIFIED_NOT_DEPLOYED. Actual DEV source targets/hashes and tests are in manifest.json; no daemon reload/restart or production DEV_COMPLETE assertion. Zero production source/database/configuration changes, provider calls, live WebSockets, service actions, historical deletion or cutover.
