"""Compact, non-authoritative recovery facts from transaction-first lineage.

This module deliberately preserves only the facts needed by the manual
session-lineage safety procedure.  It never reads provider APIs and it never
falls back to the broad historical reconstruction database.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Iterable


SCHEMA_VERSION = "transaction-first-recovery-index.v1"
AUTHORITY = "RECOVERY_ONLY_NON_AUTHORITATIVE_CACHE"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _semantic_digest(connection: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    for table, columns in (
        ("recovery_session_facts", "session_id,stored_root,stored_child,direct_sender,signature,compared_at,source_row_sha256"),
        ("recovery_signatures", "signature,block_time,source,rpc_verified,parse_status,response_sha256"),
        ("recovery_edges", "edge_id,signature,sender,recipient,block_time,amount,asset,relationship_type,mechanism,launch_context"),
    ):
        digest.update(table.encode())
        for row in connection.execute(f"SELECT {columns} FROM {table} ORDER BY 1"):
            digest.update(json.dumps(tuple(row), separators=(",", ":"), default=str).encode())
    return digest.hexdigest()


def _source_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.resolve()}?mode=ro&immutable=1", uri=True)


def _install(connection: sqlite3.Connection) -> None:
    connection.executescript("""
    CREATE TABLE recovery_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
    CREATE TABLE recovery_session_facts(
      session_id INTEGER PRIMARY KEY,
      stored_root TEXT NOT NULL,
      stored_child TEXT NOT NULL,
      direct_sender TEXT,
      signature TEXT NOT NULL,
      compared_at INTEGER NOT NULL,
      source_row_sha256 TEXT NOT NULL
    );
    CREATE TABLE recovery_signatures(
      signature TEXT PRIMARY KEY,
      block_time INTEGER,
      source TEXT NOT NULL,
      rpc_verified INTEGER NOT NULL,
      parse_status TEXT NOT NULL,
      response_sha256 TEXT
    ) WITHOUT ROWID;
    CREATE TABLE recovery_edges(
      edge_id TEXT PRIMARY KEY,
      signature TEXT NOT NULL,
      sender TEXT NOT NULL,
      recipient TEXT NOT NULL,
      block_time INTEGER NOT NULL,
      amount TEXT,
      asset TEXT NOT NULL,
      relationship_type TEXT NOT NULL,
      mechanism TEXT NOT NULL,
      launch_context TEXT
    );
    """)


def build_recovery_index(source_path: Path, target_path: Path) -> dict[str, object]:
    """Atomically build the bounded recovery-only index from a read-only source."""
    source_path, target_path = Path(source_path), Path(target_path)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    source = _source_connection(source_path)
    source.row_factory = sqlite3.Row
    temp_name: str | None = None
    try:
        facts = list(source.execute("""
            SELECT session_id,stored_root,stored_child,direct_sender,signature,compared_at
              FROM tf_session_comparison
             WHERE comparison_class='CORRECT_DIRECT_RELATIONSHIP'
             ORDER BY session_id
        """))
        if not facts:
            raise ValueError("no verified session facts available for recovery index")
        signatures = sorted({row["signature"] for row in facts if row["signature"]})
        placeholders = ",".join("?" for _ in signatures)
        cache_rows = {
            row["signature"]: row for row in source.execute(
                "SELECT signature,block_time,transaction_json,source,rpc_verified,parse_status "
                f"FROM tf_transaction_cache WHERE signature IN ({placeholders})", signatures)
        }
        matching_edges = list(source.execute(
            """SELECT e.edge_id,e.signature,e.sender,e.recipient,e.block_time,e.amount,e.asset,
                      e.relationship_type,e.mechanism,e.launch_context
                 FROM tf_edges e
                 JOIN tf_session_comparison s ON s.signature=e.signature
                WHERE s.comparison_class='CORRECT_DIRECT_RELATIONSHIP'
                  AND e.sender=s.stored_root AND e.recipient=s.stored_child
                ORDER BY e.edge_id"""
        ))
        fd, temp_name = tempfile.mkstemp(prefix=target_path.name + ".tmp.", dir=target_path.parent)
        os.close(fd)
        temp = Path(temp_name)
        output = sqlite3.connect(temp)
        try:
            _install(output)
            fact_rows = []
            for row in facts:
                body = json.dumps(tuple(row), separators=(",", ":"), default=str).encode()
                fact_rows.append((row["session_id"], row["stored_root"], row["stored_child"],
                                  row["direct_sender"], row["signature"], row["compared_at"],
                                  hashlib.sha256(body).hexdigest()))
            output.executemany("INSERT INTO recovery_session_facts VALUES (?,?,?,?,?,?,?)", fact_rows)
            signature_rows = []
            for signature in signatures:
                row = cache_rows.get(signature)
                if row is None:
                    signature_rows.append((signature, None, "SOURCE_CACHE_MISS", 0, "UNAVAILABLE", None))
                else:
                    body = row["transaction_json"]
                    signature_rows.append((signature, row["block_time"], row["source"], row["rpc_verified"],
                                           row["parse_status"], hashlib.sha256(body.encode()).hexdigest() if body else None))
            output.executemany("INSERT INTO recovery_signatures VALUES (?,?,?,?,?,?)", signature_rows)
            output.executemany("INSERT INTO recovery_edges VALUES (?,?,?,?,?,?,?,?,?,?)", [tuple(row) for row in matching_edges])
            metadata = {
                "schema_version": SCHEMA_VERSION,
                "authority": AUTHORITY,
                "purpose": "manual session-lineage recovery only",
                "source_path": str(source_path),
                "source_sha256": _sha256_file(source_path),
                "generated_at": str(int(time.time())),
                "source_session_fact_count": str(len(facts)),
                "retained_session_fact_count": str(len(facts)),
                "retained_signature_count": str(len(signatures)),
                "retained_edge_count": str(len(matching_edges)),
                "selection_rule": "tf_session_comparison.comparison_class=CORRECT_DIRECT_RELATIONSHIP; signature metadata only; no raw response bodies",
            }
            output.executemany("INSERT INTO recovery_metadata VALUES (?,?)", sorted(metadata.items()))
            metadata["semantic_digest"] = _semantic_digest(output)
            output.execute("INSERT INTO recovery_metadata VALUES (?,?)", ("semantic_digest", metadata["semantic_digest"]))
            output.commit()
        finally:
            output.close()
        os.replace(temp, target_path)
        temp_name = None
        return {**metadata, "logical_bytes": target_path.stat().st_size}
    finally:
        source.close()
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)


def open_recovery_index(path: Path) -> sqlite3.Connection:
    path = Path(path)
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    metadata = dict(connection.execute("SELECT key,value FROM recovery_metadata"))
    if metadata.get("schema_version") != SCHEMA_VERSION or metadata.get("authority") != AUTHORITY:
        connection.close()
        raise ValueError("invalid or non-recovery transaction-first index")
    if metadata.get("semantic_digest") != _semantic_digest(connection):
        connection.close()
        raise ValueError("transaction-first recovery index semantic digest mismatch")
    return connection


def verified_session_facts(path: Path) -> list[sqlite3.Row]:
    connection = open_recovery_index(path)
    try:
        return connection.execute(
            "SELECT session_id,stored_root,stored_child,signature FROM recovery_session_facts ORDER BY session_id"
        ).fetchall()
    finally:
        connection.close()
