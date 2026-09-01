import json
from pathlib import Path

import pytest

from src.evidence.contracts.psi0h_h1_historical_discovery import (
    DEFAULT_MANIFEST_PATH,
    Psi0hH1HistoricalDiscoveryError,
    build_historical_discovery_eligibility,
    verify_historical_discovery,
)


def sample_manifest(rows):
    return {
        "schema_version": "1.0.0",
        "milestone": "PSI0G-B",
        "status": "PASS",
        "run_id": "psi0g-b-retained-derivation-20260817-01",
        "files": {
            "watchtower": {"sha256": "w"},
            "three_sw2": {"sha256": "t"},
        },
        "operations": rows,
    }


def source(path):
    return {
        "path": f"database/evidence_platform/{path}/evidence.db",
        "access": "sqlite_uri_mode_ro_and_query_only",
        "identity": {"device": 16777234, "inode": 123, "size_bytes": 100, "mtime_ns": 1111},
    }


def test_h1_passes_when_counts_and_identity_are_valid_for_eligible_operations():
    payload = sample_manifest([
        {
            "operation_key": "three_sw2",
            "source": source("three_sw2_shadow_ep3_2a"),
            "candidate_count": 94,
            "evidence_count": 1000,
            "primitive_count": 858,
        },
        {
            "operation_key": "watchtower",
            "source": source("watchtower_shadow_ep3_0d"),
            "candidate_count": 14203,
            "evidence_count": 107941,
            "primitive_count": 85989,
        },
    ])
    result = build_historical_discovery_eligibility(manifest=payload)
    assert result["status"] == "PASS"
    assert result["eligible_count"] == 2
    assert result["ineligible_count"] == 0
    assert result["operation_count"] == 2
    assert len(result["eligible_operations"]) == 2
    assert len(result["eligible_operations"][0]["source_identity"]) == 4
    assert not result["authority"]["candidate_disposition"]
    assert result["scope"]["provider_access"] is False
    verify_historical_discovery(result)


def test_h1_holds_when_required_counts_are_missing():
    payload = sample_manifest([
        {
            "operation_key": "watchtower",
            "source": source("watchtower_shadow_ep3_0d"),
            "candidate_count": 14203,
            "evidence_count": 0,
            "primitive_count": 85989,
        },
    ])
    result = build_historical_discovery_eligibility(manifest=payload)
    assert result["status"] == "HOLD"
    assert result["eligible_count"] == 0
    assert result["ineligible_count"] == 1
    assert result["ineligible_operations"][0]["reasons"] == ["EVIDENCE_COUNT_EMPTY"]


def test_h1_rejects_unbound_or_unusable_manifest():
    payload = sample_manifest([])
    with pytest.raises(Psi0hH1HistoricalDiscoveryError, match="NO_OPERATION_CONTEXT"):
        build_historical_discovery_eligibility(manifest=payload)

    payload = {"schema_version": "1.0.0", "milestone": "WRONG", "status": "PASS", "operations": []}
    with pytest.raises(Psi0hH1HistoricalDiscoveryError, match="GA_BINDING_INVALID"):
        build_historical_discovery_eligibility(manifest=payload)


def test_h1_runner_default_manifest_reads_existing_file():
    assert Path(DEFAULT_MANIFEST_PATH).is_file()
    loaded = json.loads(Path(DEFAULT_MANIFEST_PATH).read_text(encoding="utf-8"))
    result = build_historical_discovery_eligibility(manifest=loaded)
    assert result["milestone"] == "PSI0H-H1"
