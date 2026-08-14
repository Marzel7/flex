import json
from pathlib import Path
import sqlite3

import pytest

from src.evidence.contracts.evidence_fulfillment_planning_proposal_extractor import (
    EvidenceFulfillmentPlanningProposalExtractorError,
    extract_evidence_fulfillment_planning_proposals,
)
from tests.test_eb1_3c_evidence_fulfillment_planning_proposal_adapters import (
    _bundle,
    _proposal,
    _review,
)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _fixture_db(path, reviews, proposals, *, extra_table=False):
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE review_records(position INTEGER PRIMARY KEY NOT NULL, canonical_json TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE proposal_records(position INTEGER PRIMARY KEY NOT NULL, canonical_json TEXT NOT NULL)"
    )
    if extra_table:
        connection.execute("CREATE TABLE unexpected(value TEXT)")
    connection.executemany(
        "INSERT INTO review_records VALUES (?,?)",
        [(position, _canonical(value)) for position, value in enumerate(reviews)],
    )
    connection.executemany(
        "INSERT INTO proposal_records VALUES (?,?)",
        [(position, _canonical(value)) for position, value in enumerate(proposals)],
    )
    connection.commit()
    connection.close()


def _inputs(tmp_path, disposition="READY_FOR_SEPARATE_PLANNING"):
    bundle, requirements, manifest, corpus = _bundle(tmp_path)
    history = _review(requirements, manifest, corpus, disposition)
    review = history.dispositions[0]
    review_record = {
        "requirement_id": review.requirement_id,
        "requirement_projection_digest": review.requirement_projection_digest,
        "requirement_manifest_digest": review.requirement_manifest_digest,
        "requirement_corpus_digest": review.requirement_corpus_digest,
        "disposition": review.disposition,
        "reviewer_identity_token": review.reviewer_identity_token,
        "review_sequence": review.review_sequence,
        "reason_code": review.reason_code,
        "rationale_digest": review.rationale_digest,
        "supersedes_disposition_id": review.supersedes_disposition_id,
    }
    return bundle, requirements, review_record, _proposal(requirements[0])


def test_fixture_extractor_projects_replay_verified_non_executable_outputs(tmp_path):
    bundle, _, review, proposal = _inputs(tmp_path)
    database = tmp_path / "fixture.db"
    _fixture_db(database, [review], [proposal])
    result = extract_evidence_fulfillment_planning_proposals(bundle, database)
    assert result.status == "PROJECTED"
    assert result.accounting.selected_proposal_count == 1
    assert result.accounting.accounting_residual == 0
    assert result.manifest.proposal_count == 1
    assert result.corpus.proposal_count == 1
    assert not result.grants_planning_authority and not result.grants_execution_authority


def test_not_ready_is_accounted_as_explicit_zero_selected_without_outputs(tmp_path):
    bundle, _, review, proposal = _inputs(tmp_path, "DEFERRED")
    database = tmp_path / "fixture.db"
    _fixture_db(database, [review], [proposal])
    result = extract_evidence_fulfillment_planning_proposals(bundle, database)
    assert result.status == "NO_ELIGIBLE_PROPOSALS"
    assert result.accounting.excluded_not_ready_count == 1
    assert result.accounting.selected_proposal_count == 0
    assert result.manifest is None and result.corpus is None


def test_unknown_requirement_fails_with_complete_accounting(tmp_path):
    bundle, _, review, proposal = _inputs(tmp_path)
    proposal["requirement_id"] = "unknown"
    database = tmp_path / "fixture.db"
    _fixture_db(database, [review], [proposal])
    with pytest.raises(EvidenceFulfillmentPlanningProposalExtractorError, match="ACCOUNTING_REJECTED") as caught:
        extract_evidence_fulfillment_planning_proposals(bundle, database)
    assert caught.value.accounting.unknown_requirement_count == 1
    assert caught.value.accounting.accounting_residual == 0


def test_exact_schema_canonical_json_and_contiguous_positions_fail_closed(tmp_path):
    bundle, _, review, proposal = _inputs(tmp_path)
    schema_database = tmp_path / "schema.db"
    _fixture_db(schema_database, [review], [proposal], extra_table=True)
    with pytest.raises(EvidenceFulfillmentPlanningProposalExtractorError, match="SCHEMA_MISMATCH"):
        extract_evidence_fulfillment_planning_proposals(bundle, schema_database)
    json_database = tmp_path / "json.db"
    _fixture_db(json_database, [review], [proposal])
    connection = sqlite3.connect(json_database)
    connection.execute("UPDATE proposal_records SET canonical_json=?", (json.dumps(proposal),))
    connection.commit(); connection.close()
    with pytest.raises(EvidenceFulfillmentPlanningProposalExtractorError, match="NONCANONICAL_JSON"):
        extract_evidence_fulfillment_planning_proposals(bundle, json_database)
    position_database = tmp_path / "position.db"
    _fixture_db(position_database, [review], [proposal])
    connection = sqlite3.connect(position_database)
    connection.execute("UPDATE proposal_records SET position=2")
    connection.commit(); connection.close()
    with pytest.raises(EvidenceFulfillmentPlanningProposalExtractorError, match="POSITION_DRIFT"):
        extract_evidence_fulfillment_planning_proposals(bundle, position_database)


def test_row_ceiling_and_review_conflict_fail_closed(tmp_path):
    bundle, _, review, proposal = _inputs(tmp_path)
    limit_database = tmp_path / "limit.db"
    _fixture_db(limit_database, [review], [dict(proposal, proposal_sequence=index) for index in range(65)])
    with pytest.raises(EvidenceFulfillmentPlanningProposalExtractorError, match="ROW_LIMIT"):
        extract_evidence_fulfillment_planning_proposals(bundle, limit_database)
    conflict_database = tmp_path / "conflict.db"
    _fixture_db(conflict_database, [review, review], [proposal])
    with pytest.raises(EvidenceFulfillmentPlanningProposalExtractorError, match="REVIEW_CONFLICT") as caught:
        extract_evidence_fulfillment_planning_proposals(bundle, conflict_database)
    assert caught.value.accounting.conflict_count == 1


def test_invalid_deadline_and_tampered_bundle_fail_closed(tmp_path):
    bundle, _, review, proposal = _inputs(tmp_path)
    database = tmp_path / "fixture.db"
    _fixture_db(database, [review], [proposal])
    with pytest.raises(EvidenceFulfillmentPlanningProposalExtractorError, match="INVALID_QUERY_BOUND"):
        extract_evidence_fulfillment_planning_proposals(bundle, database, max_query_seconds=31)
    (bundle / "manifest.json").write_text("{}\n")
    with pytest.raises(EvidenceFulfillmentPlanningProposalExtractorError, match="UNVERIFIED_BUNDLE"):
        extract_evidence_fulfillment_planning_proposals(bundle, database)


def test_query_deadline_is_checked_after_reads(tmp_path):
    bundle, _, review, proposal = _inputs(tmp_path)
    database = tmp_path / "fixture.db"
    _fixture_db(database, [review], [proposal])
    values = iter([0.0, 31.0])
    with pytest.raises(EvidenceFulfillmentPlanningProposalExtractorError, match="QUERY_TIMEOUT"):
        extract_evidence_fulfillment_planning_proposals(
            bundle, database, max_query_seconds=30, clock=lambda: next(values)
        )
