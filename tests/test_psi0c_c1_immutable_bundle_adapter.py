import json
from hashlib import sha256
import sqlite3

import pytest

from src.evidence.contracts import production_shadow_assessment_bundle_adapter as adapter
from src.evidence.contracts.production_shadow_assessment import (
    ProductionShadowAssessmentError,
    build_shadow_assessment_contract,
    verify_fixture_shadow_assessment,
)
from src.evidence.contracts.production_shadow_production_binding import ADAPTER_VERSION, AUTHORITY_CLASS


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def row(query, mint="mint-a", rowid=1, creator="creator-a"):
    if query == "creator_selected_cohort":
        return {"rowid": rowid, "creator_address": creator, "mint": mint, "created_at": 1}
    if query == "evidence_launch_facts":
        return {"rowid": rowid, "fact_family": "LaunchFact", "payload_json": json.dumps({"mint": mint, "creator": creator}), "raw_artifact_digest": "a" * 64, "acquired_at": 1, "source_id": "fixture", "source_version": "1", "verification_state": "VERIFIED"}
    if query == "main_selected_cohort":
        return {"rowid": rowid, "mint": mint, "migrated_at": 1, "first_observed_mc": 1.0, "first_observed_price": 1.0, "first_observed_at": 1, "first_observed_source": "fixture", "first_observed_confidence": 1.0, "pf_ws_creator": creator, "creator_mismatch": 0}
    if query == "ops_selected_cohort":
        return {"rowid": rowid, "mint": mint, "creator_wallet": creator, "create_signature": "sig", "create_time": 1, "create_slot": 1, "creator_extraction_method": "fixture", "confidence": "HIGH", "recorded_at": 1}
    return {"rowid": rowid, "snapshot_id": rowid, "mint": mint, "price_usd": 1.0, "market_cap": 1.0, "source": "fixture", "captured_at": 1, "created_at": 1}


def bundle_documents(results=None, **run_changes):
    results = results or {query: [row(query)] for query in adapter.QUERY_IDS}
    queries = {
        query: {
            "selected_rows": len(rows), "excluded_rows": 0,
            "canonical_output_bytes": len(canonical(rows)), "query_seconds": 0.1,
            "transaction_seconds": 0.1, "temporary_bytes": 0,
        } for query, rows in results.items()
    }
    accounting = {
        "queries": queries,
        "total_rows": sum(item["selected_rows"] for item in queries.values()),
        "total_canonical_output_bytes": sum(item["canonical_output_bytes"] for item in queries.values()),
        "connections_opened": 5, "maximum_concurrent_connections": 1,
        "process_rss_delta_bytes": 1, "sqlite_temporary_bytes": 0,
        "accounting_residual": 0,
    }
    run = {
        "runner_version": ADAPTER_VERSION, "run_id": "fixture-run",
        "preflight_digest": adapter.EXPECTED_PREFLIGHT_DIGEST,
        "authority_class": AUTHORITY_CLASS, "fixture_only": False,
        "execution_authorization_digest": "b" * 64,
        "grants_production_execution_authority": True,
        "grants_integration_authority": False, "grants_activation_authority": False,
    }
    run.update(run_changes)
    documents = {"run.json": run, "accounting.json": accounting, "results.json": results}
    payloads = {name: canonical(value) for name, value in documents.items()}
    digests = {name: sha256(value).hexdigest() for name, value in payloads.items()}
    bundle_digest = sha256(canonical(digests)).hexdigest()
    payloads["hashes.json"] = canonical({"runner_version": ADAPTER_VERSION, "files": digests, "bundle_digest": bundle_digest})
    return payloads, bundle_digest


def execute(tmp_path, monkeypatch, files=None, name="assessment"):
    files, digest = bundle_documents() if files is None else files
    if isinstance(files, tuple):
        files, digest = files
    monkeypatch.setattr(adapter, "EXPECTED_BUNDLE_DIGEST", digest)
    contract = adapter.build_immutable_bundle_adapter_contract()
    return adapter.assess_immutable_bundle_representation(
        contract, build_shadow_assessment_contract(), bundle_files=files,
        cohort_mints=("mint-a", "mint-b"), output_directory=tmp_path / name,
    )


def test_valid_bundle_preserves_production_derived_provenance(tmp_path, monkeypatch):
    files, digest = bundle_documents()
    bundle = execute(tmp_path, monkeypatch, (files, digest))
    assert verify_fixture_shadow_assessment(bundle.output_directory) == bundle
    assessment = json.loads((bundle.output_directory / "assessment.json").read_text())
    assert assessment["fixture_only"] is False
    assert assessment["provenance_class"] == adapter.PROVENANCE_CLASS
    assert not any(assessment["authority"].values())


def _physical_alias_rows(tmp_path):
    database = sqlite3.connect(tmp_path / "aliases.sqlite")
    database.row_factory = sqlite3.Row
    database.execute("CREATE TABLE ops(id INTEGER PRIMARY KEY,mint TEXT,creator_wallet TEXT,create_signature TEXT,create_time INTEGER,create_slot INTEGER,creator_extraction_method TEXT,confidence TEXT,recorded_at INTEGER)")
    database.execute("INSERT INTO ops VALUES(1,'mint-a','creator-a','sig',1,1,'fixture','HIGH',1)")
    database.execute("CREATE TABLE snapshots(snapshot_id INTEGER PRIMARY KEY,mint TEXT,price_usd REAL,market_cap REAL,source TEXT,captured_at INTEGER,created_at INTEGER)")
    database.execute("INSERT INTO snapshots VALUES(1,'mint-a',1,1,'fixture',1,1)")
    ops = [dict(row) for row in database.execute("SELECT rowid,mint,creator_wallet,create_signature,create_time,create_slot,creator_extraction_method,confidence,recorded_at FROM ops")]
    snapshots = [dict(row) for row in database.execute("SELECT rowid,snapshot_id,mint,price_usd,market_cap,source,captured_at,created_at FROM snapshots")]
    database.close()
    return ops, snapshots


def test_sqlite_rowid_alias_variants_normalize_to_logical_schema(tmp_path, monkeypatch):
    ops, snapshots = _physical_alias_rows(tmp_path)
    assert set(ops[0]) == (set(adapter.SCHEMAS["ops_selected_cohort"]) - {"rowid"}) | {"id"}
    assert set(snapshots[0]) == set(adapter.SCHEMAS["snapshot_selected_cohort"]) - {"rowid"}
    results = {query: [row(query)] for query in adapter.QUERY_IDS}
    results["ops_selected_cohort"] = ops
    results["snapshot_selected_cohort"] = snapshots
    files, digest = bundle_documents(results)
    bundle = execute(tmp_path, monkeypatch, (files, digest), name="aliases")
    assessment = json.loads((bundle.output_directory / "assessment.json").read_text())
    assert assessment["membership"]["ops_selected_cohort"]["row_count"] == 1
    assert assessment["membership"]["snapshot_selected_cohort"]["row_count"] == 1
    assert verify_fixture_shadow_assessment(bundle.output_directory) == bundle


@pytest.mark.parametrize("query,mutation,reason", (
    ("ops_selected_cohort", "both", "CONFLICTING_ROW_IDENTITIES"),
    ("ops_selected_cohort", "missing", "UNKNOWN_SERIALIZED_SCHEMA_VARIANT"),
    ("ops_selected_cohort", "extra", "UNKNOWN_SERIALIZED_SCHEMA_VARIANT"),
    ("ops_selected_cohort", "type", "INVALID_PHYSICAL_ROW_IDENTITY"),
    ("snapshot_selected_cohort", "missing", "UNKNOWN_SERIALIZED_SCHEMA_VARIANT"),
    ("snapshot_selected_cohort", "extra", "UNKNOWN_SERIALIZED_SCHEMA_VARIANT"),
    ("snapshot_selected_cohort", "type", "INVALID_PHYSICAL_ROW_IDENTITY"),
))
def test_alias_variant_faults_fail_closed(tmp_path, monkeypatch, query, mutation, reason):
    ops, snapshots = _physical_alias_rows(tmp_path)
    results = {item: [row(item)] for item in adapter.QUERY_IDS}
    results["ops_selected_cohort"] = ops
    results["snapshot_selected_cohort"] = snapshots
    target = results[query][0]
    identity = "id" if query == "ops_selected_cohort" else "snapshot_id"
    if mutation == "both":
        target["rowid"] = target[identity]
    elif mutation == "missing":
        target.pop(identity)
    elif mutation == "extra":
        target["unexpected"] = None
    else:
        target[identity] = "not-an-integer"
    files, digest = bundle_documents(results)
    monkeypatch.setattr(adapter, "EXPECTED_BUNDLE_DIGEST", digest)
    with pytest.raises(adapter.ImmutableShadowBundleAdapterError, match=reason):
        adapter.assess_immutable_bundle_representation(
            adapter.build_immutable_bundle_adapter_contract(), build_shadow_assessment_contract(),
            bundle_files=files, cohort_mints=("mint-a",), output_directory=tmp_path / f"{query}-{mutation}",
        )


def test_alias_normalization_is_input_order_independent(tmp_path, monkeypatch):
    ops, snapshots = _physical_alias_rows(tmp_path)
    ops.append({**ops[0], "id": 2})
    first = {query: [row(query)] for query in adapter.QUERY_IDS}
    first["ops_selected_cohort"] = ops
    first["snapshot_selected_cohort"] = snapshots
    second = {query: list(reversed(rows)) for query, rows in first.items()}
    files_a, digest_a = bundle_documents(first)
    bundle_a = execute(tmp_path, monkeypatch, (files_a, digest_a), name="ordered-a")
    files_b, digest_b = bundle_documents(second)
    bundle_b = execute(tmp_path, monkeypatch, (files_b, digest_b), name="ordered-b")
    assert json.loads((bundle_a.output_directory / "assessment.json").read_text()) == json.loads((bundle_b.output_directory / "assessment.json").read_text())


@pytest.mark.parametrize("mutation", ("missing", "extra", "altered", "noncanonical"))
def test_file_and_byte_faults_fail_without_publication(tmp_path, monkeypatch, mutation):
    files, digest = bundle_documents(); files = dict(files)
    if mutation == "missing":
        files.pop("run.json")
    elif mutation == "extra":
        files["extra.json"] = b"{}\n"
    elif mutation == "altered":
        files["results.json"] = canonical({})
    else:
        files["run.json"] = files["run.json"].rstrip(b"\n")
    monkeypatch.setattr(adapter, "EXPECTED_BUNDLE_DIGEST", digest)
    with pytest.raises(adapter.ImmutableShadowBundleAdapterError):
        adapter.assess_immutable_bundle_representation(
            adapter.build_immutable_bundle_adapter_contract(), build_shadow_assessment_contract(),
            bundle_files=files, cohort_mints=("mint-a",), output_directory=tmp_path / mutation,
        )
    assert not (tmp_path / mutation).exists()


@pytest.mark.parametrize("field,value", (
    ("preflight_digest", "0" * 64),
    ("authority_class", "WRONG"),
    ("fixture_only", True),
    ("grants_integration_authority", True),
    ("grants_activation_authority", True),
))
def test_lineage_and_authority_drift_fail(tmp_path, monkeypatch, field, value):
    files, digest = bundle_documents(**{field: value})
    monkeypatch.setattr(adapter, "EXPECTED_BUNDLE_DIGEST", digest)
    with pytest.raises(adapter.ImmutableShadowBundleAdapterError, match="LINEAGE_OR_AUTHORITY"):
        adapter.assess_immutable_bundle_representation(
            adapter.build_immutable_bundle_adapter_contract(), build_shadow_assessment_contract(),
            bundle_files=files, cohort_mints=("mint-a",), output_directory=tmp_path / field,
        )


def test_query_accounting_and_ceiling_drift_fail(tmp_path, monkeypatch):
    results = {query: [row(query)] for query in adapter.QUERY_IDS}
    results.pop("ops_selected_cohort")
    files, digest = bundle_documents(results)
    monkeypatch.setattr(adapter, "EXPECTED_BUNDLE_DIGEST", digest)
    with pytest.raises(adapter.ImmutableShadowBundleAdapterError, match="QUERY_IDENTITY"):
        adapter.assess_immutable_bundle_representation(
            adapter.build_immutable_bundle_adapter_contract(), build_shadow_assessment_contract(),
            bundle_files=files, cohort_mints=("mint-a",), output_directory=tmp_path / "query",
        )
    results = {query: [row(query)] for query in adapter.QUERY_IDS}
    results["creator_selected_cohort"] = [row("creator_selected_cohort", rowid=i + 1) for i in range(5001)]
    files, digest = bundle_documents(results)
    monkeypatch.setattr(adapter, "EXPECTED_BUNDLE_DIGEST", digest)
    with pytest.raises(adapter.ImmutableShadowBundleAdapterError, match="CEILING"):
        adapter.assess_immutable_bundle_representation(
            adapter.build_immutable_bundle_adapter_contract(), build_shadow_assessment_contract(),
            bundle_files=files, cohort_mints=("mint-a",), output_directory=tmp_path / "ceiling",
        )


def test_malformed_payload_output_reuse_adapter_and_replay_tamper(tmp_path, monkeypatch):
    results = {query: [row(query)] for query in adapter.QUERY_IDS}
    results["evidence_launch_facts"][0]["payload_json"] = "bad-json"
    files, digest = bundle_documents(results)
    monkeypatch.setattr(adapter, "EXPECTED_BUNDLE_DIGEST", digest)
    with pytest.raises(ProductionShadowAssessmentError, match="MALFORMED_EVIDENCE"):
        adapter.assess_immutable_bundle_representation(
            adapter.build_immutable_bundle_adapter_contract(), build_shadow_assessment_contract(),
            bundle_files=files, cohort_mints=("mint-a",), output_directory=tmp_path / "malformed",
        )
    files, digest = bundle_documents(); target = tmp_path / "used"; target.mkdir()
    monkeypatch.setattr(adapter, "EXPECTED_BUNDLE_DIGEST", digest)
    with pytest.raises(ProductionShadowAssessmentError, match="OUTPUT_NOT_NEW"):
        adapter.assess_immutable_bundle_representation(
            adapter.build_immutable_bundle_adapter_contract(), build_shadow_assessment_contract(),
            bundle_files=files, cohort_mints=("mint-a",), output_directory=target,
        )
    contract = adapter.build_immutable_bundle_adapter_contract()
    tampered = type(contract)(**{**contract.__dict__, "grants_integration_authority": True})
    with pytest.raises(adapter.ImmutableShadowBundleAdapterError, match="CONTRACT_REPLAY"):
        adapter.verify_immutable_bundle_adapter_contract(tampered)
    bundle = execute(tmp_path, monkeypatch, (files, digest), name="replay")
    (bundle.output_directory / "assessment.json").write_text("{}\n")
    with pytest.raises(ProductionShadowAssessmentError, match="DIGEST"):
        verify_fixture_shadow_assessment(bundle.output_directory)


def test_fixture_entry_point_still_rejects_production_marker(tmp_path):
    from src.evidence.contracts.production_shadow_assessment import assess_fixture_shadow
    with pytest.raises(ProductionShadowAssessmentError, match="PRODUCTION_ROWS_PROHIBITED"):
        assess_fixture_shadow(
            build_shadow_assessment_contract(), cohort_mints=("mint-a",),
            synthetic_results={query: [row(query)] for query in adapter.QUERY_IDS},
            output_directory=tmp_path / "bypass",
            input_lineage={
                "psi0c_a_digest": adapter.PSI0C_A_DIGEST,
                "psi0b_g_digest": adapter.PSI0B_G_DIGEST,
                "psi0b_bundle_identity_digest": adapter.PSI0B_BUNDLE_DIGEST,
            },
            fixture_only=False,
        )
