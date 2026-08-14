from contextlib import contextmanager

import pytest

import src.core.vault_discovery as vault_discovery


class FakeConnection:
    def __init__(self, events, fail_on=None):
        self.events = events
        self.fail_on = fail_on
        self.execute_count = 0
        self.closed = False

    def cursor(self):
        self.events.append("cursor")
        if self.fail_on == "select":
            raise RuntimeError("injected cursor/select preparation failure")
        return self

    def execute(self, sql, parameters=()):
        self.execute_count += 1
        statement = " ".join(sql.split())
        self.events.append(f"execute:{statement.split()[0]}")
        if self.fail_on == "insert" and statement.startswith("INSERT"):
            raise RuntimeError("injected insert failure")
        if self.fail_on == "update" and statement.startswith("UPDATE"):
            raise RuntimeError("injected update failure")
        return self

    def commit(self):
        self.events.append("commit")
        if self.fail_on == "commit":
            raise RuntimeError("injected commit failure")

    def close(self):
        self.events.append("close")
        self.closed = True


class PriceWorker:
    def __init__(self, events, fail=False):
        self.events = events
        self.fail = fail

    def trigger_pool_refresh(self):
        self.events.append("refresh")
        if self.fail:
            raise RuntimeError("injected refresh failure")


def _account(address="base"):
    decoded = vault_discovery.DecodedTokenAccount(
        mint="mint", owner="owner", amount=1, delegated_amount=0,
        delegate=None, state=1, is_native=False, close_authority=None,
    )
    return vault_discovery.ValidatedTokenAccount(address, 1, decoded)


async def _run(monkeypatch, *, fail_on=None, price_worker=None,
               candidates=True, provider_failure=None, malformed_quote=False):
    events = []
    connection = FakeConnection(events, fail_on=fail_on)

    @contextmanager
    def managed(*_args, **_kwargs):
        events.append("open")
        try:
            yield connection
        finally:
            connection.close()

    async def largest(*_args, **_kwargs):
        events.append("largest")
        if provider_failure == "largest":
            raise RuntimeError("provider failure")
        return [{"address": "candidate"}] if candidates else []

    async def validate(*_args, **_kwargs):
        events.append("validate")
        if provider_failure == "validate":
            raise RuntimeError("validation failure")
        return [_account()]

    async def resolve(*_args, **_kwargs):
        events.append("resolve")
        return "quote"

    async def quote(*_args, **_kwargs):
        events.append("quote")
        if malformed_quote:
            return {"decoded": None}
        return {"address": "quote", "decoded": None}

    monkeypatch.setattr(vault_discovery, "managed_db_connect", managed)
    monkeypatch.setattr(vault_discovery, "get_token_largest_accounts", largest)
    monkeypatch.setattr(vault_discovery, "validate_token_accounts", validate)
    monkeypatch.setattr(vault_discovery, "resolve_quote_vault_from_base", resolve)
    monkeypatch.setattr(vault_discovery, "validate_quote_vault", quote)

    worker = price_worker(events) if price_worker else None
    result = await vault_discovery.discover_and_register_all_pools(
        "mint", object(), "fixture.db", price_worker=worker,
    )
    return result, events, connection


@pytest.mark.asyncio
async def test_provider_work_finishes_before_database_opens_and_refresh_follows_close(monkeypatch):
    result, events, connection = await _run(monkeypatch, price_worker=PriceWorker)
    assert result is True
    assert events[:4] == ["largest", "validate", "resolve", "quote"]
    assert events.index("open") > events.index("quote")
    assert events.index("close") < events.index("refresh")
    assert connection.closed
    assert events.count("commit") == 3


@pytest.mark.asyncio
async def test_no_candidates_returns_without_opening_database(monkeypatch):
    result, events, _ = await _run(monkeypatch, candidates=False)
    assert result is False
    assert events == ["largest"]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_failure", ["largest", "validate"])
async def test_provider_or_validation_exception_never_opens_database(monkeypatch, provider_failure):
    result, events, _ = await _run(monkeypatch, provider_failure=provider_failure)
    assert result is False
    assert "open" not in events


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_on", ["select", "insert", "update", "commit"])
async def test_database_exceptions_close_owned_connection(monkeypatch, fail_on):
    result, events, connection = await _run(monkeypatch, fail_on=fail_on)
    assert result is False
    assert connection.closed
    assert events.count("close") == 1


@pytest.mark.asyncio
async def test_json_projection_failure_returns_before_database_open(monkeypatch):
    result, events, _ = await _run(monkeypatch, malformed_quote=True)
    assert result is False
    assert "open" not in events


@pytest.mark.asyncio
async def test_refresh_exception_is_fail_open_and_occurs_after_close(monkeypatch):
    result, events, connection = await _run(
        monkeypatch, price_worker=lambda events: PriceWorker(events, fail=True)
    )
    assert result is True
    assert connection.closed
    assert events.index("close") < events.index("refresh")


def test_source_contains_single_managed_database_scope_after_provider_awaits():
    import inspect

    source = inspect.getsource(vault_discovery.discover_and_register_all_pools)
    assert source.count("with managed_db_connect") == 1
    assert source.index("await validate_quote_vault") < source.index("with managed_db_connect")
    assert ".close()" not in source
