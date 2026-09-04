"""Current post-dense-history market-data schema contract.

Historical PSI0/EB0 contracts intentionally remain separate: they replay the
snapshot-era schema that existed at their recorded boundary.  This successor
contract is the production-facing compatibility surface after Stage 3.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import sqlite3
from pathlib import Path

CONTRACT_VERSION = "current-market-data.v1"


@dataclass(frozen=True)
class CurrentMarketDataContract:
    version: str
    required_relations: tuple[str, ...]
    required_token_analysis_columns: tuple[str, ...]
    digest: str


def build_current_market_data_contract() -> CurrentMarketDataContract:
    required_relations = ("token_analysis", "token_market_cap_peaks")
    required_columns = (
        "mint", "first_observed_mc", "first_observed_price",
        "first_observed_at", "first_observed_source",
        "first_observed_confidence",
    )
    body = {"version": CONTRACT_VERSION, "relations": required_relations,
            "token_analysis_columns": required_columns}
    return CurrentMarketDataContract(
        CONTRACT_VERSION, required_relations, required_columns,
        sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
    )


def validate_current_market_data_contract(db_path: str | Path) -> CurrentMarketDataContract:
    contract = build_current_market_data_contract()
    conn = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = set(contract.required_relations) - tables
        if missing:
            raise RuntimeError(f"CURRENT_MARKET_DATA_REQUIRED_RELATION_MISSING:{sorted(missing)}")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(token_analysis)")}
        missing_columns = set(contract.required_token_analysis_columns) - columns
        if missing_columns:
            raise RuntimeError(f"CURRENT_MARKET_DATA_REQUIRED_COLUMN_MISSING:{sorted(missing_columns)}")
        return contract
    finally:
        conn.close()
