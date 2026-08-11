from pathlib import Path
from tempfile import TemporaryDirectory

from src.acquisition.retained_observations import RetainedAcquisitionStore
from src.acquisition.transaction import AcquisitionMetadata, AcquisitionResponse
from src.evidence.artifacts import ArtifactStore


MINT = "11111111111111111111111111111111"


def response(*, provider="helius_rpc", body=b'{"result":{}}', correlation="correlation"):
    metadata = AcquisitionMetadata("acquisition", correlation, "creator_funding", "creator", MINT,
        "json_rpc", provider, "getTransaction", 1, None, 10.0, "miss", 0)
    return AcquisitionResponse(200, {"result": {}}, None, {"Content-Type": "application/json"}, metadata, 1.0, body, "EXACT_PROVIDER_ARTIFACT")


def store(root: Path):
    return RetainedAcquisitionStore(root / "retained.db", ArtifactStore(root / "artifacts", enabled=True))


def test_retained_observation_reconstructs_same_canonical_envelope_and_redacts_url():
    with TemporaryDirectory() as path:
        value = store(Path(path)); observed = value.retain(response(), http_method="POST", url="https://rpc.test/?api-key=secret&x=1", request_payload={"method":"getTransaction","params":["sig"]})
        rebuilt = value.dry_run_envelope(observed)
        assert rebuilt["state"] == "REPLAYABLE"
        envelope = rebuilt["envelope"]
        assert envelope["acquisition"]["launch"] == MINT
        assert envelope["acquisition"]["correlation_id"] == "correlation"
        assert envelope["artifact"]["digest"] == observed.artifact_digest
        assert "secret" not in envelope["provenance"]["source_metadata"]["request"]["url"]


def test_duplicate_is_idempotent_and_provider_disagreement_is_preserved():
    with TemporaryDirectory() as path:
        value = store(Path(path)); one = value.retain(response(), http_method="POST", url="https://rpc.test", request_payload={})
        duplicate = value.retain(response(), http_method="POST", url="https://rpc.test", request_payload={})
        other = value.retain(response(provider="solana_public_rpc", body=b'{"result":{"other":true}}'), http_method="POST", url="https://rpc.test", request_payload={})
        rows = value.get(mints=[MINT])
        assert one.observation_id == duplicate.observation_id
        assert len(rows) == 2
        assert {row.metadata["provider"] for row in rows} == {"helius_rpc", "solana_public_rpc"}
        assert len({row.artifact_digest for row in rows}) == 2


def test_reopen_is_deterministic_and_missing_input_is_explicit():
    with TemporaryDirectory() as path:
        root = Path(path); value = store(root); observation = value.retain(response(), http_method="POST", url="https://rpc.test", request_payload={})
        reopened = store(root); recovered = reopened.get(observation_ids=[observation.observation_id])[0]
        assert recovered == observation
        broken = object.__new__(type(observation)); object.__setattr__(broken, "observation_id", observation.observation_id); object.__setattr__(broken, "metadata", {"acquisition_id":"x"})
        for field in ("schema_version", "http_method", "url", "request_payload", "response_status", "response_data", "response_text", "response_headers", "raw_body_base64", "artifact_representation", "artifact_digest", "artifact_size_bytes", "artifact_compressed_bytes", "content_type"):
            object.__setattr__(broken, field, getattr(observation, field))
        assert reopened.dry_run_envelope(broken)["state"] == "NOT_REPLAYABLE"
