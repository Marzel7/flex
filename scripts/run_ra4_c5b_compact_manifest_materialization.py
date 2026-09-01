"""RA4-C5B: materialize a compact, deterministic historical retirement index
for the legacy retained_acquisition.db (61.6 GiB), so it can eventually be
deleted without losing the correlation/lineage evidence classified
REQUIRED_UNIQUE in RA4-C5.

READ-ONLY against the legacy DB. Streams rows via a server-side cursor with a
bounded fetch size -- never fetchall()'s the full 1.9M-row table. Writes a new
compact SQLite index at database/evidence_platform/archive/retained_acquisition_legacy_compact.db
and never touches the legacy file.

Compact schema keeps only fields independently classified REQUIRED_UNIQUE or
needed to resolve REDUNDANT raw bytes via the ArtifactStore:
  observation_id, acquisition_id, correlation_id, launch_mint, retained_at,
  provider, method, purpose, request_type, creator, http_method, response_status,
  artifact_digest, artifact_size_bytes, artifact_compressed_bytes,
  artifact_representation, content_type, url (sanitized), request_payload_sha256,
  response_headers_sha256, source_schema_version.

Excludes (recoverable via artifact_digest -> ArtifactStore):
  response_data, raw_body_base64, response_text, request_payload (body),
  response_headers (full dict).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.acquisition.retained_observations import canonical, sanitize_url  # noqa: E402

LEGACY_PATH = Path("database/evidence_platform/production/retained_acquisition.db")
COMPACT_PATH = Path("database/evidence_platform/archive/retained_acquisition_legacy_compact.db")
FETCH_CHUNK = 2000

COMPACT_SCHEMA = """
CREATE TABLE compact_observations (
    observation_id TEXT PRIMARY KEY,
    source_schema_version INTEGER NOT NULL,
    acquisition_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    launch_mint TEXT,
    retained_at INTEGER NOT NULL,
    source_timestamp REAL,
    provider TEXT,
    method TEXT,
    purpose TEXT,
    request_type TEXT,
    creator TEXT,
    http_method TEXT,
    response_status INTEGER,
    url TEXT,
    artifact_digest TEXT NOT NULL,
    artifact_size_bytes INTEGER,
    artifact_compressed_bytes INTEGER,
    artifact_representation TEXT,
    content_type TEXT,
    request_payload_sha256 TEXT,
    response_headers_sha256 TEXT
);
CREATE INDEX idx_compact_mint ON compact_observations(launch_mint);
CREATE INDEX idx_compact_acquisition ON compact_observations(acquisition_id);
CREATE INDEX idx_compact_correlation ON compact_observations(correlation_id);
CREATE INDEX idx_compact_retained_at ON compact_observations(retained_at);

CREATE TABLE compact_outcomes_summary (
    outcome TEXT PRIMARY KEY,
    count INTEGER NOT NULL
);

CREATE TABLE compact_gaps (
    gap_id TEXT PRIMARY KEY,
    acquisition_id TEXT,
    launch_mint TEXT,
    correlation_id TEXT,
    purpose TEXT,
    provider TEXT,
    method TEXT,
    reason TEXT NOT NULL,
    recorded_at INTEGER NOT NULL
);

CREATE TABLE manifest_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _sha256_json(value) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(canonical(value)).hexdigest()


def main() -> None:
    if COMPACT_PATH.exists():
        print(f"FAIL_CLOSED: {COMPACT_PATH} already exists. Refusing to overwrite silently.")
        sys.exit(1)

    legacy_identity = {
        "size_bytes": LEGACY_PATH.stat().st_size,
        "mtime": int(LEGACY_PATH.stat().st_mtime),
        "inode": LEGACY_PATH.stat().st_ino,
    }
    print("legacy identity:", legacy_identity)

    COMPACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    out = sqlite3.connect(COMPACT_PATH)
    out.executescript(COMPACT_SCHEMA)

    src = sqlite3.connect(f"file:{LEGACY_PATH.resolve()}?mode=ro", uri=True)
    src_cur = src.cursor()
    src_cur.execute(
        "SELECT observation_id, schema_version, launch_mint, acquisition_id, "
        "correlation_id, payload_json, retained_at FROM retained_acquisition_observations"
    )

    row_count = 0
    t0 = time.time()
    insert_sql = (
        "INSERT INTO compact_observations VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    )
    while True:
        chunk = src_cur.fetchmany(FETCH_CHUNK)
        if not chunk:
            break
        to_insert = []
        for observation_id, schema_version, launch_mint, acquisition_id, correlation_id, payload_json, retained_at in chunk:
            payload = json.loads(payload_json)
            metadata = payload.get("metadata") or {}
            url = payload.get("url")
            sanitized_url = sanitize_url(url) if url else None
            request_payload = payload.get("request_payload")
            response_headers = payload.get("response_headers")
            to_insert.append((
                observation_id,
                schema_version,
                acquisition_id,
                correlation_id,
                launch_mint,
                retained_at,
                metadata.get("timestamp"),
                metadata.get("provider"),
                metadata.get("method"),
                metadata.get("purpose"),
                metadata.get("request_type"),
                metadata.get("creator"),
                payload.get("http_method"),
                payload.get("response_status"),
                sanitized_url,
                payload.get("artifact_digest"),
                payload.get("artifact_size_bytes"),
                payload.get("artifact_compressed_bytes"),
                payload.get("artifact_representation"),
                payload.get("content_type"),
                _sha256_json(request_payload) if request_payload is not None else None,
                _sha256_json(response_headers) if response_headers is not None else None,
            ))
        out.executemany(insert_sql, to_insert)
        out.commit()
        row_count += len(chunk)
        if row_count % 200000 < FETCH_CHUNK:
            elapsed = time.time() - t0
            print(f"  ... {row_count} rows streamed ({elapsed:.1f}s)")

    src_cur.close()

    # outcomes summary (aggregate, since 100% RETAINED / 0 gaps per RA4-C5)
    out_cur = src.execute("SELECT outcome, COUNT(*) FROM retained_acquisition_outcomes GROUP BY outcome")
    outcome_rows = out_cur.fetchall()
    out.executemany("INSERT INTO compact_outcomes_summary VALUES (?,?)", outcome_rows)

    gap_rows = src.execute(
        "SELECT gap_id, acquisition_id, launch_mint, correlation_id, purpose, provider, method, reason, recorded_at "
        "FROM retained_acquisition_gaps"
    ).fetchall()
    out.executemany("INSERT INTO compact_gaps VALUES (?,?,?,?,?,?,?,?,?)", gap_rows)

    src.close()

    manifest_metadata = {
        "source_path": str(LEGACY_PATH),
        "source_size_bytes": str(legacy_identity["size_bytes"]),
        "source_mtime": str(legacy_identity["mtime"]),
        "source_inode": str(legacy_identity["inode"]),
        "source_row_count": str(row_count),
        "materialized_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "schema_contract_version": "ra4.c5b.compact-manifest.v1",
    }
    out.executemany(
        "INSERT INTO manifest_metadata VALUES (?,?)",
        list(manifest_metadata.items()),
    )
    out.commit()
    out.execute("VACUUM")  # compact file is new and small -- VACUUM here is fine, this is NOT the legacy DB
    out.close()

    print(f"DONE. {row_count} rows materialized in {time.time() - t0:.1f}s")
    print("compact size bytes:", COMPACT_PATH.stat().st_size)


if __name__ == "__main__":
    main()
