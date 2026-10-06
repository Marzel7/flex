"""Compose the versioned, read-only Watchtower 96-record offset ledger."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from src.ops.watchtower_offset_audit import OffsetAuditStore, _validate_record


VERSION = "watchtower-opening-offset-audit.v2"
SOURCE_MANIFEST = {
    "B8S7WGQYteNSBz4rU4Xs1hkr1seGpxYgUuV2wgtbpump": {
        "source_commit": "34d03ece", "source_blob": "148eb8f68ce6d46aa1117a8f05c0a4280117676b",
        "source_kind": "retained_compact_diagnostic", "sha256": "148eb8f68ce6d46aa1117a8f05c0a4280117676b",
    },
    "7iXZi55AyKbJv5e9XTQuVcywdT2ejHjGBf3NKuCLpump": {
        "source_commit": "34d03ece", "source_blob": "148eb8f68ce6d46aa1117a8f05c0a4280117676b",
        "source_kind": "retained_compact_diagnostic", "sha256": "148eb8f68ce6d46aa1117a8f05c0a4280117676b",
    },
    "INJECTOR": {
        "source_commit": "bb9644bbd39c8f2f8d240b348acca72ce661289e", "source_blob": "96d87bbfdd7dc2c35adfb371b72ad8590f094969",
        "source_kind": "pinned_retained_diagnostic_authority", "sha256": "96d87bbfdd7dc2c35adfb371b72ad8590f094969",
    },
}


def control_records() -> list[dict]:
    return [
        # These source records retain the selected t value and t+1 absence.
        # Later non-selected MC values are intentionally not invented here.
        {"mint": "B8S7WGQYteNSBz4rU4Xs1hkr1seGpxYgUuV2wgtbpump", "migration_timestamp": 1791287674, "http_status": 200, "normalization_state": "COMPLETE", "normalized_item_count": 4, "normalized_timestamps": [1791287674, 1791287676, 1791287677, 1791287678], "offsets": {"0": 113847.0368040435}, "duplicate_offsets": [], "failure_state": "NONE"},
        {"mint": "7iXZi55AyKbJv5e9XTQuVcywdT2ejHjGBf3NKuCLpump", "migration_timestamp": 1791294235, "http_status": 200, "normalization_state": "COMPLETE", "normalized_item_count": 4, "normalized_timestamps": [1791294235, 1791294237, 1791294238, 1791294239], "offsets": {"0": 166526.78513950607}, "duplicate_offsets": [], "failure_state": "NONE"},
        {"mint": "INJECTOR", "migration_timestamp": 1791307928, "http_status": 200, "normalization_state": "COMPLETE", "normalized_item_count": 3, "normalized_timestamps": [1791307930, 1791307931, 1791307932], "offsets": {"2": 134293.7821862771, "3": 134690.51189096452, "4": 134806.71480907808}, "duplicate_offsets": [], "failure_state": "NONE"},
    ]


def compose(base_path: Path) -> dict:
    base = OffsetAuditStore(base_path).records()
    if len(base) != 93:
        raise ValueError("BASE_AUDIT_LEDGER_RECORD_COUNT")
    controls = [_validate_record(record) for record in control_records()]
    records = sorted([*base, *controls], key=lambda row: row["mint"])
    if len(records) != 96 or len({row["mint"] for row in records}) != 96:
        raise ValueError("COMPOSED_AUDIT_LEDGER_IDENTITY")
    return {"schema_version": 1, "raw_provider_payload_retention": False, "records": records}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    args = parser.parse_args()
    document = compose(args.base)
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    args.output.write_text(encoded)
    provenance = {
        "artifact_version": VERSION,
        "base_ledger": {"path": str(args.base), "record_count": 93,
                        "sha256": hashlib.sha256(args.base.read_bytes()).hexdigest()},
        "retained_control_sources": SOURCE_MANIFEST,
        "authoritative_mutations": 0,
        "provider_calls": 0,
        "raw_provider_payload_retention": False,
    }
    args.provenance.write_text(json.dumps(provenance, sort_keys=True, separators=(",", ":")) + "\n")
    print(json.dumps({"version": VERSION, "records": len(document["records"]), "sha256": hashlib.sha256(encoded.encode()).hexdigest()}, sort_keys=True))


if __name__ == "__main__":
    main()
