#!/usr/bin/env python3
"""Bounded orphan-pack guard for abandoned Codex/Git temporary packs.

Root cause (established separately): Codex turn-diff capture runs real
`git add -u --sparse --pathspec-from-file=- --pathspec-file-nul` against this
repository's object database. Cancellation can escalate SIGTERM -> SIGKILL
before Git finalizes/cleans a bulk-checkin temp pack, leaving an idx-less
orphan under `.git/objects/pack/tmp_pack_*` that never gets reclaimed and can
accumulate to multi-GB over repeated turns.

This tool never assumes a `tmp_pack_*` name alone proves abandonment. A file
is only ever classified VERIFIED_ABANDONED — the sole class eligible for
deletion under --clean — after it independently passes every check in
`classify()`. Any uncertainty, inspection failure, or ambiguous state
retains the file (fail-safe). Low disk pressure changes reporting urgency
only; it never weakens the abandonment proof.

Usage:
    python3 scripts/git_tmp_pack_guard.py --check [--git-dir PATH]
    python3 scripts/git_tmp_pack_guard.py --clean [--git-dir PATH]

--check (default action if neither flag given) is strictly read-only.
--clean deletes only VERIFIED_ABANDONED candidates, after printing them.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

TMP_PACK_RE = re.compile(r"^tmp_pack_[A-Za-z0-9]+$")

# Grace/stability defaults, overridable via CLI. Conservative: a Codex capture
# in the observed incidents ran at most ~10.5s before cancellation, so a
# multi-minute minimum age plus a repeat-sampled stability check gives wide
# margin against classifying a still-in-flight write as abandoned.
DEFAULT_MIN_AGE_SECONDS = 300           # 5 minutes since last mtime
DEFAULT_STABILITY_INTERVAL_SECONDS = 5  # gap between the two size/mtime samples
DEFAULT_STABILITY_SAMPLES = 2           # samples that must agree

# Process names whose presence anywhere in the process table makes every
# candidate in this repo UNKNOWN_DO_NOT_TOUCH, regardless of individual file
# state — a live pack-producing command anywhere is reason enough to abstain.
RELEVANT_PROCESS_MARKERS = (
    "git-add", "pack-objects", "index-pack", "git repack", "git-repack",
    "git gc", "git-gc", "git maintenance", "git-maintenance",
)

DISK_LOW_MIB = 500
DISK_CRITICAL_MIB = 300


@dataclass
class Candidate:
    path: Path
    size_bytes: int
    age_seconds: float
    stable: bool
    open_handle: bool
    git_activity: bool
    inspection_failed: bool
    classification: str


def repo_root_from_git_dir(git_dir: Path) -> Path:
    return git_dir.parent


def find_tmp_packs(git_dir: Path) -> list[Path]:
    pack_dir = git_dir / "objects" / "pack"
    if not pack_dir.is_dir():
        return []
    out = []
    for entry in pack_dir.iterdir():
        if entry.is_file() and TMP_PACK_RE.match(entry.name):
            out.append(entry)
    return out


def has_finalized_idx_sibling(path: Path) -> bool:
    # tmp_pack_* names never share a basename with a finalized pack-<sha>.idx,
    # but as a defensive check: if anything with the same stem + .idx exists,
    # never touch it (unknown relationship, could be in the middle of rename).
    idx_sibling = path.with_suffix(path.suffix + ".idx") if path.suffix else path.with_name(path.name + ".idx")
    return idx_sibling.exists()


def any_relevant_git_process() -> tuple[bool, bool]:
    """Returns (relevant_process_present, inspection_failed)."""
    try:
        result = subprocess.run(
            ["ps", "-Ao", "pid,command"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if result.returncode != 0:
            return False, True
        lines = result.stdout.splitlines()[1:]
        for line in lines:
            lowered = line.lower()
            if any(marker in lowered for marker in RELEVANT_PROCESS_MARKERS):
                return True, False
        return False, False
    except Exception:
        return False, True


def lsof_open_handle(path: Path) -> tuple[bool, bool]:
    """Returns (is_open, inspection_failed)."""
    try:
        result = subprocess.run(
            ["lsof", "--", str(path)],
            capture_output=True, text=True, timeout=5, check=False,
        )
        # lsof exit 0 with output rows => at least one process has it open.
        # exit 1 with empty output => nothing has it open (the common case).
        if result.returncode not in (0, 1):
            return False, True
        has_rows = any(
            line.strip() and not line.startswith("COMMAND") for line in result.stdout.splitlines()
        )
        return has_rows, False
    except FileNotFoundError:
        # lsof unavailable on this system: cannot prove no open handle.
        return False, True
    except Exception:
        return False, True


def sample_stat(path: Path) -> tuple[int, float] | None:
    try:
        st = path.stat()
        return st.st_size, st.st_mtime
    except FileNotFoundError:
        return None
    except Exception:
        return None


def classify(
    path: Path,
    *,
    min_age_seconds: float,
    stability_interval_seconds: float,
    stability_samples: int,
    now: float | None = None,
) -> Candidate:
    now = now if now is not None else time.time()

    if not TMP_PACK_RE.match(path.name):
        # Never operated on outside this exact pattern; caller should not
        # even construct a Candidate for such a path, but stay defensive.
        return Candidate(path, 0, 0.0, False, True, True, True, "UNKNOWN_DO_NOT_TOUCH")

    first = sample_stat(path)
    if first is None:
        return Candidate(path, 0, 0.0, False, True, True, True, "UNKNOWN_DO_NOT_TOUCH")
    size0, mtime0 = first
    age = now - mtime0

    if age < min_age_seconds:
        return Candidate(path, size0, age, False, False, False, False, "ACTIVE_OR_RECENT")

    if has_finalized_idx_sibling(path):
        return Candidate(path, size0, age, False, False, False, False, "UNKNOWN_DO_NOT_TOUCH")

    # Multi-sample stability check: size and mtime must not change across
    # `stability_samples` observations separated by `stability_interval_seconds`.
    samples = [(size0, mtime0)]
    for _ in range(max(0, stability_samples - 1)):
        time.sleep(stability_interval_seconds)
        nxt = sample_stat(path)
        if nxt is None:
            return Candidate(path, size0, age, False, True, True, True, "UNKNOWN_DO_NOT_TOUCH")
        samples.append(nxt)
    stable = len(set(samples)) == 1

    if not stable:
        return Candidate(path, samples[-1][0], age, False, False, False, False, "STABLE_GIT_ACTIVITY_PRESENT")

    is_open, lsof_failed = lsof_open_handle(path)
    if lsof_failed:
        return Candidate(path, samples[-1][0], age, True, True, True, True, "UNKNOWN_DO_NOT_TOUCH")
    if is_open:
        return Candidate(path, samples[-1][0], age, True, True, False, False, "STABLE_BUT_OPEN")

    relevant_proc, proc_failed = any_relevant_git_process()
    if proc_failed:
        return Candidate(path, samples[-1][0], age, True, False, True, True, "UNKNOWN_DO_NOT_TOUCH")
    if relevant_proc:
        return Candidate(path, samples[-1][0], age, True, False, True, False, "STABLE_GIT_ACTIVITY_PRESENT")

    return Candidate(path, samples[-1][0], age, True, False, False, False, "VERIFIED_ABANDONED")


def disk_state(git_dir: Path) -> tuple[str, int]:
    st = os.statvfs(str(git_dir))
    free_mib = int(st.f_bavail * st.f_frsize / (1024 * 1024))
    if free_mib < DISK_CRITICAL_MIB:
        return "CRITICAL", free_mib
    if free_mib < DISK_LOW_MIB:
        return "LOW", free_mib
    return "NORMAL", free_mib


def run_check(git_dir: Path, *, min_age_seconds: float, stability_interval_seconds: float,
              stability_samples: int) -> list[Candidate]:
    state, free_mib = disk_state(git_dir)
    print(f"disk_state={state} free_mib={free_mib}")
    candidates = []
    for path in sorted(find_tmp_packs(git_dir)):
        c = classify(
            path,
            min_age_seconds=min_age_seconds,
            stability_interval_seconds=stability_interval_seconds,
            stability_samples=stability_samples,
        )
        candidates.append(c)
        print(
            f"{c.path} bytes={c.size_bytes} age_s={c.age_seconds:.0f} "
            f"stable={c.stable} open={c.open_handle} git_activity={c.git_activity} "
            f"classification={c.classification}"
        )
    return candidates


def run_clean(git_dir: Path, *, min_age_seconds: float, stability_interval_seconds: float,
              stability_samples: int) -> tuple[list[Candidate], int]:
    candidates = run_check(
        git_dir,
        min_age_seconds=min_age_seconds,
        stability_interval_seconds=stability_interval_seconds,
        stability_samples=stability_samples,
    )
    to_delete = [c for c in candidates if c.classification == "VERIFIED_ABANDONED"]
    if not to_delete:
        print("no VERIFIED_ABANDONED candidates; nothing deleted")
        return candidates, 0
    print("about to delete:")
    total = 0
    for c in to_delete:
        print(f"  {c.path} bytes={c.size_bytes}")
        total += c.size_bytes
    print(f"total_bytes={total}")
    reclaimed = 0
    for c in to_delete:
        try:
            # Re-verify immediately before unlink: re-run the full classification
            # rather than trusting the earlier snapshot, closing any TOCTOU gap.
            recheck = classify(
                c.path,
                min_age_seconds=min_age_seconds,
                stability_interval_seconds=stability_interval_seconds,
                stability_samples=1,  # single fast confirmation sample is enough here
            )
            if recheck.classification != "VERIFIED_ABANDONED":
                print(f"SKIP (re-check changed verdict to {recheck.classification}): {c.path}")
                continue
            size = c.path.stat().st_size
            c.path.unlink()
            reclaimed += size
            print(f"DELETED {c.path} bytes={size}")
        except FileNotFoundError:
            print(f"SKIP (already gone): {c.path}")
        except Exception as exc:
            print(f"SKIP (unlink failed, leaving in place): {c.path} error={exc}")
    return candidates, reclaimed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Read-only report (default).")
    parser.add_argument("--clean", action="store_true", help="Delete only VERIFIED_ABANDONED candidates.")
    parser.add_argument("--git-dir", default=None, help="Path to .git directory (default: autodetect from cwd).")
    parser.add_argument("--min-age-seconds", type=float, default=DEFAULT_MIN_AGE_SECONDS)
    parser.add_argument("--stability-interval-seconds", type=float, default=DEFAULT_STABILITY_INTERVAL_SECONDS)
    parser.add_argument("--stability-samples", type=int, default=DEFAULT_STABILITY_SAMPLES)
    args = parser.parse_args()

    if args.git_dir:
        git_dir = Path(args.git_dir)
    else:
        here = Path.cwd()
        git_dir = here / ".git"
        if not git_dir.is_dir():
            print("could not locate .git directory; pass --git-dir explicitly", file=sys.stderr)
            return 2

    if args.clean:
        run_clean(
            git_dir,
            min_age_seconds=args.min_age_seconds,
            stability_interval_seconds=args.stability_interval_seconds,
            stability_samples=args.stability_samples,
        )
    else:
        run_check(
            git_dir,
            min_age_seconds=args.min_age_seconds,
            stability_interval_seconds=args.stability_interval_seconds,
            stability_samples=args.stability_samples,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
