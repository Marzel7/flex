"""PSI0B-A immutable run-specific shadow extraction preflight."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Mapping, Tuple


PREFLIGHT_VERSION = "psi0b-a.v1"
COHORT_VERSION = "psi0b-a.cohort.v1"
AUTHORITY_CLASS = "NON_EXECUTING_PRODUCTION_SHADOW_RUN_PREFLIGHT"
ENGINEERING_REVISION = "04ac82cef40e0c7dbe8e6a26070ba6bf78719c6f"
PSI0A_H_BUNDLE_DIGEST = "308e1d15b2ca9af74c6f1c6d57455150b5f7efdc64e36b06b4ff4f82ee1bb860"
PSI0A_G_DIGEST = "c9ff90b6cbfb332bc00691c87528abf5f6b6158b54072bfb48c60d9f920e5bab"
PSI0A_F_DIGEST = "8c92231a76c9daad4305bd3859760bc6f1d1ef31249b255b471c213f1ce1c3bf"
PSI0A_E_DIGEST = "f5eea8b9f8ba6b102f57e4ae59eb35eb8f0e23d3f8ac0f493f35671f8271f736"
PSI0A_D_DIGEST = "38d0605e77e1503e9d5e952d13a3e1501aacf6c84db7a3debc334bca8fc484ce"
READ_BOUNDARY_DIGEST = "fdf11dc5e29c176d3724a4ccd1e3ff56584727512853bfb58a71fb3979c246f8"
PATH_BINDINGS = {
    "creator": ("pumpswap_tokens.db", "e0f28444336d5b744dbeedf0474b33a9bee1f0c7877477bc069db8b35c12ee4c"),
    "evidence": ("evidence.db", "26d0e3847ca99f7a2b6216156adca59e14e40b8d7054d3c33709460a6286aeb7"),
    "main": ("flex_complete_database.db", "47aecc6ad649ba5ec1bbfcba62df30cfc04d0dc97b632df1ce0c153e3612b934"),
    "ops": ("wt_ops_v2.db", "13dca5ea3bcc50b135dbbbab61042930ac692cce74a3388705a35e924a0a11a6"),
}
QUERY_BOUNDARIES = {
    "creator_selected_cohort": 53_700,
    "evidence_launch_facts": 0,
    "main_selected_cohort": 1_774_740,
    "ops_selected_cohort": 178,
    "snapshot_selected_cohort": 32_626_118,
}
MAXIMUM_ROWS_PER_QUERY = 5_000
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ProductionShadowRunPreflightError(RuntimeError):
    """Named fail-closed PSI0B-A preflight violation."""


@dataclass(frozen=True)
class ImmutableCohortArtifact:
    cohort_version: str
    cohort_id: str
    mints: Tuple[str, ...]
    source_artifact_digest: str
    authority_class: str
    cohort_digest: str


@dataclass(frozen=True)
class SourcePathBinding:
    logical_source: str
    expected_filename: str
    path_binding_digest: str


@dataclass(frozen=True)
class QueryRunParameter:
    query_id: str
    rowid_upper_inclusive: int
    row_limit: int
    cohort_digest: str | None
    fact_family: str | None


@dataclass(frozen=True)
class ProductionShadowRunPreflight:
    preflight_version: str
    run_id: str
    engineering_revision: str
    psi0a_h_bundle_digest: str
    psi0a_g_digest: str
    psi0a_f_digest: str
    psi0a_e_digest: str
    psi0a_d_digest: str
    read_boundary_digest: str
    cohort: ImmutableCohortArtifact
    source_bindings: Tuple[SourcePathBinding, ...]
    query_parameters: Tuple[QueryRunParameter, ...]
    output_directory_fingerprint: str
    health_checkpoint_placeholders: Tuple[str, ...]
    deterministic_accounting_fields: Tuple[str, ...]
    retry_allowed: bool
    pagination_allowed: bool
    failover_allowed: bool
    widening_allowed: bool
    grants_extraction_authority: bool
    grants_integration_authority: bool
    grants_activation_authority: bool
    preflight_digest: str


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def production_shadow_run_preflight_contract_digest() -> str:
    """Return the run-independent identity of the frozen PSI0B-A schema."""
    return _digest({
        "preflight_version": PREFLIGHT_VERSION,
        "cohort_version": COHORT_VERSION,
        "authority_class": AUTHORITY_CLASS,
        "engineering_revision": ENGINEERING_REVISION,
        "upstream_digests": (
            PSI0A_H_BUNDLE_DIGEST, PSI0A_G_DIGEST, PSI0A_F_DIGEST,
            PSI0A_E_DIGEST, PSI0A_D_DIGEST, READ_BOUNDARY_DIGEST,
        ),
        "path_bindings": PATH_BINDINGS,
        "query_boundaries": QUERY_BOUNDARIES,
        "maximum_rows_per_query": MAXIMUM_ROWS_PER_QUERY,
        "health_checkpoint_count": 3,
        "authority_grants": (False, False, False),
        "retry_pagination_failover_widening": (False, False, False, False),
    })


def build_immutable_cohort_artifact(
    *, cohort_id: str, mints: Tuple[str, ...], source_artifact_digest: str,
) -> ImmutableCohortArtifact:
    if not cohort_id or not _DIGEST.fullmatch(source_artifact_digest):
        raise ProductionShadowRunPreflightError("PSI0B_A_INVALID_COHORT_IDENTITY")
    if not 0 < len(mints) <= MAXIMUM_ROWS_PER_QUERY or len(set(mints)) != len(mints):
        raise ProductionShadowRunPreflightError("PSI0B_A_INVALID_COHORT_MEMBERSHIP")
    if any(not isinstance(mint, str) or not mint.strip() for mint in mints):
        raise ProductionShadowRunPreflightError("PSI0B_A_INVALID_COHORT_MEMBER")
    body = {
        "cohort_version": COHORT_VERSION,
        "cohort_id": cohort_id,
        "mints": mints,
        "source_artifact_digest": source_artifact_digest,
        "authority_class": "CALLER_SUPPLIED_IMMUTABLE_COHORT",
    }
    return ImmutableCohortArtifact(**body, cohort_digest=_digest(body))


def verify_immutable_cohort_artifact(cohort: ImmutableCohortArtifact) -> bool:
    expected = build_immutable_cohort_artifact(
        cohort_id=cohort.cohort_id, mints=cohort.mints,
        source_artifact_digest=cohort.source_artifact_digest,
    )
    if cohort != expected:
        raise ProductionShadowRunPreflightError("PSI0B_A_COHORT_REPLAY_MISMATCH")
    return True


def canonical_source_bindings() -> Tuple[SourcePathBinding, ...]:
    return tuple(SourcePathBinding(source, filename, digest) for source, (filename, digest) in sorted(PATH_BINDINGS.items()))


def canonical_query_parameters(
    cohort: ImmutableCohortArtifact, *, fact_family: str,
) -> Tuple[QueryRunParameter, ...]:
    if not isinstance(fact_family, str) or not fact_family.strip():
        raise ProductionShadowRunPreflightError("PSI0B_A_INVALID_FACT_FAMILY")
    rows = []
    for query_id, boundary in sorted(QUERY_BOUNDARIES.items()):
        evidence = query_id == "evidence_launch_facts"
        rows.append(QueryRunParameter(
            query_id=query_id,
            rowid_upper_inclusive=boundary,
            row_limit=MAXIMUM_ROWS_PER_QUERY,
            cohort_digest=None if evidence else cohort.cohort_digest,
            fact_family=fact_family if evidence else None,
        ))
    return tuple(rows)


def build_production_shadow_run_preflight(
    *, run_id: str, cohort: ImmutableCohortArtifact, fact_family: str,
    output_directory: Path,
) -> ProductionShadowRunPreflight:
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise ProductionShadowRunPreflightError("PSI0B_A_INVALID_RUN_ID")
    verify_immutable_cohort_artifact(cohort)
    output = Path(output_directory)
    if output.exists():
        raise ProductionShadowRunPreflightError("PSI0B_A_OUTPUT_NOT_NEW")
    output_fingerprint = _digest({"resolved_output_directory": str(output.resolve()), "run_id": run_id})
    values = {
        "preflight_version": PREFLIGHT_VERSION,
        "run_id": run_id,
        "engineering_revision": ENGINEERING_REVISION,
        "psi0a_h_bundle_digest": PSI0A_H_BUNDLE_DIGEST,
        "psi0a_g_digest": PSI0A_G_DIGEST,
        "psi0a_f_digest": PSI0A_F_DIGEST,
        "psi0a_e_digest": PSI0A_E_DIGEST,
        "psi0a_d_digest": PSI0A_D_DIGEST,
        "read_boundary_digest": READ_BOUNDARY_DIGEST,
        "cohort": cohort,
        "source_bindings": canonical_source_bindings(),
        "query_parameters": canonical_query_parameters(cohort, fact_family=fact_family),
        "output_directory_fingerprint": output_fingerprint,
        "health_checkpoint_placeholders": (
            "PRESTART_CHECKPOINT_1_REQUIRED_AT_EXECUTION",
            "PRESTART_CHECKPOINT_2_REQUIRED_AT_EXECUTION",
            "PRESTART_CHECKPOINT_3_REQUIRED_AT_EXECUTION",
        ),
        "deterministic_accounting_fields": (
            "selected_rows", "excluded_rows", "canonical_output_bytes",
            "query_seconds", "transaction_seconds", "temporary_bytes",
            "connections_opened", "accounting_residual",
        ),
        "retry_allowed": False,
        "pagination_allowed": False,
        "failover_allowed": False,
        "widening_allowed": False,
        "grants_extraction_authority": False,
        "grants_integration_authority": False,
        "grants_activation_authority": False,
    }
    body = asdict(ProductionShadowRunPreflight(**values, preflight_digest=""))
    body.pop("preflight_digest")
    preflight = ProductionShadowRunPreflight(**values, preflight_digest=_digest(body))
    verify_production_shadow_run_preflight(preflight)
    return preflight


def verify_production_shadow_run_preflight(preflight: ProductionShadowRunPreflight) -> bool:
    identities = (
        preflight.psi0a_h_bundle_digest, preflight.psi0a_g_digest,
        preflight.psi0a_f_digest, preflight.psi0a_e_digest,
        preflight.psi0a_d_digest, preflight.read_boundary_digest,
        preflight.output_directory_fingerprint, preflight.preflight_digest,
    )
    if any(not _DIGEST.fullmatch(value) for value in identities):
        raise ProductionShadowRunPreflightError("PSI0B_A_INVALID_IDENTITY")
    expected_identities = (
        PSI0A_H_BUNDLE_DIGEST, PSI0A_G_DIGEST, PSI0A_F_DIGEST,
        PSI0A_E_DIGEST, PSI0A_D_DIGEST, READ_BOUNDARY_DIGEST,
    )
    if identities[:6] != expected_identities:
        raise ProductionShadowRunPreflightError("PSI0B_A_UPSTREAM_LINEAGE_DRIFT")
    verify_immutable_cohort_artifact(preflight.cohort)
    if preflight.source_bindings != canonical_source_bindings():
        raise ProductionShadowRunPreflightError("PSI0B_A_PATH_BINDING_DRIFT")
    if not preflight.query_parameters:
        raise ProductionShadowRunPreflightError("PSI0B_A_QUERY_OR_BOUNDARY_DRIFT")
    expected_queries = canonical_query_parameters(
        preflight.cohort,
        fact_family=next((item.fact_family for item in preflight.query_parameters if item.query_id == "evidence_launch_facts"), ""),
    )
    if preflight.query_parameters != expected_queries:
        raise ProductionShadowRunPreflightError("PSI0B_A_QUERY_OR_BOUNDARY_DRIFT")
    if len(preflight.health_checkpoint_placeholders) != 3 or any("REQUIRED_AT_EXECUTION" not in item for item in preflight.health_checkpoint_placeholders):
        raise ProductionShadowRunPreflightError("PSI0B_A_HEALTH_PLACEHOLDER_DRIFT")
    if any((preflight.retry_allowed, preflight.pagination_allowed, preflight.failover_allowed,
            preflight.widening_allowed, preflight.grants_extraction_authority,
            preflight.grants_integration_authority, preflight.grants_activation_authority)):
        raise ProductionShadowRunPreflightError("PSI0B_A_AUTHORITY_OR_WIDENING_DRIFT")
    body = asdict(preflight)
    digest = body.pop("preflight_digest")
    if digest != _digest(body):
        raise ProductionShadowRunPreflightError("PSI0B_A_PREFLIGHT_REPLAY_MISMATCH")
    return True
