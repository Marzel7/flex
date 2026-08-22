import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path


RUNNER = Path("scripts/freeze_ops_discovery_p3r_s2b_source_boundary.py")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def surface_digest(path, table, fields):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    digest = hashlib.sha256()
    count = 0
    for row in conn.execute(f"SELECT {','.join(fields)} FROM {table} ORDER BY mint ASC"):
        digest.update((json.dumps(row, separators=(",", ":"), ensure_ascii=True) + "\n").encode())
        count += 1
    conn.close()
    return count, digest.hexdigest()


def reference_snapshot(source, target):
    source_conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    out = sqlite3.connect(target)
    out.executescript("CREATE TABLE token_analysis (mint TEXT PRIMARY KEY, pf_ws_creator TEXT); CREATE INDEX idx_ta_pf_ws_creator ON token_analysis(pf_ws_creator); CREATE TABLE pumpfun_migration_verification (mint TEXT PRIMARY KEY);")
    for table, fields in (("token_analysis", "mint,pf_ws_creator"), ("pumpfun_migration_verification", "mint")):
        batch = []
        for row in source_conn.execute(f"SELECT {fields} FROM {table} ORDER BY mint ASC"):
            batch.append(row)
            if len(batch) == 5000:
                out.executemany(f"INSERT INTO {table} ({fields}) VALUES ({','.join('?' for _ in fields.split(','))})", batch)
                out.commit()
                batch = []
        if batch:
            out.executemany(f"INSERT INTO {table} ({fields}) VALUES ({','.join('?' for _ in fields.split(','))})", batch)
            out.commit()
    source_conn.close()
    out.close()


def test_optimized_capture_preserves_reference_snapshot_and_boundary_semantics(tmp_path):
    source = tmp_path / "source.sqlite"
    conn = sqlite3.connect(source)
    conn.executescript("CREATE TABLE token_analysis (mint TEXT PRIMARY KEY, pf_ws_creator TEXT); CREATE TABLE pumpfun_migration_verification (mint TEXT PRIMARY KEY);")
    conn.executemany("INSERT INTO token_analysis VALUES (?,?)", [(f"mint-{i:05d}", None if i % 19 == 0 else f"creator-{i % 29}") for i in range(7000, 0, -1)])
    conn.executemany("INSERT INTO pumpfun_migration_verification VALUES (?)", [(f"mint-{i:05d}",) for i in range(7000, 0, -3)])
    conn.commit()
    conn.close()
    reference = tmp_path / "reference.sqlite"
    optimized = tmp_path / "optimized.sqlite"
    audit = tmp_path / "optimized.audit.json"
    reference_snapshot(source, reference)
    result = subprocess.run([sys.executable, str(RUNNER), "--source-db", str(source), "--snapshot-db", str(optimized), "--audit-path", str(audit), "--wall-seconds", "300"], text=True, capture_output=True, check=False)
    outcome = json.loads(audit.read_text())
    assert result.returncode == 0, result.stderr
    assert outcome["status"] == "COMPLETE"
    assert outcome["replay_identical"] is True
    assert sha256(reference) == sha256(optimized)
    reference_surfaces = {}
    for table, fields in (("token_analysis", ("mint", "pf_ws_creator")), ("pumpfun_migration_verification", ("mint",))):
        count, digest = surface_digest(reference, table, fields)
        reference_surfaces[table] = {"columns": list(fields), "row_count": count, "sha256": digest, "source_rowid_high_water": outcome["surfaces"][table]["source_rowid_high_water"]}
    assert all(outcome["surfaces"][table]["row_count"] == count for table, count in {"token_analysis": 7000, "pumpfun_migration_verification": 2334}.items())
    assert outcome["surfaces"] == reference_surfaces
    assert all(outcome["surfaces"][table]["sha256"] == outcome["replay_surfaces"][table]["sha256"] for table in outcome["surfaces"])
    normalized_reference = {"source_meta": outcome["source_identity_at_read_open"], "surfaces": reference_surfaces, "snapshot_path": "NORMALIZED", "snapshot_sha256": sha256(reference)}
    normalized_optimized = {"source_meta": outcome["source_identity_at_read_open"], "surfaces": outcome["surfaces"], "snapshot_path": "NORMALIZED", "snapshot_sha256": sha256(optimized)}
    assert hashlib.sha256(json.dumps(normalized_reference, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == hashlib.sha256(json.dumps(normalized_optimized, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert optimized.with_suffix(".sqlite.capture.sqlite").exists()
