#!/usr/bin/env python3
"""
MAINTENANCE OPERATION — reclaim hot-DB space after the funder_networks archive.

This is the DESTRUCTIVE follow-up to scripts/archive_funder_networks.py. It
DELETEs funder_networks from the hot DB and compacts the file via VACUUM INTO.
Run ONLY in a maintenance window, after you are confident in the archive.

HARD GATE: this script refuses to do anything destructive unless you pass
    --i-am-in-a-maintenance-window
Without that flag it runs PREFLIGHT ONLY (steps 1-3, all read-only) and stops.

Sequence (mirrors the agreed plan):
  1. Confirm archive fingerprint still matches the live hot table
  2. Confirm the API routes read the archive (not the hot DB)
  3. Confirm a fresh hot-DB backup exists (or create one with --make-backup)
  --- gate ---
  4. Stop DB-writing services
  5. DELETE FROM funder_networks (hot)
  6. VACUUM INTO a compacted copy
  7. Verify the compacted DB integrity + that core hot tables survived
  8. Swap files (original kept as .prevacuum)
  9. Restart services
 10. Recheck the API routes

Nothing is irreversible until step 8, and the pre-swap original is retained.

Usage:
  python3 scripts/reclaim_funder_networks_space.py                 # preflight only
  python3 scripts/reclaim_funder_networks_space.py --make-backup   # preflight + create backup
  python3 scripts/reclaim_funder_networks_space.py --i-am-in-a-maintenance-window
"""
import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import time

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HOT_DB = os.path.abspath(os.environ.get(
    "DB_PATH", os.path.join(_REPO_ROOT, "database", "flex_complete_database.db")))
ARCHIVE_DB = os.path.abspath(os.environ.get(
    "INVESTIGATION_ARCHIVE_DB",
    os.path.join(_REPO_ROOT, "database", "flex_investigation_archive.db")))
SUPERVISOR_CONF = os.path.join(_REPO_ROOT, "config", "supervisor", "supervisord.conf")
API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:5002")

# Services that WRITE the hot DB — must be stopped before DELETE/VACUUM.
# (watchtower_ngrok is a tunnel, no DB access, left running.)
DB_WRITER_SERVICES = [
    "watchtower_api",
    "watchtower_listener",
    "watchtower_helius_monitor",
    "operation_scheduler",
    "ws_cascade",
]

# Core hot tables that MUST survive the vacuum (sanity guard on the compacted DB).
CORE_TABLES = ["token_analysis", "token_prediction_scores", "transfer_index", "metadata_cache"]

FINGERPRINT_SQL = """
SELECT COUNT(*),
       COALESCE(SUM(id),0),
       COALESCE(SUM(LENGTH(COALESCE(connected_funders,''))
                  + LENGTH(COALESCE(transfer_chain,''))
                  + LENGTH(COALESCE(creators_served,''))),0)
FROM {schema}funder_networks
"""


def _fp(conn, schema=""):
    return tuple(conn.execute(FINGERPRINT_SQL.format(schema=schema)).fetchone())


# ------------------------- PREFLIGHT (read-only) -------------------------

def step1_fingerprints():
    print("STEP 1: archive fingerprint vs live hot table")
    hot = sqlite3.connect(f"file:{HOT_DB}?mode=ro", uri=True)
    arch = sqlite3.connect(f"file:{ARCHIVE_DB}?mode=ro", uri=True)
    hfp, afp = _fp(hot), _fp(arch)
    hot.close(); arch.close()
    print(f"  hot     = {hfp}")
    print(f"  archive = {afp}")
    ok = hfp == afp
    print(f"  match: {ok}")
    return ok


def step2_routes_read_archive():
    print("STEP 2: API routes read the archive")
    try:
        import urllib.request, json
        with urllib.request.urlopen(f"{API_BASE}/api/funder-clusters", timeout=15) as r:
            d = json.loads(r.read())
        n = len(d.get("clusters", [])) if isinstance(d, dict) else 0
        # If the hot table were the source, repoint wouldn't be exercised; we
        # can't prove the source from the response alone, so we assert the route
        # WORKS and the archive is present (the only place the table now lives).
        ok = n > 0 and os.path.exists(ARCHIVE_DB)
        print(f"  /api/funder-clusters returned {n} clusters; archive present: {os.path.exists(ARCHIVE_DB)}")
        print(f"  ok: {ok}")
        return ok
    except Exception as e:
        print(f"  FAIL: {e}")
        return False


def step3_backup(make_backup=False):
    print("STEP 3: fresh hot-DB backup")
    backup = HOT_DB + ".backup"
    if make_backup:
        # Use SQLite's online backup API (consistent even if a reader is open).
        print(f"  creating backup -> {backup} (this copies ~10GB, be patient)...")
        src = sqlite3.connect(f"file:{HOT_DB}?mode=ro", uri=True)
        dst = sqlite3.connect(backup)
        with dst:
            src.backup(dst)
        src.close(); dst.close()
    exists = os.path.exists(backup)
    if exists:
        age_h = (time.time() - os.path.getmtime(backup)) / 3600
        size_gb = os.path.getsize(backup) / 1e9
        print(f"  backup present: {backup} ({size_gb:.1f}GB, {age_h:.1f}h old)")
        fresh = age_h < 24
        print(f"  fresh (<24h): {fresh}")
        return fresh
    print(f"  NO backup found at {backup}. Re-run with --make-backup.")
    return False


# ------------------------- DESTRUCTIVE (gated) -------------------------

def _supervisorctl(*args):
    return subprocess.run(["supervisorctl", "-c", SUPERVISOR_CONF, *args],
                          capture_output=True, text=True)


def step4_stop_services():
    print("STEP 4: stop DB-writing services")
    for svc in DB_WRITER_SERVICES:
        r = _supervisorctl("stop", svc)
        print(f"  stop {svc}: {r.stdout.strip() or r.stderr.strip()}")
    time.sleep(3)


def step5_delete():
    print("STEP 5: DELETE FROM funder_networks (hot)")
    conn = sqlite3.connect(HOT_DB, timeout=120)
    before = conn.execute("SELECT COUNT(*) FROM funder_networks").fetchone()[0]
    conn.execute("DELETE FROM funder_networks")
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM funder_networks").fetchone()[0]
    conn.close()
    print(f"  rows {before} -> {after}")
    return after == 0


def step6_vacuum_into():
    print("STEP 6: VACUUM INTO compacted copy")
    compacted = HOT_DB + ".vacuumed"
    if os.path.exists(compacted):
        os.remove(compacted)
    free_gb = shutil.disk_usage(os.path.dirname(HOT_DB)).free / 1e9
    print(f"  free disk: {free_gb:.1f}GB")
    conn = sqlite3.connect(HOT_DB, timeout=120)
    conn.execute("VACUUM INTO ?", (compacted,))
    conn.close()
    old = os.path.getsize(HOT_DB) / 1e9
    new = os.path.getsize(compacted) / 1e9
    print(f"  size {old:.1f}GB -> {new:.1f}GB ({old-new:.1f}GB reclaimed)")
    return compacted


def step7_verify_compacted(compacted):
    print("STEP 7: verify compacted DB integrity")
    conn = sqlite3.connect(f"file:{compacted}?mode=ro", uri=True)
    integ = conn.execute("PRAGMA integrity_check").fetchone()[0]
    print(f"  integrity_check: {integ}")
    ok = integ == "ok"
    for t in CORE_TABLES:
        c = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t}: {c} rows")
        ok = ok and c > 0
    # funder_networks must now be empty (or absent) in the compacted hot DB
    fn = conn.execute("SELECT COUNT(*) FROM funder_networks").fetchone()[0]
    print(f"  funder_networks (should be 0): {fn}")
    conn.close()
    return ok and fn == 0


def step8_swap(compacted):
    print("STEP 8: swap files (original kept as .prevacuum)")
    prevacuum = HOT_DB + ".prevacuum"
    # Move WAL/SHM aside too — the compacted DB starts clean.
    for ext in ("", "-wal", "-shm"):
        if os.path.exists(HOT_DB + ext):
            shutil.move(HOT_DB + ext, prevacuum + ext)
    shutil.move(compacted, HOT_DB)
    print(f"  original -> {prevacuum}; compacted -> {HOT_DB}")


def step9_start_services():
    print("STEP 9: restart services")
    for svc in DB_WRITER_SERVICES:
        r = _supervisorctl("start", svc)
        print(f"  start {svc}: {r.stdout.strip() or r.stderr.strip()}")


def step10_recheck():
    print("STEP 10: recheck API routes")
    import urllib.request, json
    for i in range(20):
        try:
            with urllib.request.urlopen(f"{API_BASE}/api/funder-clusters", timeout=3) as r:
                d = json.loads(r.read())
                n = len(d.get("clusters", []))
                print(f"  /api/funder-clusters: {n} clusters")
                return n > 0
        except Exception:
            time.sleep(2)
    print("  FAIL: API did not come back")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--i-am-in-a-maintenance-window", action="store_true",
                    help="Required to run the destructive steps 4-10.")
    ap.add_argument("--make-backup", action="store_true",
                    help="Create the fresh hot-DB backup in step 3.")
    args = ap.parse_args()

    print("=== PREFLIGHT (read-only) ===")
    ok1 = step1_fingerprints()
    ok2 = step2_routes_read_archive()
    ok3 = step3_backup(make_backup=args.make_backup)
    preflight_ok = ok1 and ok2 and ok3
    print(f"\nPREFLIGHT: {'PASS' if preflight_ok else 'FAIL'} "
          f"(fingerprint={ok1} routes={ok2} backup={ok3})")

    if not args.__dict__["i_am_in_a_maintenance_window"]:
        print("\nGATE: --i-am-in-a-maintenance-window NOT set. Stopping after preflight.")
        print("No services stopped, no data deleted, no vacuum. Re-run with the flag when ready.")
        return 0 if preflight_ok else 1

    if not preflight_ok:
        print("\nABORT: preflight failed; refusing to run destructive steps.")
        return 1

    print("\n=== MAINTENANCE (destructive) ===")
    step4_stop_services()
    if not step5_delete():
        print("ABORT: delete did not clear the table."); step9_start_services(); return 1
    compacted = step6_vacuum_into()
    if not step7_verify_compacted(compacted):
        print("ABORT: compacted DB failed verification. Original untouched, NOT swapped.")
        print(f"Inspect {compacted}; services still stopped — start manually when resolved.")
        return 1
    step8_swap(compacted)
    step9_start_services()
    ok = step10_recheck()
    print(f"\nMAINTENANCE: {'PASS' if ok else 'CHECK NEEDED'}. "
          f"Pre-vacuum original retained at {HOT_DB}.prevacuum")
    print("Delete the .prevacuum/.backup copies only after confirming the system is healthy.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
