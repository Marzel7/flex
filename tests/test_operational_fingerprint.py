from src.discovery.operational_fingerprint import (
    EvidenceLineage, OperationalFingerprint, higher_order_eligibility,
)


def test_shared_funder_alone_is_not_higher_order_operation_evidence():
    result = higher_order_eligibility((
        OperationalFingerprint(mint="a", direct_funder="same"),
        OperationalFingerprint(mint="b", direct_funder="same"),
    ))
    assert result["eligible"] is False
    assert result["classification"] == "NOT_ELIGIBLE_DATA_GAP"
    assert "EP3_POPULATION_GAP" in result["missing_gates"]


def test_independent_topology_and_behaviour_are_required_without_scoring():
    result = higher_order_eligibility((OperationalFingerprint(
        mint="a", direct_funder="f", topology_refs=("topology-1",),
        behaviour_refs=("motif-1",), migration_signer="signer",
        source_lineage={"topology": EvidenceLineage.INDEPENDENT,
                        "behaviour": EvidenceLineage.INDEPENDENT},
    ),))
    assert result["eligible"] is True
    assert result["authority"] == "NON_AUTHORITATIVE_NO_PROMOTION"
