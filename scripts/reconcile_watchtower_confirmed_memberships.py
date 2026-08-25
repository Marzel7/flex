#!/usr/bin/env python3
"""Project strict WATCHTOWER_CONFIRMED rows missing operator membership."""
from __future__ import annotations

import argparse
import json
import sqlite3

from src.core.watchtower_registry_promotion import reconcile_confirmed_watchtower_memberships


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ops-db", default="database/wt_ops_v2.db")
    parser.add_argument("--core-db", default="database/flex_complete_database.db")
    args = parser.parse_args()
    conn = sqlite3.connect(args.ops_db)
    conn.row_factory = sqlite3.Row
    try:
        result = reconcile_confirmed_watchtower_memberships(conn, core_db_path=args.core_db)
        conn.commit()
    finally:
        conn.close()
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
