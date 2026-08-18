#!/usr/bin/env python3
"""B2N-P3C isolated migration-lineage bounded-run runner.

Executes ONLY after explicit --live (network) invocation; default mode is
--dry-run, which constructs and validates everything except the network
call itself.

Credential handling: the Helius endpoint (including any embedded API key)
is read ONLY from the environment variable B2N_P3C_HELIUS_ENDPOINT at
process start. It is NEVER read from .env, config/.env, or
supervisord.conf, NEVER printed to stdout/stderr, and NEVER written into
any artifact this script produces. If the variable is unset, --live mode
refuses to start.

Authorization: this script hard-binds to the exact P3B authorization
(run_id, manifest digest, provider, endpoint_family, method, budget). Any
mismatch fails closed before any network access.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.acquisition.b2n_qualification import (
    AppendOnlyLedger,
    B2NAttemptLedger,
    B2NExecutor,
    B2NManifest,
    B2NMember,
    B2NQualificationRunAuthorization,
    B2N_MAX_PHYSICAL_REQUESTS,
    B2N_MAX_REQUESTS_PER_MEMBER,
)
from src.acquisition.b2w_projection import B2WInputProjection, MigrationGetTransactionAdapter

ROOT = Path(__file__).resolve().parents[1]
FROZEN_MANIFEST_PATH = ROOT / "docs/evidence_platform/oip_v2_2e_2b2u_b2r_frozen_manifest.json"
REVIEWED_BINDING_PATH = ROOT / "docs/audits/b2n_cohort_eligibility_reviewed_provenance_binding.json"
SUCCESSOR_PREFLIGHT_PATH = ROOT / "docs/audits/b2n_p3b_bounded_migration_lineage_run_authorization_preflight.json"

EXPECTED_RUN_ID = "b2n-p3b-c39499f523e42083ce045d70"
EXPECTED_AUTHORIZATION_DIGEST = "e2414ef97516b4a03ff260b6b333749769d6a5a1bc70035ed62f1d6e76db1569"
EXPECTED_PROVIDER = "helius"
EXPECTED_ENDPOINT_FAMILY = "helius-mainnet-json-rpc"
EXPECTED_METHOD = "getTransaction"

CREDENTIAL_ENV_VAR = "B2N_P3C_HELIUS_ENDPOINT"


class B2NP3CError(RuntimeError):
    pass


def _load_frozen_manifest() -> B2NManifest:
    payload = json.loads(FROZEN_MANIFEST_PATH.read_text())
    members = tuple(B2NMember(**m) for m in payload["members"])
    return B2NManifest(
        members=members,
        source_milestone=payload["source_milestone"],
        source_receive_utc_ns_exclusive=payload["source_receive_utc_ns_exclusive"],
    )


def _load_reviewed_binding() -> dict:
    return json.loads(REVIEWED_BINDING_PATH.read_text())


def _verify_authorization_digest() -> dict:
    import hashlib

    raw = SUCCESSOR_PREFLIGHT_PATH.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED_AUTHORIZATION_DIGEST:
        raise B2NP3CError(f"B2N_P3C_AUTHORIZATION_DIGEST_MISMATCH: expected {EXPECTED_AUTHORIZATION_DIGEST}, got {digest}")
    payload = json.loads(raw)
    if payload.get("run_id") != EXPECTED_RUN_ID:
        raise B2NP3CError("B2N_P3C_RUN_ID_MISMATCH")
    binding = payload.get("provider_endpoint_method_binding", {})
    if binding.get("provider") != EXPECTED_PROVIDER:
        raise B2NP3CError("B2N_P3C_PROVIDER_MISMATCH")
    if binding.get("endpoint_family") != EXPECTED_ENDPOINT_FAMILY:
        raise B2NP3CError("B2N_P3C_ENDPOINT_FAMILY_MISMATCH")
    if binding.get("method") != EXPECTED_METHOD:
        raise B2NP3CError("B2N_P3C_METHOD_MISMATCH")
    budget = payload.get("request_budget", {})
    if budget.get("max_total_requests") != B2N_MAX_PHYSICAL_REQUESTS:
        raise B2NP3CError("B2N_P3C_BUDGET_MISMATCH")
    if budget.get("max_requests_per_member") != B2N_MAX_REQUESTS_PER_MEMBER:
        raise B2NP3CError("B2N_P3C_PER_MEMBER_BUDGET_MISMATCH")
    if budget.get("retry_budget") != 0 or budget.get("pagination_budget") != 0 or budget.get("fallback_budget") != 0:
        raise B2NP3CError("B2N_P3C_FORBIDDEN_POLICY_PRESENT")
    return payload


class RedactingJsonRpcTransport:
    """Wraps the endpoint so it is never exposed via repr/str/logging."""

    def __init__(self, endpoint: str, *, timeout_seconds: float = 30.0) -> None:
        if not endpoint.startswith("https://mainnet.helius-rpc.com/"):
            raise B2NP3CError("B2N_P3C_ENDPOINT_PREFIX_MISMATCH")
        if timeout_seconds <= 0:
            raise B2NP3CError("B2N_P3C_TIMEOUT_REQUIRED")
        self._endpoint = endpoint
        self._timeout = timeout_seconds
        self.physical_request_count = 0

    def __repr__(self) -> str:
        return "RedactingJsonRpcTransport(endpoint=<redacted>)"

    def post_json(self, request: dict) -> dict:
        import urllib.error
        import urllib.request

        body = json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        outbound = urllib.request.Request(
            self._endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        self.physical_request_count += 1
        try:
            with urllib.request.urlopen(outbound, timeout=self._timeout) as response:
                return json.loads(response.read())
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
            # Redact: urllib exceptions can embed the request URL (and thus the
            # credential) in their default message/repr. Never let that surface.
            raise B2NP3CError(f"B2N_P3C_TRANSPORT_ERROR:{type(exc).__name__}") from None


def build_projection(manifest: B2NManifest, reviewed: dict) -> B2WInputProjection:
    reviewed_by_ordinal = {m["ordinal"]: m for m in reviewed["members"]}
    members = []
    for member in manifest.members:
        row = reviewed_by_ordinal.get(member.sample_ordinal)
        if row is None:
            raise B2NP3CError(f"B2N_P3C_REVIEWED_MEMBER_MISSING:{member.sample_ordinal}")
        if row["mint"] != member.mint or row["census_event_id"] != member.census_event_id:
            raise B2NP3CError(f"B2N_P3C_REVIEWED_MEMBER_MISMATCH:{member.sample_ordinal}")
        if row["human_approval_decision_record"]["decision"] != "APPROVED":
            raise B2NP3CError(f"B2N_P3C_MEMBER_NOT_APPROVED:{member.sample_ordinal}")
        members.append({
            "sample_ordinal": member.sample_ordinal,
            "mint": member.mint,
            "census_event_id": member.census_event_id,
            "migration_signature": row["reviewed_migration_origin"],
        })
    from src.acquisition.b2w_projection import B2WRequestInput
    return B2WInputProjection(tuple(B2WRequestInput(**m) for m in members))


def dry_run() -> dict:
    manifest = _load_frozen_manifest()
    manifest.validate()
    auth_payload = _verify_authorization_digest()
    reviewed = _load_reviewed_binding()
    if reviewed["closure_state"] != {"COMPLETE": 20, "PARTIAL": 0, "MISSING": 0, "CONFLICTING": 0}:
        raise B2NP3CError("B2N_P3C_PROVENANCE_CLOSURE_NOT_QUALIFIED")

    projection = build_projection(manifest, reviewed)
    if len(projection.members) != 20:
        raise B2NP3CError("B2N_P3C_PROJECTION_COUNT_MISMATCH")

    authorization = B2NQualificationRunAuthorization(
        provider=EXPECTED_PROVIDER,
        endpoint_family=EXPECTED_ENDPOINT_FAMILY,
        run_id=EXPECTED_RUN_ID,
        manifest_digest=manifest.digest(),
        ledger_path=str(ROOT.parent / "flex-b2n-p3c-ledger" / f"{EXPECTED_RUN_ID}.jsonl"),
        max_physical_requests=B2N_MAX_PHYSICAL_REQUESTS,
        require_not_production=True,
    )
    authorization.validate(manifest=manifest)  # dry validation only, no client, no network

    ledger_path = Path(authorization.ledger_path)
    ledger = B2NAttemptLedger(ledger_path)
    ledger.require_empty()  # raises if a stale ledger exists

    constructed_requests = []
    for member in projection.members:
        payload = {
            "jsonrpc": "2.0",
            "id": member.sample_ordinal,
            "method": EXPECTED_METHOD,
            "params": [member.migration_signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
        }
        constructed_requests.append({
            "sample_ordinal": member.sample_ordinal,
            "mint": member.mint,
            "method": payload["method"],
            "params_shape_valid": payload["params"][1] == {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
        })

    return {
        "mode": "DRY_RUN",
        "authorization_digest_verified": True,
        "run_id_verified": EXPECTED_RUN_ID,
        "manifest_digest": manifest.digest(),
        "provenance_closure_verified": reviewed["closure_state"],
        "constructed_request_count": len(constructed_requests),
        "constructed_requests_max_20": len(constructed_requests) <= 20,
        "one_request_per_member": len(constructed_requests) == len({r["sample_ordinal"] for r in constructed_requests}),
        "endpoint_family": EXPECTED_ENDPOINT_FAMILY,
        "provider": EXPECTED_PROVIDER,
        "method": EXPECTED_METHOD,
        "ledger_path": str(ledger_path),
        "ledger_verified_empty": True,
        "network_calls_made": 0,
        "requests": constructed_requests,
    }


def live_run() -> dict:
    endpoint = os.environ.get(CREDENTIAL_ENV_VAR)
    if not endpoint:
        raise B2NP3CError(f"B2N_P3C_CREDENTIAL_ENV_VAR_MISSING:{CREDENTIAL_ENV_VAR}")

    manifest = _load_frozen_manifest()
    manifest.validate()
    _verify_authorization_digest()
    reviewed = _load_reviewed_binding()
    if reviewed["closure_state"] != {"COMPLETE": 20, "PARTIAL": 0, "MISSING": 0, "CONFLICTING": 0}:
        raise B2NP3CError("B2N_P3C_PROVENANCE_CLOSURE_NOT_QUALIFIED")

    projection = build_projection(manifest, reviewed)

    authorization = B2NQualificationRunAuthorization(
        provider=EXPECTED_PROVIDER,
        endpoint_family=EXPECTED_ENDPOINT_FAMILY,
        run_id=EXPECTED_RUN_ID,
        manifest_digest=manifest.digest(),
        ledger_path=str(ROOT.parent / "flex-b2n-p3c-ledger" / f"{EXPECTED_RUN_ID}.jsonl"),
        max_physical_requests=B2N_MAX_PHYSICAL_REQUESTS,
        require_not_production=True,
    )

    transport = RedactingJsonRpcTransport(endpoint)
    adapter = MigrationGetTransactionAdapter(transport, projection)
    ledger = B2NAttemptLedger(Path(authorization.ledger_path))

    executor = B2NExecutor(
        manifest=manifest, ledger=ledger, client=adapter,
        provider=EXPECTED_PROVIDER, run_id=EXPECTED_RUN_ID, authorization=authorization,
    )
    results = executor.run()

    return {
        "mode": "LIVE",
        "run_id": EXPECTED_RUN_ID,
        "entries": len(results),
        "physical_requests_used": transport.physical_request_count,
        "ledger_path": authorization.ledger_path,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="B2N-P3C isolated migration-lineage bounded-run runner.")
    parser.add_argument("--live", action="store_true", help="Perform the live network run. Default is dry-run.")
    parser.add_argument("--output", default=None, help="Optional path to write the JSON result.")
    args = parser.parse_args()

    try:
        result = live_run() if args.live else dry_run()
    except (B2NP3CError, RuntimeError, ValueError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}))
        return 1

    text = json.dumps(result, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(text + "\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
