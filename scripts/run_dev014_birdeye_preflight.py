#!/usr/bin/env python3
"""Run DEV-014 credential checks in a minimal, process-local environment.

The parent process never parses or receives the credential.  It invokes the
established POSIX ``set -a; . MONITOR_ENV_FILE`` convention in a short-lived
shell, which re-execs this file.  The bound child rebuilds its environment in
Python and supplies ``BIRDEYE`` only through ``subprocess.run(env=...)``.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

from src.ops.watchtower_lifecycle_probe import request_identity, validate_authorization


ROOT = Path(__file__).resolve().parents[1]
BOUND = "DEV014_BIRDEYE_ENV_BOUND"
ENV_FILE = Path("/Users/kevinkeaveney/Dev/claude/flex/.env")
MANIFEST = Path("/private/tmp/dev014_rank71_90_continuation_11_execution_20261010.v1.json")
STATE_DIR = Path("/private/tmp/dev014-r71-90-state")
EVIDENCE_DIR = Path("/private/tmp/dev014-r71-90-evidence")
SUPERVISOR_CONFIG = Path("/Users/kevinkeaveney/Dev/claude/flex/config/supervisor/supervisord.conf")
QUEUE_ROOT = Path("/Users/kevinkeaveney/Dev/claude/flex/.dev_runtime/monitor/dev_005a/queue")
LIFECYCLE_AUTHORIZATION_ROOT = Path("/private/tmp/dev014-lifecycle-authorizations")
LIFECYCLE_JOURNAL_ROOT = Path("/private/tmp/dev014-lifecycle-probes")


class CredentialBindingDenied(RuntimeError):
    """A credential may never be guessed, substituted, or disclosed."""


def _minimal_environment(credential: str) -> dict[str, str]:
    if not credential.strip():
        raise CredentialBindingDenied("BIRDEYE_REQUIRED")
    return {
        "PATH": "/Users/kevinkeaveney/anaconda3/envs/algotrader/bin:/usr/bin:/bin",
        "PYTHONPATH": str(ROOT),
        "BIRDEYE": credential,
        BOUND: "1",
    }


def _lifecycle_probe_paths(authorization: Path, *, authorization_root: Path = LIFECYCLE_AUTHORIZATION_ROOT,
                           journal_root: Path = LIFECYCLE_JOURNAL_ROOT) -> tuple[Path, Path]:
    """Bind frozen data to the one fixed probe runner, never an arbitrary command."""
    root = authorization_root.resolve(strict=True)
    candidate = authorization.resolve(strict=True)
    if candidate.is_symlink() or candidate.parent != root or candidate.suffix != ".json":
        raise CredentialBindingDenied("LIFECYCLE_AUTHORIZATION_PATH_DENIED")
    try:
        value = validate_authorization(json.loads(candidate.read_text()))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise CredentialBindingDenied("LIFECYCLE_AUTHORIZATION_INVALID") from exc
    identity = request_identity(value)
    if candidate.stem != identity:
        raise CredentialBindingDenied("LIFECYCLE_AUTHORIZATION_FILENAME_DENIED")
    if journal_root.is_symlink():
        raise CredentialBindingDenied("LIFECYCLE_JOURNAL_ROOT_DENIED")
    prior_journals = list(Path("/private/tmp").glob("dev014-lifecycle-probe*/journal.json"))
    prior_journals.extend(journal_root.glob("*/journal.json"))
    for journal in prior_journals:
        try:
            prior = json.loads(journal.read_text())
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise CredentialBindingDenied("LIFECYCLE_PRIOR_JOURNAL_UNREADABLE") from exc
        if prior.get("state") != "NEW" and (prior.get("request_identity") == identity or prior.get("mint") == value["mint"]):
            raise CredentialBindingDenied("LIFECYCLE_PRIOR_ATTEMPT_DENIED")
    return candidate, journal_root / identity / "journal.json"


def _source_then_reexec(env_file: Path, mode: str, cohort_root: Path | None = None,
                         max_requests: int | None = None, max_runtime_seconds: int | None = None,
                         max_evidence_bytes: int | None = None, lifecycle_authorization: Path | None = None) -> int:
    if not env_file.is_file():
        raise CredentialBindingDenied("MONITOR_ENV_FILE_UNAVAILABLE")
    # "$@" contains only interpreter/script/mode arguments.  BIRDEYE remains
    # in the operating-system environment and is never interpolated into argv.
    shell = 'set -aeu; . "$1"; shift; : "${BIRDEYE:?BIRDEYE_REQUIRED}"; export DEV014_BIRDEYE_ENV_BOUND=1; exec "$@"'
    target = [sys.executable, str(Path(__file__).resolve()), "--bound", "--mode", mode]
    if mode == "cohort":
        target = [sys.executable, str(ROOT / "scripts" / "run_dev014_historical_cohort.py"), "--bound", "--execute"]
        if cohort_root is not None:
            target.extend(("--cohort-root", str(cohort_root)))
        if max_requests is not None:
            target.extend(("--max-requests", str(max_requests)))
        if max_runtime_seconds is not None:
            target.extend(("--max-runtime-seconds", str(max_runtime_seconds)))
        if max_evidence_bytes is not None:
            target.extend(("--max-evidence-bytes", str(max_evidence_bytes)))
    if mode == "lifecycle-probe":
        if lifecycle_authorization is None:
            raise CredentialBindingDenied("LIFECYCLE_AUTHORIZATION_REQUIRED")
        authorization, journal = _lifecycle_probe_paths(lifecycle_authorization)
        target = [sys.executable, str(ROOT / "scripts" / "run_watchtower_lifecycle_probe.py"), "--authorization", str(authorization), "--journal", str(journal), "--queue-root", str(QUEUE_ROOT), "--supervisor-config", str(SUPERVISOR_CONFIG)]
    if mode == "lifecycle-cohort":
        target = [sys.executable, str(ROOT / "scripts" / "run_watchtower_lifecycle_cohort.py")]
    if mode == "rank-42-lifecycle-probe":
        target = [sys.executable, str(ROOT / "scripts" / "run_watchtower_lifecycle_probe.py"), "--authorization", "/private/tmp/dev014-lifecycle-probe-2.authorization.json", "--journal", "/private/tmp/dev014-lifecycle-probe-2/journal.json", "--queue-root", str(QUEUE_ROOT), "--supervisor-config", str(SUPERVISOR_CONFIG)]
    if mode == "rank-44-lifecycle-probe":
        target = [sys.executable, str(ROOT / "scripts" / "run_watchtower_lifecycle_probe.py"), "--authorization", "/private/tmp/dev014-lifecycle-probe-3.authorization.json", "--journal", "/private/tmp/dev014-lifecycle-probe-3/journal.json", "--queue-root", str(QUEUE_ROOT), "--supervisor-config", str(SUPERVISOR_CONFIG)]
    command = ["/bin/sh", "-ceu", shell, "sh", str(env_file), *target]
    return subprocess.run(command, check=False).returncode


def _credential_check() -> dict[str, object]:
    credential = os.environ.get("BIRDEYE", "")
    child_env = _minimal_environment(credential)
    code = """import json, os, sys
from src.ops.token_data_provider_bindings import BirdeyeProductionBinding
BirdeyeProductionBinding()
credential = os.environ.get('BIRDEYE', '')
print(json.dumps({'BIRDEYE_PRESENT': bool(credential), 'credential_label': 'BIRDEYE', 'fallback_selected': False, 'argv_contains_credential': credential in sys.argv}, sort_keys=True))
"""
    result = subprocess.run([sys.executable, "-c", code], env=child_env, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise CredentialBindingDenied("BIRDEYE_BINDING_REJECTED")
    return json.loads(result.stdout)


def _preflight() -> dict[str, object]:
    credential = os.environ.get("BIRDEYE", "")
    child_env = _minimal_environment(credential)
    code = """import json
from src.ops.token_data_provider_bindings import BirdeyeProductionBinding
from src.ops.dev014_batch4_runtime_gate import Batch4RuntimeGate, resolve_runtime_authority
BirdeyeProductionBinding()
authority = resolve_runtime_authority('/Users/kevinkeaveney/Dev/claude/flex/config/supervisor/supervisord.conf')
gate = Batch4RuntimeGate(authority).check()
print(json.dumps({'BIRDEYE_PRESENT': True, 'credential_label': 'BIRDEYE', 'fallback_selected': False, 'gate': gate}, sort_keys=True))
"""
    result = subprocess.run([sys.executable, "-c", code], env=child_env, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise CredentialBindingDenied("READ_ONLY_PREFLIGHT_REJECTED")
    return json.loads(result.stdout)


def _production_cli_command() -> list[str]:
    """The only paid command this wrapper may launch; no secret is an argument."""
    return [
        sys.executable, str(ROOT / "scripts" / "run_watchtower_historical_backfill_session.py"),
        "--mode", "execute", "--manifest", str(MANIFEST),
        "--state-dir", str(STATE_DIR), "--evidence-dir", str(EVIDENCE_DIR),
        "--max-requests", "11", "--max-runtime-seconds", "1800",
        "--max-evidence-bytes", "2000000", "--max-consecutive-failures", "2",
        "--max-health-failures", "1", "--live-opt-in",
        "--supervisor-config", str(SUPERVISOR_CONFIG), "--queue-root", str(QUEUE_ROOT),
    ]


def _execute_frozen_session(run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> int:
    """Launch exactly the already-authorized frozen session in a clean env."""
    env = _minimal_environment(os.environ.get("BIRDEYE", ""))
    result = run(_production_cli_command(), env=env, check=False)
    return int(result.returncode)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("verify", "preflight", "execute", "cohort", "lifecycle-probe", "lifecycle-cohort", "rank-42-lifecycle-probe", "rank-44-lifecycle-probe"), default="verify")
    parser.add_argument("--env-file", type=Path, default=ENV_FILE)
    parser.add_argument("--cohort-root", type=Path)
    parser.add_argument("--max-requests", type=int)
    parser.add_argument("--max-runtime-seconds", type=int)
    parser.add_argument("--max-evidence-bytes", type=int)
    parser.add_argument("--lifecycle-authorization", type=Path)
    parser.add_argument("--bound", action="store_true")
    args = parser.parse_args()
    try:
        if not args.bound:
            return _source_then_reexec(args.env_file, args.mode, args.cohort_root,
                                       args.max_requests, args.max_runtime_seconds, args.max_evidence_bytes,
                                       args.lifecycle_authorization)
        if os.environ.get(BOUND) != "1":
            raise CredentialBindingDenied("BOUND_PROCESS_REQUIRED")
        if args.mode == "execute":
            return _execute_frozen_session()
        if args.mode == "cohort":
            raise CredentialBindingDenied("COHORT_BOUND_PROCESS_REQUIRED")
        if args.mode in {"lifecycle-probe", "lifecycle-cohort", "rank-42-lifecycle-probe", "rank-44-lifecycle-probe"}:
            raise CredentialBindingDenied("LIFECYCLE_PROBE_BOUND_PROCESS_REQUIRED")
        value = _credential_check() if args.mode == "verify" else _preflight()
        print(json.dumps(value, sort_keys=True))
        return 0
    except CredentialBindingDenied as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
