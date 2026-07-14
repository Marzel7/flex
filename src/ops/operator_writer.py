"""Write-only boundary for the canonical operator model."""
from __future__ import annotations

import os
from typing import Any, Callable

from src.core.database_write_service import database_write_service, execute_script
from src.ops.operator_model import DDL


class OperatorWriter:
    """Submit operator mutations to the shared database write service."""

    def __init__(self, db_path: str, *, write_service: Any = None) -> None:
        self._path = db_path
        self._service = write_service or database_write_service
        self._database = f"operations:{os.path.realpath(db_path)}"
        self._service.register_database(self._database, db_path)

    def transaction(self, command: str, operation: Callable) -> Any:
        return self._service.submit(self._database, command, operation)

    def initialize_schema(self) -> None:
        """Explicit startup/migration operation; never called by readers."""
        self.transaction("operator-schema-upgrade", lambda conn: execute_script(conn, DDL))


def initialize_operator_schema(db_path: str, *, write_service: Any = None) -> None:
    OperatorWriter(db_path, write_service=write_service).initialize_schema()

