"""Dry-run-first, byte-reversible installer for exactly two Watchtower programs."""
import argparse
import subprocess
import sys
from pathlib import Path
if __package__ in {None, ""}: sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.render_watchtower_supervisor import ALLOWED, remove_stanza, render_in_place

# Kept for the direct legacy installer unit test and callers.
remove = remove_stanza

def authority(root, sha):
    head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(root), "status", "--porcelain"], text=True)
    if head != sha or dirty: raise SystemExit("DEPLOYMENT_AUTHORITY_INVALID")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True); parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--sha", required=True); parser.add_argument("--include-dir", type=Path, required=True, help="rejected legacy include mode")
    parser.add_argument("--apply", action="store_true"); parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args(); authority(args.root, args.sha)
    if args.include_dir.name != "in-place-backup": raise SystemExit("INCLUDE_DEPLOYMENT_REJECTED_ROOT_GLOBAL_HERE_INTERPOLATION_MUTATED_BY_SUPERVISOR_4_3_0_INCLUDE_PROCESSING")
    managed = None; backup = args.include_dir / "supervisord.original.conf"
    if args.rollback:
        if not backup.exists(): raise SystemExit("ROLLBACK_BACKUP_MISSING")
        if args.apply:
            args.config.write_bytes(backup.read_bytes()); backup.unlink()
        else: print(backup.read_text())
        return
    source = args.config.read_text(); candidate, _ = render_in_place(source, args.root, args.sha)
    if args.apply:
        args.include_dir.mkdir(parents=True, exist_ok=True)
        if not backup.exists(): backup.write_bytes(args.config.read_bytes())
        args.config.write_text(candidate)
    else: sys.stdout.write(candidate)

if __name__ == "__main__": main()
