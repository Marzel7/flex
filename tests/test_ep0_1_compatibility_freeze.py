from __future__ import annotations

import importlib.util
import hashlib
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ep0_1", ROOT / "scripts" / "ep0_1_generate_compatibility.py")
ep = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(ep)


def test_stable_json_has_sorted_keys_and_no_generated_clock():
    first = ep.stable_json({"b": 2, "generated_at": 10, "a": [2, 1]})
    second = ep.stable_json(ep.clean({"a": [2, 1], "generated_at": 99, "b": 2}))
    assert second == b'{"a":[2,1],"b":2}\n'
    assert b"generated_at" in first  # stripping is explicit at the capture boundary


def test_read_only_connection_rejects_writes(tmp_path):
    path = tmp_path / "source.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE facts(id INTEGER PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO facts(value) VALUES ('observed')")
    conn.commit()
    conn.close()

    with ep.ro(path) as readonly:
        assert readonly.execute("SELECT value FROM facts").fetchone()[0] == "observed"
        try:
            readonly.execute("INSERT INTO facts(value) VALUES ('changed')")
        except sqlite3.OperationalError as exc:
            assert "readonly" in str(exc).lower() or "read-only" in str(exc).lower()
        else:
            raise AssertionError("EP0.1 source connection accepted a write")


def test_table_capture_is_deterministic(tmp_path):
    path = tmp_path / "source.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE facts(k TEXT PRIMARY KEY, n INTEGER)")
    conn.executemany("INSERT INTO facts VALUES (?,?)", [("z", 2), ("a", 1)])
    conn.commit()
    conn.close()
    with ep.ro(path) as source:
        one = ep.stable_json(ep.table_rows(source, "facts"))
    with ep.ro(path) as source:
        two = ep.stable_json(ep.table_rows(source, "facts"))
    assert one == two
    assert ep.digest_bytes(one) == ep.digest_bytes(two)


def test_large_table_uses_full_digest_and_bounded_sample(tmp_path):
    path = tmp_path / "source.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE facts(k INTEGER PRIMARY KEY, value TEXT)")
    conn.executemany("INSERT INTO facts VALUES (?,?)", [(n, str(n)) for n in range(20)])
    conn.commit()
    conn.close()
    with ep.ro(path) as source:
        fixture = ep.table_fixture(source, "facts", full_row_limit=5)
    assert fixture["row_count"] == 20
    assert fixture["capture"] == "digest_and_boundaries"
    assert len(fixture["logical_sha256"]) == 64
    assert fixture["first_rows"][0]["k"] == 0
    assert fixture["last_rows"][-1]["k"] == 19
    assert "rows" not in fixture


def test_golden_contract_has_required_consumers():
    contract = json.loads((ROOT / "compatibility" / "ep0_1" / "golden_behaviour_contract.json").read_text())
    required = {"creator_funding", "discovery", "governance", "investigation", "operator_identity", "registry", "treasury_review", "walkback", "watchtower_and_3sw2"}
    assert required <= set(contract["consumers"])
    for consumer in contract["consumers"].values():
        assert consumer["expected_input"]
        assert consumer["expected_output"]
        assert "permitted_differences" in consumer
        assert consumer["forbidden_differences"]


def test_checked_in_manifest_verifies_every_payload():
    base = ROOT / "compatibility" / "ep0_1"
    manifest = json.loads((base / "compatibility_manifest.json").read_text())
    expected = []
    expected.extend((base / "database" / f"{name}.json", digest) for name, digest in manifest["fixture_hashes"].items())
    expected.extend((base / "api" / f"{name}.json", digest) for name, digest in manifest["api_hashes"].items())
    expected.append((base / "ui" / "routes.json", manifest["ui_hashes"]["ui_routes"]))
    expected.append((base / "performance_baseline.json", manifest["performance_hashes"]["performance_baseline"]))
    assert len(expected) >= 40
    for path, digest in expected:
        assert path.is_file(), path
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, path
