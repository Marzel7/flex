import sqlite3
from pathlib import Path

import pytest

from src.ops.investigation_trigger_provenance import capture_and_apply


ROOT = Path(__file__).resolve().parents[1]


def population(launches=None):
    return {
        "family_id": "family:trigger", "family_name": "Trigger Family",
        "first_seen_at": 100, "state_changed_at": 100,
        "launch_list": launches or ["M1", "M2"], "launches": 2,
        "member_wallets": ["CLIENT"], "client_wallets": ["CLIENT"],
        "provisioning_clients": ["CLIENT"], "unique_creators": ["CREATOR"],
        "treasuries": ["TREASURY"], "walkback_descendant_count": 2,
        "funding_mechanisms": ["PLAIN_XFER"],
        "dominant_topology": "Treasury → client → creator",
        "evidence_sources": ["wt_attribution_outcomes", "wt_provisioning_edges"],
    }


def test_creation_trigger_is_captured_once_and_never_overwritten(tmp_path):
    path = str(tmp_path / "ops.db")
    sqlite3.connect(path).close()
    family = population()
    first = capture_and_apply(path, [family], {family["family_id"]: {"disposition": "UNRESOLVED"}})
    assert first[family["family_id"]]["initial_population_size"] == 2
    assert "Provisioning Lineage" in first[family["family_id"]]["signals"]

    changed = population(["M1", "M2", "M3"])
    changed["dominant_topology"] = "Changed topology"
    second = capture_and_apply(path, [changed], {changed["family_id"]: {"disposition": "REVIEW"}})
    assert second[changed["family_id"]]["initial_population_size"] == 2
    assert second[changed["family_id"]]["initial_disposition"] == "UNRESOLVED"
    assert second[changed["family_id"]]["initial_topology"] == "Treasury → client → creator"

    conn = sqlite3.connect(path)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE wt_investigation_trigger_provenance SET initial_population_size=99")
    conn.close()


def test_trigger_contract_is_integrated_into_profile_and_discovery():
    profile = (ROOT / "templates/operation_profile.html").read_text()
    discovery = (ROOT / "templates/discovery.html").read_text()
    convergence = (ROOT / "src/discovery/operation_convergence.py").read_text()
    snapshot = (ROOT / "src/ops/emerging_operators_snapshot.py").read_text()
    assert "Investigation Trigger" in profile
    assert "Created because" in profile
    assert "Initial population" in profile
    # X78.6: trigger detail belongs to the Investigation profile; Discovery
    # is a lightweight recent-change feed.
    assert "Triggered by" not in discovery
    assert "Open Investigation →" in discovery
    assert "investigation_trigger" in convergence
    assert "capture_and_apply" in snapshot
