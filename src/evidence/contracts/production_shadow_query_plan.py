"""PSI0A-D deterministic, non-executing production-shadow query plans."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Callable, Mapping, Tuple
from urllib.parse import quote


CONTRACT_VERSION = "psi0a-d.v1"
AUTHORITY_CLASS = "NON_EXECUTING_PRODUCTION_SHADOW_QUERY_PLAN"
_REVISION = re.compile(r"^[0-9a-f]{7,64}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN = re.compile(
    r"\b(?:insert|update|delete|replace|create|alter|drop|vacuum|attach|detach|"
    r"reindex|analyze|pragma|begin|commit|rollback|savepoint|release)\b",
    re.IGNORECASE,
)


class ProductionShadowQueryPlanError(RuntimeError):
    pass


@dataclass(frozen=True)
class ShadowQueryTemplate:
    query_id: str
    database_id: str
    relation_name: str
    sql: str
    parameter_names: Tuple[str, ...]
    maximum_result_rows_parameter: str
    required_index_prefixes: Tuple[Tuple[str, ...], ...]


@dataclass(frozen=True)
class ProductionShadowQueryContract:
    contract_version: str
    engineering_revision: str
    canonical_manifest_digest: str
    read_boundary_digest: str
    authority_class: str
    templates: Tuple[ShadowQueryTemplate, ...]
    grants_extraction_authority: bool
    grants_activation_authority: bool
    contract_digest: str


@dataclass(frozen=True)
class QueryPlanFinding:
    query_id: str
    database_id: str
    relation_name: str
    plan_details: Tuple[str, ...]
    indexes_used: Tuple[str, ...]
    full_relation_scan: bool
    temporary_structures: Tuple[str, ...]
    compatible: bool
    plan_digest: str


@dataclass(frozen=True)
class ProductionShadowPlanQualification:
    contract_digest: str
    input_fingerprint: str
    findings: Tuple[QueryPlanFinding, ...]
    compatible_query_count: int
    incompatible_query_count: int
    select_templates_executed: int
    production_rows_read: int
    grants_extraction_authority: bool
    grants_activation_authority: bool
    qualification_digest: str


def _digest(value: object) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _templates() -> Tuple[ShadowQueryTemplate, ...]:
    cohort = "SELECT value FROM json_each(?)"
    return tuple(sorted((
        ShadowQueryTemplate(
            "creator_selected_cohort", "creator", "creator_tokens",
            "SELECT rowid,creator_address,mint,created_at FROM creator_tokens "
            f"WHERE rowid<=? AND mint IN ({cohort}) ORDER BY mint,rowid LIMIT ?",
            ("creator_rowid_upper_inclusive", "cohort_mints_json", "row_limit"),
            "row_limit", (("mint",),),
        ),
        ShadowQueryTemplate(
            "evidence_launch_facts", "evidence", "normalized_evidence_records",
            "SELECT rowid,fact_family,payload_json,raw_artifact_digest,acquired_at,source_id,"
            "source_version,verification_state FROM normalized_evidence_records "
            "WHERE rowid<=? AND fact_family=? ORDER BY rowid LIMIT ?",
            ("evidence_rowid_upper_inclusive", "fact_family", "row_limit"),
            "row_limit", (("fact_family",),),
        ),
        ShadowQueryTemplate(
            "main_selected_cohort", "main", "token_analysis",
            "SELECT rowid,mint,migrated_at,first_observed_mc,first_observed_price,"
            "first_observed_at,first_observed_source,first_observed_confidence,pf_ws_creator,"
            f"creator_mismatch FROM token_analysis WHERE rowid<=? AND mint IN ({cohort}) "
            "ORDER BY mint,rowid LIMIT ?",
            ("token_analysis_rowid_upper_inclusive", "cohort_mints_json", "row_limit"),
            "row_limit", (("mint",),),
        ),
        ShadowQueryTemplate(
            "ops_selected_cohort", "ops", "wt_watchtower_launches",
            "SELECT rowid,mint,creator_wallet,create_signature,create_time,create_slot,"
            "creator_extraction_method,confidence,recorded_at FROM wt_watchtower_launches "
            f"WHERE rowid<=? AND mint IN ({cohort}) ORDER BY mint,rowid LIMIT ?",
            ("ops_rowid_upper_inclusive", "cohort_mints_json", "row_limit"),
            "row_limit", (("mint",),),
        ),
        ShadowQueryTemplate(
            "snapshot_selected_cohort", "main", "token_price_snapshots",
            "SELECT rowid,snapshot_id,mint,price_usd,market_cap,source,captured_at,created_at "
            f"FROM token_price_snapshots WHERE rowid<=? AND mint IN ({cohort}) "
            "ORDER BY mint,captured_at,snapshot_id LIMIT ?",
            ("snapshot_rowid_upper_inclusive", "cohort_mints_json", "row_limit"),
            "row_limit", (("mint", "captured_at"),),
        ),
    ), key=lambda item: item.query_id))


def build_production_shadow_query_contract(
    *, engineering_revision: str, canonical_manifest_digest: str,
    read_boundary_digest: str,
) -> ProductionShadowQueryContract:
    if not isinstance(engineering_revision, str) or not _REVISION.fullmatch(engineering_revision):
        raise ProductionShadowQueryPlanError("PSI0A_D_INVALID_ENGINEERING_REVISION")
    if not isinstance(canonical_manifest_digest, str) or not _DIGEST.fullmatch(canonical_manifest_digest):
        raise ProductionShadowQueryPlanError("PSI0A_D_INVALID_MANIFEST_DIGEST")
    if not isinstance(read_boundary_digest, str) or not _DIGEST.fullmatch(read_boundary_digest):
        raise ProductionShadowQueryPlanError("PSI0A_D_INVALID_BOUNDARY_DIGEST")
    templates = _templates()
    for item in templates:
        normalized = " ".join(item.sql.split())
        if ";" in normalized or "--" in normalized or "/*" in normalized or _FORBIDDEN.search(normalized):
            raise ProductionShadowQueryPlanError("PSI0A_D_WRITE_CAPABLE_OR_MULTI_STATEMENT")
        if not normalized.lower().startswith("select ") or " rowid<=? " not in normalized.lower():
            raise ProductionShadowQueryPlanError("PSI0A_D_UNBOUNDED_OR_NON_SELECT_TEMPLATE")
        if " limit ?" not in normalized.lower():
            raise ProductionShadowQueryPlanError("PSI0A_D_MISSING_RESULT_CEILING")
    body = {
        "contract_version": CONTRACT_VERSION,
        "engineering_revision": engineering_revision,
        "canonical_manifest_digest": canonical_manifest_digest,
        "read_boundary_digest": read_boundary_digest,
        "authority_class": AUTHORITY_CLASS,
        "templates": [asdict(item) for item in templates],
        "grants_extraction_authority": False,
        "grants_activation_authority": False,
    }
    return ProductionShadowQueryContract(
        contract_version=CONTRACT_VERSION,
        engineering_revision=engineering_revision,
        canonical_manifest_digest=canonical_manifest_digest,
        read_boundary_digest=read_boundary_digest,
        authority_class=AUTHORITY_CLASS,
        templates=templates,
        grants_extraction_authority=False,
        grants_activation_authority=False,
        contract_digest=_digest(body),
    )


def verify_production_shadow_query_contract(contract: ProductionShadowQueryContract) -> bool:
    expected = build_production_shadow_query_contract(
        engineering_revision=contract.engineering_revision,
        canonical_manifest_digest=contract.canonical_manifest_digest,
        read_boundary_digest=contract.read_boundary_digest,
    )
    if contract != expected:
        raise ProductionShadowQueryPlanError("PSI0A_D_CONTRACT_REPLAY_MISMATCH")
    return True


def _open(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise ProductionShadowQueryPlanError("PSI0A_D_SOURCE_NOT_FOUND")
    connection = sqlite3.connect(
        f"file:{quote(str(path.resolve()), safe='/')}?mode=ro", uri=True,
        timeout=0.25, isolation_level=None,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
        connection.close()
        raise ProductionShadowQueryPlanError("PSI0A_D_QUERY_ONLY_NOT_ENFORCED")
    return connection


def qualify_production_shadow_query_plans(
    contract: ProductionShadowQueryContract,
    source_paths: Mapping[str, Path],
    parameters: Mapping[str, object],
    *,
    input_fingerprint: str,
    max_query_seconds: float = 2.0,
    clock: Callable[[], float] = time.monotonic,
) -> ProductionShadowPlanQualification:
    verify_production_shadow_query_contract(contract)
    if set(source_paths) != {item.database_id for item in contract.templates}:
        raise ProductionShadowQueryPlanError("PSI0A_D_SOURCE_SET_MISMATCH")
    if not isinstance(input_fingerprint, str) or not _DIGEST.fullmatch(input_fingerprint):
        raise ProductionShadowQueryPlanError("PSI0A_D_INVALID_INPUT_FINGERPRINT")
    if isinstance(max_query_seconds, bool) or not isinstance(max_query_seconds, (int, float)) or not 0 < max_query_seconds <= 2:
        raise ProductionShadowQueryPlanError("PSI0A_D_INVALID_QUERY_DEADLINE")
    findings = []
    for template in contract.templates:
        if any(name not in parameters for name in template.parameter_names):
            raise ProductionShadowQueryPlanError("PSI0A_D_MISSING_PARAMETER")
        args = tuple(parameters[name] for name in template.parameter_names)
        connection = _open(Path(source_paths[template.database_id]))
        deadline = clock() + float(max_query_seconds)
        exceeded = False
        def stop() -> int:
            nonlocal exceeded
            exceeded = clock() >= deadline
            return int(exceeded)
        try:
            connection.set_progress_handler(stop, 1_000)
            try:
                rows = connection.execute("EXPLAIN QUERY PLAN " + template.sql, args).fetchall()
            except sqlite3.OperationalError as exc:
                if exceeded:
                    raise ProductionShadowQueryPlanError("PSI0A_D_QUERY_DEADLINE_EXCEEDED") from exc
                raise ProductionShadowQueryPlanError("PSI0A_D_PLAN_INSPECTION_FAILED") from exc
        finally:
            connection.set_progress_handler(None, 0)
            connection.close()
        details = tuple(str(row["detail"]) for row in rows)
        relation_scan = any(
            re.match(rf"^SCAN (?:TABLE )?{re.escape(template.relation_name)}(?:\s|$)", detail, re.I)
            for detail in details
        )
        indexes = tuple(sorted(set(
            match.group(1) for detail in details
            if (match := re.search(r"USING (?:COVERING )?INDEX ([^ ]+)", detail, re.I))
        )))
        temporary = tuple(detail for detail in details if "TEMP B-TREE" in detail.upper())
        compatible = not relation_scan and all(
            any(index_columns[:len(prefix)] == prefix for index_columns in template.required_index_prefixes)
            for prefix in template.required_index_prefixes
        )
        # The exact expected index names vary by schema, so prefix compatibility is
        # proven separately by PSI0A-C15; here the planner must select some index.
        compatible = compatible and bool(indexes)
        plan_body = {
            "query_id": template.query_id, "database_id": template.database_id,
            "relation_name": template.relation_name, "plan_details": details,
            "indexes_used": indexes, "full_relation_scan": relation_scan,
            "temporary_structures": temporary, "compatible": compatible,
        }
        findings.append(QueryPlanFinding(**plan_body, plan_digest=_digest(plan_body)))
    ordered = tuple(findings)
    compatible_count = sum(item.compatible for item in ordered)
    body = {
        "contract_digest": contract.contract_digest,
        "input_fingerprint": input_fingerprint,
        "findings": [asdict(item) for item in ordered],
        "compatible_query_count": compatible_count,
        "incompatible_query_count": len(ordered) - compatible_count,
        "select_templates_executed": 0,
        "production_rows_read": 0,
        "grants_extraction_authority": False,
        "grants_activation_authority": False,
    }
    return ProductionShadowPlanQualification(
        contract_digest=contract.contract_digest,
        input_fingerprint=input_fingerprint,
        findings=ordered,
        compatible_query_count=compatible_count,
        incompatible_query_count=len(ordered) - compatible_count,
        select_templates_executed=0,
        production_rows_read=0,
        grants_extraction_authority=False,
        grants_activation_authority=False,
        qualification_digest=_digest(body),
    )


def verify_production_shadow_plan_qualification(result: ProductionShadowPlanQualification) -> bool:
    body = asdict(result); digest = body.pop("qualification_digest", None)
    if digest != _digest(body):
        raise ProductionShadowQueryPlanError("PSI0A_D_QUALIFICATION_REPLAY_MISMATCH")
    if result.select_templates_executed or result.production_rows_read or result.grants_extraction_authority or result.grants_activation_authority:
        raise ProductionShadowQueryPlanError("PSI0A_D_AUTHORITY_OR_EXECUTION_MISMATCH")
    return True
