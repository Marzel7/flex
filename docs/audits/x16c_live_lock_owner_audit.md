# X16C Live Operations Lock-Owner Audit

Date: 2026-07-13 (Europe/London)

## Failure phase

The failed command used a fresh native SQLite connection and a deferred `BEGIN`.
Before the first canonical mutation it performed only read-only proposal
revalidation and ledger `SELECT`s. A deferred `BEGIN` does not acquire SQLite's
write lock. The first statement capable of returning the observed `SQLITE_BUSY`
was therefore:

```sql
INSERT INTO operators (...)
```

The 63 ms transaction age includes proposal revalidation. The original telemetry
did not capture the extended SQLite error code. Phase and extended-code capture
has now been added at connection, BEGIN, revalidation, every table group, and
COMMIT boundaries.

## Runtime holders

At 15:25–15:30 BST, `lsof` found only two processes with the operations database,
WAL, or shared-memory files open:

| PID | PPID | Service | Command | Descriptors | Classification |
|---:|---:|---|---|---|---|
| 83047 | 34495 | `ws_cascade` | `python -m src.core.ws_cascade --loop` | dozens of writable DB/WAL/SHM descriptors | lock-owner process |
| 93970 | 93753 | Gunicorn worker | `gunicorn ... src.core.main:app` | read DB descriptors and WAL/SHM; promotion's managed native connection during the failure | failed writer |

`ws_cascade` had started on July 10, before the shared operations write lane was
deployed. It therefore could not be participating in code added to
`db_locking.py` or `DatabaseWriteService` on July 13.

A two-second macOS `/usr/bin/sample` of PID 83047 captured a cascade thread in
`sqlite3BtreeBeginTrans -> sqliteDefaultBusyCallback`, proving live transaction
contention inside that process. This is stronger than file-holder inference:
the stale cascade was actively attempting operations-database writes while
retaining the other operations descriptors.

## Unmanaged writer audit

| File / function | Service | Write | Lifetime | Status before fix |
|---|---|---|---|---|
| `ws_cascade_store._event_writer_loop` | ws_cascade | events and webhook hits | background thread, raw connection | **UNMANAGED**: explicitly bypassed serializer |
| `ws_cascade_store.treasury_ws_register` | ws_cascade | treasury usage | raw connection per call | **UNMANAGED**: explicitly bypassed serializer |
| `ws_cascade._heartbeat_loop` | ws_cascade | heartbeat DDL/upsert | connection per heartbeat | managed-connection compatibility path, runtime DDL |
| `ws_cascade.Cascade.__init__` | ws_cascade | cascade DDL | process startup | managed-connection compatibility path |
| remaining `Cascade._ops()` mutations | ws_cascade | sessions, candidates, lifecycle, audit | explicit `finally: close`; store helpers commit bounded groups | stale process was unmanaged; current code acquires shared cross-process lane |
| listener migration enqueue/lifecycle | watchtower_listener | walkback/lifecycle | bounded per event | managed-connection compatibility path |
| walkback worker | walkback_worker | queue, lineage, heartbeat | commits before RPC and closes per loop | stale process; managed lane after restart |
| promotion service | Gunicorn | five canonical/governance tables | one callback, one commit, close | **MANAGED** |
| dashboard dismiss action | Gunicorn | operation state | one callback | **MANAGED** |
| operation scheduler | operation_scheduler | operational intelligence | stopped at incident time | not a runtime holder |

The two `_telemetry_conn` call paths were the only deliberate raw SQLite bypasses
found in the active cascade. They also contained their own busy waits and retry
loop, contradicting the shared-write invariant.

## Proven cause and minimal migration

The promotion owned the advisory application lane, but PID 83047 was a stale
pre-deployment cascade process and contained raw write paths that deliberately
bypassed that lane. Continuous cascade writes could commit between promotion's
ledger reads and first canonical insert, causing the deferred WAL read snapshot
to fail its write upgrade immediately.

Required migration:

1. Route cascade event, hit, treasury registration, schema, and heartbeat writes
   through `DatabaseWriteService` callbacks.
2. Route listener walkback and lifecycle mutations through the same service.
3. Remove raw connection, retry, sleep, and busy-timeout logic from cascade writes.
4. Restart every long-lived operations writer so no pre-X16C process remains.
5. Keep callbacks atomic even when a legacy helper calls `commit()` by reserving
   the actual commit/rollback for the service-owned connection.

## Controlled live verification

After migrating the raw cascade paths and restarting `ws_cascade`,
`walkback_worker`, `watchtower_listener`, and Gunicorn, the WATCHTOWER proposal
was approved once successfully.

- canonical operator: `04265d9f-6eb2-568c-a49e-9253091a4dbb`
- transaction: `04c04215-2750-4cba-93b5-bfd003df91ce`
- status: `COMMITTED`
- queue wait: 0.592 ms
- duration: 116.49 ms
- rows modified: 17
- commit completed: 116.122 ms after lane acquisition

The phase trace reached every expected boundary: native connection, BEGIN,
proposal revalidation, all five table groups, commit attempted, and commit
completed. Read-only verification found one operator, nine entities, five pieces
of evidence, one canonical review, and one promotion-ledger row.

Post-commit intelligence activation then exposed an unrelated missing
`LIVE_DB_PATH` alias. Governance remained committed as designed; the canonical
database module now defines `LIVE_DB_PATH = DB_PATH`.
