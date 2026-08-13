# OIP v2.2E.2B2AD owner-thread SQLite cleanup policy

SQLite connections are thread-affine.  A background reaper must not read their
transaction state, roll them back, or close them from another thread.  Doing so
cannot recover a connection and creates recurring `CLOSE_FAILED_WRONG_THREAD`
noise while leaving the owner connection alive.

The B2AD policy is conservative: a foreign reaper marks a stale tracked
connection once as `OWNER_THREAD_CLEANUP_REQUIRED` and emits a bounded
diagnostic.  It does not manipulate the native resource.  Existing
owner-thread `managed_db_connect` and explicit `finally` paths remain
responsible for close/rollback.  A weakref-dead record is still removed.

The included regression creates a connection in one thread, invokes the reaper
from another, verifies no foreign close occurs, and verifies that owner-thread
closure removes the registry record.  This is local-only qualification.  It
does not prove production recovery or authorize deployment/restart/provider
work.
