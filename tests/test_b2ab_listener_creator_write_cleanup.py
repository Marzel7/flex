"""B2AB regression: the listener's thread-owned creator writer always closes."""
import ast
from pathlib import Path


SOURCE = Path("src/core/pumpfun_curve_listener.py")


def test_creator_write_has_owner_thread_finally_close():
    tree = ast.parse(SOURCE.read_text())
    outer = next(node for node in ast.walk(tree)
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name == "_update_token_entry_with_creator")
    writer = next(node for node in outer.body
                  if isinstance(node, ast.FunctionDef) and node.name == "_update_creator_write")
    guarded = next(node for node in writer.body if isinstance(node, ast.Try))
    assert any(isinstance(node, ast.Expr)
               and isinstance(node.value, ast.Call)
               and isinstance(node.value.func, ast.Attribute)
               and node.value.func.attr == "close"
               for node in guarded.finalbody)
