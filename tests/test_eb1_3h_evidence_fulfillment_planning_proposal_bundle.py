import json
from pathlib import Path

import pytest

from src.evidence.contracts.evidence_fulfillment_planning_proposal_bundle import (
    EvidenceFulfillmentPlanningProposalBundleError,
    verify_evidence_fulfillment_planning_proposal_bundle,
    write_evidence_fulfillment_planning_proposal_bundle,
)
from src.evidence.contracts.evidence_fulfillment_planning_proposal_extractor import (
    extract_evidence_fulfillment_planning_proposals,
)
from tests.test_eb1_3g_evidence_fulfillment_planning_proposal_extractor import (
    _fixture_db,
    _inputs,
)


def _result(tmp_path, disposition="READY_FOR_SEPARATE_PLANNING"):
    bundle, _, review, proposal = _inputs(tmp_path, disposition)
    database = tmp_path / "fixture.db"
    _fixture_db(database, [review], [proposal])
    return extract_evidence_fulfillment_planning_proposals(bundle, database)


def test_projected_bundle_has_exact_files_canonical_hashes_and_replay(tmp_path):
    result = _result(tmp_path)
    output = tmp_path / "output"
    written = write_evidence_fulfillment_planning_proposal_bundle(
        result, output, run_id="run-projected", engineering_revision="abcdef0"
    )
    assert {item.name for item in output.iterdir()} == {
        "run.json", "accounting.json", "manifest.json", "corpus.json", "hashes.json"
    }
    assert verify_evidence_fulfillment_planning_proposal_bundle(output) == written


def test_zero_eligible_bundle_never_fabricates_manifest_or_corpus(tmp_path):
    result = _result(tmp_path, "DEFERRED")
    output = tmp_path / "output"
    written = write_evidence_fulfillment_planning_proposal_bundle(
        result, output, run_id="run-zero", engineering_revision="abcdef0"
    )
    assert written.status == "NO_ELIGIBLE_PROPOSALS"
    assert {item.name for item in output.iterdir()} == {
        "run.json", "accounting.json", "hashes.json"
    }
    assert verify_evidence_fulfillment_planning_proposal_bundle(output) == written


def test_write_once_and_run_metadata_fail_closed(tmp_path):
    result = _result(tmp_path)
    output = tmp_path / "output"
    output.mkdir(); (output / "existing").write_text("x")
    with pytest.raises(EvidenceFulfillmentPlanningProposalBundleError, match="OUTPUT_NOT_EMPTY"):
        write_evidence_fulfillment_planning_proposal_bundle(
            result, output, run_id="run", engineering_revision="abcdef0"
        )
    with pytest.raises(EvidenceFulfillmentPlanningProposalBundleError, match="INVALID_RUN_METADATA"):
        write_evidence_fulfillment_planning_proposal_bundle(
            result, tmp_path / "other", run_id="", engineering_revision="bad"
        )


def test_missing_extra_and_altered_files_fail_closed(tmp_path):
    result = _result(tmp_path)
    missing = tmp_path / "missing"
    write_evidence_fulfillment_planning_proposal_bundle(
        result, missing, run_id="run", engineering_revision="abcdef0"
    )
    (missing / "manifest.json").unlink()
    with pytest.raises(EvidenceFulfillmentPlanningProposalBundleError, match="FILE_SET"):
        verify_evidence_fulfillment_planning_proposal_bundle(missing)
    extra = tmp_path / "extra"
    write_evidence_fulfillment_planning_proposal_bundle(
        result, extra, run_id="run", engineering_revision="abcdef0"
    )
    (extra / "extra.json").write_text("{}")
    with pytest.raises(EvidenceFulfillmentPlanningProposalBundleError, match="FILE_SET"):
        verify_evidence_fulfillment_planning_proposal_bundle(extra)
    altered = tmp_path / "altered"
    write_evidence_fulfillment_planning_proposal_bundle(
        result, altered, run_id="run", engineering_revision="abcdef0"
    )
    (altered / "accounting.json").write_text("{}\n")
    with pytest.raises(EvidenceFulfillmentPlanningProposalBundleError, match="DIGEST_MISMATCH"):
        verify_evidence_fulfillment_planning_proposal_bundle(altered)


def test_authority_change_with_rehashed_files_still_fails(tmp_path):
    result = _result(tmp_path)
    output = tmp_path / "output"
    write_evidence_fulfillment_planning_proposal_bundle(
        result, output, run_id="run", engineering_revision="abcdef0"
    )
    run = json.loads((output / "run.json").read_text())
    run["grants_execution_authority"] = True
    (output / "run.json").write_text(
        json.dumps(run, sort_keys=True, separators=(",", ":")) + "\n"
    )
    hashes = json.loads((output / "hashes.json").read_text())
    import hashlib
    hashes["files"]["run.json"] = hashlib.sha256((output / "run.json").read_bytes()).hexdigest()
    canonical_files = (json.dumps(hashes["files"], sort_keys=True, separators=(",", ":")) + "\n").encode()
    hashes["bundle_digest"] = hashlib.sha256(canonical_files).hexdigest()
    (output / "hashes.json").write_text(
        json.dumps(hashes, sort_keys=True, separators=(",", ":")) + "\n"
    )
    with pytest.raises(EvidenceFulfillmentPlanningProposalBundleError, match="AUTHORITY_MISMATCH"):
        verify_evidence_fulfillment_planning_proposal_bundle(output)


def test_noncanonical_and_result_replay_mutation_fail_closed(tmp_path):
    result = _result(tmp_path)
    output = tmp_path / "output"
    write_evidence_fulfillment_planning_proposal_bundle(
        result, output, run_id="run", engineering_revision="abcdef0"
    )
    run = json.loads((output / "run.json").read_text())
    (output / "run.json").write_text(json.dumps(run, indent=2))
    with pytest.raises(EvidenceFulfillmentPlanningProposalBundleError, match="NONCANONICAL"):
        verify_evidence_fulfillment_planning_proposal_bundle(output)
