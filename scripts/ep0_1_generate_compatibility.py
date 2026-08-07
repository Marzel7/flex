#!/usr/bin/env python3
"""EP0.1 deterministic, read-only production compatibility capture.

The generator deliberately does not import the Flask application or any schema
bootstrap code.  Both source databases are opened with SQLite ``mode=ro`` and
``query_only``.  Outputs contain no generator clock: the snapshot timestamp is
derived from the newest source-database mtime unless supplied explicitly.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import platform
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_OUT = ROOT / "compatibility" / "ep0_1"

MAIN_TABLES = (
    "creator_funding_queue", "creator_resolution_queue", "creator_funders",
    "operator_creator_edges", "watchtower_operator_graph",
    "wt_known_operator_hubs", "wt_operator_clusters", "wt_operator_launches",
    "wt_operator_treasuries", "wt_worker_heartbeat",
)
OPS_TABLES = (
    "operators", "operator_entities", "operator_evidence",
    "operator_identity_assets", "operator_identity_events",
    "operator_identity_merges", "operator_identity_splits",
    "operator_identity_state", "operator_promotion_reviews", "operator_reviews",
    "attribution_evidence", "watchtower_identity_reconciliations",
    "wt_attribution_outcomes", "wt_confirmed_treasuries",
    "wt_lineage_verified_session_edges", "wt_ops_v2_treasury_resolution",
    "wt_treasury_approval_audit", "wt_treasury_review",
    "wt_treasury_review_actions", "wt_walkback_atomic_flows",
    "wt_walkback_queue", "wt_worker_heartbeat",
)
QUEUE_TABLES = ("creator_funding_queue", "creator_resolution_queue", "wt_walkback_queue")
VOLATILE_KEYS = {"generated_at", "requested_at", "response_time_ms"}


def stable_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                       allow_nan=False) + "\n").encode("utf-8")


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def clean(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"encoding": "hex", "value": value.hex()}
    if isinstance(value, float):
        return round(value, 9)
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in sorted(value.items()) if k not in VOLATILE_KEYS}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


def primary_order(conn: sqlite3.Connection, table: str) -> str:
    cols = list(conn.execute(f'PRAGMA table_info("{table}")'))
    pk = [r["name"] for r in sorted((r for r in cols if r["pk"]), key=lambda r: r["pk"])]
    names = [r["name"] for r in cols]
    order = pk or names
    return ",".join('"' + name.replace('"', '""') + '"' for name in order)


def table_rows(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not exists:
        return []
    order = primary_order(conn, table)
    return [clean(dict(row)) for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY {order}')]


def table_fixture(conn: sqlite3.Connection, table: str, full_row_limit: int = 2000) -> dict[str, Any]:
    """Full small-table fixture; digest + deterministic boundary sample for large tables."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not exists:
        return {"table": table, "present": False, "row_count": 0, "rows": []}
    order = primary_order(conn, table)
    count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    digest = hashlib.sha256()
    first: list[dict[str, Any]] = []
    last: collections.deque[dict[str, Any]] = collections.deque(maxlen=25)
    all_rows: list[dict[str, Any]] | None = [] if count <= full_row_limit else None
    for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY {order}'):
        value = clean(dict(row))
        digest.update(stable_json(value))
        if all_rows is not None:
            all_rows.append(value)
        else:
            if len(first) < 25:
                first.append(value)
            last.append(value)
    result = {
        "table": table, "present": True, "row_count": count,
        "logical_sha256": digest.hexdigest(), "capture": "full" if all_rows is not None else "digest_and_boundaries",
    }
    if all_rows is not None:
        result["rows"] = all_rows
    else:
        result["first_rows"] = first
        result["last_rows"] = list(last)
    return result


def queue_summary(conn: sqlite3.Connection, table: str) -> dict[str, Any]:
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    if not exists:
        return {"table": table, "present": False}
    columns = {r["name"] for r in conn.execute(f'PRAGMA table_info("{table}")')}
    if "status" not in columns:
        return {"table": table, "present": True, "rows": conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]}
    counts = {str(r[0]): r[1] for r in conn.execute(
        f'SELECT status,COUNT(*) FROM "{table}" GROUP BY status ORDER BY status'
    )}
    return {"table": table, "present": True, "counts": counts, "total": sum(counts.values())}


def schema_digest(conn: sqlite3.Connection) -> str:
    rows = [tuple(row) for row in conn.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    )]
    return digest_bytes(stable_json(rows))


def git_version() -> dict[str, Any]:
    def run(*args: str) -> str:
        return subprocess.run(args, cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
    return {"commit": run("git", "rev-parse", "HEAD")}


def feature_flags() -> dict[str, str]:
    prefixes = ("WATCHTOWER_", "LISTENER_", "DISCOVERY_", "WS_", "SECOND_HOP_", "CREATOR_")
    configured: dict[str, str] = {}
    supervisor = ROOT / "config" / "supervisor" / "supervisord.conf"
    for line in supervisor.read_text(encoding="utf-8").splitlines():
        line = line.strip().rstrip(",")
        if "=" not in line or line.startswith((";", "#")):
            continue
        key, value = line.split("=", 1)
        if key.strip().startswith(prefixes):
            configured[key.strip()] = value.strip().strip('"')
    for key, value in os.environ.items():
        if key.startswith(prefixes):
            configured[key] = value
    return dict(sorted(configured.items()))


def write_json(path: Path, value: Any) -> str:
    payload = stable_json(clean(value))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return digest_bytes(payload)


def compact_large_payload(value: Any, threshold: int = 2_000_000) -> Any:
    cleaned = clean(value)
    payload = stable_json(cleaned)
    if len(payload) <= threshold:
        return cleaned
    summary: dict[str, Any] = {}
    if isinstance(cleaned, dict):
        for key, child in cleaned.items():
            if isinstance(child, list):
                summary[key] = {"count": len(child), "first": child[:3], "last": child[-3:]}
            elif isinstance(child, dict):
                summary[key] = {"keys": sorted(child), "scalar_values": {
                    k: v for k, v in child.items() if v is None or isinstance(v, (str, int, float, bool))
                }}
            else:
                summary[key] = child
    elif isinstance(cleaned, list):
        summary = {"count": len(cleaned), "first": cleaned[:3], "last": cleaned[-3:]}
    return {
        "capture": "digest_and_structure", "payload_bytes": len(payload),
        "payload_sha256": digest_bytes(payload), "summary": summary,
    }


def capture_database(label: str, path: Path, tables: Iterable[str], out: Path, *, quick_check: bool = False) -> dict[str, Any]:
    before = path.stat()
    file_hash = digest_file(path)
    with ro(path) as conn:
        # A full quick_check scans the multi-gigabyte production databases and
        # is deliberately opt-in. File + schema + fixture digests are the
        # compatibility identity; integrity validation is an operational gate.
        integrity = conn.execute("PRAGMA quick_check").fetchone()[0] if quick_check else "NOT_RUN"
        schema_hash = schema_digest(conn)
        fixture_hashes = {}
        for table in tables:
            fixture = table_fixture(conn, table)
            fixture["database"] = label
            fixture_hashes[table] = write_json(out / "database" / label / f"{table}.json", fixture)
        queue_health = [queue_summary(conn, table) for table in QUEUE_TABLES]
        table_counts = {
            r[0]: conn.execute(f'SELECT COUNT(*) FROM "{r[0]}"').fetchone()[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        }
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError(f"{path} changed during capture; retry against a stable snapshot")
    return {
        "source_file": path.name, "source_kind": "immutable_snapshot", "size_bytes": before.st_size,
        "mtime_ns": before.st_mtime_ns, "sha256": file_hash,
        "schema_sha256": schema_hash, "quick_check": integrity,
        "table_counts": table_counts, "fixture_hashes": fixture_hashes,
        "queue_health": queue_health,
    }


def capture_api_models(ops_path: Path, main_path: Path, out: Path, projection_dir: Path | None = None) -> dict[str, str]:
    # Stable read-model contracts behind the routes, without Flask clocks or
    # expensive on-request reconstruction. EP0.1 must not become a live workload.
    from src.ops.operator_reader import OperatorReader

    reader = OperatorReader(str(ops_path))
    operators = reader.fetch_all_operators(exclude_rejected=False, limit=500)
    api: dict[str, Any] = {
        "operators": {"ok": True, "count": len(operators), "operators": operators},
        "operator_summary": {"ok": True, **reader.fetch_summary()},
    }
    for operator in operators:
        operator_id = operator.get("operator_id")
        if operator_id:
            api[f"operator_{operator_id}"] = {"ok": True, "operator": reader.fetch_operator(operator_id)}

    with ro(ops_path) as ops:
        dispositions = [dict(r) for r in ops.execute(
            "SELECT COALESCE(outcome_type,'UNKNOWN') outcome,COUNT(*) count "
            "FROM wt_attribution_outcomes GROUP BY outcome_type ORDER BY outcome"
        )]
        review = [dict(r) for r in ops.execute(
            "SELECT * FROM wt_treasury_review ORDER BY treasury"
        )]
        walkback = [dict(r) for r in ops.execute(
            "SELECT status,COUNT(*) count FROM wt_walkback_queue GROUP BY status ORDER BY status"
        )]
        health = [dict(r) for r in ops.execute(
            "SELECT * FROM wt_worker_heartbeat ORDER BY worker_name"
        )]
    with ro(main_path) as main:
        discovery = [dict(r) for r in main.execute(
            "SELECT * FROM wt_discovery_log ORDER BY rowid DESC LIMIT 100"
        )]
        funding = [dict(r) for r in main.execute(
            "SELECT status,COUNT(*) count FROM creator_funding_queue GROUP BY status ORDER BY status"
        )]
        health.extend(dict(r) for r in main.execute(
            "SELECT * FROM wt_worker_heartbeat ORDER BY worker_name"
        ))
    api["operations_registry"] = {
        "operators": operators, "reconciled_dispositions": dispositions,
        "treasury_review_count": len(review),
    }
    api["discovery_recent"] = discovery
    api["treasury_review"] = review
    api["walkback_health"] = walkback
    api["creator_funding_health"] = funding
    api["system_health"] = sorted(health, key=lambda row: (str(row.get("worker_name")), str(row.get("last_seen"))))

    projection_dir = projection_dir or ROOT / "database" / "intelligence_snapshots"
    for name in ("emerging_operators__0", "operational_intelligence__86400", "pipeline_health__86400"):
        path = projection_dir / f"{name}.json"
        if path.exists():
            api[f"snapshot_{name}"] = compact_large_payload(json.loads(path.read_text(encoding="utf-8")))

    return {name: write_json(out / "api" / f"{name}.json", value) for name, value in sorted(api.items())}


def capture_ui_contract(out: Path, api_hashes: dict[str, str]) -> tuple[dict[str, str], int]:
    routes = {
        "/intelligence/operators": {"projection": "operations_registry", "template": "operator_registry.html"},
        "/intelligence/operations/<entity>": {"projection": "operator/family detail", "template": "investigation_profile.html"},
        "/discovery": {"projection": "discovery_recent", "template": "discovery.html"},
        "/intelligence/treasury-review": {"projection": "treasury review", "template": "treasury_review.html"},
    }
    payload = []
    templates = ROOT / "src" / "core" / "templates"
    for route, spec in sorted(routes.items()):
        candidates = list(ROOT.glob(f"**/{spec['template']}"))
        template = candidates[0] if candidates else templates / spec["template"]
        payload.append({
            "route": route, "projection": spec["projection"],
            "template": str(template.relative_to(ROOT)) if template.exists() else spec["template"],
            "template_sha256": digest_file(template) if template.exists() else None,
            "api_sha256": api_hashes.get(spec["projection"]),
        })
    return {"ui_routes": write_json(out / "ui" / "routes.json", payload)}, len(payload)


def capture_performance(path: Path | None, out: Path) -> dict[str, str]:
    observed: dict[str, Any] = {
        "measurement_policy": "observed_only",
        "unavailable_without_bounded_sampler": ["cpu", "memory", "worker_restart_rate", "rpc_volume", "wal_growth", "launch_throughput", "detector_throughput"],
    }
    if path and path.exists():
        observed["serializer"] = json.loads(path.read_text(encoding="utf-8"))
    return {"performance_baseline": write_json(out / "performance_baseline.json", observed)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--main-db", type=Path, default=ROOT / "database" / "flex_complete_database.db")
    parser.add_argument("--ops-db", type=Path, default=ROOT / "database" / "wt_ops_v2.db")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--snapshot-timestamp", type=int)
    parser.add_argument("--quick-check", action="store_true")
    parser.add_argument("--projection-dir", type=Path, default=ROOT / "database" / "intelligence_snapshots")
    parser.add_argument("--serializer-metrics", type=Path, default=ROOT / "logs" / "db_serializer_metrics.json")
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)

    dbs = {
        "main": capture_database("main", args.main_db.resolve(), MAIN_TABLES, out, quick_check=args.quick_check),
        "operations": capture_database("operations", args.ops_db.resolve(), OPS_TABLES, out, quick_check=args.quick_check),
    }
    api_hashes = capture_api_models(args.ops_db.resolve(), args.main_db.resolve(), out, args.projection_dir.resolve())
    ui_hashes, ui_route_count = capture_ui_contract(out, api_hashes)
    performance_hashes = capture_performance(args.serializer_metrics.resolve(), out)
    fixture_hashes = {
        **{f"main/{k}": v for k, v in dbs["main"]["fixture_hashes"].items()},
        **{f"operations/{k}": v for k, v in dbs["operations"]["fixture_hashes"].items()},
    }
    snapshot_timestamp = args.snapshot_timestamp or max(
        int(args.main_db.stat().st_mtime), int(args.ops_db.stat().st_mtime)
    )
    manifest = {
        "platform_version": "EP0.1",
        "snapshot_timestamp": snapshot_timestamp,
        "build": {**git_version(), "python": platform.python_version()},
        "feature_flags": feature_flags(),
        "databases": dbs,
        "fixture_hashes": dict(sorted(fixture_hashes.items())),
        "consumer_hashes": dict(sorted(api_hashes.items())),
        "api_hashes": dict(sorted(api_hashes.items())),
        "ui_hashes": dict(sorted(ui_hashes.items())),
        "canonical_hashes": {k: v for k, v in fixture_hashes.items() if "operator" in k or "treasur" in k},
        "governance_hashes": {k: v for k, v in fixture_hashes.items() if any(x in k for x in ("identity", "review", "approval"))},
        "coverage_state": {
            "captured_tables": len(fixture_hashes),
            "api_models": len(api_hashes),
            "ui_routes": ui_route_count,
            "missing_representative_states_are_explicit": True,
        },
        "performance_hashes": performance_hashes,
    }
    # The manifest cannot include its own hash. All referenced payloads are hashed.
    write_json(out / "compatibility_manifest.json", manifest)
    print(json.dumps({"ok": True, "out": str(out), "manifest_sha256": digest_file(out / "compatibility_manifest.json")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
