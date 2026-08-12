"""Isolated OIP v2.2E B2N one-request qualification primitive.

This module deliberately has no production database, queue, service, provider
configuration, or HTTP-client dependency.  A caller injects a single-purpose
client; this layer enforces the frozen cohort and physical-attempt budget.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


@dataclass(frozen=True)
class B2NMember:
    sample_ordinal: int
    mint: str
    census_event_id: str
    observation_required: bool


@dataclass(frozen=True)
class B2NManifest:
    members: tuple[B2NMember, ...]

    def digest(self) -> str:
        return hashlib.sha256(_canonical([asdict(member) for member in self.members])).hexdigest()

    def validate(self) -> None:
        if len(self.members) != 20:
            raise ValueError("B2N_MANIFEST_MUST_HAVE_EXACTLY_20_MEMBERS")
        if [member.sample_ordinal for member in self.members] != list(range(1, 21)):
            raise ValueError("B2N_MANIFEST_ORDINALS_INVALID")
        mints = [member.mint for member in self.members]
        if any(not mint for mint in mints) or len(set(mints)) != 20:
            raise ValueError("B2N_MANIFEST_MINTS_INVALID")
        if not all(member.observation_required for member in self.members):
            raise ValueError("B2N_MANIFEST_MEMBER_NOT_MARKED")


@dataclass(frozen=True)
class OneRequestResponse:
    outcome: str
    evidence_observed: bool
    provenance_complete: bool
    provider_signature: str | None = None
    provider_slot: int | None = None
    provider_block_time_utc: str | None = None
    error_class: str | None = None


class OneRequestClient(Protocol):
    def acquire_once(self, *, mint: str) -> OneRequestResponse: ...


class AppendOnlyLedger:
    """JSONL ledger that rejects a second result for a frozen ordinal."""
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def append(self, entry: dict[str, Any]) -> None:
        existing = self.entries()
        if any(row["sample_ordinal"] == entry["sample_ordinal"] for row in existing):
            raise RuntimeError("B2N_LEDGER_MEMBER_ALREADY_RECORDED")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(_canonical(entry).decode())


class B2NExecutor:
    """A one-shot executor: one injected client call per manifest member."""
    def __init__(self, *, manifest: B2NManifest, ledger: AppendOnlyLedger,
                 client: OneRequestClient, provider: str, run_id: str | None = None) -> None:
        manifest.validate()
        if not provider:
            raise ValueError("B2N_PROVIDER_REQUIRED")
        self.manifest, self.ledger, self.client, self.provider = manifest, ledger, client, provider
        self.run_id = run_id or str(uuid.uuid4())

    def run(self) -> list[dict[str, Any]]:
        previous = self.ledger.entries()
        if previous:
            raise RuntimeError("B2N_LEDGER_MUST_BE_EMPTY_FOR_NEW_RUN")
        results: list[dict[str, Any]] = []
        for member in self.manifest.members:
            started_wall, started_mono = time.time_ns(), time.monotonic_ns()
            response = self.client.acquire_once(mint=member.mint)
            finished_wall, elapsed = time.time_ns(), time.monotonic_ns() - started_mono
            if response.outcome not in {"SUCCESS", "CACHE_HIT", "TIMEOUT", "RATE_LIMITED", "TRANSPORT_ERROR", "RPC_ERROR", "MALFORMED_RESPONSE"}:
                raise ValueError("B2N_INVALID_REQUEST_OUTCOME")
            entry = {
                "contract_version": "OIP_v2.2E.2B2N.v1", "run_id": self.run_id,
                "manifest_digest": self.manifest.digest(), "sample_ordinal": member.sample_ordinal,
                "mint": member.mint, "observation_required": True, "provider": self.provider,
                "request_count": 1, "request_outcome": response.outcome,
                "provider_signature": response.provider_signature, "provider_slot": response.provider_slot,
                "provider_block_time_utc": response.provider_block_time_utc,
                "request_started_utc_ns": started_wall, "response_received_utc_ns": finished_wall,
                "elapsed_monotonic_ns": elapsed, "evidence_observed": response.evidence_observed,
                "provenance_complete": response.provenance_complete, "error_class": response.error_class,
            }
            self.ledger.append(entry)
            results.append(entry)
            if response.outcome != "SUCCESS" or not response.evidence_observed or not response.provenance_complete:
                break
        return results
