"""X70.1 creator-reuse evidence acceptance and regression tests."""
from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path

import pytest

from src.ops.disposition_resolver import (
    CONFIRMED_OPERATION,
    INFRASTRUCTURE,
    OPERATOR_CANDIDATE,
    REJECTED,
    REVIEW,
    UNRESOLVED,
    DispositionResolver,
)
from src.ops.emerging_operator_service import EmergingOperatorService
from src.ops.evidence_reconciliation import (
    CreatorReuseEvidence,
    EvidenceReconciliationService,
)
from src.ops.investigation_population import InvestigationPopulation


ROOT = Path(__file__).resolve().parents[1]
OPS_DB = str(ROOT / "database" / "wt_ops_v2.db")
LIVE_DB = str(ROOT / "database" / "flex_complete_database.db")
EXPECTED_CANDIDATES = {
    "3uBN Family", "68xd Family", "6Sv3 Family", "6tck Family",
    "8Ubp Family", "9cDD Family", "B94V Family", "BDWy Family",
    "CLK3 Family", "DhPY Family", "DssT Family", "Em9h Family",
    "F3Cc Family", "FUCK Family", "FxxX Family", "Hri2 Family",
}


def _population(creator="CREATOR"):
    return InvestigationPopulation(
        population_id="family:test", anchor="ANCHOR", population_basis=(),
        members=("ANCHOR",), launches=("MINT-A", "MINT-B", "MINT-C"), timeline=(),
        metadata={
            "treasuries": ("TREASURY",), "member_treasuries": (),
            "creators": (creator,), "mechanisms": (), "signatures": (),
            "first_seen_at": 100, "last_seen_at": 400,
            "session_count": 0, "active_session_count": 0,
            "observation_count": 3, "launch_count_hint": 3,
            "sources": ("wt_provisioning_edges",), "exclusions": (),
            "warnings": (), "edge_times": (), "evidence": (), "templates": (),
            "campaigns": (), "infrastructure_roles": (), "session_amounts": (),
        },
    )


@pytest.fixture
def reuse_db(tmp_path):
    path = tmp_path / "ops.db"
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE wt_provisioning_edges (
            edge_id TEXT, to_wallet TEXT, source_mint TEXT,
            funding_tx_signature TEXT, first_observed_by_flex INTEGER,
            last_observed_by_flex INTEGER
        )
    """)
    conn.executemany(
        "INSERT INTO wt_provisioning_edges VALUES (?,?,?,?,?,?)",
        [
            ("edge-a1", "CREATOR", "MINT-A", "sig-a1", 100, 110),
            ("edge-a2", "CREATOR", "MINT-A", "sig-a2", 105, 120),
            ("edge-b", "CREATOR", "MINT-B", "sig-b", 200, 210),
            ("edge-c", "CREATOR", "MINT-C", None, 300, 310),
            ("edge-other", "OTHER", "MINT-Z", "sig-z", 50, 50),
        ],
    )
    conn.commit()
    conn.close()
    return str(path)


def test_creator_reuse_model_is_immutable_complete_and_traceable(reuse_db):
    population = _population()
    package = EvidenceReconciliationService(
        reuse_db, infrastructure_lookup=lambda _: None
    ).build(population)

    assert len(package.creator_reuse_evidence) == 1
    reuse = package.creator_reuse_evidence[0]
    assert isinstance(reuse, CreatorReuseEvidence)
    assert reuse.creator_wallet == "CREATOR"
    assert tuple(item.mint for item in reuse.mint_evidence) == (
        "MINT-A", "MINT-B", "MINT-C"
    )
    assert reuse.supporting_edge_ids == ("edge-a1", "edge-a2", "edge-b", "edge-c")
    assert reuse.funding_transaction_signatures == ("sig-a1", "sig-a2", "sig-b")
    assert (reuse.first_observed_at, reuse.last_observed_at) == (100, 310)
    assert reuse.population_revision_ids == (population.revision_id,)
    assert reuse.provenance.source == "wt_provisioning_edges"
    assert reuse.provenance.table == "wt_provisioning_edges"
    assert reuse.provenance.rpc is False
    assert reuse.provenance.dependency_group == "creator_reuse"
    assert reuse.evidence_id.startswith("cre:")
    with pytest.raises(dataclasses.FrozenInstanceError):
        reuse.creator_wallet = "CHANGED"


def test_repeated_edges_and_mints_create_one_behavioural_source(reuse_db):
    package = EvidenceReconciliationService(
        reuse_db, infrastructure_lookup=lambda _: None
    ).build(_population())
    items = [
        item for item in package.supporting_evidence
        if item.evidence_type == "CREATOR_REUSE_CONTROL"
    ]
    assert len(items) == 1
    assert items[0].details["mints"] == ("MINT-A", "MINT-B", "MINT-C")
    group = next(group for group in package.dependency_groups if group.name == "creator_reuse")
    assert group.evidence_ids == (items[0].evidence_id,)
    assert all(
        item.evidence_type != "CREATOR_REUSE_UNAVAILABLE"
        for item in package.missing_evidence
    )
    assert DispositionResolver.resolve(package).disposition == OPERATOR_CANDIDATE


def test_single_mint_remains_missing_without_inference(tmp_path):
    path = tmp_path / "single.db"
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE wt_provisioning_edges (
        edge_id TEXT, to_wallet TEXT, source_mint TEXT,
        funding_tx_signature TEXT, first_observed_by_flex INTEGER,
        last_observed_by_flex INTEGER)""")
    conn.executemany("INSERT INTO wt_provisioning_edges VALUES (?,?,?,?,?,?)", [
        ("one", "CREATOR", "MINT-A", "sig-one", 1, 1),
        ("two", "CREATOR", "MINT-A", "sig-two", 2, 2),
    ])
    conn.commit(); conn.close()
    package = EvidenceReconciliationService(
        str(path), infrastructure_lookup=lambda _: None
    ).build(_population())
    assert package.creator_reuse_evidence == ()
    assert any(
        item.evidence_type == "CREATOR_REUSE_UNAVAILABLE"
        for item in package.missing_evidence
    )


def test_live_16_population_gain_and_named_controls():
    service = EmergingOperatorService(OPS_DB, LIVE_DB)
    with service._connect(service.ops_db_path) as conn:
        populations = service._population_builder().build(
            service._discovery_profiles(conn, service._tables(conn))
        )
    families = service._compose()
    family_by_id = {family["family_id"]: family for family in families}
    reconciler = EvidenceReconciliationService(OPS_DB)
    packages = reconciler.build_all(populations)
    results = {
        package.population.population_id: DispositionResolver.resolve(package)
        for package in packages
    }
    package_by_id = {package.population.population_id: package for package in packages}

    candidate_names = {
        family_by_id[population_id]["family_name"]
        for population_id, result in results.items()
        if result.disposition == OPERATOR_CANDIDATE
    }
    assert candidate_names == EXPECTED_CANDIDATES
    assert sum(bool(package.creator_reuse_evidence) for package in packages) == 25
    assert sum(len(package.creator_reuse_evidence) for package in packages) == 37

    b48 = next(f for f in families if "B48k" in f["family_name"])
    c7 = next(f for f in families if "C7Ha" in f["family_name"])
    assert results[b48["family_id"]].disposition == UNRESOLVED
    assert package_by_id[b48["family_id"]].creator_reuse_evidence == ()
    assert results[c7["family_id"]].disposition == REVIEW
    assert package_by_id[c7["family_id"]].creator_reuse_evidence == ()

    infrastructure = [r for r in results.values() if r.disposition == INFRASTRUCTURE]
    rejected = [r for r in results.values() if r.disposition == REJECTED]
    assert len(infrastructure) == len(rejected) == 9

    watchtower = next(f for f in families if f["family_name"] == "WATCHTOWER")
    watchtower_result = DispositionResolver.resolve(reconciler.build(
        reconciler.population_from_canonical_registry(watchtower)
    ))
    assert watchtower_result.disposition == CONFIRMED_OPERATION


def test_projection_has_no_rpc_or_external_access():
    source = Path(ROOT / "src" / "ops" / "evidence_reconciliation.py").read_text()
    assert "urllib" not in source
    assert "requests." not in source
    assert "http://" not in source and "https://" not in source
