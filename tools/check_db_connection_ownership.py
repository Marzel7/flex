#!/usr/bin/env python3
"""Static check: every SQLite connection must have deterministic closure.

Added after X21D.3 — a real production incident where a connection opened via
`self._ops()` (which wraps `db_connect`) in `src/core/ws_cascade.py` was
closed with a bare sequential `conn.close()` statement inside a `try:` block
instead of a `finally:`. When the statement between open and close raised (a
transient "database is locked"), the close was skipped and the surrounding
`except: pass` hid it completely — a silent, unbounded file-descriptor leak
that eventually blocked an unrelated process's writes for the better part of
an hour before being diagnosed.

The architectural rule this enforces:

    Every database connection has exactly one owner and exactly one
    deterministic close — via `with`, `@contextmanager`, or `try/finally`.
    A sequential `conn.close()` that a prior exception could skip is
    never acceptable, no matter how unlikely that exception seems today.

Policy (two-tier, deliberately NOT a single repo-wide gate):

  1. HARD GATE — supervisor-managed long-running daemons (see DAEMON_FILES
     below). These run for days/weeks; a leak here compounds silently and can
     take down an unrelated process, exactly as happened in the ws_cascade
     incident. Zero violations tolerated — this mode exits 1 on any hit.

  2. DIFF-ONLY GATE — everything else under src/. A one-shot migration script
     leaking a connection is a much smaller blast radius (the process exits
     immediately after). Re-litigating all pre-existing occurrences repo-wide
     would create an unpayable backlog before this check could ship at all.
     Instead, this mode only scans lines actually touched by the current
     diff (or explicitly-passed files), so it prevents the pattern from being
     *introduced or reintroduced* without demanding an immediate full-repo
     fix.

Usage:
    # Hard gate — CI must run this and fail the build on any hit:
    python3 tools/check_db_connection_ownership.py --daemons

    # Diff-only — check only files changed vs. a base ref (or explicit paths):
    python3 tools/check_db_connection_ownership.py --diff-against origin/main
    python3 tools/check_db_connection_ownership.py path/to/changed_file.py

    # Backlog report — count pre-existing hits across all of src/, grouped by
    # a rough risk tier, WITHOUT failing. Informational only.
    python3 tools/check_db_connection_ownership.py --backlog-report
"""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Every supervisor-managed, continuously-running process (confirmed against
# config/supervisor/supervisord.conf's [program:*] `command=` entries as of
# the X21D.3 incident). A leak in any of these compounds over the process's
# entire uptime, which can be days — this is the tier that actually caused
# the incident, so it gets the hard gate.
DAEMON_FILES = [
    "src/core/operation_scheduler.py",
    "src/core/ws_cascade.py",
    "src/core/ws_cascade_store.py",
    "src/core/creator_resolution_worker.py",
    "src/core/walkback_worker.py",
    "src/core/dust_observatory.py",
    "src/core/alert_evaluator.py",
    "src/core/webhook_worker.py",
    # Not a standalone supervisor program, but a background daemon THREAD
    # started inside long-running processes (gunicorn, pumpfun_curve_listener)
    # via _ensure_started()/threading.Thread(daemon=True). Added after the
    # X21D.4 incident: this thread's connection leak held a write lease for
    # ~15 hours inside pumpfun_curve_listener.
    "src/metrics/usage_tracker.py",
]

_CONNECTION_FACTORY_NAMES = {"_ops", "db_connect", "_connect", "connect", "_conn", "_conn_rw"}


def _is_connection_factory_call(call: ast.expr) -> bool:
    if not isinstance(call, ast.Call):
        return False
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr in _CONNECTION_FACTORY_NAMES
    if isinstance(func, ast.Name):
        return func.id in _CONNECTION_FACTORY_NAMES
    return False


def _closes_var_in_finally(finalbody: list[ast.stmt], varname: str) -> bool:
    return any(
        isinstance(sub, ast.Call)
        and isinstance(sub.func, ast.Attribute)
        and sub.func.attr == "close"
        and isinstance(sub.func.value, ast.Name)
        and sub.func.value.id == varname
        for stmt in finalbody
        for sub in ast.walk(stmt)
    )


def _closes_var_anywhere(stmts: list[ast.stmt], varname: str) -> bool:
    return any(
        isinstance(sub, ast.Call)
        and isinstance(sub.func, ast.Attribute)
        and sub.func.attr == "close"
        and isinstance(sub.func.value, ast.Name)
        and sub.func.value.id == varname
        for stmt in stmts
        for sub in ast.walk(stmt)
    )


def find_violations(source: str, filename: str) -> list[tuple[int, str, str]]:
    """Returns (line, varname, filename) for each unguarded sequential close."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []

    violations: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for i, stmt in enumerate(node.body):
            if not (
                isinstance(stmt, ast.Assign)
                and _is_connection_factory_call(stmt.value)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
            ):
                continue
            varname = stmt.targets[0].id

            if _closes_var_in_finally(node.finalbody, varname):
                continue

            # Guarded by ANY later nested try/finally in the same body (not just
            # the statement immediately following the assignment) — e.g. a
            # `row_factory = ...` setup line between the connect and the nested
            # try/finally is fine and must not produce a false positive.
            guarded_by_nested_try = any(
                isinstance(later, ast.Try) and _closes_var_in_finally(later.finalbody, varname)
                for later in node.body[i + 1:]
            )
            if guarded_by_nested_try:
                continue

            # Guarded by an explicit close() in EVERY except handler, in addition
            # to the sequential success-path close? This is a genuinely safe
            # (if verbose) both-paths-covered pattern — success closes it, and
            # each except handler independently re-closes it (typically wrapped
            # in its own try/except: pass, since it may already be closed).
            # Only exempt if ALL handlers close it, not just one.
            if node.handlers and all(
                _closes_var_anywhere(handler.body, varname) for handler in node.handlers
            ):
                continue

            if _closes_var_anywhere(node.body[i + 1:], varname):
                violations.append((stmt.lineno, varname, filename))

    return violations


def _scan_files(paths: list[Path]) -> list[tuple[int, str, str]]:
    all_violations: list[tuple[int, str, str]] = []
    for f in paths:
        if not f.exists() or f.suffix != ".py":
            continue
        try:
            source = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        all_violations.extend(find_violations(source, str(f)))
    return all_violations


def _changed_python_files(diff_against: str) -> list[Path]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{diff_against}...HEAD"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    files = [REPO_ROOT / line.strip() for line in result.stdout.splitlines() if line.strip().endswith(".py")]
    return files


def _risk_tier(path: Path) -> str:
    parts = path.parts
    name = path.name
    if any(str(path).endswith(d) for d in DAEMON_FILES):
        return "daemon"
    if "scripts" in parts and name.startswith("run_"):
        return "daemon_entrypoint"
    if "migration" in name.lower() or "schema" in name.lower():
        return "migration_script"
    if "test" in parts or name.startswith("test_"):
        return "test"
    return "one_shot_utility"


def cmd_daemons() -> int:
    paths = [REPO_ROOT / f for f in DAEMON_FILES]
    violations = _scan_files(paths)
    if violations:
        print("HARD GATE FAILURE — unguarded sequential connection-close pattern(s) "
              "found in a supervisor-managed long-running daemon file. These processes "
              "run for days/weeks; a leaked connection here compounds silently and can "
              "block an unrelated process's writes (exactly what happened in the "
              "X21D.3 incident — see tools/check_db_connection_ownership.py docstring).\n")
        for line, var, filename in sorted(violations):
            print(f"  {filename}:{line}  variable={var!r}")
        print(f"\n{len(violations)} violation(s) in daemon files. Fix: wrap the "
              "connection-using statement(s) in their own try/finally so close() "
              "always runs, or use a context manager.")
        return 1
    print(f"OK — zero unguarded connection-close patterns in {len(DAEMON_FILES)} daemon file(s).")
    return 0


def cmd_diff(diff_against: str | None, explicit_paths: list[str]) -> int:
    if explicit_paths:
        paths = [Path(p) for p in explicit_paths]
    elif diff_against:
        paths = _changed_python_files(diff_against)
    else:
        print("error: --diff-against <ref> or explicit file paths required for diff mode", file=sys.stderr)
        return 2

    violations = _scan_files(paths)
    if violations:
        print("Unguarded sequential connection-close pattern(s) introduced or "
              "present in changed file(s):\n")
        for line, var, filename in sorted(violations):
            print(f"  {filename}:{line}  variable={var!r}")
        print(f"\n{len(violations)} violation(s) in changed files. Fix: wrap the "
              "connection-using statement(s) in their own try/finally so close() "
              "always runs, or use a context manager.")
        return 1
    print(f"OK — no unguarded connection-close patterns in {len(paths)} changed file(s).")
    return 0


def cmd_backlog_report() -> int:
    src_dir = REPO_ROOT / "src"
    all_py = list(src_dir.rglob("*.py"))
    violations = _scan_files(all_py)

    by_tier: dict[str, set[str]] = {}
    for _, _, filename in violations:
        tier = _risk_tier(Path(filename))
        by_tier.setdefault(tier, set()).add(filename)

    print("Historical connection-lifecycle findings (informational — does not fail)\n")
    tier_order = ["daemon", "daemon_entrypoint", "migration_script", "one_shot_utility", "test"]
    for tier in tier_order:
        files = by_tier.get(tier, set())
        print(f"  {tier.replace('_', ' ').title()}")
        print(f"    {len(files)} file(s), {sum(1 for v in violations if _risk_tier(Path(v[2])) == tier)} occurrence(s)")
    print(f"\nTotal: {len(violations)} occurrence(s) across {len(set(v[2] for v in violations))} file(s).")
    print("\nTreat as technical debt, not a release blocker. The `daemon` tier should "
          "read 0 — if it doesn't, run --daemons to see the hard-gate failure.")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--daemons", action="store_true", help="Hard gate: scan only the curated daemon file list, fail on any hit.")
    parser.add_argument("--diff-against", metavar="REF", help="Diff-only gate: scan only .py files changed vs. this git ref.")
    parser.add_argument("--backlog-report", action="store_true", help="Informational report across all of src/, grouped by risk tier. Never fails.")
    parser.add_argument("paths", nargs="*", help="Explicit file paths to scan (diff-only gate, no git diff needed).")
    args = parser.parse_args(argv)

    if args.daemons:
        return cmd_daemons()
    if args.backlog_report:
        return cmd_backlog_report()
    if args.diff_against or args.paths:
        return cmd_diff(args.diff_against, args.paths)

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
