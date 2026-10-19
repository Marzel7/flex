"""Durable, fail-closed runtime feature state for an isolated deployment."""
from __future__ import annotations

import json
import os
from pathlib import Path

FEATURE = "OPERATION_EVIDENCE_PRIORITY_ENABLED"
SCHEMA = "durable-runtime-feature-state.v1"


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True))
    os.replace(temporary, path)


class DurableRuntimeFeatureAdapter:
    """Controls one named feature state file bound to one isolated runtime."""
    def __init__(self, state_path: str | Path, runtime_root: str | Path, feature: str = FEATURE):
        self.path = Path(state_path).resolve(); self.runtime_root = Path(runtime_root).resolve(); self.feature = feature
    def adapter_identity(self) -> dict:
        return {"kind":"durable-runtime-feature.v1","schema":SCHEMA,"feature":self.feature,"state_path":str(self.path),"runtime_root":str(self.runtime_root)}
    @property
    def watchdog_spec(self) -> dict: return self.adapter_identity()
    def _read(self) -> dict:
        return json.loads(self.path.read_text()) if self.path.exists() else {"schema":SCHEMA,"feature":self.feature,"runtime_root":str(self.runtime_root),"state":"OFF"}
    def _valid(self, value: dict) -> bool:
        return value.get("schema")==SCHEMA and value.get("feature")==self.feature and value.get("runtime_root")==str(self.runtime_root)
    def read_state(self) -> str:
        value=self._read(); return value.get("state","OFF") if self._valid(value) else "OFF"
    def set_on(self) -> None: _write(self.path,{**self.adapter_identity(),"state":"ON"})
    def set_off(self) -> None: _write(self.path,{**self.adapter_identity(),"state":"OFF"})
    def verify_off(self) -> bool: return self.read_state()=="OFF"
    @classmethod
    def from_spec(cls, spec: dict) -> "DurableRuntimeFeatureAdapter":
        if spec.get("kind")!="durable-runtime-feature.v1" or spec.get("schema")!=SCHEMA: raise ValueError("invalid durable runtime feature adapter")
        return cls(spec["state_path"],spec["runtime_root"],spec["feature"])
