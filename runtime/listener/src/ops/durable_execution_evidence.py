"""Append-safe phase evidence for destructive storage maintenance runs."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


class PhaseEvidenceStore:
    """Persists each lifecycle phase before proceeding to the next one."""

    def __init__(self, root: str | Path, run_id: str):
        self.root = Path(root) / run_id
        self.root.mkdir(parents=True, exist_ok=True)
        self._sequence = len(list(self.root.glob("*.json")))

    def emit(self, phase: str, **payload: object) -> Path:
        self._sequence += 1
        record = {
            "sequence": self._sequence,
            "phase": phase,
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **payload,
        }
        target = self.root / f"{self._sequence:03d}_{phase}.json"
        temporary = target.with_suffix(".tmp")
        with temporary.open("w") as handle:
            json.dump(record, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        return target

    def recovery(self) -> dict:
        records = [json.loads(path.read_text()) for path in sorted(self.root.glob("*.json"))]
        phases = [record["phase"] for record in records]
        return {
            "records": records,
            "phases": phases,
            "complete": "COMPLETE" in phases,
            "retirement_committed": "HOT_RETIRE_COMMIT" in phases,
            "selected_manifest": next((r.get("manifest_path") for r in records if r["phase"] == "SELECTED"), None),
            "cold_destination": next((r.get("cold_destination") for r in records if r["phase"] == "PUBLISHED"), None),
        }
