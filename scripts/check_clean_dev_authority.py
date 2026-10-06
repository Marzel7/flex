#!/usr/bin/env python3
"""Fail-closed preflight for commit-pinned DEV runtimes."""
from __future__ import annotations
import argparse, subprocess, sys
from pathlib import Path

def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument("--sha", required=True); p.add_argument("--worktree", required=True); p.add_argument("--remote-ref"); p.add_argument("--require-clean", action="store_true"); p.add_argument("--require-tracked", action="append", default=[])
    a=p.parse_args(); root=Path(a.worktree).resolve()
    try:
        if git(root,"rev-parse","HEAD") != a.sha: raise ValueError("HEAD_SHA_MISMATCH")
        git(root,"cat-file","-e",a.sha+"^{commit}")
        if a.require_clean and git(root,"status","--porcelain"): raise ValueError("WORKTREE_DIRTY")
        if a.remote_ref and not git(root,"merge-base","--is-ancestor",a.sha,a.remote_ref) == "": raise ValueError("SHA_NOT_ON_APPROVED_REMOTE_REF")
        for value in a.require_tracked:
            path=(root/value).resolve()
            if root not in path.parents and path != root: raise ValueError("RUNTIME_ROOT_OUTSIDE_APPROVED_WORKTREE")
            if not path.exists() or git(root,"ls-files","--error-unmatch",str(path.relative_to(root))) == "": raise ValueError("REQUIRED_SOURCE_UNTRACKED")
    except (subprocess.CalledProcessError, ValueError) as exc:
        print(str(exc), file=sys.stderr); return 2
    print("CLEAN_DEV_AUTHORITY_PASS"); return 0
if __name__ == "__main__": raise SystemExit(main())
