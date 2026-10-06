"""Dry-run-first, byte-reversible installer for exactly two Watchtower programs."""
import argparse
import subprocess
import sys
from pathlib import Path
if __package__ in {None, ""}: sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.render_watchtower_supervisor import ALLOWED, absolutize_supervisord_log_paths, remove_stanza, render_fragment, render_proposed_live

# Kept for the direct legacy installer unit test and callers.
remove = remove_stanza

def authority(root, sha):
    head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(root), "status", "--porcelain"], text=True)
    if head != sha or dirty: raise SystemExit("DEPLOYMENT_AUTHORITY_INVALID")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True); parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--sha", required=True); parser.add_argument("--include-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true"); parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args(); authority(args.root, args.sha)
    managed = args.include_dir / "watchtower_final.conf"; backup = args.include_dir / "watchtower_final.original.conf"
    if args.rollback:
        if not backup.exists(): raise SystemExit("ROLLBACK_BACKUP_MISSING")
        if args.apply:
            args.config.write_bytes(backup.read_bytes()); managed.unlink(missing_ok=True); backup.unlink()
        else: print(backup.read_text())
        return
    source = args.config.read_text()
    if all(f"[program:{name}]" not in source for name in ALLOWED) and managed.exists():
        candidate, fragment = source, render_fragment(args.root, args.sha)
        if str(args.include_dir / "*.conf") not in source or managed.read_text() != fragment:
            raise SystemExit("MANAGED_INSTALL_STATE_INVALID")
    else:
        candidate, fragment = render_proposed_live(source, args.root, args.sha, args.include_dir)
        candidate = absolutize_supervisord_log_paths(candidate, args.config)
    if args.apply:
        args.include_dir.mkdir(parents=True, exist_ok=True)
        if not backup.exists(): backup.write_bytes(args.config.read_bytes())
        managed.write_text(fragment); args.config.write_text(candidate)
    else: print(candidate)

if __name__ == "__main__": main()
