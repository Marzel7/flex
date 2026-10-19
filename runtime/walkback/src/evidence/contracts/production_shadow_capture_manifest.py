"""PSI0A-C8 immutable canonical inputs for production-shadow capture."""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re
from typing import Tuple

from .production_shadow_boundary import ProductionSurface
from .production_shadow_high_water import HighWaterSpec
from .production_shadow_schema_audit import RequiredRelation


MANIFEST_VERSION = "psi0a-c8.v1"
HISTORICAL_BOUNDARY_ENGINEERING_REVISION = "491aa4ce"
HISTORICAL_BOUNDARY_DIGEST = "6b0005bfcd063ba38d2eefe33557e334a5b90e3f83474a7d4db3a0033f4cabfb"
HISTORICAL_SCHEMA_AUDIT_DIGEST = "3da006925e29dcaf15fdc3094502521d466390e44f224b86041024b6248c392b"
REVISION = re.compile(r"^[0-9a-f]{7,64}$")


class ProductionShadowCaptureManifestError(ValueError):
    pass


@dataclass(frozen=True)
class CaptureSource:
    database_id: str
    expected_filename: str


@dataclass(frozen=True)
class ProductionShadowCaptureInputManifest:
    manifest_version: str
    engineering_revision: str
    supersedes_uncommitted_prior_tuple: bool
    cursor_policy: str
    historical_boundary_engineering_revision: str
    historical_boundary_digest: str
    historical_schema_audit_digest: str
    sources: Tuple[CaptureSource, ...]
    surfaces: Tuple[ProductionSurface, ...]
    requirements: Tuple[RequiredRelation, ...]
    high_water_specs: Tuple[HighWaterSpec, ...]
    grants_extraction_authority: bool
    grants_activation_authority: bool
    manifest_digest: str


def _digest(value: object) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def build_canonical_capture_input_manifest(
    *, engineering_revision: str
) -> ProductionShadowCaptureInputManifest:
    if not isinstance(engineering_revision, str) or not REVISION.fullmatch(engineering_revision):
        raise ProductionShadowCaptureManifestError("PSI0A_C8_INVALID_ENGINEERING_REVISION")

    sources = tuple(sorted((
        CaptureSource("creator", "pumpswap_tokens.db"),
        CaptureSource("evidence", "evidence.db"),
        CaptureSource("main", "flex_complete_database.db"),
        CaptureSource("ops", "wt_ops_v2.db"),
    ), key=lambda item: item.database_id))
    surfaces = tuple(sorted((
        ProductionSurface("creator", "creator_tokens", "TABLE"),
        ProductionSurface("evidence", "normalized_evidence_records", "TABLE"),
        ProductionSurface("main", "token_analysis", "TABLE"),
        ProductionSurface("main", "token_price_snapshots", "TABLE"),
        ProductionSurface("ops", "wt_watchtower_launches", "TABLE"),
    ), key=lambda item: (item.database_id, item.relation_name)))
    requirements = tuple(sorted((
        RequiredRelation(
            "creator", "creator_tokens", "TABLE",
            (("creator_address", "TEXT"), ("mint", "TEXT"), ("created_at", "INTEGER")),
            (("mint",),),
        ),
        RequiredRelation(
            "evidence", "normalized_evidence_records", "TABLE",
            (
                ("fact_family", "TEXT"), ("payload_json", "TEXT"),
                ("raw_artifact_digest", "TEXT"), ("acquired_at", "INTEGER"),
                ("source_id", "TEXT"), ("source_version", "TEXT"),
                ("verification_state", "TEXT"),
            ),
            (("fact_family",),),
        ),
        RequiredRelation(
            "main", "token_analysis", "TABLE",
            (
                ("mint", "TEXT"), ("migrated_at", "INTEGER"),
                ("first_observed_mc", "REAL"), ("first_observed_price", "REAL"),
                ("first_observed_at", "INTEGER"), ("first_observed_source", "TEXT"),
                ("first_observed_confidence", "REAL"), ("pf_ws_creator", "TEXT"),
                ("creator_mismatch", "INTEGER"),
            ),
            (("mint",), ("migrated_at", "mint")),
        ),
        RequiredRelation(
            "main", "token_price_snapshots", "TABLE",
            (
                ("snapshot_id", "INTEGER"), ("mint", "TEXT"),
                ("price_usd", "REAL"), ("market_cap", "REAL"),
                ("source", "TEXT"), ("captured_at", "INTEGER"),
                ("created_at", "INTEGER"),
            ),
            (("mint", "captured_at"),),
        ),
        RequiredRelation(
            "ops", "wt_watchtower_launches", "TABLE",
            (
                ("mint", "TEXT"), ("creator_wallet", "TEXT"),
                ("create_signature", "TEXT"), ("create_time", "INTEGER"),
                ("create_slot", "INTEGER"), ("creator_extraction_method", "TEXT"),
                ("confidence", "TEXT"), ("recorded_at", "INTEGER"),
            ),
            (("mint",),),
        ),
    ), key=lambda item: (item.database_id, item.relation_name)))
    high_water_specs = tuple(
        HighWaterSpec(item.database_id, item.relation_name, "rowid", None)
        for item in surfaces
    )
    body = {
        "manifest_version": MANIFEST_VERSION,
        "engineering_revision": engineering_revision,
        "supersedes_uncommitted_prior_tuple": True,
        "cursor_policy": "STABLE_INCLUSIVE_ROWID_ONLY_NO_EVENT_TIME_HIGH_WATER",
        "historical_boundary_engineering_revision": HISTORICAL_BOUNDARY_ENGINEERING_REVISION,
        "historical_boundary_digest": HISTORICAL_BOUNDARY_DIGEST,
        "historical_schema_audit_digest": HISTORICAL_SCHEMA_AUDIT_DIGEST,
        "sources": [asdict(item) for item in sources],
        "surfaces": [asdict(item) for item in surfaces],
        "requirements": [asdict(item) for item in requirements],
        "high_water_specs": [asdict(item) for item in high_water_specs],
        "grants_extraction_authority": False,
        "grants_activation_authority": False,
    }
    return ProductionShadowCaptureInputManifest(
        manifest_version=MANIFEST_VERSION,
        engineering_revision=engineering_revision,
        supersedes_uncommitted_prior_tuple=True,
        cursor_policy="STABLE_INCLUSIVE_ROWID_ONLY_NO_EVENT_TIME_HIGH_WATER",
        historical_boundary_engineering_revision=HISTORICAL_BOUNDARY_ENGINEERING_REVISION,
        historical_boundary_digest=HISTORICAL_BOUNDARY_DIGEST,
        historical_schema_audit_digest=HISTORICAL_SCHEMA_AUDIT_DIGEST,
        sources=sources,
        surfaces=surfaces,
        requirements=requirements,
        high_water_specs=high_water_specs,
        grants_extraction_authority=False,
        grants_activation_authority=False,
        manifest_digest=_digest(body),
    )


def verify_canonical_capture_input_manifest(
    manifest: ProductionShadowCaptureInputManifest,
) -> bool:
    expected = build_canonical_capture_input_manifest(
        engineering_revision=manifest.engineering_revision
    )
    if manifest != expected:
        raise ProductionShadowCaptureManifestError("PSI0A_C8_MANIFEST_REPLAY_MISMATCH")
    if manifest.grants_extraction_authority or manifest.grants_activation_authority:
        raise ProductionShadowCaptureManifestError("PSI0A_C8_AUTHORITY_EXPANSION")
    return True
