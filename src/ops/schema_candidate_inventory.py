"""Deterministic static candidate inventory; classification is intentionally separate."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = ("src/core", "src/ops", "src/analysis")
DDL = ("CREATE TABLE", "ALTER TABLE", "DROP TABLE", "CREATE INDEX", "DROP INDEX", "EXECUTESCRIPT")


def _callee(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name): return node.func.id
    if isinstance(node.func, ast.Attribute): return node.func.attr
    return "<dynamic>"


def scan() -> list[dict]:
    records = []
    for root in SCAN_ROOTS:
        for path in sorted((ROOT / root).rglob("*.py")):
            rel = str(path.relative_to(ROOT))
            tree = ast.parse(path.read_text(), filename=rel)
            for fn in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
                for node in ast.walk(fn):
                    kind = callee = mutation = None
                    if isinstance(node, ast.Call):
                        callee = _callee(node)
                        if callee == "executescript": kind = "EXECUTESCRIPT"
                        elif callee == "migrate_schema_step": kind = "MIGRATION_CALL"
                        elif "schema" in callee.lower() or callee.startswith("ensure_"):
                            kind = "ENSURE_CALL"
                    if isinstance(node, ast.Constant) and isinstance(node.value, str):
                        upper = node.value.upper()
                        hit = next((word for word in DDL if word in upper), None)
                        if hit: kind, mutation = "DIRECT_DDL", hit
                    if not kind: continue
                    identity = f"{rel}|{fn.name}|{kind}|{callee or mutation}|{getattr(node, 'col_offset', 0)}"
                    records.append({
                        "candidate_id": hashlib.sha256(identity.encode()).hexdigest()[:16],
                        "file": rel, "qualified_function": fn.name,
                        "source_kind": kind, "call_expression_or_ddl_class": callee or mutation,
                        "callee_if_any": callee, "target_db": "UNCLASSIFIED",
                        "reachability": "UNCLASSIFIED",
                    })
    records = sorted(records, key=lambda r: (r["file"], r["qualified_function"], r["source_kind"], r["candidate_id"]))
    # Parent AST walks can expose identical semantic matches more than once.
    # Keep every record but make its source-order occurrence part of the stable
    # identity so persisted overlays never have colliding keys.
    seen: dict[str, int] = {}
    for record in records:
        base = record["candidate_id"]
        ordinal = seen.get(base, 0)
        seen[base] = ordinal + 1
        record["candidate_id"] = f"{base}-{ordinal:03d}"
    return records


def digest(records: list[dict]) -> str:
    return hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def source_baseline() -> tuple[str, list[dict]]:
    files = []
    for root in SCAN_ROOTS:
        for path in sorted((ROOT / root).rglob("*.py")):
            rel = str(path.relative_to(ROOT))
            files.append({"path": rel, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest(), files


def persist(records: list[dict], path: Path, *, baseline_id: str, files: list[dict]) -> None:
    payload = {
        "schema": "operations-db-schema-candidate-manifest/v1",
        "source_baseline_id": baseline_id,
        "candidate_count": len(records),
        "candidate_manifest_digest": digest(records),
        "source_files": files,
        "candidates": records,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
