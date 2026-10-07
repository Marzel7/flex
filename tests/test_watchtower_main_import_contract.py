"""Static import contracts for the Watchtower application entry point."""

import ast
from pathlib import Path


def test_main_does_not_import_removed_dashboard_market_cap_symbol():
    """The dashboard module does not export this legacy profitability constant."""
    main_path = Path(__file__).parents[1] / "src" / "core" / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"))

    stale_imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "src.core.flex_dashboard_routes"
        and any(alias.name == "MIN_LIVE_MARKET_CAP" for alias in node.names)
    ]

    assert stale_imports == []
