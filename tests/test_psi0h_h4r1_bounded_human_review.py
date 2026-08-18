import json

import pytest

from scripts.run_psi0h_h4r1_bounded_human_review import run as run_h4r1
from src.evidence.contracts.psi0h_h4r1_bounded_human_review import (
    HUMAN_REVIEW_OPTIONS,
    PENDING_DISPOSITION,
    Psi0hH4R1BoundedHumanReviewError,
    prepare_bounded_human_review,
    verify_bounded_human_review,
)

H4R_PACKET = "docs/audits/psi0h_h4r_historical_continuity_review_packet.json"
H4R_INDEX = "docs/audits/psi0h_h4r_historical_continuity_review_index.json"


def load_fixture(path: str):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def test_h4r1_prepares_13_pending_rows_from_immutable_h4r():
    packet = load_fixture(H4R_PACKET)
    index = load_fixture(H4R_INDEX)
    result = prepare_bounded_human_review(h4r_packet=packet, h4r_index=index)
    assert result["status"] == "READY_FOR_HUMAN_REVIEW"
    assert result["candidate_count"] == 13
    assert len(result["review_sheet"]) == 13
    assert result["pending_human_decisions"] == 13
    assert result["recorded_human_decisions"] == 0
    for row in result["review_sheet"]:
        assert row["human_disposition"] == PENDING_DISPOSITION
        assert row["allowed_human_review_options"] == list(HUMAN_REVIEW_OPTIONS)
    verify_bounded_human_review(result)


def test_h4r1_rejects_packet_digest_tamper():
    packet = load_fixture(H4R_PACKET)
    index = load_fixture(H4R_INDEX)
    packet["artifact_digest"] = "0" * 64
    with pytest.raises(Psi0hH4R1BoundedHumanReviewError, match="H4R_PACKET_DIGEST_MISMATCH"):
        prepare_bounded_human_review(h4r_packet=packet, h4r_index=index)


def test_h4r1_rejects_index_digest_tamper():
    packet = load_fixture(H4R_PACKET)
    index = load_fixture(H4R_INDEX)
    index["artifact_digest"] = "1" * 64
    with pytest.raises(Psi0hH4R1BoundedHumanReviewError, match="H4R_INDEX_DIGEST_MISMATCH"):
        prepare_bounded_human_review(h4r_packet=packet, h4r_index=index)


def test_h4r1_no_new_candidates_no_widened_corpus_no_authority():
    packet = load_fixture(H4R_PACKET)
    index = load_fixture(H4R_INDEX)
    result = prepare_bounded_human_review(h4r_packet=packet, h4r_index=index)
    scope = result["scope"]
    for key in (
        "generates_new_candidates",
        "widens_operation_corpus",
        "performs_historical_backfill",
        "makes_provider_or_rpc_calls",
        "auto_selects_human_disposition",
        "operation_family_membership_authority",
        "same_human_or_operator_authority",
        "monitoring_or_watchlist_authority",
        "ranking_or_scoring_authority",
        "policy_or_trading_authority",
        "production_activation_authority",
    ):
        assert scope[key] is False
    disallowed = set(result["disallowed_terms"])
    assert "PROPOSED" in disallowed
    assert "SUPPORTED" in disallowed
    assert "SAME_OPERATION" in disallowed


def test_h4r1_disposition_taxonomy_excludes_banned_terms():
    assert set(HUMAN_REVIEW_OPTIONS) == {
        "COMMON_PLAYBOOK_ONLY",
        "PLAUSIBLE_OPERATIONAL_CONTINUITY",
        "PLAUSIBLE_OPERATIONAL_FAMILY",
        "INSUFFICIENT_EVIDENCE",
        "CONFLICTING_EVIDENCE",
    }
    assert "PROPOSED" not in HUMAN_REVIEW_OPTIONS
    assert "SUPPORTED" not in HUMAN_REVIEW_OPTIONS


def test_h4r1_digest_stability_and_row_ordering():
    packet = load_fixture(H4R_PACKET)
    index = load_fixture(H4R_INDEX)
    result = prepare_bounded_human_review(h4r_packet=packet, h4r_index=index)
    ids = [r["continuity_candidate_id"] for r in result["review_sheet"]]
    assert ids == sorted(ids)
    assert len(set(ids)) == 13

    again = prepare_bounded_human_review(h4r_packet=packet, h4r_index=index)
    assert again["artifact_digest"] == result["artifact_digest"]


def test_h4r1_runner_writes_review_sheet(tmp_path):
    output = tmp_path / "psi0h_h4r1_bounded_human_review.json"
    result = run_h4r1(h4r_packet=H4R_PACKET, h4r_index=H4R_INDEX, output=str(output))
    assert result["candidate_count"] == 13
    assert result["pending_human_decisions"] == 13
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["artifact_digest"] == result["artifact_digest"]
    verify_bounded_human_review(payload)
