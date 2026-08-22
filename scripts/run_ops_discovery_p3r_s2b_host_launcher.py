"""Lifecycle-only persistent host wrapper for the fixed S2B reproduction runner."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--runner', required=True)
    ap.add_argument('--runner-audit', required=True)
    ap.add_argument('--manifest', required=True)
    ap.add_argument('--lifecycle-audit', required=True)
    ap.add_argument('--stdout-path', required=True)
    ap.add_argument('--stderr-path', required=True)
    ap.add_argument('--equivalence-digest', required=True)
    args = ap.parse_args()
    lifecycle, runner_audit = Path(args.lifecycle_audit), Path(args.runner_audit)
    command = [sys.executable, args.runner, '--manifest-path', args.manifest, '--audit-path', args.runner_audit,
               '--wall-seconds', '120', '--max-progress-calls', '100000', '--equivalence-digest', args.equivalence_digest]
    started = time.time()
    base = {'milestone': 'OPS-DISCOVERY-P3R-S2B-HOST-LAUNCH', 'launcher_pid': os.getpid(),
            'runner_command': command, 'working_directory': os.getcwd(), 'python_executable': sys.executable,
            'stdout_path': args.stdout_path, 'stderr_path': args.stderr_path, 'started_at_epoch': started,
            'provider_calls_made': 0, 'production_writes': 0, 'identity_selection': False, 'cohort_formed': False}
    write(lifecycle, {**base, 'status': 'LAUNCHER_STARTED'})
    with Path(args.stdout_path).open('w') as stdout, Path(args.stderr_path).open('w') as stderr:
        child = subprocess.Popen(command, cwd=os.getcwd(), stdout=stdout, stderr=stderr)
        write(lifecycle, {**base, 'status': 'RUNNER_SPAWNED', 'runner_pid': child.pid})
        checkpoint = False
        while child.poll() is None:
            if runner_audit.exists():
                try:
                    checkpoint = json.loads(runner_audit.read_text()).get('status') == 'STARTED' or checkpoint
                except json.JSONDecodeError:
                    pass
            time.sleep(0.25)
        terminal = None
        if runner_audit.exists():
            try: terminal = json.loads(runner_audit.read_text())
            except json.JSONDecodeError: terminal = {'status': 'UNPARSEABLE'}
    write(lifecycle, {**base, 'status': 'COMPLETE', 'runner_pid': child.pid, 'runner_started_checkpoint_observed': checkpoint,
                      'runner_exit_code': child.returncode, 'ended_at_epoch': time.time(), 'terminal_runner_audit': terminal})
    return child.returncode or 0

if __name__ == '__main__':
    raise SystemExit(main())
