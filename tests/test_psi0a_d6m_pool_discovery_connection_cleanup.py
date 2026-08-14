from contextlib import contextmanager
import sqlite3

import pytest

import src.core.pool_discovery as pool_discovery


class FakeConnection:
    def __init__(self, fail_phase=None):
        self.fail_phase = fail_phase
        self.closed = 0
        self.commits = 0
        self.statements = []

    def cursor(self):
        if self.fail_phase == "CURSOR":
            raise RuntimeError("injected cursor failure")
        return self

    def execute(self, sql, parameters=()):
        self.statements.append((" ".join(sql.split()), parameters))
        if self.fail_phase in {"SELECT", "WRITE"}:
            raise RuntimeError(f"injected {self.fail_phase.lower()} failure")
        return self

    def commit(self):
        self.commits += 1
        if self.fail_phase == "COMMIT":
            raise RuntimeError("injected commit failure")

    def close(self):
        self.closed += 1


class FailingJsonProjection(dict):
    def __getitem__(self, key):
        if key == "base_token":
            raise RuntimeError("injected JSON/projection failure")
        return super().__getitem__(key)


def _reserves(mapping_type=dict):
    return mapping_type(
        base_account="base",
        quote_account="quote",
        base_token="mint",
        quote_token=pool_discovery.SOL_MINT,
        base_decimals=6,
        quote_decimals=9,
        pool_program=pool_discovery.PUMPSWAP_PROGRAM,
        pool_address="pool",
        authority_account="authority",
    )


async def _run(monkeypatch, connection, reserves=None):
    instance = pool_discovery.PoolDiscovery("fixture.db", "https://unused.invalid")
    migrated = []

    @contextmanager
    def managed(*_args, **_kwargs):
        try:
            yield connection
        finally:
            connection.close()

    async def immediate(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(pool_discovery, "managed_db_connect", managed)
    monkeypatch.setattr(pool_discovery.asyncio, "to_thread", immediate)
    monkeypatch.setattr(
        instance,
        "_mark_token_migrated",
        lambda *args: migrated.append(args),
    )
    result = await instance.register_pool_to_db(
        "mint", reserves or _reserves(), "fixture"
    )
    return result, migrated


@pytest.mark.asyncio
async def test_success_preserves_write_commit_migration_and_closes_once(monkeypatch):
    connection = FakeConnection()
    result, migrated = await _run(monkeypatch, connection)
    assert result is True
    assert connection.closed == 1
    assert connection.commits == 1
    assert len(connection.statements) == 1
    assert connection.statements[0][0].startswith("INSERT OR REPLACE")
    assert migrated == [("mint", "pool", pool_discovery.PUMPSWAP_PROGRAM, True)]


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["CURSOR", "SELECT", "WRITE", "COMMIT"])
async def test_every_named_database_exception_closes_once(monkeypatch, phase):
    connection = FakeConnection(fail_phase=phase)
    result, migrated = await _run(monkeypatch, connection)
    assert result is False
    assert connection.closed == 1
    assert migrated == []


@pytest.mark.asyncio
async def test_json_projection_exception_closes_once(monkeypatch):
    connection = FakeConnection()
    result, migrated = await _run(
        monkeypatch, connection, _reserves(FailingJsonProjection)
    )
    assert result is False
    assert connection.closed == 1
    assert connection.commits == 0
    assert migrated == []


def test_source_uses_managed_connection_without_manual_close():
    import inspect

    source = inspect.getsource(pool_discovery.PoolDiscovery.register_pool_to_db)
    body = source.split("def _do_db_write():", 1)[1].split(
        "await asyncio.to_thread(_do_db_write)", 1
    )[0]
    assert "with managed_db_connect(" in body
    assert "conn.close()" not in body


@pytest.mark.asyncio
async def test_ephemeral_sqlite_connection_is_released_after_success(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "pool.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE token_pool_accounts (
                mint TEXT PRIMARY KEY,
                base_account TEXT,
                quote_account TEXT,
                base_token TEXT,
                quote_token TEXT,
                base_decimals INTEGER,
                quote_decimals INTEGER,
                pool_program TEXT,
                pool_address TEXT,
                is_active INTEGER,
                vault_validation_status TEXT,
                vault_validation_error TEXT,
                vault_validation_attempts INTEGER,
                last_vault_validation_at INTEGER,
                discovery_method TEXT,
                pool_score REAL,
                created_at INTEGER,
                updated_at INTEGER,
                authority_account TEXT
            )
            """
        )

    instance = pool_discovery.PoolDiscovery(str(db_path), "https://unused.invalid")
    monkeypatch.setattr(instance, "_mark_token_migrated", lambda *_args: None)
    assert await instance.register_pool_to_db("mint", _reserves(), "fixture") is True

    # A new immediate write proves the managed scope released both the
    # connection and the serialized write lane after the successful commit.
    with sqlite3.connect(db_path, timeout=0.1) as connection:
        connection.execute(
            "UPDATE token_pool_accounts SET discovery_method = ? WHERE mint = ?",
            ("verified", "mint"),
        )
        assert connection.execute(
            "SELECT discovery_method FROM token_pool_accounts WHERE mint = ?",
            ("mint",),
        ).fetchone() == ("verified",)
