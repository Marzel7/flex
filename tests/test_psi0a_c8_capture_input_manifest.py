from dataclasses import replace

import pytest

from src.evidence.contracts.production_shadow_capture_manifest import (
    HISTORICAL_BOUNDARY_DIGEST,
    HISTORICAL_SCHEMA_AUDIT_DIGEST,
    ProductionShadowCaptureManifestError,
    build_canonical_capture_input_manifest,
    verify_canonical_capture_input_manifest,
)


def _manifest():
    return build_canonical_capture_input_manifest(engineering_revision="bd0d9447")


def test_manifest_is_deterministic_replayable_and_non_authorizing():
    first = _manifest()
    second = _manifest()
    assert first == second
    assert verify_canonical_capture_input_manifest(first)
    assert first.historical_boundary_digest == HISTORICAL_BOUNDARY_DIGEST
    assert first.historical_schema_audit_digest == HISTORICAL_SCHEMA_AUDIT_DIGEST
    assert not first.grants_extraction_authority
    assert not first.grants_activation_authority


def test_manifest_binds_exact_five_surfaces_and_logical_filenames():
    manifest = _manifest()
    assert [(item.database_id, item.relation_name) for item in manifest.surfaces] == [
        ("creator", "creator_tokens"),
        ("evidence", "normalized_evidence_records"),
        ("main", "token_analysis"),
        ("main", "token_price_snapshots"),
        ("ops", "wt_watchtower_launches"),
    ]
    assert {item.expected_filename for item in manifest.sources} == {
        "pumpswap_tokens.db", "evidence.db", "flex_complete_database.db", "wt_ops_v2.db"
    }
    assert all("/" not in item.expected_filename for item in manifest.sources)


def test_all_surfaces_use_explicit_superseding_rowid_only_policy():
    manifest = _manifest()
    assert manifest.supersedes_uncommitted_prior_tuple
    assert manifest.cursor_policy == "STABLE_INCLUSIVE_ROWID_ONLY_NO_EVENT_TIME_HIGH_WATER"
    assert len(manifest.high_water_specs) == 5
    assert all(item.cursor_column == "rowid" for item in manifest.high_water_specs)
    assert all(item.event_column is None for item in manifest.high_water_specs)


def test_requirements_bind_query_columns_affinities_and_index_prefixes():
    manifest = _manifest()
    requirements = {
        (item.database_id, item.relation_name): item for item in manifest.requirements
    }
    assert requirements[("creator", "creator_tokens")].required_index_prefixes == (("mint",),)
    assert requirements[("evidence", "normalized_evidence_records")].required_index_prefixes == (("fact_family",),)
    assert requirements[("main", "token_analysis")].required_index_prefixes == (
        ("mint",), ("migrated_at", "mint")
    )
    assert requirements[("main", "token_price_snapshots")].required_index_prefixes == (
        ("mint", "captured_at"),
    )
    assert requirements[("ops", "wt_watchtower_launches")].required_index_prefixes == (("mint",),)
    assert dict(requirements[("main", "token_analysis")].required_columns)["first_observed_at"] == "INTEGER"


@pytest.mark.parametrize("revision", ["", "not-a-revision", "A" * 40, None])
def test_invalid_engineering_revision_fails_closed(revision):
    with pytest.raises(ProductionShadowCaptureManifestError, match="INVALID_ENGINEERING_REVISION"):
        build_canonical_capture_input_manifest(engineering_revision=revision)


def test_digest_or_authority_change_fails_exact_replay():
    manifest = _manifest()
    with pytest.raises(ProductionShadowCaptureManifestError, match="REPLAY_MISMATCH"):
        verify_canonical_capture_input_manifest(
            replace(manifest, manifest_digest="0" * 64)
        )
    with pytest.raises(ProductionShadowCaptureManifestError):
        verify_canonical_capture_input_manifest(
            replace(manifest, grants_extraction_authority=True)
        )
