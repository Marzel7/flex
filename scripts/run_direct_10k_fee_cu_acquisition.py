"""
Bounded raw-transaction acquisition for the 93 known-signature DIRECT_10K_CREATOR_PROVISIONING
population (84 STRICT + 6 QVtW + 3 ALTERNATE), to fill the fee/Compute-Budget evidence gap
identified in creator_launch_provisioning_fee_compute_fingerprint.v1.json.

Known-signature only: getTransaction per signature, NEVER getSignaturesForAddress.
Cache-before-classify: full raw provider response persisted before any feature extraction.
Uses HELIUS_TEMP_API_KEY per repo RPC-investigation-discipline convention.
"""
from __future__ import annotations

import base58
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.ops.rpc_acquisition_checkpoint import DurableAuthorizationLedger

ROOT = Path(__file__).resolve().parents[1]
AUDIT_DIR = ROOT / "docs" / "audits"
EVIDENCE_V1 = AUDIT_DIR / "creator_launch_provisioning_fee_compute_evidence.v1.jsonl"
POPULATION_FILE = AUDIT_DIR / "direct_10k_creator_provisioning_shadow_qualification.v1.json"
SHAPES_FILE = AUDIT_DIR / "potential_operations_6437_defining_transaction_shapes.v1.jsonl"
LEDGER_PATH = AUDIT_DIR / "direct_10k_fee_cu_acquisition_run_ledger.v1.json"
RAW_CACHE_PATH = AUDIT_DIR / "direct_10k_fee_cu_transaction_cache.v1.jsonl"

RUN_ID = "direct-10k-fee-cu-acquisition-v1"
PURPOSE = "DIRECT_10K_FEE_CU_RAW_TX_ACQUISITION"
CANDIDATE_ID = "direct_10k_creator_provisioning_93_population"
AUTHORIZED_MAX_CALLS = 150

_HELIUS_KEY = os.environ.get("HELIUS_TEMP_API_KEY") or os.environ.get("HELIUS_API_KEY", "")
_HELIUS_RPC = f"https://mainnet.helius-rpc.com/?api-key={_HELIUS_KEY}"

COMPUTE_BUDGET_PROGRAM_ID = "ComputeBudget111111111111111111111111111111"

# ---------------------------------------------------------------------------
# ComputeBudget instruction decoding (validated against real cached instruction
# data in canonical_birth_transaction_cache.v1.jsonl: disc 0x02 = SetComputeUnitLimit
# u32 LE, disc 0x03 = SetComputeUnitPrice u64 LE).
# ---------------------------------------------------------------------------
CB_REQUEST_UNITS_DEPRECATED = 0
CB_REQUEST_HEAP_FRAME = 1
CB_SET_COMPUTE_UNIT_LIMIT = 2
CB_SET_COMPUTE_UNIT_PRICE = 3
CB_SET_LOADED_ACCOUNTS_DATA_SIZE_LIMIT = 4


def decode_compute_budget_instruction(raw_bytes: bytes) -> dict:
    if not raw_bytes:
        return {"kind": "UNSUPPORTED_EMPTY", "raw_len": 0}
    disc = raw_bytes[0]
    try:
        if disc == CB_SET_COMPUTE_UNIT_LIMIT and len(raw_bytes) >= 5:
            (val,) = struct.unpack("<I", raw_bytes[1:5])
            return {"kind": "SetComputeUnitLimit", "compute_unit_limit": val}
        if disc == CB_SET_COMPUTE_UNIT_PRICE and len(raw_bytes) >= 9:
            (val,) = struct.unpack("<Q", raw_bytes[1:9])
            return {"kind": "SetComputeUnitPrice", "compute_unit_price_microlamports": val}
        if disc == CB_REQUEST_HEAP_FRAME and len(raw_bytes) >= 5:
            (val,) = struct.unpack("<I", raw_bytes[1:5])
            return {"kind": "RequestHeapFrame", "heap_frame_bytes": val}
        if disc == CB_SET_LOADED_ACCOUNTS_DATA_SIZE_LIMIT and len(raw_bytes) >= 5:
            (val,) = struct.unpack("<I", raw_bytes[1:5])
            return {"kind": "SetLoadedAccountsDataSizeLimit", "bytes_limit": val}
        if disc == CB_REQUEST_UNITS_DEPRECATED:
            return {"kind": "RequestUnits_deprecated", "raw_len": len(raw_bytes)}
    except struct.error:
        return {"kind": "UNSUPPORTED_MALFORMED", "discriminator": disc, "raw_len": len(raw_bytes)}
    return {"kind": "UNSUPPORTED_DISCRIMINATOR", "discriminator": disc, "raw_len": len(raw_bytes)}


def _instr_raw_bytes(instr: dict) -> bytes | None:
    """Extract raw instruction data bytes from a jsonParsed-shape instruction that
    Helius does NOT parse for ComputeBudget (comes back as base58 'data' field)."""
    data = instr.get("data")
    if not isinstance(data, str):
        return None
    try:
        return base58.b58decode(data)
    except Exception:
        return None


def extract_compute_budget(outer_instructions: list[dict]) -> dict:
    cb_instrs = []
    cu_limit = None
    cu_price = None
    heap_frame = None
    loaded_accounts_limit = None
    for instr in outer_instructions:
        if instr.get("programId") != COMPUTE_BUDGET_PROGRAM_ID:
            continue
        raw = _instr_raw_bytes(instr)
        if raw is None:
            cb_instrs.append({"kind": "UNSUPPORTED_REPRESENTATION"})
            continue
        decoded = decode_compute_budget_instruction(raw)
        cb_instrs.append(decoded)
        if decoded.get("kind") == "SetComputeUnitLimit":
            cu_limit = decoded["compute_unit_limit"]
        elif decoded.get("kind") == "SetComputeUnitPrice":
            cu_price = decoded["compute_unit_price_microlamports"]
        elif decoded.get("kind") == "RequestHeapFrame":
            heap_frame = decoded["heap_frame_bytes"]
        elif decoded.get("kind") == "SetLoadedAccountsDataSizeLimit":
            loaded_accounts_limit = decoded["bytes_limit"]
    return {
        "compute_budget_instructions": cb_instrs,
        "compute_unit_limit": cu_limit,
        "compute_unit_price_microlamports": cu_price,
        "request_heap_frame": heap_frame,
        "loaded_accounts_data_size_limit": loaded_accounts_limit,
    }


def extract_features(tx_result: dict, funder: str | None) -> dict:
    meta = tx_result.get("meta") or {}
    txn = tx_result.get("transaction") or {}
    message = txn.get("message") or {}
    account_keys = message.get("accountKeys") or []
    outer_instructions = message.get("instructions") or []
    inner_instructions = meta.get("innerInstructions") or []
    signers = [k.get("pubkey") for k in account_keys if k.get("signer")]
    fee_payer = signers[0] if signers else None
    cb = extract_compute_budget(outer_instructions)
    inner_count = sum(len(grp.get("instructions") or []) for grp in inner_instructions)
    return {
        "slot": tx_result.get("slot"),
        "block_time": tx_result.get("blockTime"),
        "transaction_version": tx_result.get("version"),
        "meta_fee_lamports": meta.get("fee"),
        "signer_count": len(signers),
        "fee_payer": fee_payer,
        "funder_is_fee_payer": (fee_payer == funder) if (fee_payer and funder) else None,
        "outer_instruction_count": len(outer_instructions),
        "inner_instruction_count": inner_count,
        **cb,
    }


def rpc_get_transaction(signature: str) -> tuple[dict | None, str, str | None]:
    """Returns (result_or_None, outcome_code, error_message)."""
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "getTransaction",
        "params": [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
    }).encode()
    req = urllib.request.Request(
        _HELIUS_RPC, data=body,
        headers={"Content-Type": "application/json", "User-Agent": "direct10k-fee-cu/1.0"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=20)
        payload = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return None, "RATE_LIMIT", str(e)
        return None, "TERMINAL_PROVIDER_ERROR", f"HTTP {e.code}: {e}"
    except (urllib.error.URLError, TimeoutError) as e:
        return None, "TRANSIENT_TRANSPORT_FAILURE", str(e)
    except Exception as e:
        return None, "TERMINAL_PROVIDER_ERROR", str(e)

    if "error" in payload:
        err = payload["error"]
        msg = err.get("message", "") if isinstance(err, dict) else str(err)
        if "version" in msg.lower():
            return None, "UNSUPPORTED_VERSION", msg
        return None, "TERMINAL_PROVIDER_ERROR", msg

    result = payload.get("result")
    if result is None:
        return None, "NULL_RESULT", None
    return result, "SUCCESS", None


def _atomic_append_jsonl(path: Path, row: dict):
    line = json.dumps(row, sort_keys=True) + "\n"
    with open(path, "a") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def load_raw_cache_index() -> dict:
    idx = {}
    if RAW_CACHE_PATH.exists():
        for line in RAW_CACHE_PATH.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            idx[row["signature"]] = row
    return idx


def main():
    if not _HELIUS_KEY:
        print("FATAL: no HELIUS_TEMP_API_KEY or HELIUS_API_KEY in environment", file=sys.stderr)
        sys.exit(1)

    population = json.loads(POPULATION_FILE.read_text())
    mint_cohort = {r["mint"]: r["cohort"] for r in population["results"]}

    shapes = {}
    for line in SHAPES_FILE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get("mint") in mint_cohort:
            shapes[r["mint"]] = r

    assert len(shapes) == 93, f"expected 93 shape rows, got {len(shapes)}"

    unique_sigs = {}
    for mint, cohort in mint_cohort.items():
        r = shapes[mint]
        sig = r["signature"]
        unique_sigs.setdefault(sig, []).append((mint, cohort, r["funder"], r["creator"]))

    print(f"UNIQUE_SIGNATURE_COUNT={len(unique_sigs)}")

    if LEDGER_PATH.exists():
        ledger = DurableAuthorizationLedger.resume(LEDGER_PATH, RUN_ID, PURPOSE, CANDIDATE_ID)
        print(f"RESUMED ledger, remaining={ledger.remaining}")
    else:
        ledger = DurableAuthorizationLedger.new(LEDGER_PATH, RUN_ID, PURPOSE, CANDIDATE_ID, AUTHORIZED_MAX_CALLS)
        print(f"NEW ledger authorized_max_network_calls={AUTHORIZED_MAX_CALLS}")

    raw_cache_index = load_raw_cache_index()
    raw_cache_hits = 0
    refetches_avoided = 0
    provider_failures = []

    for sig, memberships in unique_sigs.items():
        if sig in raw_cache_index:
            raw_cache_hits += 1
            refetches_avoided += 1
            continue

        if ledger.remaining <= 0:
            print(f"LEDGER EXHAUSTED before {sig}; stopping population acquisition")
            break

        def _do_call(sig=sig):
            result, outcome, err = rpc_get_transaction(sig)
            if outcome != "SUCCESS":
                raise RuntimeError(f"{outcome}: {err}")
            return result

        try:
            result = ledger.call("helius", "getTransaction", sig, _do_call, context={"purpose": "population_defining_tx"})
            row = {
                "signature": sig,
                "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "provider_result": result,
                "provider_error": None,
                "outcome": "SUCCESS",
            }
        except Exception as e:
            outcome_code = str(e).split(":")[0]
            row = {
                "signature": sig,
                "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "provider_result": None,
                "provider_error": str(e),
                "outcome": outcome_code,
            }
            provider_failures.append({"signature": sig, "outcome": outcome_code, "error": str(e)})

        _atomic_append_jsonl(RAW_CACHE_PATH, row)
        raw_cache_index[sig] = row
        time.sleep(0.08)  # gentle pacing, not a retry loop

    print(f"RAW_CACHE_HITS={raw_cache_hits}")
    print(f"REFETCHES_AVOIDED={refetches_avoided}")
    print(f"CALLS_USED={ledger.data['calls_attempted']}")
    print(f"CALLS_SUCCEEDED={ledger.data['calls_succeeded']}")
    print(f"CALLS_REMAINING={ledger.remaining}")
    print(f"PROVIDER_FAILURES={json.dumps(provider_failures)}")

    if ledger.data["status"] != "EXHAUSTED":
        ledger.data["status"] = "COMPLETE"
        ledger.data["completed_at"] = ledger._now()
        ledger._save()


if __name__ == "__main__":
    main()
