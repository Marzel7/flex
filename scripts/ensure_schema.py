#!/usr/bin/env python3
"""
Standalone script: apply pending schema migrations to flex_complete_database.db.

Run once before starting Gunicorn, or after adding new migrations.
Idempotent — safe to run multiple times.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.schema_init import ensure_schema

if __name__ == "__main__":
    print("[ensure_schema] Starting schema check…", flush=True)
    try:
        ensure_schema()
        print("[ensure_schema] Done.", flush=True)
        sys.exit(0)
    except Exception as e:
        print(f"[ensure_schema] FAILED: {e}", flush=True)
        sys.exit(1)
