"""Isolated OIP v2.2E B2N one-request qualification primitive.

This module deliberately has no production database, queue, service, provider
configuration, or HTTP-client dependency.  A caller injects a single-purpose
client; this layer enforces the frozen cohort and physical-attempt budget.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

CONTRACT_VERSION = "OIP_v2.2E.2B2N.v1"
REQUIREMENT_SOURCE_MILESTONE = "OIP v2.2E.2B2R"
B2N_MAX_PHYSICAL_REQUESTS = 20
B2N_MAX_REQUESTS_PER_MEMBER = 1
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
OUTCOME_SUCCESS = "SUCCESS"
OUTCOME_CACHE_HIT = "CACHE_HIT"
B2N_VALID_OUTCOMES = {
    OUTCOME_SUCCESS,
    OUTCOME_CACHE_HIT,
    "NOT_ATTEMPTED",
    "TIMEOUT",
    "RATE_LIMITED",
    "TRANSPORT_ERROR",
    "RPC_ERROR",
    "MALFORMED_RESPONSE",
    "BUDGET_STOP",
}


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
    source_milestone: str = REQUIREMENT_SOURCE_MILESTONE
    source_receive_utc_ns_exclusive: int | None = None
    manifest_digest: str | None = None
    contract_version: str = CONTRACT_VERSION

    def source_identity(self) -> tuple[str, int | None]:
        return (self.source_milestone, self.source_receive_utc_ns_exclusive)

    def digest(self) -> str:
        return hashlib.sha256(_canonical([asdict(member) for member in self.members])).hexdigest()

    def validate(self) -> None:
        if len(self.members) != 20:
            raise ValueError("B2N_MANIFEST_MUST_HAVE_EXACTLY_20_MEMBERS")
        if self.source_milestone != REQUIREMENT_SOURCE_MILESTONE:
            raise ValueError("B2N_MANIFEST_SOURCE_MILESTONE_INVALID")
        if not self.source_milestone or not str(self.source_milestone).strip():
            raise ValueError("B2N_MANIFEST_SOURCE_MILESTONE_MISSING")
        if [member.sample_ordinal for member in self.members] != list(range(1, 21)):
            raise ValueError("B2N_MANIFEST_ORDINALS_INVALID")
        mints = [member.mint for member in self.members]
        if any(not mint for mint in mints) or len(set(mints)) != 20:
            raise ValueError("B2N_MANIFEST_MINTS_INVALID")
        if not all(member.observation_required for member in self.members):
            raise ValueError("B2N_MANIFEST_MEMBER_NOT_MARKED")
        if self.manifest_digest and self.manifest_digest != self.digest():
            raise ValueError("B2N_MANIFEST_DIGEST_MISMATCH")


@dataclass(frozen=True)
class OneRequestResponse:
    outcome: str
    evidence_observed: bool
    provenance_complete: bool
    provider_signature: str | None = None
    provider_slot: int | None = None
    provider_block_time_utc: str | None = None
    error_class: str | None = None
    provider_request_count: int = 1


@dataclass(frozen=True)
class B2NQualificationRunAuthorization:
    provider: str
    endpoint_family: str
    run_id: str
    manifest_digest: str
    ledger_path: str
    max_physical_requests: int = B2N_MAX_PHYSICAL_REQUESTS
    require_not_production: bool = True

    def validate(self, *, manifest: B2NManifest) -> None:
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise ValueError("B2N_AUTH_PROVIDER_REQUIRED")
        if not isinstance(self.endpoint_family, str) or not self.endpoint_family.strip():
            raise ValueError("B2N_AUTH_ENDPOINT_FAMILY_REQUIRED")
        if not RUN_ID_PATTERN.fullmatch(self.run_id):
            raise ValueError("B2N_AUTH_RUN_ID_INVALID")
        if self.max_physical_requests != B2N_MAX_PHYSICAL_REQUESTS:
            raise ValueError("B2N_AUTH_REQUEST_CEILING_MISMATCH")
        if self.manifest_digest != manifest.digest():
            raise ValueError("B2N_AUTH_MANIFEST_DIGEST_MISMATCH")
        if not isinstance(self.ledger_path, str) or not self.ledger_path:
            raise ValueError("B2N_AUTH_LEDGER_PATH_REQUIRED")
        if not self.require_not_production:
            raise ValueError("B2N_AUTH_PRODUCTION_BOUNDARY_VIOLATION")


class OneRequestClient(Protocol):
    def acquire_once(self, *, mint: str) -> OneRequestResponse: ...


class AppendOnlyLedger:
    """JSONL ledger that rejects a second result for a frozen ordinal."""
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def require_empty(self) -> None:
        if self.entries():
            raise RuntimeError("B2N_LEDGER_MUST_BE_EMPTY_FOR_NEW_RUN")

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


class B2NAttemptLedger(AppendOnlyLedger):
    """Append-only B2N ledger with schema-aware validations for replay."""

    required_fields = {
        "contract_version", "run_id", "manifest_digest", "sample_ordinal",
        "mint", "observation_required", "provider", "request_count", "request_outcome",
        "request_started_utc_ns", "response_received_utc_ns", "elapsed_monotonic_ns",
        "evidence_observed", "provenance_complete"
    }

    def require_empty(self) -> None:
        if self.entries():
            raise RuntimeError("B2N_LEDGER_MUST_BE_EMPTY_FOR_NEW_RUN")

    def append(self, entry: dict[str, Any]) -> None:
        if not isinstance(entry.get("sample_ordinal"), int) or not (1 <= entry["sample_ordinal"] <= 20):
            raise ValueError("B2N_LEDGER_SAMPLE_ORDINAL_INVALID")
        if not isinstance(entry.get("mint"), str) or not entry["mint"]:
            raise ValueError("B2N_LEDGER_MINT_INVALID")
        if entry.get("request_count") not in {0, 1}:
            raise ValueError("B2N_LEDGER_REQUEST_COUNT_INVALID")
        if entry.get("request_outcome") not in B2N_VALID_OUTCOMES:
            raise ValueError("B2N_LEDGER_REQUEST_OUTCOME_INVALID")
        if not isinstance(entry.get("observation_required"), bool):
            raise ValueError("B2N_LEDGER_OBSERVATION_REQUIRED_INVALID")
        for key in self.required_fields:
            if key not in entry:
                raise ValueError(f"B2N_LEDGER_FIELD_MISSING:{key}")
        super().append(entry)


class B2NExecutor:
    """A one-shot executor: one injected client call per manifest member."""
    def __init__(self, *, manifest: B2NManifest, ledger: AppendOnlyLedger,
                 client: OneRequestClient, provider: str, run_id: str | None = None,
                 authorization: B2NQualificationRunAuthorization | None = None) -> None:
        manifest.validate()
        if not provider:
            raise ValueError("B2N_PROVIDER_REQUIRED")
        self.authorization = authorization
        self.manifest, self.ledger, self.client, self.provider = manifest, ledger, client, provider
        self.run_id = run_id or str(uuid.uuid4())
        if not self.authorization:
            # Legacy, caller supplied only provider + run_id.
            self.authorization = B2NQualificationRunAuthorization(
                provider=provider,
                endpoint_family="UNVERIFIED",
                run_id=self.run_id,
                manifest_digest=self.manifest.digest(),
                ledger_path=str(ledger.path),
                max_physical_requests=B2N_MAX_PHYSICAL_REQUESTS,
            )

    def run(self) -> list[dict[str, Any]]:
        if not isinstance(self.authorization, B2NQualificationRunAuthorization):
            raise RuntimeError("B2N_AUTHORIZATION_MISSING")
        if self.authorization.run_id != self.run_id:
            raise RuntimeError("B2N_AUTHORIZATION_RUN_ID_MISMATCH")
        self.authorization.validate(manifest=self.manifest)
        self.ledger.require_empty()
        results: list[dict[str, Any]] = []
        total_requests = 0
        for member in self.manifest.members:
            if not member.observation_required:
                raise RuntimeError("B2N_MEMBER_NOT_MARKED")
            if total_requests >= self.authorization.max_physical_requests:
                raise RuntimeError("B2N_BUDGET_EXHAUSTED")
            started_wall, started_mono = time.time_ns(), time.monotonic_ns()
            before = getattr(self.client, "provider_request_count", 0)
            response = self.client.acquire_once(mint=member.mint)
            finished_wall, elapsed = time.time_ns(), time.monotonic_ns() - started_mono
            if response.outcome not in B2N_VALID_OUTCOMES:
                raise ValueError("B2N_INVALID_REQUEST_OUTCOME")
            after = getattr(self.client, "provider_request_count", before)
            if (delta := after - before) not in {0, 1}:
                raise RuntimeError("B2N_CLIENT_REQUEST_COUNTER_INVALID")
            request_count = int(response.provider_request_count)
            if request_count not in {0, 1}:
                raise ValueError("B2N_REQUEST_COUNT_INVALID")
            if response.outcome == OUTCOME_CACHE_HIT and request_count != 0:
                raise ValueError("B2N_CACHE_HIT_MUST_HAVE_ZERO_REQUESTS")
            if request_count == 0 and response.outcome != OUTCOME_CACHE_HIT:
                raise ValueError("B2N_REQUEST_COUNT_MISMATCH")
            if request_count != delta:
                raise RuntimeError("B2N_CLIENT_REQUEST_COUNTER_MISMATCH")
            entry = {
                "contract_version": CONTRACT_VERSION, "run_id": self.run_id,
                "manifest_digest": self.manifest.digest(), "sample_ordinal": member.sample_ordinal,
                "mint": member.mint, "observation_required": True, "provider": self.provider,
                "request_count": request_count, "request_outcome": response.outcome,
                "provider_signature": response.provider_signature, "provider_slot": response.provider_slot,
                "provider_block_time_utc": response.provider_block_time_utc,
                "request_started_utc_ns": started_wall, "response_received_utc_ns": finished_wall,
                "elapsed_monotonic_ns": elapsed, "evidence_observed": response.evidence_observed,
                "provenance_complete": response.provenance_complete, "error_class": response.error_class,
            }
            self.ledger.append(entry)
            results.append(entry)
            total_requests += request_count
            if total_requests > self.authorization.max_physical_requests:
                raise RuntimeError("B2N_BUDGET_EXHAUSTED")
            if response.outcome != OUTCOME_SUCCESS or not response.evidence_observed or not response.provenance_complete:
                break
        return results
