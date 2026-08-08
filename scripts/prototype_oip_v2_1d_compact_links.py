#!/usr/bin/env python3
"""Build an isolated compact-key representation of Primitive provenance links."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "database/evidence_platform/oip_v2_1c_retry_failover/evidence.db"
OUTPUT = ROOT / "database/evidence_platform/oip_v2_1d_storage_audit/prototype.sqlite"
CHECKPOINT = ROOT / "database/evidence_platform/oip_v2_1d_storage_audit/prototype_checkpoint.json"


def write_json(path: Path, value) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def relation_digest(rows) -> tuple[str, int]:
    digest = hashlib.sha256(); count = 0
    for primitive_id, evidence_id in rows:
        digest.update(primitive_id.encode()); digest.update(b"\0")
        digest.update(evidence_id.encode()); digest.update(b"\n"); count += 1
    return digest.hexdigest(), count


def schema(connection) -> None:
    connection.executescript("""
    PRAGMA journal_mode=WAL;
    PRAGMA synchronous=FULL;
    CREATE TABLE primitive_identity(
        primitive_key INTEGER PRIMARY KEY,
        primitive_id TEXT NOT NULL UNIQUE
    );
    CREATE TABLE evidence_identity(
        evidence_key INTEGER PRIMARY KEY,
        evidence_id TEXT NOT NULL UNIQUE
    );
    CREATE TABLE compact_primitive_evidence_inputs(
        primitive_key INTEGER NOT NULL,
        evidence_key INTEGER NOT NULL,
        PRIMARY KEY(primitive_key,evidence_key)
    ) WITHOUT ROWID;
    CREATE VIEW primitive_evidence_inputs AS
        SELECT p.primitive_id,e.evidence_id
        FROM compact_primitive_evidence_inputs i
        JOIN primitive_identity p USING(primitive_key)
        JOIN evidence_identity e USING(evidence_key);
    CREATE TRIGGER primitive_evidence_inputs_insert INSTEAD OF INSERT ON primitive_evidence_inputs
    BEGIN
      INSERT INTO compact_primitive_evidence_inputs(primitive_key,evidence_key)
      VALUES(
        (SELECT primitive_key FROM primitive_identity WHERE primitive_id=NEW.primitive_id),
        (SELECT evidence_key FROM evidence_identity WHERE evidence_id=NEW.evidence_id)
      );
    END;
    CREATE TRIGGER primitive_evidence_inputs_no_update INSTEAD OF UPDATE ON primitive_evidence_inputs
    BEGIN SELECT RAISE(ABORT,'immutable primitive input cannot be updated'); END;
    CREATE TRIGGER primitive_evidence_inputs_no_delete INSTEAD OF DELETE ON primitive_evidence_inputs
    BEGIN SELECT RAISE(ABORT,'immutable primitive input cannot be deleted'); END;
    """)


def build(corpus: Path, output: Path, checkpoint: Path) -> dict:
    if output.exists() and checkpoint.exists():
        state = json.loads(checkpoint.read_text())
        if state.get("complete"):
            return {**state, "reused": True}
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    source = sqlite3.connect(f"file:{corpus}?mode=ro", uri=True)
    target = sqlite3.connect(output)
    schema(target)
    started = time.perf_counter()
    primitive_map = {}
    for key, (identity,) in enumerate(source.execute(
            "SELECT primitive_id FROM primitive_observations ORDER BY primitive_id"), 1):
        primitive_map[identity] = key
    evidence_map = {}
    for key, (identity,) in enumerate(source.execute(
            "SELECT evidence_id FROM normalized_evidence_records ORDER BY evidence_id"), 1):
        evidence_map[identity] = key
    target.executemany("INSERT INTO primitive_identity VALUES(?,?)",
                       ((key, identity) for identity, key in primitive_map.items()))
    target.executemany("INSERT INTO evidence_identity VALUES(?,?)",
                       ((key, identity) for identity, key in evidence_map.items()))
    target.commit()
    state = {"complete": False, "links_inserted": 0, "primitive_identities": len(primitive_map),
             "evidence_identities": len(evidence_map), "rpc_calls": 0, "production_interaction": False}
    write_json(checkpoint, state)
    batch = []
    for primitive_id, evidence_id in source.execute(
            "SELECT primitive_id,evidence_id FROM primitive_evidence_inputs ORDER BY primitive_id,evidence_id"):
        batch.append((primitive_map[primitive_id], evidence_map[evidence_id]))
        if len(batch) == 10_000:
            target.executemany("INSERT INTO compact_primitive_evidence_inputs VALUES(?,?)", batch)
            target.commit(); state["links_inserted"] += len(batch); batch.clear()
            if state["links_inserted"] % 500_000 == 0:
                write_json(checkpoint, state)
                print(json.dumps({"prototype_links": state["links_inserted"]}), flush=True)
    if batch:
        target.executemany("INSERT INTO compact_primitive_evidence_inputs VALUES(?,?)", batch)
        target.commit(); state["links_inserted"] += len(batch)
    source_digest, source_count = relation_digest(source.execute(
        "SELECT primitive_id,evidence_id FROM primitive_evidence_inputs ORDER BY primitive_id,evidence_id"))
    prototype_digest, prototype_count = relation_digest(target.execute(
        "SELECT primitive_id,evidence_id FROM primitive_evidence_inputs ORDER BY primitive_id,evidence_id"))
    target.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    target.execute("VACUUM")
    target.close(); source.close()
    measure = sqlite3.connect(output)
    objects = {row[0]: int(row[1]) for row in measure.execute(
        "SELECT name,SUM(pgsize) FROM dbstat GROUP BY name")}
    sample_ids = [row[0] for row in measure.execute(
        "SELECT primitive_id FROM primitive_identity ORDER BY primitive_key LIMIT 100")]
    lookup_started = time.perf_counter()
    for identity in sample_ids:
        measure.execute("SELECT evidence_id FROM primitive_evidence_inputs WHERE primitive_id=?", (identity,)).fetchall()
    lookup_seconds = time.perf_counter() - lookup_started
    state.update({"complete": True, "runtime_seconds": round(time.perf_counter() - started, 6),
                  "file_bytes": output.stat().st_size, "objects": objects,
                  "source_relation_digest": source_digest, "prototype_relation_digest": prototype_digest,
                  "source_relation_count": source_count, "prototype_relation_count": prototype_count,
                  "semantic_relation_identical": source_digest == prototype_digest and source_count == prototype_count,
                  "sample_100_lookup_seconds": round(lookup_seconds, 6),
                  "query_plan": [row[3] for row in measure.execute(
                      "EXPLAIN QUERY PLAN SELECT evidence_id FROM primitive_evidence_inputs WHERE primitive_id=?",
                      (sample_ids[0],))], "reused": False})
    measure.close(); write_json(checkpoint, state)
    return state


def main() -> int:
    result = build(CORPUS, OUTPUT, CHECKPOINT)
    print(json.dumps({key: result[key] for key in ("links_inserted", "file_bytes",
        "semantic_relation_identical", "runtime_seconds", "reused")}, sort_keys=True))
    return 0 if result["semantic_relation_identical"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
