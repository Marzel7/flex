"""Isolated B2Z execution boundary.

Construction is inert: callers inject an endpoint or fake transport explicitly.
There is no environment, production database, queue, service, cache, retry,
failover, pagination, or concurrency dependency in this module.
"""
from __future__ import annotations

import json
import time
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from src.acquisition.b2n_qualification import B2NManifest
from src.acquisition.b2w_projection import B2WInputProjection


CONTRACT_VERSION = "OIP_v2.2E.2B2Z.v1"
PROVIDER = "helius_rpc"
MAX_REQUESTS = 60


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"


class JsonRpcTransport(Protocol):
    physical_request_count: int

    def post_json(self, request: dict[str, Any]) -> dict[str, Any]: ...


class HeliusJsonRpcTransport:
    """Single-endpoint, single-attempt JSON-RPC transport with no retry path."""

    def __init__(self, endpoint: str, *, timeout_seconds: float = 30.0) -> None:
        if not endpoint.startswith("https://"):
            raise ValueError("B2Z_HTTPS_ENDPOINT_REQUIRED")
        if timeout_seconds <= 0:
            raise ValueError("B2Z_TIMEOUT_REQUIRED")
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self.physical_request_count = 0

    def post_json(self, request: dict[str, Any]) -> dict[str, Any]:
        body = _canonical(request).encode("utf-8")
        outbound = urllib.request.Request(
            self.endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        self.physical_request_count += 1
        with urllib.request.urlopen(outbound, timeout=self.timeout_seconds) as response:
            return json.loads(response.read())


@dataclass(frozen=True)
class Attempt:
    contract_version: str
    run_id: str
    manifest_digest: str
    sample_ordinal: int
    mint: str
    provider: str
    member_request_number: int
    physical_attempt_number: int
    rpc_method: str
    target: str
    request_started_utc_ns: int
    response_received_utc_ns: int
    elapsed_monotonic_ns: int
    outcome: str
    error_class: str | None


class PhysicalAttemptLedger:
    """Append-only JSONL physical-attempt ledger; a new run must start empty."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def require_empty(self) -> None:
        if self.entries():
            raise RuntimeError("B2Z_LEDGER_MUST_BE_EMPTY")

    def append(self, attempt: Attempt) -> None:
        existing = self.entries()
        if any(row["physical_attempt_number"] == attempt.physical_attempt_number for row in existing):
            raise RuntimeError("B2Z_DUPLICATE_PHYSICAL_ATTEMPT")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as output:
            output.write(_canonical(asdict(attempt)))


def _result(response: dict[str, Any]) -> Any:
    if not isinstance(response, dict) or response.get("error") is not None:
        raise ValueError("B2Z_RPC_ERROR")
    result = response.get("result")
    if not isinstance(result, (dict, list)):
        raise ValueError("B2Z_RESULT_MISSING")
    if isinstance(result, dict) and isinstance(result.get("meta"), dict) and result["meta"].get("err") is not None:
        raise ValueError("B2Z_TRANSACTION_FAILED")
    return result


def _account_keys(transaction: dict[str, Any]) -> list[dict[str, Any]]:
    keys = transaction.get("transaction", {}).get("message", {}).get("accountKeys", [])
    if not isinstance(keys, list):
        raise ValueError("B2Z_ACCOUNT_KEYS_MALFORMED")
    normalized = []
    for key in keys:
        if isinstance(key, str):
            normalized.append({"pubkey": key, "signer": False})
        elif isinstance(key, dict) and isinstance(key.get("pubkey"), str):
            normalized.append(key)
    return normalized


def resolve_creator(migration: dict[str, Any], mint: str) -> tuple[str, int]:
    keys = _account_keys(migration)
    if mint not in {key["pubkey"] for key in keys}:
        raise ValueError("B2Z_MINT_NOT_IN_MIGRATION")
    signers = [key["pubkey"] for key in keys if key.get("signer") is True]
    if len(signers) != 1:
        raise ValueError("B2Z_CREATOR_AMBIGUOUS")
    block_time = migration.get("blockTime")
    if not isinstance(block_time, int):
        raise ValueError("B2Z_MIGRATION_TIME_MISSING")
    return signers[0], block_time


def proves_inbound_sol_funding(transaction: dict[str, Any], creator: str) -> bool:
    """Require a parsed System Program SOL transfer with positive lamports."""
    message = transaction.get("transaction", {}).get("message", {})
    top_level = message.get("instructions", [])
    if not isinstance(top_level, list):
        raise ValueError("B2Z_INSTRUCTIONS_MALFORMED")
    instructions = list(top_level)
    for group in transaction.get("meta", {}).get("innerInstructions", []):
        if isinstance(group, dict) and isinstance(group.get("instructions"), list):
            instructions.extend(group["instructions"])
    for instruction in instructions:
        if not isinstance(instruction, dict) or instruction.get("program") != "system":
            continue
        parsed = instruction.get("parsed")
        if not isinstance(parsed, dict) or parsed.get("type") not in {"transfer", "transferWithSeed"}:
            continue
        info = parsed.get("info", {})
        lamports = info.get("lamports")
        if info.get("destination") == creator and isinstance(lamports, int) and lamports > 0:
            return True
    return False


class B2ZRunner:
    """Sequential, stop-on-first-non-success executor for the frozen B2R cohort."""

    def __init__(self, *, manifest: B2NManifest, projection: B2WInputProjection,
                 transport: JsonRpcTransport, ledger: PhysicalAttemptLedger,
                 run_id: str | None = None) -> None:
        manifest.validate()
        self.manifest = manifest
        self.digest = manifest.digest()
        projected = {member.mint: member for member in projection.members}
        if len(projected) != 20 or set(projected) != {member.mint for member in manifest.members}:
            raise ValueError("B2Z_PROJECTION_MISMATCH")
        self.projected = projected
        self.transport = transport
        self.ledger = ledger
        self.run_id = run_id or str(uuid.uuid4())
        self.physical_count = 0

    def _call(self, *, ordinal: int, mint: str, member_number: int,
              method: str, params: list[Any], target: str) -> Any:
        if self.physical_count >= MAX_REQUESTS:
            raise RuntimeError("B2Z_GLOBAL_BUDGET_EXHAUSTED")
        before = self.transport.physical_request_count
        started_wall, started_mono = time.time_ns(), time.monotonic_ns()
        outcome, error_class, response = "SUCCESS", None, None
        try:
            response = self.transport.post_json({
                "jsonrpc": "2.0", "id": self.physical_count + 1,
                "method": method, "params": params,
            })
            _result(response)
        except Exception as error:
            outcome, error_class = "NON_SUCCESS", type(error).__name__
        finished_wall, elapsed = time.time_ns(), time.monotonic_ns() - started_mono
        after = self.transport.physical_request_count
        counter_mismatch = after - before != 1
        if counter_mismatch:
            outcome, error_class = "NON_SUCCESS", "B2Z_CLIENT_COUNTER_MISMATCH"
        self.physical_count += 1
        self.ledger.append(Attempt(
            CONTRACT_VERSION, self.run_id, self.digest, ordinal, mint, PROVIDER,
            member_number, self.physical_count, method, target, started_wall,
            finished_wall, elapsed, outcome, error_class,
        ))
        if counter_mismatch:
            raise RuntimeError("B2Z_CLIENT_COUNTER_MISMATCH")
        if outcome != "SUCCESS":
            raise RuntimeError("B2Z_FIRST_NON_SUCCESS_STOP")
        return _result(response)

    def run(self) -> list[dict[str, Any]]:
        self.ledger.require_empty()
        if self.transport.physical_request_count != 0:
            raise RuntimeError("B2Z_TRANSPORT_COUNTER_NOT_ZERO")
        results = []
        for member in self.manifest.members:
            projected = self.projected[member.mint]
            migration = self._call(
                ordinal=member.sample_ordinal, mint=member.mint, member_number=1,
                method="getTransaction",
                params=[projected.migration_signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
                target=projected.migration_signature,
            )
            creator, migration_time = resolve_creator(migration, member.mint)
            signatures = self._call(
                ordinal=member.sample_ordinal, mint=member.mint, member_number=2,
                method="getSignaturesForAddress", params=[creator, {"limit": 1000}], target=creator,
            )
            rows = signatures if isinstance(signatures, list) else signatures.get("value")
            candidates = [row for row in rows if isinstance(row, dict)
                          and isinstance(row.get("signature"), str)
                          and isinstance(row.get("blockTime"), int)
                          and row["blockTime"] < migration_time] if isinstance(rows, list) else []
            if not candidates:
                raise RuntimeError("B2Z_FIRST_NON_SUCCESS_STOP_NO_CANDIDATE")
            signature = candidates[0]["signature"]
            funding = self._call(
                ordinal=member.sample_ordinal, mint=member.mint, member_number=3,
                method="getTransaction",
                params=[signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
                target=signature,
            )
            funding_time = funding.get("blockTime")
            if not isinstance(funding_time, int) or funding_time >= migration_time:
                raise RuntimeError("B2Z_FIRST_NON_SUCCESS_STOP_FUNDING_TIME_INVALID")
            if not proves_inbound_sol_funding(funding, creator):
                raise RuntimeError("B2Z_FIRST_NON_SUCCESS_STOP_NO_FUNDING_EDGE")
            results.append({
                "sample_ordinal": member.sample_ordinal, "mint": member.mint,
                "migration_signature": projected.migration_signature,
                "creator": creator, "funding_signature": signature,
                "evidence_observed": True, "provenance_complete": True,
            })
        if self.physical_count != len(self.ledger.entries()) or self.physical_count != self.transport.physical_request_count:
            raise RuntimeError("B2Z_FINAL_COUNTER_MISMATCH")
        return results
