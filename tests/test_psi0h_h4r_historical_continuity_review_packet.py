import json

import pytest

from scripts.run_psi0h_h4r_historical_continuity_review import run as run_review
from src.evidence.contracts.psi0h_h4r_historical_continuity_review_packet import (
    Psi0hH4RHistoricalContinuityReviewPacketError,
    qualify_historical_continuity_review_packet,
    verify_historical_continuity_review_packet,
    BINDING_DIGESTS,
)


H2_ARTIFACT = "docs/audits/psi0h_h2_historical_candidate_generation_from_h4_150_continuity.json"
H3_ARTIFACT = "docs/audits/psi0h_h3_historical_candidate_disposition_from_h4_150_continuity.json"
H4_ARTIFACT = "docs/audits/psi0h_h4_historical_operation_census_from_h8_projection_continuity.json"


def load_fixture(path: str):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def test_h4r_review_packet_honors_boundary_and_shape():
    h2 = load_fixture(H2_ARTIFACT)
    h3 = load_fixture(H3_ARTIFACT)
    h4 = load_fixture(H4_ARTIFACT)
    result = qualify_historical_continuity_review_packet(h2_artifact=h2, h3_artifact=h3, h4_artifact=h4)
    assert result["status"] == "PASS"
    assert result["candidate_count"] == 13
    assert result["unique_operation_count"] == len(
        {row["operation_a"]["operation_id"] for row in result["reviewed_rows"]} | {row["operation_b"]["operation_id"] for row in result["reviewed_rows"]}
    )
    assert len(result["reviewed_rows"]) == 13
    assert len(result["review_index"]) == 13
    assert result["review_options_distribution"]["PLAUSIBLE_OPERATIONAL_CONTINUITY"] + result["review_options_distribution"]["PLAUSIBLE_OPERATIONAL_FAMILY"] == 13
    assert all(not row.get("candidate_disposition") for row in result["reviewed_rows"])
    assert result["same_operation_inference_blocked"] is True
    assert result["same_human_inference_blocked"] is True
    verify_historical_continuity_review_packet(result)


def test_h4r_review_packet_enforces_expected_input_digests():
    h2 = load_fixture(H2_ARTIFACT)
    h3 = load_fixture(H3_ARTIFACT)
    h4 = load_fixture(H4_ARTIFACT)
    h2["artifact_digest"] = "0" * 64
    with pytest.raises(Psi0hH4RHistoricalContinuityReviewPacketError, match="H2_DIGEST_MISMATCH"):
        qualify_historical_continuity_review_packet(h2_artifact=h2, h3_artifact=h3, h4_artifact=h4)


def test_h4r_review_packet_rejects_h2_h3_candidate_boundary_drift():
    h2 = load_fixture(H2_ARTIFACT)
    h3 = load_fixture(H3_ARTIFACT)
    h4 = load_fixture(H4_ARTIFACT)
    evidence_rows = [r for r in h2.get("candidate_rows", []) if r.get("relationship") == "evidence_of_continuity"]
    if not evidence_rows:
        pytest.skip("No H2 rows available in fixture")
    evidence_rows[0]["continuity_candidate_id"] = "tampered_id"
    with pytest.raises(Psi0hH4RHistoricalContinuityReviewPacketError, match="H3_H2_BOUNDARY_MISMATCH"):
        qualify_historical_continuity_review_packet(h2_artifact=h2, h3_artifact=h3, h4_artifact=h4)


def test_h4r_review_packet_rejects_h3_digest_tamper():
    h2 = load_fixture(H2_ARTIFACT)
    h3 = load_fixture(H3_ARTIFACT)
    h4 = load_fixture(H4_ARTIFACT)
    h3["artifact_digest"] = "1" * 64
    with pytest.raises(Psi0hH4RHistoricalContinuityReviewPacketError, match="H3_DIGEST_MISMATCH"):
        qualify_historical_continuity_review_packet(h2_artifact=h2, h3_artifact=h3, h4_artifact=h4)


def test_h4r_review_packet_rejects_h4_schema_or_projection_tamper():
    h2 = load_fixture(H2_ARTIFACT)
    h3 = load_fixture(H3_ARTIFACT)
    h4 = load_fixture(H4_ARTIFACT)
    h4["artifact_digest"] = "2" * 64
    with pytest.raises(Psi0hH4RHistoricalContinuityReviewPacketError, match="H4_DIGEST_MISMATCH"):
        qualify_historical_continuity_review_packet(h2_artifact=h2, h3_artifact=h3, h4_artifact=h4)

    h4 = load_fixture(H4_ARTIFACT)
    h4_source = h4.setdefault("source", {})
    if h4_source.get("manifest_digest"):
        h4_source["manifest_digest"] = "3" * 64
    else:
        h4_source["manifest_digest"] = "3" * 64
    with pytest.raises(Psi0hH4RHistoricalContinuityReviewPacketError, match="MANIFEST_DIGEST_MISMATCH"):
        qualify_historical_continuity_review_packet(h2_artifact=h2, h3_artifact=h3, h4_artifact=h4)


def test_h4r_boundary_invariants_and_derived_counts():
    h2 = load_fixture(H2_ARTIFACT)
    h3 = load_fixture(H3_ARTIFACT)
    h4 = load_fixture(H4_ARTIFACT)
    result = qualify_historical_continuity_review_packet(h2_artifact=h2, h3_artifact=h3, h4_artifact=h4)

    continuity_candidate_rows = [r for r in h2["candidate_rows"] if r.get("relationship") == "evidence_of_continuity"]
    continuity_candidate_ids = [str(r["continuity_candidate_id"]) for r in continuity_candidate_rows]
    assert len(continuity_candidate_ids) == 13
    assert len(set(continuity_candidate_ids)) == 13
    reviewed_ids = [row["continuity_candidate_id"] for row in result["reviewed_rows"]]
    assert reviewed_ids == sorted(reviewed_ids)

    h3_candidate_ids = [
        str(r["continuity_candidate_id"])
        for r in h3["reviewed_rows"]
        if r.get("relationship") == "evidence_of_continuity"
    ]
    assert len(set(h3_candidate_ids)) == 13
    assert set(continuity_candidate_ids) == set(h3_candidate_ids)
    assert len(result["reviewed_rows"]) == len(continuity_candidate_ids)
    assert reviewed_ids == sorted(continuity_candidate_ids)

    derived_unique_ops = len(
        {
            row["operation_a"]["operation_id"]
            for row in result["reviewed_rows"]
        }
        | {
            row["operation_b"]["operation_id"]
            for row in result["reviewed_rows"]
        }
    )
    assert result["unique_operation_count"] == derived_unique_ops == 22


def test_h4r_duplicate_candidate_ids_fail_closed():
    h2 = load_fixture(H2_ARTIFACT)
    h3 = load_fixture(H3_ARTIFACT)
    h4 = load_fixture(H4_ARTIFACT)
    continuity_rows = [r for r in h2["candidate_rows"] if r.get("relationship") == "evidence_of_continuity"]
    if not continuity_rows:
        pytest.skip("No H2 continuity rows available in fixture")
    duplicate = dict(continuity_rows[0])
    h2["candidate_rows"].append(duplicate)
    with pytest.raises(Psi0hH4RHistoricalContinuityReviewPacketError, match="DUPLICATE_CANDIDATE_ID"):
        qualify_historical_continuity_review_packet(h2_artifact=h2, h3_artifact=h3, h4_artifact=h4)


def test_h4r_provenance_and_authority_are_zeroed():
    h2 = load_fixture(H2_ARTIFACT)
    h3 = load_fixture(H3_ARTIFACT)
    h4 = load_fixture(H4_ARTIFACT)
    result = qualify_historical_continuity_review_packet(h2_artifact=h2, h3_artifact=h3, h4_artifact=h4)
    for row in result["reviewed_rows"]:
        assert row["identity_guards"]["same_human_claim"] is False
        assert row["identity_guards"]["same_operation_claim"] is False
        assert row["identity_guards"]["operation_a_separate_identity"] is True
        assert row["identity_guards"]["operation_b_separate_identity"] is True
        assert not row.get("candidate_disposition")
        assert "common_playbook" in row["evidence_accounting"]["provenance_refs"]
        assert "continuity_signal" in row["evidence_accounting"]["provenance_refs"]
    authority = result["authority"]
    for key in (
        "candidate_generation",
        "candidate_disposition",
        "comparison",
        "monitoring",
        "activation",
        "supported",
        "same_operation",
        "same_human",
        "ranking",
        "policy",
        "alerting",
        "trading",
    ):
        assert authority[key] is False


def test_h4r_digest_and_replay_stability(tmp_path):
    h2 = load_fixture(H2_ARTIFACT)
    h3 = load_fixture(H3_ARTIFACT)
    h4 = load_fixture(H4_ARTIFACT)
    output = tmp_path / "review_packet.json"
    run_review(h2_artifact=H2_ARTIFACT, h3_artifact=H3_ARTIFACT, h4_artifact=H4_ARTIFACT, output=str(output))
    packet = json.loads(output.read_text(encoding="utf-8"))
    index = json.loads(
        output.with_name("psi0h_h4r_historical_continuity_review_index.json").read_text(encoding="utf-8")
    )
    replay = dict(packet)
    replay.pop("artifact_digest")
    replay_digest = __import__("hashlib").sha256(
        __import__("json").dumps(replay, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert replay_digest == packet["artifact_digest"]
    index_replay = dict(index)
    index_replay.pop("artifact_digest")
    index_digest = __import__("hashlib").sha256(
        __import__("json").dumps(index_replay, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert index_digest == index["artifact_digest"]

    run_review(h2_artifact=H2_ARTIFACT, h3_artifact=H3_ARTIFACT, h4_artifact=H4_ARTIFACT, output=str(output))
    again_packet = json.loads(output.read_text(encoding="utf-8"))
    again_index = json.loads(
        output.with_name("psi0h_h4r_historical_continuity_review_index.json").read_text(encoding="utf-8")
    )
    assert again_packet["artifact_digest"] == packet["artifact_digest"]
    assert again_index["artifact_digest"] == index["artifact_digest"]


def test_h4r_review_runner_generates_packet_and_index(tmp_path):
    artifact = tmp_path / "review_packet.json"
    out = run_review(
        h2_artifact=H2_ARTIFACT,
        h3_artifact=H3_ARTIFACT,
        h4_artifact=H4_ARTIFACT,
        output=str(artifact),
    )
    packet = json.loads((tmp_path / "review_packet.json").read_text(encoding="utf-8"))
    index = json.loads((tmp_path / "psi0h_h4r_historical_continuity_review_index.json").read_text(encoding="utf-8"))
    assert out["candidate_count"] == 13
    assert out["artifact"] == str(artifact)
    assert packet["artifact_digest"] == out["artifact_digest"]
    assert index["artifact_digest"] == out["index_artifact_digest"]
    assert len(index["review_index"]) == 13
    verify_historical_continuity_review_packet(packet)


def test_h4r_binding_expected_digests_are_stable_constants():
    assert BINDING_DIGESTS["h2"] == "db1febb4c282695cc3fdd63886e7e7cd95ea4ffaace9842f85aab23cc81f8325"
    assert BINDING_DIGESTS["h3"] == "9baa117841ec4be023495f1627c96e9810dec43af270a132d71cdd923bf301f8"
    assert BINDING_DIGESTS["h4"] == "cf18ed4c461d811c3485d9af31ce45694080f2f68017e730e59a214fecd005d7"
