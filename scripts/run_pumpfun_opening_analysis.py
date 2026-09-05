"""Thin sequential runner for the durable Pump.fun opening executor."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Callable, Iterable
from src.ops.pumpfun_opening_executor import execute_birth_anchored_opening_analysis

def resolve_rich_birth(root: str | Path, mint: str) -> dict[str, Any]:
    for path in Path(root).glob("*.json"):
        try: value = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError): continue
        if value.get("mint") == mint and value.get("raw_payload", {}).get("txType") == "create": return value
    raise LookupError(f"rich birth not found: {mint}")

def run_mints(mints: Iterable[str], *, operation_id: str, birth_root: str | Path, helius: Any, alchemy: Any, artifact_store: Any, execute: Callable[..., dict[str, Any]] = execute_birth_anchored_opening_analysis) -> list[dict[str, Any]]:
    results=[]
    for mint in mints:
        try:
            results.append(execute(operation_id=operation_id, mint=mint, rich_birth=resolve_rich_birth(birth_root,mint), helius=helius, alchemy=alchemy, artifact_store=artifact_store))
        except Exception as exc:
            results.append({"mint": mint, "status": "RUNNER_FAILURE", "reason": str(exc)})
    return results
