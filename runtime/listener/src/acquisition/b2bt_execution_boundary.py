"""Inert B2BS execution boundary with physical-attempt and projection ledgers."""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from src.acquisition.b2n_qualification import B2NManifest
from src.acquisition.b2w_projection import B2WInputProjection
from src.acquisition.b2y_creator_funding_contract import (
    B2YCreatorFundingProbe,
    B2YMember,
    B2YResult,
    ResponseProjection,
)


CONTRACT_VERSION = "OIP_v2.2E.2B2BT.v1"
MAX_REQUESTS = 60


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"


class B2BTTransport(Protocol):
    physical_request_count: int

    def get_transaction(self, signature: str) -> dict[str, Any]: ...
    def get_oldest_enhanced_transaction(self, address: str) -> list[dict[str, Any]]: ...


class HeliusB2BTTransport:
    """Explicit dual endpoint transport; construction performs no I/O."""

    def __init__(self, *, rpc_endpoint: str, enhanced_base: str,
                 api_key: str, timeout_seconds: float = 30.0) -> None:
        if not rpc_endpoint.startswith("https://mainnet.helius-rpc.com/"):
            raise ValueError("B2BT_REVIEWED_RPC_ENDPOINT_REQUIRED")
        if not enhanced_base.rstrip("/") == "https://api-mainnet.helius-rpc.com/v0/addresses":
            raise ValueError("B2BT_REVIEWED_ENHANCED_ENDPOINT_REQUIRED")
        if not api_key or timeout_seconds <= 0:
            raise ValueError("B2BT_EXPLICIT_CREDENTIAL_AND_TIMEOUT_REQUIRED")
        self.rpc_endpoint, self.enhanced_base = rpc_endpoint, enhanced_base.rstrip("/")
        self._api_key, self.timeout_seconds = api_key, timeout_seconds
        self.physical_request_count = 0

    def get_transaction(self, signature: str) -> dict[str, Any]:
        payload = json.dumps({"jsonrpc":"2.0","id":self.physical_request_count + 1,
                              "method":"getTransaction","params":[signature,{"encoding":"jsonParsed","maxSupportedTransactionVersion":0}]},
                             sort_keys=True,separators=(",", ":")).encode()
        request = urllib.request.Request(self.rpc_endpoint, data=payload,
                                         headers={"Content-Type":"application/json"}, method="POST")
        self.physical_request_count += 1
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read())

    def get_oldest_enhanced_transaction(self, address: str) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode({"api-key": self._api_key, "limit": 1,
                                        "sort-order": "asc", "commitment": "finalized"})
        url = f"{self.enhanced_base}/{urllib.parse.quote(address, safe='')}/transactions?{query}"
        self.physical_request_count += 1
        with urllib.request.urlopen(url, timeout=self.timeout_seconds) as response:
            result = json.loads(response.read())
        if not isinstance(result, list):
            raise ValueError("B2BT_ENHANCED_RESULT_MALFORMED")
        return result


class AppendOnlyJsonl:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def rows(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def require_empty(self) -> None:
        if self.rows():
            raise RuntimeError("B2BT_LEDGER_MUST_BE_EMPTY")

    def append(self, row: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(_canonical(row))


@dataclass(frozen=True)
class PhysicalAttempt:
    contract_version: str
    run_id: str
    manifest_digest: str
    sample_ordinal: int
    member_request_number: int
    physical_attempt_number: int
    endpoint_class: str
    request_kind: str
    started_utc_ns: int
    finished_utc_ns: int
    elapsed_monotonic_ns: int
    outcome: str
    error_class: str | None


class LedgerTransport:
    """Counts and seals every injected physical call without retaining responses."""

    def __init__(self, inner: B2BTTransport, *, ledger: AppendOnlyJsonl, run_id: str,
                 digest: str, ordinal: int) -> None:
        self.inner, self.ledger = inner, ledger
        self.run_id, self.digest, self.ordinal = run_id, digest, ordinal
        self.physical_request_count = 0

    def _call(self, kind: str, endpoint_class: str, call):
        if self.physical_request_count >= MAX_REQUESTS:
            raise RuntimeError("B2BT_GLOBAL_BUDGET_EXHAUSTED")
        before = self.inner.physical_request_count
        started_wall, started_mono = time.time_ns(), time.monotonic_ns()
        outcome, error_class = "SUCCESS", None
        try:
            response = call()
        except Exception as error:
            outcome, error_class = "NON_SUCCESS", type(error).__name__
            response = None
        finished_wall, elapsed = time.time_ns(), time.monotonic_ns() - started_mono
        if self.inner.physical_request_count - before != 1:
            outcome, error_class = "NON_SUCCESS", "B2BT_CLIENT_COUNTER_MISMATCH"
        self.physical_request_count += 1
        self.ledger.append(asdict(PhysicalAttempt(
            CONTRACT_VERSION, self.run_id, self.digest, self.ordinal,
            self.physical_request_count % 3 or 3, self.physical_request_count,
            endpoint_class, kind, started_wall, finished_wall, elapsed, outcome, error_class,
        )))
        if outcome != "SUCCESS":
            raise RuntimeError(error_class or "B2BT_FIRST_NON_SUCCESS")
        return response

    def get_transaction(self, signature: str) -> dict[str, Any]:
        return self._call("getTransaction", "HELIUS_JSON_RPC", lambda: self.inner.get_transaction(signature))

    def get_oldest_enhanced_transaction(self, address: str) -> list[dict[str, Any]]:
        return self._call("getOldestEnhancedAddressTransaction", "HELIUS_ENHANCED_REST",
                          lambda: self.inner.get_oldest_enhanced_transaction(address))


class B2BTRunner:
    def __init__(self, *, manifest: B2NManifest, projection: B2WInputProjection,
                 transport: B2BTTransport, attempts: AppendOnlyJsonl,
                 projections: AppendOnlyJsonl, run_id: str) -> None:
        manifest.validate()
        projected = {row.mint: row for row in projection.members}
        if set(projected) != {row.mint for row in manifest.members} or len(projected) != 20:
            raise ValueError("B2BT_PROJECTION_MISMATCH")
        self.manifest, self.projected, self.transport = manifest, projected, transport
        self.attempts, self.projections, self.run_id = attempts, projections, run_id
        self.digest = manifest.digest()

    def run(self) -> list[B2YResult]:
        self.attempts.require_empty(); self.projections.require_empty()
        if self.transport.physical_request_count != 0:
            raise RuntimeError("B2BT_TRANSPORT_COUNTER_NOT_ZERO")
        results = []
        total = 0
        for member in self.manifest.members:
            wrapped = LedgerTransport(self.transport, ledger=self.attempts, run_id=self.run_id,
                                      digest=self.digest, ordinal=member.sample_ordinal)
            wrapped.physical_request_count = total
            result = B2YCreatorFundingProbe(wrapped).probe_once(B2YMember(
                member.mint, self.projected[member.mint].migration_signature
            ))
            total = wrapped.physical_request_count
            for projection in result.projections:
                self.projections.append({
                    "contract_version": CONTRACT_VERSION, "run_id": self.run_id,
                    "manifest_digest": self.digest, "sample_ordinal": member.sample_ordinal,
                    **asdict(projection),
                })
            results.append(result)
            if result.outcome != "SUCCESS":
                raise RuntimeError(f"B2BT_FIRST_NON_SUCCESS_{result.outcome}")
        if total != 60 or total != self.transport.physical_request_count:
            raise RuntimeError("B2BT_FINAL_COUNTER_MISMATCH")
        return results
