"""Bounded, resumable evidence acquisition for the approved ByZc7 study.

The store is deliberately filesystem-only: it never touches production tables.
Every cursor advance is preceded by an atomic artifact checkpoint.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

RUN_ID = "BYZC7_EARLY_CREATOR_HISTORY_V2"
BYZC7 = "ByZc7RNeYowEg2jKo2giytWb9WmNyZPrQ1hXhnGSzHTY"
CAPS = {"pages_per_creator": 1, "signatures_per_page": 100, "exact_transactions_per_creator": 25,
        "exact_transaction_batch_size": 5, "max_intermediary_depth": 1}
TERMINAL = {"COMPLETE", "CAP_EXHAUSTED", "PROVIDER_INTERRUPTED", "FAILED_NONRETRYABLE"}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def write_json(path: Path, value: Any) -> str:
    """Atomically replace one artifact; callers only advance after this succeeds."""
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    handle, temporary = tempfile.mkstemp(prefix=".checkpoint-", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(encoded); out.flush(); os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
    return hashlib.sha256(encoded).hexdigest()


class Runner:
    def __init__(self, root: Path, rpc: Callable[[str, list], Any] | None = None):
        self.root = root
        self.audit = root / "docs/audits"
        self.base = self.audit / "byzc7_early_creator_history_v2"
        self.manifest_path = self.audit / "byzc7_early_creator_history_v2_manifest.json"
        self.births = json.loads((self.audit / "byzantine_all_births_denominator.v1.json").read_text())["births"]
        self.creators = sorted({b["creator"] for b in self.births})
        self.rpc = rpc or self._network_rpc
        self.calls = {"signature_history": 0, "exact_transaction": 0, "retries": 0}

    def _network_rpc(self, method: str, params: list) -> Any:
        import re
        env = (self.root / ".env").read_text()
        url = re.search(r'HELIUS_RPC_URL=["\']?([^"\'\n]+)', env).group(1)
        request = urllib.request.Request(url, data=json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode(), headers={"Content-Type":"application/json"})
        body = json.loads(urllib.request.urlopen(request, timeout=30).read())
        if body.get("error"): raise RuntimeError(str(body["error"]))
        return body["result"]

    def inputs(self) -> dict[str, Any]:
        return {"run_id": RUN_ID, "byzc7": BYZC7, "caps": CAPS, "creator_set_sha256": digest(self.creators),
                "births_sha256": digest(self.births), "provider_configuration_identity": "helius-mainnet-json-rpc"}

    def _checkpoint_path(self, creator: str) -> Path: return self.base / "checkpoints" / f"{creator}.json"
    def _result_path(self, creator: str) -> Path: return self.base / "results" / f"{creator}.json"

    def initialise(self) -> dict[str, Any]:
        inputs = self.inputs()
        if self.manifest_path.exists():
            manifest = json.loads(self.manifest_path.read_text())
            for key, value in inputs.items():
                if manifest.get(key) != value: raise ValueError("RUN_INPUT_DIGEST_MISMATCH")
            # An initialization crash may have written the manifest before all state rows.
            for creator in self.creators:
                path = self._checkpoint_path(creator)
                if not path.exists():
                    first = min((b for b in self.births if b["creator"] == creator), key=lambda b: int(b["birth_timestamp"]) if str(b["birth_timestamp"]).isdigit() else 2**63)
                    write_json(path, {"creator": creator, "first_birth": first, "state": "NOT_STARTED", "pages_completed": 0, "signatures_examined": 0, "exact_transactions_fetched": 0, "before_signature": None, "page_checkpoints": [], "batch_checkpoints": [], "candidate_relationships": [], "qualified_earliest_relationship": None, "qualified_earliest_material_capital_event": None, "last_checkpoint_at": int(time.time()), "error": None})
            return manifest
        manifest = {**inputs, "started_at": int(time.time()), "state": "RUNNING", "completed_creator_count": 0,
                    "failed_run_evidence_verdict": "NO_CONCLUSION", "page_checkpoint_before_next_rpc": True,
                    "completed_creators_refetched": 0, "creator_count": len(self.creators)}
        write_json(self.manifest_path, manifest)
        for creator in self.creators:
            first = min((b for b in self.births if b["creator"] == creator), key=lambda b: int(b["birth_timestamp"]) if str(b["birth_timestamp"]).isdigit() else 2**63)
            write_json(self._checkpoint_path(creator), {"creator": creator, "first_birth": first, "state": "NOT_STARTED",
                "pages_completed": 0, "signatures_examined": 0, "exact_transactions_fetched": 0, "before_signature": None,
                "page_checkpoints": [], "batch_checkpoints": [], "candidate_relationships": [], "qualified_earliest_relationship": None,
                "qualified_earliest_material_capital_event": None, "last_checkpoint_at": int(time.time()), "error": None})
        return manifest

    def _checkpoint(self, creator: str) -> dict[str, Any]: return json.loads(self._checkpoint_path(creator).read_text())
    def _save_checkpoint(self, c: dict[str, Any]) -> None:
        c["last_checkpoint_at"] = int(time.time()); write_json(self._checkpoint_path(c["creator"]), c)

    @staticmethod
    def _classify(tx: dict[str, Any], creator: str, signature: str) -> dict[str, Any] | None:
        keys = [x.get("pubkey") if isinstance(x, dict) else x for x in tx.get("transaction",{}).get("message",{}).get("accountKeys",[])]
        if BYZC7 not in keys: return None
        transfers = []
        for group in (tx.get("transaction",{}).get("message",{}).get("instructions",[]), *(x.get("instructions",[]) for x in tx.get("meta",{}).get("innerInstructions",[]))):
            for ix in group:
                info = ix.get("parsed",{}).get("info",{}) if isinstance(ix, dict) else {}
                source, destination = info.get("source"), info.get("destination")
                lamports = info.get("lamports")
                if source == BYZC7 and destination == creator: transfers.append(("BYZC7_DIRECT_CAPITAL_TO_CREATOR", lamports))
                if source == creator and destination == BYZC7: transfers.append(("CREATOR_DIRECT_CAPITAL_TO_BYZC7", lamports))
        kind, amount = transfers[0] if transfers else ("COPRESENCE_ONLY", None)
        return {"signature": signature, "slot": tx.get("slot"), "block_time": tx.get("blockTime"), "relationship_class": kind,
                "amount_lamports": amount, "direction": "TO_CREATOR" if kind.startswith("BYZC7") else "TO_BYZC7" if kind.startswith("CREATOR") else None,
                "material_capital": bool(amount and amount > 0), "qualified": kind != "COPRESENCE_ONLY"}

    def _raw(self, signature: str, tx: dict[str, Any]) -> str:
        path = self.base / "raw" / f"{signature}.json"
        if path.exists():
            if digest(json.loads(path.read_text())) != digest(tx): raise ValueError("RAW_ARTIFACT_INTEGRITY_MISMATCH")
        else: write_json(path, tx)
        return str(path.relative_to(self.audit))

    def _finish(self, c: dict[str, Any]) -> None:
        rel = sorted((x for x in c["candidate_relationships"] if x["qualified"]), key=lambda x: (x.get("block_time") or 2**63, x["signature"]))
        material = [x for x in rel if x["material_capital"]]
        c["qualified_earliest_relationship"] = rel[0] if rel else None
        c["qualified_earliest_material_capital_event"] = material[0] if material else None
        c["state"] = "CAP_EXHAUSTED" if c["pages_completed"] >= CAPS["pages_per_creator"] else "COMPLETE"
        result = {k:c[k] for k in ("creator","first_birth","state","pages_completed","signatures_examined","exact_transactions_fetched","candidate_relationships","qualified_earliest_relationship","qualified_earliest_material_capital_event")}
        result["cap_exhausted"] = c["state"] == "CAP_EXHAUSTED"; result["early_relationship_unresolved_within_bound"] = not bool(rel)
        result["evidence_refs"] = [x["raw_ref"] for x in c["candidate_relationships"]]
        write_json(self._result_path(c["creator"]), result)  # materialize before terminal checkpoint
        self._save_checkpoint(c)

    def acquire(self) -> dict[str, Any]:
        manifest = self.initialise()
        for creator in self.creators:
            c = self._checkpoint(creator)
            if c["state"] in {"COMPLETE", "CAP_EXHAUSTED", "FAILED_NONRETRYABLE"}: continue
            c["state"] = "ACQUIRING_HISTORY"; self._save_checkpoint(c)
            try:
                if c["pages_completed"]:
                    # A crash after page persistence resumes from the durable page, never refetches it.
                    signatures = c["page_checkpoints"][-1]["returned_signatures"]
                else:
                    request_before = c["before_signature"]
                    page = self.rpc("getSignaturesForAddress", [creator, {"limit": CAPS["signatures_per_page"], "before": request_before}])
                    self.calls["signature_history"] += 1
                    signatures = [x["signature"] for x in page if x.get("signature")]
                    c["pages_completed"] += 1; c["signatures_examined"] += len(signatures); c["before_signature"] = signatures[-1] if signatures else request_before
                    c["page_checkpoints"].append({"page_number":c["pages_completed"],"request_before_signature":request_before,"returned_signatures":signatures,"earliest_event_reached":page[-1] if page else None,"cap_remaining":CAPS["pages_per_creator"]-c["pages_completed"]})
                    self._save_checkpoint(c)  # page checkpoint before any subsequent RPC
                chosen = signatures[-CAPS["exact_transactions_per_creator"]:]
                c["state"] = "DECODING_TRANSACTIONS"; self._save_checkpoint(c)
                done = {s for b in c["batch_checkpoints"] for s in b["requested_signatures"] if s not in {x["signature"] for x in b["failed_retryable"]}}
                for offset in range(0, len(chosen), CAPS["exact_transaction_batch_size"]):
                    requested = chosen[offset:offset+CAPS["exact_transaction_batch_size"]]; normalized=[]; failures=[]
                    for signature in requested:
                        if signature in done: continue
                        try:
                            tx = self.rpc("getTransaction", [signature, {"encoding":"jsonParsed","maxSupportedTransactionVersion":0}]); self.calls["exact_transaction"] += 1
                            raw_ref = self._raw(signature, tx)
                            event = self._classify(tx or {}, creator, signature)
                            if event: event["raw_ref"] = raw_ref; normalized.append(event)
                        except (urllib.error.URLError, TimeoutError) as err: failures.append({"signature":signature,"error":str(err),"retryable":True})
                    c["candidate_relationships"] += [x for x in normalized if x not in c["candidate_relationships"]]
                    c["exact_transactions_fetched"] += len(requested) - len(failures)
                    c["batch_checkpoints"].append({"requested_signatures":requested,"fetched_signatures":[x["signature"] for x in normalized],"normalized":normalized,"failed_retryable":failures})
                    self._save_checkpoint(c)
                    if failures: c["state"]="PROVIDER_INTERRUPTED"; c["error"]=failures; self._save_checkpoint(c); break
                if c["state"] != "PROVIDER_INTERRUPTED": self._finish(c)
            except (urllib.error.URLError, TimeoutError) as err:
                c["state"]="PROVIDER_INTERRUPTED"; c["error"]=str(err); self._save_checkpoint(c)
            except Exception as err:
                c["state"]="FAILED_NONRETRYABLE"; c["error"]=str(err); self._save_checkpoint(c)
        states = [self._checkpoint(x)["state"] for x in self.creators]
        manifest["state"] = "COMPLETE" if all(x in {"COMPLETE", "CAP_EXHAUSTED", "FAILED_NONRETRYABLE"} for x in states) else "INTERRUPTED"; manifest["completed_creator_count"] = sum(x in {"COMPLETE","CAP_EXHAUSTED"} for x in states); manifest["calls"] = self.calls
        write_json(self.manifest_path, manifest)
        return manifest
