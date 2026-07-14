#!/usr/bin/env python3
"""
Standalone script: create WATCHTOWER tables and seed static wallet tiers.

Run once before starting Gunicorn, or after adding new tables/migrations.
Idempotent — safe to run multiple times.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.db_locking import db_connect
from src.core.watchtower_init import ensure_watchtower_tables, seed_wallet_tiers, DB_PATH

# _WT_INFRA_ROLES lives in main.py (used by 50+ functions there).
# Import it directly rather than duplicating it here.
from src.core.watchtower_init import DB_PATH  # already imported above

# Import infra roles from the canonical source without triggering Flask startup.
# main.py cannot be imported safely (it creates the Flask app at module level),
# so we import the roles lazily via a targeted exec of just the dict literal.
# This is intentionally narrow — if _WT_INFRA_ROLES moves, update this import.
def _load_infra_roles():
    """Load _WT_INFRA_ROLES from main.py without importing the Flask app."""
    import ast, os as _os
    main_path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                              "src", "core", "main.py")
    src = open(main_path).read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "_WT_INFRA_ROLES":
                    return ast.literal_eval(node.value)
    raise RuntimeError("_WT_INFRA_ROLES not found in main.py")


if __name__ == "__main__":
    print("[ensure_watchtower_tables] Starting…", flush=True)
    try:
        infra_roles = _load_infra_roles()
        print(f"[ensure_watchtower_tables] Loaded {len(infra_roles)} infra roles", flush=True)

        conn = db_connect(DB_PATH, timeout=60)
        conn.execute("PRAGMA busy_timeout=55000")

        print("[ensure_watchtower_tables] Creating/verifying tables…", flush=True)
        ensure_watchtower_tables(conn)
        print("[ensure_watchtower_tables] Tables OK", flush=True)

        print("[ensure_watchtower_tables] Seeding wallet tiers…", flush=True)
        n = seed_wallet_tiers(conn, infra_roles)
        print(f"[ensure_watchtower_tables] Tier seed attempted for {n} wallets (INSERT OR IGNORE)", flush=True)

        conn.close()
        print("[ensure_watchtower_tables] Done.", flush=True)
        sys.exit(0)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[ensure_watchtower_tables] FAILED: {e}", flush=True)
        sys.exit(1)
