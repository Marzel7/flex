"""Thin sequential runner for the durable Pump.fun opening executor."""
from __future__ import annotations
import argparse
import json
import os
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable
from src.ops.pumpfun_opening_executor import execute_birth_anchored_opening_analysis
from src.evidence.artifacts import ArtifactStore

class JsonRpcClient:
    def __init__(self, endpoint: str): self._endpoint = endpoint
    def _call(self, method: str, params: list[Any]) -> dict[str, Any]:
        body=json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode()
        request=urllib.request.Request(self._endpoint,data=body,headers={"Content-Type":"application/json"})
        with urllib.request.urlopen(request,timeout=30) as response: return json.loads(response.read())
    def get_transaction(self, signature: str): return self._call("getTransaction",[signature,{"encoding":"json","maxSupportedTransactionVersion":0}])
    def get_block(self, slot: int): return self._call("getBlock",[slot,{"transactionDetails":"full","rewards":False,"maxSupportedTransactionVersion":0}])
    def get_account_info(self, account: str, slot: int): return self._call("getAccountInfo",[account,{"encoding":"base64","minContextSlot":slot}])

def build_helius_client_from_env() -> JsonRpcClient:
    endpoint=os.environ.get("HELIUS_RPC_URL")
    if not endpoint: raise RuntimeError("HELIUS_RPC_URL is required")
    return JsonRpcClient(endpoint)

def build_alchemy_client_from_env() -> JsonRpcClient | None:
    endpoint=os.environ.get("ALCHEMY")
    return JsonRpcClient(endpoint) if endpoint else None

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

def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--mint",action="append",required=True); parser.add_argument("--dry-run",action="store_true"); parser.add_argument("--birth-root",default="database/evidence_platform/production/pumpportal_birth_evidence"); parser.add_argument("--artifact-root",default="database/evidence_platform/production/byzantine_opening_results")
    args=parser.parse_args(argv)
    births=[resolve_rich_birth(args.birth_root,mint) for mint in args.mint]
    if args.dry_run:
        print(json.dumps([{"mint":b["mint"],"creator":b.get("creator"),"signature":b.get("signature")} for b in births])); return 0
    results=run_mints(args.mint,operation_id="d8ee4d7a-fcd6-5a5b-b897-24f6ab56e334",birth_root=args.birth_root,helius=build_helius_client_from_env(),alchemy=build_alchemy_client_from_env(),artifact_store=ArtifactStore(Path(args.artifact_root),enabled=True))
    print(json.dumps(results,default=str)); return 0
if __name__ == "__main__": raise SystemExit(main())
