"""X78.19 -- birth persistence durability under write-lane starvation.

MC1.2B proved live: births are received continuously by the listener, but a
material fraction (measured 47/65 = ~72% in one window) fail to persist
because the shared cross-process write lane is occupied by long-running
background work (network_membership_builder, intelligence_refresh,
second_hop_builder). Before this change, a failed birth insert was logged
and dropped -- no durable retry existed for births specifically (only for
migrations, via migration_persist_queue).

This adds birth_persist_queue (same shape/conventions as the existing
migration_persist_queue) plus a drain worker
(PumpFunCurveListener.drain_birth_persist_queue). mint is the idempotency
key: token_analysis.mint is UNIQUE PRIMARY KEY and
_insert_bonding_curve_token's INSERT...ON CONFLICT(mint) DO UPDATE is itself
idempotent, so replaying a queued row -- including after a crash/restart --
can never create a duplicate token row.

These tests exercise the real DB write path against a temp SQLite file
(so the ON CONFLICT idempotency semantics are actually verified, not
mocked away) while stubbing out the unrelated heavy side effects
(_upsert_birth_metadata_cache, prediction scoring, websocket state) that a
real PumpFunCurveListener.__init__ would otherwise pull in -- the same
"bare stand-in object" approach used by test_x78_10_listener_ensure_db_retry.py
for this class's other heavy-constructor methods.
"""
import asyncio
import os
import sqlite3
import tempfile

import pytest

from src.core.database_write_service import CrossProcessDatabaseWriteTimeout
from src.core import pumpfun_curve_listener as listener_mod
from src.core.pumpfun_curve_listener import (
    PumpFunCurveListener,
    _ensure_webhook_birth_queue_schema,
    birth_persistence_telemetry,
    _BIRTH_TELEMETRY,
    _BIRTH_TELEMETRY_LOCK,
    _fallback_append_birth,
    _fallback_file_path,
    _fallback_file_line_count,
    _reconcile_fallback_file_into_queue,
)


SCHEMA_SQL = """
CREATE TABLE token_analysis (
    mint TEXT UNIQUE PRIMARY KEY,
    created_at NUM,
    analyzed_at REAL,
    earliest_tx_creator TEXT,
    pf_ws_creator TEXT,
    bonding_curve_pda TEXT,
    create_tx_signature TEXT,
    source_platform TEXT,
    lifecycle_stage TEXT,
    is_new INTEGER DEFAULT 0,
    migration_signal_source TEXT,
    migration_signal_updated_at INTEGER,
    first_pre_migration_signal_at INTEGER
);
CREATE TABLE birth_persist_queue (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    mint                 TEXT    NOT NULL,
    creator              TEXT,
    created_at           TEXT,
    bonding_curve_pda    TEXT,
    create_tx_signature  TEXT,
    symbol               TEXT,
    name                 TEXT,
    received_at          INTEGER NOT NULL,
    status               TEXT    NOT NULL DEFAULT 'PENDING',
    retry_count          INTEGER NOT NULL DEFAULT 0,
    last_error           TEXT,
    last_attempt_at      INTEGER,
    processed_at         INTEGER,
    UNIQUE(mint)
);
"""


@pytest.fixture
def temp_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()

    monkeypatch.setattr(listener_mod, "DB_PATH", path, raising=False)
    with _BIRTH_TELEMETRY_LOCK:
        for k in _BIRTH_TELEMETRY:
            _BIRTH_TELEMETRY[k] = 0

    yield path
    os.unlink(path)


class _BareListener:
    """Stand-in exposing only what _insert_bonding_curve_token /
    drain_birth_persist_queue need, avoiding PumpFunCurveListener.__init__'s
    DB/websocket/RPC side effects (same rationale as test_x78_10's
    _FakeListener)."""

    _insert_bonding_curve_token = PumpFunCurveListener._insert_bonding_curve_token
    drain_birth_persist_queue = PumpFunCurveListener.drain_birth_persist_queue

    def _remember_recent_birth_token(self, mint, bonding_curve_pda=None):
        pass

    async def _upsert_birth_metadata_cache(self, mint, symbol, name):
        pass  # unrelated side channel (dexscreener-ish cache), not under test


def _run(coro):
    return asyncio.run(coro)


def _fetch_token(db_path, mint):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM token_analysis WHERE mint=?", (mint,)).fetchone()
    conn.close()
    return row


def _fetch_queue_row(db_path, mint):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM birth_persist_queue WHERE mint=?", (mint,)).fetchone()
    conn.close()
    return row


# ---------------------------------------------------------------------------
# Phase D / F -- direct-path success (no contention)
# ---------------------------------------------------------------------------

def test_successful_insert_persists_directly_no_queue_row(temp_db):
    listener = _BareListener()
    _run(listener._insert_bonding_curve_token(
        "MintAAA", "CreatorX", "1786300000",
        bonding_curve_pda="BCPda1", create_tx_signature="Sig1",
        symbol="FOO", name="Foo Token",
    ))

    row = _fetch_token(temp_db, "MintAAA")
    assert row is not None
    assert row["lifecycle_stage"] == "bonding_curve"

    queued = _fetch_queue_row(temp_db, "MintAAA")
    assert queued is None, "a successful direct insert must not create a retry-queue row"

    telemetry = birth_persistence_telemetry(temp_db)
    assert telemetry["births_received"] == 1
    assert telemetry["births_persisted_direct"] == 1
    assert telemetry["births_queued_for_retry"] == 0


# ---------------------------------------------------------------------------
# Phase J -- failure injection: write lane occupied, birth must be durably
# retained, not lost.
# ---------------------------------------------------------------------------

def test_write_lane_timeout_durably_queues_birth_not_lost(temp_db, monkeypatch):
    """background writer holds lane -> birth arrives -> first write times out
    -> birth is durably retained (queued), not silently dropped."""

    def _blocked_managed_db_connect(*a, **kw):
        raise CrossProcessDatabaseWriteTimeout(
            database="tracked", lock_path="/fake/path", waiting_pid=1,
            waiting_thread="MainThread", command="pumpfun_curve_listener.py:_insert_bonding_curve_token",
            wait_seconds=60.0,
            current_owner={"command": "network_membership_builder.py:399 in assign_live_network_for_creator", "process_pid": 999},
        )

    monkeypatch.setattr(listener_mod, "managed_db_connect", _blocked_managed_db_connect)

    listener = _BareListener()
    _run(listener._insert_bonding_curve_token(
        "MintBBB", "CreatorY", "1786300100",
        bonding_curve_pda="BCPda2", create_tx_signature="Sig2",
        symbol="BAR", name="Bar Token",
    ))

    # Not in token_analysis yet (write genuinely failed)...
    assert _fetch_token(temp_db, "MintBBB") is None

    # ...but durably queued, not lost.
    queued = _fetch_queue_row(temp_db, "MintBBB")
    assert queued is not None
    assert queued["status"] == "PENDING"
    assert queued["creator"] == "CreatorY"
    assert queued["bonding_curve_pda"] == "BCPda2"

    telemetry = birth_persistence_telemetry(temp_db)
    assert telemetry["births_queued_for_retry"] == 1
    assert telemetry["birth_write_timeout_count"] == 1
    assert telemetry["birth_queue_pending_durable"] == 1
    assert telemetry["birth_queue_oldest_pending_age_s"] is not None


def test_background_writer_releases_then_queued_birth_persists(temp_db, monkeypatch):
    """background writer holds lane -> birth arrives -> first write times out
    -> birth is durably retained -> background writer releases -> birth
    persists. No permanent loss."""

    fail_first_call = {"failed_once": False}
    real_managed_db_connect = listener_mod.managed_db_connect

    def _flaky_managed_db_connect(*a, **kw):
        if not fail_first_call["failed_once"]:
            fail_first_call["failed_once"] = True
            raise CrossProcessDatabaseWriteTimeout(
                database="tracked", lock_path="/fake/path", waiting_pid=1,
                waiting_thread="MainThread", command="_insert_bonding_curve_token",
                wait_seconds=60.0, current_owner={"command": "intelligence_refresh.py:55 in _db"},
            )
        return real_managed_db_connect(*a, **kw)

    monkeypatch.setattr(listener_mod, "managed_db_connect", _flaky_managed_db_connect)

    listener = _BareListener()
    _run(listener._insert_bonding_curve_token(
        "MintCCC", "CreatorZ", "1786300200",
        bonding_curve_pda="BCPda3", create_tx_signature="Sig3",
        symbol="BAZ", name="Baz Token",
    ))
    assert _fetch_token(temp_db, "MintCCC") is None
    assert _fetch_queue_row(temp_db, "MintCCC")["status"] == "PENDING"

    # Background writer has now released the lane (real managed_db_connect
    # will succeed). Run one drain cycle manually (drain loop body, not the
    # infinite `while True` wrapper).
    _run(_drain_once(listener, temp_db))

    row = _fetch_token(temp_db, "MintCCC")
    assert row is not None, "queued birth must persist once the write lane is free"

    queued = _fetch_queue_row(temp_db, "MintCCC")
    assert queued["status"] == "PROCESSED"
    assert queued["processed_at"] is not None

    telemetry = birth_persistence_telemetry(temp_db)
    assert telemetry["birth_retries_succeeded"] == 1
    assert telemetry["birth_queue_pending_durable"] == 0


async def _drain_once(listener, db_path):
    """Runs exactly one iteration of drain_birth_persist_queue's body
    (not the infinite loop) against PENDING/RETRY rows."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, mint, creator, created_at, bonding_curve_pda, create_tx_signature,"
        " symbol, name, received_at FROM birth_persist_queue"
        " WHERE status IN ('PENDING','RETRY') ORDER BY id LIMIT 50"
    ).fetchall()
    conn.close()

    for row in rows:
        mint = row["mint"]
        row_id = row["id"]
        try:
            await listener._insert_bonding_curve_token(
                mint, row["creator"], row["created_at"] or str(row["received_at"]),
                bonding_curve_pda=row["bonding_curve_pda"],
                create_tx_signature=row["create_tx_signature"],
                symbol=row["symbol"], name=row["name"],
            )
            vc = sqlite3.connect(db_path)
            persisted = vc.execute("SELECT 1 FROM token_analysis WHERE mint=? LIMIT 1", (mint,)).fetchone()
            vc.close()
            if persisted:
                uc = sqlite3.connect(db_path)
                with uc:
                    uc.execute(
                        "UPDATE birth_persist_queue SET status='PROCESSED', processed_at=? WHERE id=?",
                        (1786300999, row_id),
                    )
                uc.close()
                with _BIRTH_TELEMETRY_LOCK:
                    _BIRTH_TELEMETRY["retry_succeeded"] += 1
            else:
                raise RuntimeError("mint not in token_analysis after retry attempt")
        except Exception as exc:
            with _BIRTH_TELEMETRY_LOCK:
                _BIRTH_TELEMETRY["retry_failed"] += 1
            ec = sqlite3.connect(db_path)
            with ec:
                ec.execute(
                    "UPDATE birth_persist_queue SET status='RETRY', retry_count=retry_count+1,"
                    " last_attempt_at=?, last_error=? WHERE id=?",
                    (1786300999, str(exc)[:200], row_id),
                )
            ec.close()


# ---------------------------------------------------------------------------
# Phase F -- idempotency
# ---------------------------------------------------------------------------

def test_repeated_retry_of_already_persisted_birth_is_idempotent_no_duplicate(temp_db):
    """A row that reaches PROCESSED must never be re-inserted as a duplicate
    if the drain loop (or a crash-replay) sees it again before the status
    update lands -- ON CONFLICT(mint) DO UPDATE guarantees this at the SQL
    level regardless of how many times the same mint is replayed."""
    listener = _BareListener()

    for _ in range(3):
        _run(listener._insert_bonding_curve_token(
            "MintDDD", "CreatorRepeat", "1786300300",
            bonding_curve_pda="BCPda4", create_tx_signature="Sig4",
            symbol="DUP", name="Dup Token",
        ))

    conn = sqlite3.connect(temp_db)
    count = conn.execute("SELECT COUNT(*) FROM token_analysis WHERE mint=?", ("MintDDD",)).fetchone()[0]
    conn.close()
    assert count == 1, "repeated delivery of the same mint must never create duplicate rows"


def test_queued_retry_of_already_persisted_mint_resolves_without_duplicate(temp_db, monkeypatch):
    """Simulates: birth already persisted directly, but a stale queue row for
    the same mint exists (e.g. from an earlier failed attempt that the
    ON CONFLICT enqueue-on-failure path had already written before a later
    direct success). Draining that stale row must not create a duplicate."""
    listener = _BareListener()

    _run(listener._insert_bonding_curve_token(
        "MintEEE", "CreatorAlready", "1786300400",
        bonding_curve_pda="BCPda5", create_tx_signature="Sig5",
        symbol="ALR", name="Already Token",
    ))
    assert _fetch_token(temp_db, "MintEEE") is not None

    # Manually insert a stale PENDING queue row for the same mint (simulating
    # a race where the enqueue-on-failure path wrote before an earlier retry
    # succeeded).
    conn = sqlite3.connect(temp_db)
    with conn:
        conn.execute(
            "INSERT INTO birth_persist_queue (mint, creator, created_at, received_at, status)"
            " VALUES (?, ?, ?, ?, 'PENDING')",
            ("MintEEE", "CreatorAlready", "1786300400", 1786300400),
        )
    conn.close()

    _run(_drain_once(listener, temp_db))

    conn = sqlite3.connect(temp_db)
    count = conn.execute("SELECT COUNT(*) FROM token_analysis WHERE mint=?", ("MintEEE",)).fetchone()[0]
    conn.close()
    assert count == 1, "draining a stale queue row for an already-persisted mint must not duplicate"


# ---------------------------------------------------------------------------
# Phase K -- crash recovery
# ---------------------------------------------------------------------------

def test_crash_between_failure_and_retry_leaves_row_recoverable(temp_db, monkeypatch):
    """Birth received -> persistence fails -> retry record durably created ->
    (simulated) process exits before any drain runs -> a fresh listener
    instance (simulating restart) resumes and the queued row is still
    PENDING/RETRY and persists exactly once."""

    real_managed_db_connect = listener_mod.managed_db_connect

    def _always_times_out(*a, **kw):
        raise CrossProcessDatabaseWriteTimeout(
            database="tracked", lock_path="/fake/path", waiting_pid=1,
            waiting_thread="MainThread", command="_insert_bonding_curve_token",
            wait_seconds=60.0, current_owner={"command": "second_hop_builder.py:98 in build"},
        )

    monkeypatch.setattr(listener_mod, "managed_db_connect", _always_times_out)

    listener_before_crash = _BareListener()
    _run(listener_before_crash._insert_bonding_curve_token(
        "MintFFF", "CreatorCrash", "1786300500",
        bonding_curve_pda="BCPda6", create_tx_signature="Sig6",
        symbol="CRSH", name="Crash Token",
    ))

    # "process exits" -- nothing more happens with listener_before_crash.
    queued = _fetch_queue_row(temp_db, "MintFFF")
    assert queued is not None
    assert queued["status"] == "PENDING"

    # "listener restarts" -- fresh instance, real managed_db_connect restored
    # (contention has cleared, matching the ticket's crash-recovery scenario).
    # DB_PATH stays pointed at temp_db throughout (only the write-lane
    # simulation is reverted) -- undo() would also revert temp_db's own
    # monkeypatch of DB_PATH, sending the drain at the real production DB.
    monkeypatch.setattr(listener_mod, "managed_db_connect", real_managed_db_connect)
    listener_after_restart = _BareListener()
    _run(_drain_once(listener_after_restart, temp_db))

    row = _fetch_token(temp_db, "MintFFF")
    assert row is not None, "birth must survive a crash between failed persistence and retry"

    conn = sqlite3.connect(temp_db)
    count = conn.execute("SELECT COUNT(*) FROM token_analysis WHERE mint=?", ("MintFFF",)).fetchone()[0]
    conn.close()
    assert count == 1, "post-crash-recovery persistence must be exactly-once, not duplicated"

    assert _fetch_queue_row(temp_db, "MintFFF")["status"] == "PROCESSED"


# ---------------------------------------------------------------------------
# Regression -- schema creation is idempotent / additive only
# ---------------------------------------------------------------------------

def test_webhook_birth_queue_schema_helper_unaffected(temp_db):
    """X78.19 must not alter the pre-existing webhook_birth_queue schema
    helper's behavior (separate table, separate mechanism, still used for
    the Helius webhook fallback path)."""
    conn = sqlite3.connect(temp_db)
    conn.close()
    _ensure_webhook_birth_queue_schema(temp_db)  # must not raise

    conn = sqlite3.connect(temp_db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(webhook_birth_queue)").fetchall()}
    conn.close()
    assert {"signature", "consumed", "payload", "source", "retry_count"} <= cols


# ---------------------------------------------------------------------------
# Phase J/K -- file-based last-resort fallback (added after live validation
# on 2026-08-09 caught mint=8Vv9jE9nasuQxGzf being lost: sqlite3.connect
# against the real DB path is globally intercepted by db_locking.py's
# _patched_connect and routed through the SAME cross-process write lane as
# every other write, so birth_persist_queue's own enqueue INSERT can itself
# time out under sustained contention. A plain file append touches no
# SQLite connection at all and so cannot be blocked by that lane.)
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_fallback_file(temp_db):
    path = _fallback_file_path()
    if os.path.exists(path):
        os.unlink(path)
    yield path
    if os.path.exists(path):
        os.unlink(path)


def test_fallback_append_writes_one_line_per_birth(temp_db, temp_fallback_file):
    _fallback_append_birth(
        "MintGGG", "CreatorFallback", "1786300600", "BCPda7", "Sig7",
        "FBK", "Fallback Token", 1786300600, "CrossProcessDatabaseWriteTimeout: simulated",
    )
    assert _fallback_file_line_count() == 1

    _fallback_append_birth(
        "MintHHH", "CreatorFallback2", "1786300601", "BCPda8", "Sig8",
        "FBK2", "Fallback Token 2", 1786300601, "CrossProcessDatabaseWriteTimeout: simulated",
    )
    assert _fallback_file_line_count() == 2


def test_insert_bonding_curve_token_falls_back_to_file_when_queue_write_also_fails(
    temp_db, temp_fallback_file, monkeypatch
):
    """The scenario that was actually caught live: the primary insert AND the
    birth_persist_queue enqueue both hit the write lane and both fail.
    The birth must still be durably recoverable -- via the file, not lost."""

    def _always_times_out(*a, **kw):
        raise CrossProcessDatabaseWriteTimeout(
            database="tracked", lock_path="/fake/path", waiting_pid=1,
            waiting_thread="MainThread", command="_insert_bonding_curve_token",
            wait_seconds=60.0, current_owner={"command": "second_hop_builder.py:98 in build"},
        )

    monkeypatch.setattr(listener_mod, "managed_db_connect", _always_times_out)
    # Also break the enqueue path's sqlite3.connect (module-level `sqlite3`
    # import inside _insert_bonding_curve_token's except-block uses a fresh
    # `import sqlite3 as _sq`, which resolves to the real stdlib module --
    # patch its .connect directly to simulate the SAME lane being unavailable
    # for the enqueue attempt too).
    import sqlite3 as real_sqlite3

    def _connect_also_times_out(*a, **kw):
        raise CrossProcessDatabaseWriteTimeout(
            database="tracked", lock_path="/fake/path", waiting_pid=1,
            waiting_thread="MainThread", command="birth_persist_queue enqueue",
            wait_seconds=60.0, current_owner={"command": "intelligence_refresh.py:55 in _db"},
        )

    # _fetch_token/_fetch_queue_row use sqlite3.connect too -- capture the
    # real connect function before patching so verification isn't itself
    # broken by the simulated lane outage under test.
    unpatched_connect = real_sqlite3.connect
    monkeypatch.setattr(real_sqlite3, "connect", _connect_also_times_out)

    listener = _BareListener()
    _run(listener._insert_bonding_curve_token(
        "MintIII", "CreatorDoubleFail", "1786300700",
        bonding_curve_pda="BCPda9", create_tx_signature="Sig9",
        symbol="DBLF", name="Double Fail Token",
    ))

    # Not persisted, not queued in the DB table (both paths failed)...
    conn = unpatched_connect(temp_db)
    conn.row_factory = real_sqlite3.Row
    assert conn.execute("SELECT * FROM token_analysis WHERE mint=?", ("MintIII",)).fetchone() is None
    assert conn.execute("SELECT * FROM birth_persist_queue WHERE mint=?", ("MintIII",)).fetchone() is None
    conn.close()

    # ...but durably retained in the file-based backstop.
    assert _fallback_file_line_count() == 1
    with open(_fallback_file_path(), encoding="utf-8") as f:
        import json
        record = json.loads(f.readline())
    assert record["mint"] == "MintIII"
    assert record["creator"] == "CreatorDoubleFail"


def test_reconcile_fallback_file_moves_records_into_queue_and_truncates(temp_db, temp_fallback_file):
    _fallback_append_birth(
        "MintJJJ", "CreatorReconcile", "1786300800", "BCPda10", "Sig10",
        "RCN", "Reconcile Token", 1786300800, "simulated failure",
    )
    assert _fallback_file_line_count() == 1

    moved = _reconcile_fallback_file_into_queue()

    assert moved == 1
    assert _fallback_file_line_count() == 0, "file must be truncated after successful reconcile"

    queued = _fetch_queue_row(temp_db, "MintJJJ")
    assert queued is not None
    assert queued["status"] == "PENDING"
    assert queued["creator"] == "CreatorReconcile"


def test_reconcile_then_drain_persists_and_is_idempotent(temp_db, temp_fallback_file):
    """Full recovery path: file -> queue -> persisted, exactly once even if
    reconcile is called multiple times."""
    _fallback_append_birth(
        "MintKKK", "CreatorFullPath", "1786300900", "BCPda11", "Sig11",
        "FULL", "Full Path Token", 1786300900, "simulated failure",
    )

    _reconcile_fallback_file_into_queue()
    listener = _BareListener()
    _run(_drain_once(listener, temp_db))

    row = _fetch_token(temp_db, "MintKKK")
    assert row is not None

    # Reconciling again (empty file) must be a no-op, not an error.
    moved_again = _reconcile_fallback_file_into_queue()
    assert moved_again == 0

    conn = sqlite3.connect(temp_db)
    count = conn.execute("SELECT COUNT(*) FROM token_analysis WHERE mint=?", ("MintKKK",)).fetchone()[0]
    conn.close()
    assert count == 1


def test_reconcile_leaves_file_intact_if_db_write_still_fails(temp_db, temp_fallback_file, monkeypatch):
    """If the write lane is STILL busy when reconcile runs, the file must be
    left untouched (not truncated, not partially written) so nothing is
    lost -- reconcile is retried on the next poll cycle."""
    _fallback_append_birth(
        "MintLLL", "CreatorStillBusy", "1786301000", "BCPda12", "Sig12",
        "BUSY", "Still Busy Token", 1786301000, "simulated failure",
    )

    import sqlite3 as real_sqlite3

    def _connect_fails(*a, **kw):
        raise real_sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(real_sqlite3, "connect", _connect_fails)

    moved = _reconcile_fallback_file_into_queue()

    assert moved == 0
    assert _fallback_file_line_count() == 1, "file must survive a failed reconcile attempt"
