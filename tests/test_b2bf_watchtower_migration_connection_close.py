import ast
import sqlite3
import sys
import types
from pathlib import Path


SOURCE = Path(__file__).parents[1] / "src/core/pumpfun_curve_listener.py"


class _InlinePool:
    def submit(self, function):
        function()


class _Connection:
    def __init__(self, *, fail_execute=False, creator_known=True, fail_commit=False):
        self.fail_execute = fail_execute
        self.creator_known = creator_known
        self.fail_commit = fail_commit
        self.closed = 0

    def execute(self, statement, parameters=()):
        if self.fail_execute:
            raise sqlite3.OperationalError("injected execute failure")
        if "FROM wt_creator_launches" in statement:
            return _Result({"creator_wallet": "creator", "evidence_grade": "STRONG"} if self.creator_known else None)
        if "FROM token_analysis" in statement:
            return _Result(None)
        return _Result(None)

    def commit(self):
        if self.fail_commit:
            raise sqlite3.OperationalError("injected commit failure")

    def close(self):
        self.closed += 1


class _Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


def _load_function():
    tree = ast.parse(SOURCE.read_text())
    node = next(
        item for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and item.name == "_check_watchtower_migration"
    )
    module = ast.Module(body=[node], type_ignores=[])
    namespace = {
        "DB_PATH": "/not-production/test.db",
        "_TOKEN_WORK_POOL": _InlinePool(),
        "log_print": lambda *args, **kwargs: None,
    }
    exec(compile(module, str(SOURCE), "exec"), namespace)
    return namespace["_check_watchtower_migration"]


def _isolate_derived_lifecycle(monkeypatch):
    cascade = types.ModuleType("src.core.ws_cascade_store")
    cascade.advance_lifecycle_migrated = lambda *args, **kwargs: None
    write_service_module = types.ModuleType("src.core.database_write_service")
    write_service_module.database_write_service = types.SimpleNamespace(
        register_database=lambda *args, **kwargs: None,
        submit=lambda *args, **kwargs: None,
    )
    monkeypatch.setitem(sys.modules, "src.core.ws_cascade_store", cascade)
    monkeypatch.setitem(sys.modules, "src.core.database_write_service", write_service_module)


def test_connection_closes_after_query_exception(monkeypatch):
    _isolate_derived_lifecycle(monkeypatch)
    connection = _Connection(fail_execute=True)
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: connection)

    _load_function()("mint", 1, "signature", "test")

    assert connection.closed == 1


def test_connection_closes_after_commit_exception(monkeypatch):
    _isolate_derived_lifecycle(monkeypatch)
    connection = _Connection(fail_commit=True)
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: connection)

    _load_function()("mint", 1, "signature", "test")

    assert connection.closed == 1


def test_connection_closes_once_on_success(monkeypatch):
    _isolate_derived_lifecycle(monkeypatch)
    connection = _Connection()
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: connection)

    _load_function()("mint", 1, "signature", "test")

    assert connection.closed == 1


def test_function_has_guaranteed_finally_close_backstop():
    tree = ast.parse(SOURCE.read_text())
    outer = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == "_check_watchtower_migration")
    runner = next(item for item in outer.body if isinstance(item, ast.FunctionDef) and item.name == "_run")
    finalizers = [node for node in ast.walk(runner) if isinstance(node, ast.Try) and node.finalbody]
    finalizer_close_calls = [
        node for finalizer in finalizers for node in ast.walk(ast.Module(body=finalizer.finalbody, type_ignores=[]))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "close"
    ]

    assert len(finalizer_close_calls) == 1
