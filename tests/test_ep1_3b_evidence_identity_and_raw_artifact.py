from __future__ import annotations

import hashlib
import json

import pytest

from src.acquisition.transaction import AcquisitionMetadata, AcquisitionResponse
from src.evidence.contracts import (
    ArtifactRepresentation,
    RawArtifact,
    canonical_json_bytes,
    evidence_id,
    logical_fact_id,
    payload_digest,
)
from src.evidence.mirror import EvidenceMirrorPublisher


def test_canonical_json_is_deterministic_and_rejects_ambiguous_values():
    left = canonical_json_bytes({"z": None, "a": ["é", 3, True]})
    right = canonical_json_bytes({"a": ["é", 3, True], "z": None})
    assert left == right == b'{"a":["\xc3\xa9",3,true],"z":null}\n'
    with pytest.raises(TypeError, match="float"):
        canonical_json_bytes({"amount": 0.1})
    with pytest.raises(TypeError, match="non-string"):
        canonical_json_bytes({1: "value"})
    with pytest.raises(TypeError, match="unsupported"):
        canonical_json_bytes({"body": b"bytes"})


def test_provider_and_parser_observations_share_only_logical_identity():
    logical = logical_fact_id(
        fact_family="NativeMovementFact",
        chain="solana",
        network="mainnet-beta",
        natural_key="signature:3:source:recipient",
    )
    normalized = payload_digest({"lamports": 1000, "source": "A", "recipient": "B"})
    artifact_a = hashlib.sha256(b'{"provider":"a"}').hexdigest()
    artifact_b = hashlib.sha256(b'{ "provider": "b" }').hexdigest()
    first = evidence_id(
        fact_family="NativeMovementFact",
        fact_schema_version="1",
        logical_fact_id_value=logical,
        parser_id="solana-json-rpc",
        parser_version="1",
        normalized_payload_digest=normalized,
        raw_artifact_digest=artifact_a,
    )
    replay = evidence_id(
        fact_family="NativeMovementFact",
        fact_schema_version="1",
        logical_fact_id_value=logical,
        parser_id="solana-json-rpc",
        parser_version="1",
        normalized_payload_digest=normalized,
        raw_artifact_digest=artifact_a,
    )
    other_provider = evidence_id(
        fact_family="NativeMovementFact",
        fact_schema_version="1",
        logical_fact_id_value=logical,
        parser_id="solana-json-rpc",
        parser_version="1",
        normalized_payload_digest=normalized,
        raw_artifact_digest=artifact_b,
    )
    other_parser = evidence_id(
        fact_family="NativeMovementFact",
        fact_schema_version="1",
        logical_fact_id_value=logical,
        parser_id="solana-json-rpc",
        parser_version="2",
        normalized_payload_digest=normalized,
        raw_artifact_digest=artifact_a,
    )
    conflicting_payload = evidence_id(
        fact_family="NativeMovementFact",
        fact_schema_version="1",
        logical_fact_id_value=logical,
        parser_id="solana-json-rpc",
        parser_version="1",
        normalized_payload_digest=payload_digest(
            {"lamports": 2000, "source": "A", "recipient": "B"}
        ),
        raw_artifact_digest=artifact_b,
    )
    assert first == replay
    assert len({first, other_provider, other_parser, conflicting_payload}) == 4


def test_raw_artifact_distinguishes_exact_canonicalized_and_unavailable():
    body = b'{ "jsonrpc": "2.0", "result": null }\n'
    request_digest = hashlib.sha256(b"request").hexdigest()
    exact = RawArtifact.from_exact_bytes(
        body,
        media_type="application/json",
        compression="none",
        encrypted=False,
        provider="helius_rpc",
        endpoint="https://mainnet.helius-rpc.com/",
        request_parameters_digest=request_digest,
        response_status=200,
        acquired_at=1,
    )
    canonical = RawArtifact(
        artifact_digest=hashlib.sha256(body).hexdigest(),
        media_type="application/json",
        compression="none",
        encrypted=False,
        byte_length=len(body),
        provider="helius_rpc",
        endpoint="https://mainnet.helius-rpc.com/",
        request_parameters_digest=request_digest,
        response_status=200,
        acquired_at=1,
        payload=body,
        representation=ArtifactRepresentation.CANONICALIZED_RESPONSE_REPRESENTATION,
    )
    assert exact.satisfies_exact_replay_contract
    assert not canonical.satisfies_exact_replay_contract
    assert exact.artifact_digest == hashlib.sha256(body).hexdigest()
    with pytest.raises(ValueError, match="cannot contain fabricated"):
        RawArtifact(
            artifact_digest=hashlib.sha256(body).hexdigest(),
            media_type="application/json",
            compression="none",
            encrypted=False,
            byte_length=len(body),
            provider="helius_rpc",
            endpoint="https://mainnet.helius-rpc.com/",
            request_parameters_digest=request_digest,
            response_status=200,
            acquired_at=1,
            payload=body,
            representation=ArtifactRepresentation.RAW_BYTES_UNAVAILABLE,
        )


def _response(raw_body: bytes | None) -> AcquisitionResponse:
    metadata = AcquisitionMetadata(
        acquisition_id="acq-exact",
        correlation_id="corr-exact",
        purpose="creator_funding",
        creator="creator",
        launch="mint",
        request_type="json_rpc",
        provider="helius_rpc",
        method="getTransaction",
        page_number=None,
        cursor=None,
        timestamp=1.0,
        cache_state="miss",
        retry_count=0,
    )
    return AcquisitionResponse(
        status=200,
        data={"jsonrpc": "2.0", "result": None},
        text=None,
        headers={"Content-Type": "application/json"},
        metadata=metadata,
        latency_ms=1.0,
        raw_body=raw_body,
        artifact_representation=(
            "EXACT_PROVIDER_ARTIFACT" if raw_body is not None else "RAW_BYTES_UNAVAILABLE"
        ),
    )


def test_mirror_preserves_exact_bytes_and_marks_parsed_only_legacy():
    exact_bytes = b'{ "result" : null, "jsonrpc" : "2.0" }\n'
    exact_item = EvidenceMirrorPublisher.item_from_response(
        _response(exact_bytes),
        http_method="POST",
        url="https://mainnet.helius-rpc.com/?api-key=secret",
        request_payload={"method": "getTransaction"},
        handoff_at=2.0,
    )
    legacy_item = EvidenceMirrorPublisher.item_from_response(
        _response(None),
        http_method="POST",
        url="https://mainnet.helius-rpc.com/",
        request_payload={"method": "getTransaction"},
        handoff_at=2.0,
    )
    assert EvidenceMirrorPublisher._artifact_payload(None, exact_item) == exact_bytes
    assert exact_item.artifact_representation == "EXACT_PROVIDER_ARTIFACT"
    assert legacy_item.artifact_representation == "CANONICALIZED_RESPONSE_REPRESENTATION"
    assert json.loads(EvidenceMirrorPublisher._artifact_payload(None, legacy_item))["data"]
