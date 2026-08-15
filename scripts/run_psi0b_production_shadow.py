#!/usr/bin/env python3
"""Path-independent PSI0B-E12 production-shadow bootstrap and execution entrypoint."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import resource
import sys
import time
from typing import Callable

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from src.evidence.contracts.production_shadow_launcher import (  # noqa: E402
    LAUNCHER_VERSION,
    launch_authorized_shadow_with_provenance,
    load_execution_authorization,
    validate_bootstrap_inputs,
)
from src.evidence.contracts.production_shadow_production_binding import (  # noqa: E402
    execute_production_shadow,
    verify_production_shadow_bundle,
)
from src.evidence.contracts.production_shadow_telemetry_observer import (  # noqa: E402
    ProductionTelemetryObserver,
    production_telemetry_dependencies,
)


ENTRYPOINT_VERSION = "psi0b-e12.v1"


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def entrypoint_contract_digest() -> str:
    return sha256(json.dumps({
        "entrypoint_version": ENTRYPOINT_VERSION,
        "path_bootstrap": "SCRIPT_PARENT_REPOSITORY_ROOT_BEFORE_SRC_IMPORT",
        "inputs": (
            "authorization", "preflight_artifact", "consumption_directory",
            "observer_attempt_directory", "output_directory", "attempt_audit",
        ),
        "composition": (
            "ProductionTelemetryObserver.prestart", "ProductionTelemetryObserver.active",
            "launch_authorized_shadow_with_provenance", "execute_production_shadow",
            "verify_production_shadow_bundle", "POST_RUN_ACTIVE_HEALTH",
        ),
        "attempts": 1,
        "retry": False,
        "authority": (False, False),
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _write_attempt_audit(path: Path, values: dict[str, object]) -> None:
    path = Path(path)
    if not path.parent.is_dir():
        raise RuntimeError("PSI0B_E12_AUDIT_PARENT_MISSING")
    with path.open("xb") as handle:
        handle.write(_canonical(values))
        handle.flush()
        os.fsync(handle.fileno())


def run_authorized_execution(
    *, authorization_path: Path, preflight_artifact: Path,
    consumption_directory: Path, observer_attempt_directory: Path,
    output_directory: Path, attempt_audit_path: Path,
    observer: ProductionTelemetryObserver,
    execute_shadow: Callable = execute_production_shadow,
    verify_bundle: Callable = verify_production_shadow_bundle,
    clock: Callable[[], float] = time.monotonic,
    resource_probe: Callable[[], tuple[int, int]] | None = None,
) -> object:
    """Compose E11 observer provenance and one bound executor; never retries."""
    started = datetime.now(timezone.utc).isoformat()
    status = "FAILED"
    error = None
    bundle = None
    active_decisions = []
    post_decision = None
    authorization_id = None
    authorization_digest = None
    authorization_consumed = False

    try:
        record = load_execution_authorization(authorization_path)
        authorization_id = record.authorization_id
        authorization_digest = record.authorization_digest
        if Path(record.output_directory).resolve() != Path(output_directory).resolve():
            raise RuntimeError("PSI0B_E12_OUTPUT_DIRECTORY_DRIFT")
        if Path(output_directory).exists():
            raise RuntimeError("PSI0B_E12_OUTPUT_NOT_NEW")

        rss_start = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

        def default_resource_probe() -> tuple[int, int]:
            current = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return max(0, int(current - rss_start)), 0

        probe = resource_probe or default_resource_probe

        def active(query_id: str):
            decision = observer.active(query_id)
            active_decisions.append({"query_id": query_id, "decision": asdict(decision)})
            return decision

        def executor(bound_record, preflight, decision):
            nonlocal post_decision
            result = execute_shadow(
                bound_record, preflight, prestart_health=decision,
                active_health_check=active, clock=clock, resource_probe=probe,
            )
            verify_bundle(Path(output_directory), bound_record)
            post_decision = active("POST_RUN")
            if post_decision.status != "PASS":
                raise RuntimeError("PSI0B_E12_POST_RUN_HEALTH_FAILED")
            return result

        bundle = launch_authorized_shadow_with_provenance(
            authorization_path, preflight_artifact, consumption_directory,
            observer_attempt_directory, observer_bootstrap=observer.prestart,
            executor=executor,
        )
        authorization_consumed = (
            Path(consumption_directory) / f"{record.authorization_id}.consumed.json"
        ).is_file()
        status = "PASS"
        return bundle
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if authorization_id is not None:
            authorization_consumed = (
                Path(consumption_directory) / f"{authorization_id}.consumed.json"
            ).is_file()
        _write_attempt_audit(Path(attempt_audit_path), {
            "schema_version": "psi0b-e12.attempt.v1",
            "entrypoint_version": ENTRYPOINT_VERSION,
            "entrypoint_contract_digest": entrypoint_contract_digest(),
            "authorization_id": authorization_id,
            "authorization_digest": authorization_digest,
            "started_at_utc": started,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "error": error,
            "authorization_consumed": authorization_consumed,
            "observer_provenance_terminal": (
                Path(observer_attempt_directory) / "observer_attempt.json"
            ).is_file(),
            "output_published": Path(output_directory).is_dir(),
            "bundle_digest": getattr(bundle, "bundle_digest", None),
            "total_rows": getattr(bundle, "total_rows", None),
            "active_health_decisions": active_decisions,
            "post_run_health_decision": asdict(post_decision) if post_decision else None,
            "production_writes_or_ddl": 0,
            "provider_rpc_calls": 0,
            "service_actions": 0,
            "grants_integration_authority": False,
            "grants_activation_authority": False,
        })


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate or execute the PSI0B production-shadow boundary")
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--preflight-artifact", type=Path, required=True)
    parser.add_argument("--consumption-directory", type=Path, required=True)
    parser.add_argument("--observer-attempt-directory", type=Path)
    parser.add_argument("--output-directory", type=Path)
    parser.add_argument("--attempt-audit", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--bootstrap-check", action="store_true")
    mode.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.bootstrap_check:
        record, preflight, marker = validate_bootstrap_inputs(
            args.authorization, args.preflight_artifact, args.consumption_directory,
        )
        print(
            f"{LAUNCHER_VERSION} BOOTSTRAP_PASS authorization={record.authorization_id} "
            f"run={preflight.run_id} unconsumed_marker={marker}"
        )
        return 0
    if not all((args.observer_attempt_directory, args.output_directory, args.attempt_audit)):
        parser.error("--execute requires --observer-attempt-directory, --output-directory and --attempt-audit")
    observer = ProductionTelemetryObserver(production_telemetry_dependencies(REPOSITORY_ROOT))
    try:
        bundle = run_authorized_execution(
            authorization_path=args.authorization,
            preflight_artifact=args.preflight_artifact,
            consumption_directory=args.consumption_directory,
            observer_attempt_directory=args.observer_attempt_directory,
            output_directory=args.output_directory,
            attempt_audit_path=args.attempt_audit,
            observer=observer,
        )
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": "PASS", "bundle_digest": bundle.bundle_digest}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
