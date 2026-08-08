#!/usr/bin/env python3
"""Print the deterministic, read-only OIP v2 coverage baseline."""
import json
from pathlib import Path

from src.intelligence.coverage import measure

if __name__ == "__main__":
    print(json.dumps(measure(Path(__file__).resolve().parents[1]), sort_keys=True, indent=2))
