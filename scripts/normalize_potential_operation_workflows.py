#!/usr/bin/env python3
"""Explicit, bounded normalization for Potential Operations workflow metadata."""
from __future__ import annotations
import argparse
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.core.db import OPS_DB_PATH
from src.ops.potential_operations import normalize_potential_operation_workflows

parser=argparse.ArgumentParser()
parser.add_argument("--apply", action="store_true")
args=parser.parse_args()
conn=sqlite3.connect(str(OPS_DB_PATH))
try:
    print(normalize_potential_operation_workflows(conn, apply=args.apply))
finally:
    conn.close()
