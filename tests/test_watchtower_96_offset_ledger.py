import hashlib
import json
from pathlib import Path

from scripts.compose_watchtower_96_offset_ledger import SOURCE_MANIFEST, VERSION, compose, control_records
from src.ops.watchtower_offset_audit import product_projection


BASE = Path("/private/tmp/watchtower-offset-audit-v1.json")


def _write(tmp_path):
    output = tmp_path / "ledger.json"
    output.write_text(json.dumps(compose(BASE), sort_keys=True, separators=(",", ":")))
    return output


def test_composes_schema_identical_96_unique_records(tmp_path):
    ledger = _write(tmp_path)
    records = json.loads(ledger.read_text())["records"]
    assert len(records) == len({row["mint"] for row in records}) == 96
    assert set(records[0]) == set(control_records()[0])


def test_controls_are_retained_authorities_and_replay_exactly(tmp_path):
    ledger = _write(tmp_path)
    rows = {row["mint"]: row for row in json.loads(ledger.read_text())["records"]}
    assert set(SOURCE_MANIFEST) == {"B8S7WGQYteNSBz4rU4Xs1hkr1seGpxYgUuV2wgtbpump", "7iXZi55AyKbJv5e9XTQuVcywdT2ejHjGBf3NKuCLpump", "INJECTOR"}
    assert rows["INJECTOR"]["offsets"].keys() == {"2", "3", "4"}
    selected = [next((key for key in ("1", "0", "2") if key in row["offsets"]), None) for row in rows.values()]
    assert selected.count("1") == 52
    assert sum(value is not None for value in selected) == 84
    assert selected.count("2") == 3
    assert selected.count(None) == 12
    assert {mint for mint, value in rows.items() if next((key for key in ("1", "0", "2") if key in value["offsets"]), None) == "2"} == {
        "INJECTOR", "4Z9eH1BCuq2Y6FFRXkZft9LoxwMqQWo7bJsEbcd7pump", "59F95Wkn5LRpiKmG2FE3g87Lzkz4redvNJNKSi1Npump"}
    assert next(key for key in ("1", "0", "2") if key in rows["42iYbRaVhj4dy6WXTWFMZ45rWVUxW3UdBPndaNRdpump"]["offsets"]) == "1"
    assert next(key for key in ("1", "0", "2") if key in rows["HRinFbhZjrb2xoqSzxYCYKX7LqJ3H6pn42CURb2cpump"]["offsets"]) == "0"
    assert all(next(key for key in ("1", "0", "2") if key in rows[mint]["offsets"]) == "0" for mint in (
        "B8S7WGQYteNSBz4rU4Xs1hkr1seGpxYgUuV2wgtbpump", "7iXZi55AyKbJv5e9XTQuVcywdT2ejHjGBf3NKuCLpump"))


def test_product_projection_is_read_only_96_fixture(tmp_path):
    ledger = _write(tmp_path)
    projection = product_projection(ledger)
    assert len(projection) == 96
    assert projection["INJECTOR"]["proposed_plus2_classification"] == "PLUS2"
    assert "entry_mc_usd" not in projection["INJECTOR"]
    assert sum(row["proposed_plus2_classification"] == "NO_VALID_OPENING_CANDLE_THROUGH_PLUS4" for row in projection.values()) == 12


def test_baseline_exception_is_exactly_the_preexisting_deep_review_test():
    record = json.loads(Path("docs/audits/watchtower_96_ledger_baseline_exception.v1.json").read_text())
    assert record["base_sha"] == "98d72e374002bcda0cbbc537a615e03f44a96e00"
    assert record["exception_count"] == 1
    assert record["exception_test"] == "tests/test_watchtower_deep_historical.py::test_deep_review_ui_is_explicitly_noncanonical"
    assert record["base_reproduction"]["reason"] == "MISSING_templates/operator_deep_review.html"
    assert record["feature_diff"]["caused_regression"] is False
