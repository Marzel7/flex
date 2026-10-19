"""Independent absolute-deadline OFF watchdog for the bounded dummy feature."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

TERMINAL = {"COMPLETED", "ABORTED", "RECOVERY_FORCED_OFF"}
ADAPTER_KIND = "durable-dummy-feature.v1"
WATCHDOG_VERSION = "independent-deadline-off-watchdog.v1"


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True))
    os.replace(temporary, path)


def _read(path: Path) -> dict:
    return json.loads(path.read_text())


class DurableDummyFeatureAdapter:
    """A file-backed test adapter; never a binding for a production feature."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    @property
    def watchdog_spec(self) -> dict:
        return {"kind": ADAPTER_KIND, "path": str(self.path.resolve())}

    def _load(self) -> dict:
        return _read(self.path) if self.path.exists() else {"state": "OFF"}

    def read_state(self) -> str:
        return self._load().get("state", "OFF")

    def set_on(self) -> None:
        data = self._load(); data["state"] = "ON"; _write(self.path, data)

    def set_off(self) -> None:
        data = self._load(); data["state"] = "OFF"; _write(self.path, data)

    def verify_off(self) -> bool:
        return not self._load().get("verify_fails", False) and self.read_state() == "OFF"


def _persist_watchdog(state_path: Path, state: dict, watchdog: dict) -> None:
    state["watchdog"] = watchdog
    _write(state_path, state)


def run(state_path: str | Path, observation_id: str, acknowledgement: str | Path | None = None) -> int:
    state_path = Path(state_path)
    acknowledgement_path = Path(acknowledgement) if acknowledgement else None
    state = _read(state_path)
    watchdog = state.get("watchdog") or {}
    adapter_spec = watchdog.get("adapter")
    valid = (state.get("id") == observation_id and watchdog.get("version") == WATCHDOG_VERSION
             and watchdog.get("state_store") == str(state_path.resolve())
             and state.get("final_state") == "OFF" and isinstance(adapter_spec, dict)
             and adapter_spec.get("kind") in {ADAPTER_KIND, "durable-runtime-feature.v1"})
    if not valid:
        _persist_watchdog(state_path, state, {**watchdog, "state": "REJECTED", "error": "VALIDATION_FAILED", "pid": os.getpid()})
        return 2
    if adapter_spec["kind"] == ADAPTER_KIND:
        if not adapter_spec.get("path"):
            return 2
        adapter = DurableDummyFeatureAdapter(adapter_spec["path"])
    else:
        from src.ops.durable_runtime_feature_adapter import DurableRuntimeFeatureAdapter
        try:
            adapter = DurableRuntimeFeatureAdapter.from_spec(adapter_spec)
        except (KeyError, ValueError):
            return 2
    if state.get("state") in TERMINAL and state.get("verified_off"):
        _persist_watchdog(state_path, state, {**watchdog, "state": "TERMINAL_NOOP", "pid": os.getpid()})
        return 0
    armed_at = time.time()
    armed = {**watchdog, "state": "ARMED", "pid": os.getpid(), "armed_at": armed_at,
             "observation_id": observation_id, "deadline_observed": state["deadline"]}
    _persist_watchdog(state_path, state, armed)
    if acknowledgement_path:
        _write(acknowledgement_path, {"state": "ARMED", "observation_id": observation_id, "pid": os.getpid()})
    while time.time() < state["deadline"]:
        time.sleep(min(0.05, max(0.0, state["deadline"] - time.time())))
    current = _read(state_path)
    action = {**(current.get("watchdog") or armed), "state": "OFF_ATTEMPTED", "pid": os.getpid(),
              "off_actor": "WATCHDOG", "reason": "ABSOLUTE_DEADLINE", "off_attempt_at": time.time(),
              "deadline_observed": state["deadline"]}
    adapter.set_off()
    if adapter.verify_off():
        action.update({"state": "COMPLETED", "success": True, "off_verified_at": time.time()})
        current.update({"state": "COMPLETED", "stop_reason": "ABSOLUTE_DEADLINE", "verified_off": True,
                        "finalized_at": action["off_verified_at"]})
        _persist_watchdog(state_path, current, action)
        return 0
    action.update({"state": "OFF_VERIFICATION_FAILED", "success": False, "off_verification_failed_at": time.time()})
    _persist_watchdog(state_path, current, action)
    return 3


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True)
    parser.add_argument("--observation-id", required=True)
    parser.add_argument("--ack")
    return run(parser.parse_args().state, parser.parse_args().observation_id, parser.parse_args().ack)


if __name__ == "__main__":
    raise SystemExit(main())
