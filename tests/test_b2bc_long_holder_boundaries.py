import ast
from pathlib import Path

from src.utils.db_locking import managed_db_connect


ROOT = Path(__file__).resolve().parents[1]
LISTENER = ROOT / "src" / "core" / "pumpfun_curve_listener.py"


def _price_worker_function() -> ast.FunctionDef:
    tree = ast.parse(LISTENER.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_sync_price_db_work":
            return node
    raise AssertionError("_sync_price_db_work not found")


def test_price_analysis_closes_read_only_connection_before_write_scope():
    function = _price_worker_function()
    connects = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "db_connect"
    ]
    assert len(connects) == 2

    read_connect, write_connect = sorted(connects, key=lambda node: node.lineno)
    read_only = next(
        keyword.value
        for keyword in read_connect.keywords
        if keyword.arg == "read_only"
    )
    assert isinstance(read_only, ast.Constant) and read_only.value is True
    assert all(keyword.arg != "read_only" for keyword in write_connect.keywords)

    read_close = next(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "read_conn"
        and node.func.attr == "close"
    )
    assert read_connect.lineno < read_close.lineno < write_connect.lineno


def test_price_write_scope_contains_only_update_commit_and_close():
    function = _price_worker_function()
    write_calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "write_conn"
    ]
    assert sorted(node.func.attr for node in write_calls) == ["close", "commit", "execute"]

    execute = next(node for node in write_calls if node.func.attr == "execute")
    sql = execute.args[0]
    assert isinstance(sql, ast.Constant)
    assert sql.value.strip().upper().startswith("UPDATE TOKEN_ANALYSIS")


def test_managed_connection_preserves_external_caller(tmp_path):
    db_path = str(tmp_path / "caller.db")

    def caller_of_interest():
        with managed_db_connect(db_path, timeout=2) as conn:
            return conn._db_caller

    caller = caller_of_interest()
    assert "in caller_of_interest" in caller
    assert "in managed_db_connect" not in caller


def test_managed_read_only_connection_preserves_external_caller(tmp_path):
    db_path = str(tmp_path / "caller-ro.db")
    with managed_db_connect(db_path, timeout=2) as conn:
        conn.execute("CREATE TABLE ready(id INTEGER PRIMARY KEY)")
        conn.commit()

    def read_caller_of_interest():
        with managed_db_connect(db_path, timeout=2, read_only=True) as conn:
            conn.execute("SELECT id FROM ready").fetchall()
            return conn._db_caller

    caller = read_caller_of_interest()
    assert "in read_caller_of_interest" in caller
    assert "in managed_db_connect" not in caller
