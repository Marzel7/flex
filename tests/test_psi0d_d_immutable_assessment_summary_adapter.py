from dataclasses import asdict, replace
from hashlib import sha256
import json

import pytest

from src.evidence.contracts import production_shadow_assessment_summary_adapter as adapter
from src.evidence.contracts.production_shadow_assessment import (
    PSI0B_BUNDLE_DIGEST,
    PSI0B_G_DIGEST,
    PSI0C_A_DIGEST,
    QUERY_IDS,
    build_shadow_assessment_contract,
)
from src.evidence.contracts.production_shadow_assessment_summary_consumer import (
    AssessmentSummaryConsumerError,
    PRODUCTION_DERIVED_PROVENANCE_CLASS,
    build_assessment_summary_consumer_contract,
    project_fixture_assessment_summary,
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def member(cohort=2, present=1, rows=1, unmatched=0, unmatched_mints=None):
    unique = present + unmatched
    return {
        "row_count": rows,
        "unique_mint_count": unique,
        "cohort_present_count": present,
        "cohort_denominator": cohort,
        "coverage_numerator": present,
        "coverage_denominator": cohort,
        "duplicate_row_count": rows - unique,
        "unmatched_mints": [] if unmatched_mints is None else unmatched_mints,
        "unmatched_row_count": unmatched,
    }


def assessment_document():
    membership = {query_id: member() for query_id in QUERY_IDS}
    document = {
        "schema_version": "psi0c-b.assessment.v1",
        "contract_digest": adapter.PSI0C_B_DIGEST,
        "input_lineage": {
            "psi0c_a_digest": PSI0C_A_DIGEST,
            "psi0b_g_digest": PSI0B_G_DIGEST,
            "psi0b_bundle_identity_digest": PSI0B_BUNDLE_DIGEST,
        },
        "fixture_only": False,
        "provenance_class": adapter.SOURCE_PROVENANCE_CLASS,
        "cohort_count": 2,
        "membership": membership,
        "missingness": [
            {"mint": "SECRET_MINT_A", "surfaces": {query_id: "PRESENT" for query_id in QUERY_IDS}, "negative_outcome_inferred": False},
            {"mint": "SECRET_MINT_B", "surfaces": {query_id: "ABSENT_NOT_NEGATIVE" for query_id in QUERY_IDS}, "negative_outcome_inferred": False},
        ],
        "conflicts": [],
        "orphan_unmatched_accounting": {"unmatched_mints": [], "unmatched_rows": 0},
        "reason_codes": ["PSI0C_B_ABSENCE_IS_NOT_NEGATIVE"],
        "authority": {"policy": False, "ranking": False, "integration": False, "activation": False},
    }
    document["assessment_digest"] = digest(document)
    return document


def bundle_files(assessment=None):
    assessment = assessment_document() if assessment is None else assessment
    contract = asdict(build_shadow_assessment_contract())
    contract["result_schemas"] = [[key, list(value)] for key, value in contract["result_schemas"]]
    payloads = {"assessment.json": canonical(assessment), "contract.json": canonical(contract)}
    file_digests = {name: sha256(payload).hexdigest() for name, payload in payloads.items()}
    bundle_digest = digest(file_digests)
    payloads["hashes.json"] = canonical({"file_digests": file_digests, "bundle_digest": bundle_digest})
    return payloads, assessment["assessment_digest"], bundle_digest


def execute(monkeypatch, files=None):
    files, assessment_digest, bundle_digest = bundle_files() if files is None else files
    monkeypatch.setattr(adapter, "EXPECTED_ASSESSMENT_DIGEST", assessment_digest)
    monkeypatch.setattr(adapter, "EXPECTED_BUNDLE_DIGEST", bundle_digest)
    adapter_contract = adapter.build_immutable_assessment_summary_adapter_contract()
    consumer_contract = build_assessment_summary_consumer_contract()
    result = adapter.adapt_immutable_assessment_summary(
        adapter_contract, consumer_contract, bundle_files=files,
    )
    assert adapter.verify_immutable_assessment_summary_projection(
        adapter_contract, consumer_contract, bundle_files=files, projection=result,
    )
    return result


def projection_document(result):
    return json.loads(result.canonical_projection)


def test_valid_frozen_bundle_preserves_provenance_and_emits_no_values(monkeypatch):
    result = execute(monkeypatch)
    document = projection_document(result)
    assert document["fixture_only"] is False
    assert document["provenance_class"] == PRODUCTION_DERIVED_PROVENANCE_CLASS
    assert not any(document["authority"].values())
    assert b"SECRET_MINT" not in result.canonical_projection
    assert set(document["surfaces"]) == set(QUERY_IDS)


def test_duplicate_conflict_and_unmatched_counts_are_preserved(monkeypatch):
    assessment = assessment_document()
    assessment["membership"]["snapshot_selected_cohort"] = member(present=1, rows=3)
    assessment["membership"]["ops_selected_cohort"] = member(
        present=1, rows=2, unmatched=1, unmatched_mints=["SECRET_ORPHAN"],
    )
    assessment["conflicts"] = [{
        "mint": "SECRET_CONFLICT", "field": "creator",
        "reason_code": "PSI0C_B_CONFLICT_PRESERVED_UNRESOLVED",
        "assertions": [{"value": "SECRET_ADDRESS"}], "resolved_value": None,
    }]
    assessment["orphan_unmatched_accounting"] = {"unmatched_mints": ["SECRET_ORPHAN"], "unmatched_rows": 1}
    assessment["reason_codes"] += [
        "PSI0C_B_CONFLICT_PRESERVED_UNRESOLVED",
        "PSI0C_B_UNMATCHED_KEY_RECORDED",
    ]
    assessment["assessment_digest"] = digest({key: value for key, value in assessment.items() if key != "assessment_digest"})
    files = bundle_files(assessment)
    result = execute(monkeypatch, files)
    document = projection_document(result)
    assert document["surfaces"]["snapshot_selected_cohort"]["duplicate_row_count"] == 2
    assert document["surfaces"]["ops_selected_cohort"]["unmatched_row_count"] == 1
    assert document["unresolved_conflict_count"] == 1
    assert document["orphan_unmatched_count"] == 1
    assert not any(secret in result.canonical_projection for secret in (b"SECRET_ORPHAN", b"SECRET_CONFLICT", b"SECRET_ADDRESS"))


@pytest.mark.parametrize("mutation", ("missing", "extra", "altered", "noncanonical"))
def test_file_and_canonical_byte_faults_fail_closed(monkeypatch, mutation):
    files, assessment_digest, bundle_digest = bundle_files(); files = dict(files)
    if mutation == "missing":
        files.pop("contract.json")
    elif mutation == "extra":
        files["extra.json"] = b"{}\n"
    elif mutation == "altered":
        files["assessment.json"] = canonical({})
    else:
        files["contract.json"] = files["contract.json"].rstrip(b"\n")
    monkeypatch.setattr(adapter, "EXPECTED_ASSESSMENT_DIGEST", assessment_digest)
    monkeypatch.setattr(adapter, "EXPECTED_BUNDLE_DIGEST", bundle_digest)
    with pytest.raises(adapter.ImmutableAssessmentSummaryAdapterError):
        adapter.adapt_immutable_assessment_summary(
            adapter.build_immutable_assessment_summary_adapter_contract(),
            build_assessment_summary_consumer_contract(), bundle_files=files,
        )


@pytest.mark.parametrize("field,value,reason", (
    ("contract_digest", "0" * 64, "LINEAGE_OR_PROVENANCE"),
    ("fixture_only", True, "LINEAGE_OR_PROVENANCE"),
    ("provenance_class", "FIXTURE", "LINEAGE_OR_PROVENANCE"),
))
def test_assessment_contract_lineage_and_provenance_drift_fail(monkeypatch, field, value, reason):
    assessment = assessment_document(); assessment[field] = value
    assessment["assessment_digest"] = digest({key: val for key, val in assessment.items() if key != "assessment_digest"})
    files = bundle_files(assessment)
    monkeypatch.setattr(adapter, "EXPECTED_ASSESSMENT_DIGEST", files[1])
    monkeypatch.setattr(adapter, "EXPECTED_BUNDLE_DIGEST", files[2])
    with pytest.raises(adapter.ImmutableAssessmentSummaryAdapterError, match=reason):
        adapter.adapt_immutable_assessment_summary(
            adapter.build_immutable_assessment_summary_adapter_contract(),
            build_assessment_summary_consumer_contract(), bundle_files=files[0],
        )


def test_authority_query_membership_missingness_and_reason_drift_fail(monkeypatch):
    mutations = []
    value = assessment_document(); value["authority"]["integration"] = True; mutations.append((value, "AUTHORITY"))
    value = assessment_document(); value["membership"].pop("ops_selected_cohort"); mutations.append((value, "QUERY_IDENTITY"))
    value = assessment_document(); value["membership"]["ops_selected_cohort"]["score"] = 1; mutations.append((value, "MEMBERSHIP_SCHEMA"))
    value = assessment_document(); value["missingness"][0]["negative_outcome_inferred"] = True; mutations.append((value, "MISSINGNESS"))
    value = assessment_document(); value["reason_codes"].append("RANK"); mutations.append((value, "REASON_CODE"))
    for assessment, reason in mutations:
        assessment["assessment_digest"] = digest({key: val for key, val in assessment.items() if key != "assessment_digest"})
        files, assessment_digest, bundle_digest = bundle_files(assessment)
        monkeypatch.setattr(adapter, "EXPECTED_ASSESSMENT_DIGEST", assessment_digest)
        monkeypatch.setattr(adapter, "EXPECTED_BUNDLE_DIGEST", bundle_digest)
        with pytest.raises(adapter.ImmutableAssessmentSummaryAdapterError, match=reason):
            adapter.adapt_immutable_assessment_summary(
                adapter.build_immutable_assessment_summary_adapter_contract(),
                build_assessment_summary_consumer_contract(), bundle_files=files,
            )


def test_source_contract_and_hash_bundle_digest_drift_fail(monkeypatch):
    files, assessment_digest, bundle_digest = bundle_files(); files = dict(files)
    contract = json.loads(files["contract.json"]); contract["accepts_production_rows"] = True
    files["contract.json"] = canonical(contract)
    monkeypatch.setattr(adapter, "EXPECTED_ASSESSMENT_DIGEST", assessment_digest)
    monkeypatch.setattr(adapter, "EXPECTED_BUNDLE_DIGEST", bundle_digest)
    with pytest.raises(adapter.ImmutableAssessmentSummaryAdapterError, match="SOURCE_CONTRACT"):
        adapter.adapt_immutable_assessment_summary(
            adapter.build_immutable_assessment_summary_adapter_contract(),
            build_assessment_summary_consumer_contract(), bundle_files=files,
        )
    files, assessment_digest, bundle_digest = bundle_files(); files = dict(files)
    hashes = json.loads(files["hashes.json"]); hashes["bundle_digest"] = "0" * 64
    files["hashes.json"] = canonical(hashes)
    monkeypatch.setattr(adapter, "EXPECTED_ASSESSMENT_DIGEST", assessment_digest)
    monkeypatch.setattr(adapter, "EXPECTED_BUNDLE_DIGEST", bundle_digest)
    with pytest.raises(adapter.ImmutableAssessmentSummaryAdapterError, match="BUNDLE_DIGEST"):
        adapter.adapt_immutable_assessment_summary(
            adapter.build_immutable_assessment_summary_adapter_contract(),
            build_assessment_summary_consumer_contract(), bundle_files=files,
        )


def test_adapter_contract_bypass_and_projection_replay_tamper_fail(monkeypatch):
    files, assessment_digest, bundle_digest = bundle_files()
    monkeypatch.setattr(adapter, "EXPECTED_ASSESSMENT_DIGEST", assessment_digest)
    monkeypatch.setattr(adapter, "EXPECTED_BUNDLE_DIGEST", bundle_digest)
    contract = adapter.build_immutable_assessment_summary_adapter_contract()
    with pytest.raises(adapter.ImmutableAssessmentSummaryAdapterError, match="CONTRACT_REPLAY"):
        adapter.verify_immutable_assessment_summary_adapter_contract(replace(contract, grants_integration_authority=True))
    consumer = build_assessment_summary_consumer_contract()
    result = adapter.adapt_immutable_assessment_summary(contract, consumer, bundle_files=files)
    with pytest.raises(adapter.ImmutableAssessmentSummaryAdapterError, match="PROJECTION_REPLAY"):
        adapter.verify_immutable_assessment_summary_projection(
            contract, consumer, bundle_files=files,
            projection=replace(result, projection_digest="0" * 64),
        )


def test_input_order_independence(monkeypatch):
    first = assessment_document()
    first["membership"] = dict(reversed(list(first["membership"].items())))
    first["missingness"] = list(reversed(first["missingness"]))
    first["assessment_digest"] = digest({key: value for key, value in first.items() if key != "assessment_digest"})
    result_a = execute(monkeypatch, bundle_files(first))
    second = assessment_document()
    result_b = execute(monkeypatch, bundle_files(second))
    assert result_a.canonical_projection == result_b.canonical_projection


def test_fixture_entry_point_remains_fail_closed_for_production_provenance():
    value = {
        "schema_version": "psi0d-b.synthetic-summary.v1",
        "input_lineage": adapter._summary_lineage(),
        "fixture_only": False,
        "provenance_class": PRODUCTION_DERIVED_PROVENANCE_CLASS,
        "cohort_count": 2,
        "membership": {query_id: {key: val for key, val in member().items() if key != "unmatched_mints"} for query_id in QUERY_IDS},
        "unresolved_conflict_count": 0,
        "orphan_unmatched_count": 0,
        "reason_codes": ["PSI0C_B_ABSENCE_IS_NOT_NEGATIVE"],
        "authority": {"policy": False, "ranking": False, "integration": False, "activation": False},
    }
    with pytest.raises(AssessmentSummaryConsumerError, match="NON_FIXTURE_PROVENANCE"):
        project_fixture_assessment_summary(build_assessment_summary_consumer_contract(), value)


def test_adapter_operates_on_injected_bytes_without_file_database_or_network_io(monkeypatch):
    files, assessment_digest, bundle_digest = bundle_files()
    monkeypatch.setattr(adapter, "EXPECTED_ASSESSMENT_DIGEST", assessment_digest)
    monkeypatch.setattr(adapter, "EXPECTED_BUNDLE_DIGEST", bundle_digest)
    monkeypatch.setattr("builtins.open", lambda *a, **k: (_ for _ in ()).throw(AssertionError("file I/O")))
    monkeypatch.setattr("sqlite3.connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("database I/O")))
    monkeypatch.setattr("socket.create_connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network I/O")))
    result = adapter.adapt_immutable_assessment_summary(
        adapter.build_immutable_assessment_summary_adapter_contract(),
        build_assessment_summary_consumer_contract(), bundle_files=files,
    )
    assert result.canonical_projection
