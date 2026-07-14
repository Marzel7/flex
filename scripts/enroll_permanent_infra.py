#!/usr/bin/env python3
"""
Standalone script: enroll permanent WATCHTOWER infrastructure addresses.

Run once at deployment or after adding new permanent infra addresses.
Idempotent — safe to run multiple times.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.apis.webhook_integration import _enroll_permanent_infra

if __name__ == "__main__":
    print("[enroll_permanent_infra] Starting enrollment…", flush=True)
    try:
        _enroll_permanent_infra()
        print("[enroll_permanent_infra] Done.", flush=True)
        sys.exit(0)
    except Exception as e:
        print(f"[enroll_permanent_infra] FAILED: {e}", flush=True)
        sys.exit(1)
