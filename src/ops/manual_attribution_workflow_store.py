"""Dedicated compact storage boundary for manual V1 workflow state."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path


WORKFLOW_DB_VERSION = "MANUAL_ATTRIBUTION_WORKFLOW_DB_V1"
WORKFLOW_DB_AUTHORITY = "NON_AUTHORITATIVE_MANUAL_V1_PROPOSAL_AND_DECISION_STATE"
DEFAULT_WORKFLOW_DB_PATH = Path(__file__).resolve().parents[2] / "database" / "manual_operation_attribution.db"


def workflow_db_path() -> str:
    return os.environ.get("MANUAL_ATTRIBUTION_WORKFLOW_DB_PATH", str(DEFAULT_WORKFLOW_DB_PATH))


def connect(path: str | None = None, *, timeout: float = 5) -> sqlite3.Connection:
    target = path or workflow_db_path()
    conn = sqlite3.connect(target, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def connect_readonly(path: str | None = None, *, timeout: float = 2) -> sqlite3.Connection:
    target = path or workflow_db_path()
    conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn
