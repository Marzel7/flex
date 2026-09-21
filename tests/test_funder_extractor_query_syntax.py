"""Offline regression for the API's lazy funder extractor import/query path."""

import ast
import sqlite3
from pathlib import Path
import tempfile


SOURCE = Path(__file__).resolve().parents[1] / "src/extractors/funder_helius_extractor.py"


def test_funder_extractor_parses_and_reads_scratch_rows():
    tree = ast.parse(SOURCE.read_text())
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "get_creator_funders"
    )
    with tempfile.TemporaryDirectory() as directory:
        db_path = Path(directory) / "scratch.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute("CREATE TABLE creator_funders (creator_address TEXT, funder_address TEXT, amount_sol REAL)")
            conn.executemany(
                "INSERT INTO creator_funders VALUES (?, ?, ?)",
                [("creator", "small", 1.0), ("creator", "large", 2.0), ("other", "skip", 3.0)],
            )
        namespace = {"sqlite3": sqlite3, "DB_PATH": str(db_path)}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), "exec"), namespace)
        assert namespace["get_creator_funders"]("creator") == [("large", 2.0), ("small", 1.0)]
