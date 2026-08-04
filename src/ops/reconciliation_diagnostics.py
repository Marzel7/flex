"""Developer-only workspace model for the complete shadow reconciliation pipeline."""
from __future__ import annotations

import dataclasses
from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping

from src.ops.disposition_resolver import (
    INFRASTRUCTURE,
    REJECTED,
    REVIEW,
    UNRESOLVED,
    DispositionResolver,
    DispositionResult,
    ShadowDispositionComparison,
)
from src.ops.emerging_operator_service import EmergingOperatorService
from src.ops.evidence_reconciliation import (
    EvidenceReconciliationPackage,
    EvidenceReconciliationService,
)
from src.ops.investigation_population import InvestigationPopulation


MATCH = "MATCH"
EXPECTED_DIFFERENCE = "EXPECTED_DIFFERENCE"
UNEXPECTED_DIFFERENCE = "UNEXPECTED_DIFFERENCE"


def to_plain(value: Any) -> Any:
    """Recursively serialise frozen dataclasses and mapping proxies."""
    if dataclasses.is_dataclass(value):
        return {
            field.name: to_plain(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): to_plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_plain(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class DifferenceAnalysis:
    classification: str
    legacy_stage: str
    shadow_disposition: str
    explanation: str


@dataclass(frozen=True, slots=True)
class ReplayValidation:
    population_revision: str
    original_package_id: str
    replay_package_id: str
    original_result_id: str
    replay_result_id: str
    identical: bool


@dataclass(frozen=True, slots=True)
class DiagnosticRecord:
    population: InvestigationPopulation
    package: EvidenceReconciliationPackage
    disposition: DispositionResult
    legacy_projection: Mapping[str, Any]
    comparison: ShadowDispositionComparison
    difference: DifferenceAnalysis
    replay: ReplayValidation
    source: str


@dataclass(frozen=True, slots=True)
class ReconciliationDiagnosticsWorkspace:
    records: tuple[DiagnosticRecord, ...]
    metrics: Mapping[str, Any]

    def summary(self) -> dict[str, Any]:
        return {
            "read_only": True,
            "developer_only": True,
            "metrics": to_plain(self.metrics),
            "populations": [record_summary(record) for record in self.records],
        }

    def get(self, population_id: str) -> DiagnosticRecord | None:
        return next(
            (record for record in self.records if record.population.population_id == population_id),
            None,
        )


def record_summary(record: DiagnosticRecord) -> dict[str, Any]:
    metadata = record.population.metadata
    return {
        "population_id": record.population.population_id,
        "population_revision": record.population.revision_id,
        "population_members": list(record.population.members),
        "launch_count": len(record.population.launches),
        "population_basis": to_plain(record.population.population_basis),
        "timeline": to_plain(record.population.timeline),
        "infrastructure_relationships": {
            "treasuries": to_plain(metadata.get("treasuries") or ()),
            "exclusions": to_plain(metadata.get("exclusions") or ()),
        },
        "package_id": record.package.package_id,
        "shadow_disposition": record.disposition.disposition,
        "legacy_disposition": record.legacy_projection.get("stage"),
        "agreement": record.comparison.agreement,
        "difference_classification": record.difference.classification,
        "difference_explanation": record.difference.explanation,
        "reasoning_chain": list(record.disposition.reasoning_chain),
        "result_id": record.disposition.result_id,
        "replay_identical": record.replay.identical,
        "source": record.source,
    }


def record_detail(record: DiagnosticRecord) -> dict[str, Any]:
    return {
        "read_only": True,
        "developer_only": True,
        "summary": record_summary(record),
        "population": to_plain(record.population),
        "evidence_package": to_plain(record.package),
        "disposition": to_plain(record.disposition),
        "legacy_projection": to_plain(record.legacy_projection),
        "comparison": to_plain(record.comparison),
        "difference": to_plain(record.difference),
        "replay": to_plain(record.replay),
    }


class ReconciliationDiagnosticsService:
    """Build the shadow workspace on demand; never persists or mutates state."""

    def __init__(self, ops_db_path: str, live_db_path: str) -> None:
        self.ops_db_path = ops_db_path
        self.live_db_path = live_db_path

    def build(self) -> ReconciliationDiagnosticsWorkspace:
        legacy = EmergingOperatorService(self.ops_db_path, self.live_db_path)
        families = legacy._compose()
        family_by_id = {family["family_id"]: family for family in families}
        with legacy._connect(self.ops_db_path) as conn:
            populations = legacy._population_builder().build(
                legacy._discovery_profiles(conn, legacy._tables(conn))
            )
        reconciler = EvidenceReconciliationService(self.ops_db_path)
        population_sources = [(population, "INVESTIGATION_POPULATION") for population in populations]
        population_sources.extend(
            (reconciler.population_from_canonical_registry(family), "CANONICAL_REGISTRY_SHADOW")
            for family in families if family.get("is_canonical_operator")
        )

        records = []
        for population, source in population_sources:
            family = family_by_id[population.population_id]
            package = reconciler.build(population)
            disposition = DispositionResolver.resolve(package)
            comparison = DispositionResolver.compare_legacy(family["stage"], disposition)
            difference = self._classify_difference(comparison, disposition)
            replay_package = reconciler.build(population)
            replay_result = DispositionResolver.resolve(replay_package)
            replay = ReplayValidation(
                population_revision=population.revision_id,
                original_package_id=package.package_id,
                replay_package_id=replay_package.package_id,
                original_result_id=disposition.result_id,
                replay_result_id=replay_result.result_id,
                identical=(
                    package.package_id == replay_package.package_id
                    and disposition.result_id == replay_result.result_id
                ),
            )
            records.append(DiagnosticRecord(
                population, package, disposition, family, comparison,
                difference, replay, source,
            ))

        records.sort(key=lambda record: (
            record.difference.classification != UNEXPECTED_DIFFERENCE,
            record.difference.classification != EXPECTED_DIFFERENCE,
            record.population.population_id,
        ))
        metrics = self._metrics(records)
        return ReconciliationDiagnosticsWorkspace(tuple(records), metrics)

    @staticmethod
    def _classify_difference(
        comparison: ShadowDispositionComparison,
        result: DispositionResult,
    ) -> DifferenceAnalysis:
        if comparison.agreement:
            return DifferenceAnalysis(
                MATCH, comparison.legacy_stage, result.disposition,
                "Legacy state and its shadow equivalent agree.",
            )
        contradiction_types = {item.evidence_type for item in result.contradictory_evidence}
        missing_types = {item.evidence_type for item in result.missing_evidence}
        reasons = " ".join(result.reasoning_chain)
        if result.disposition == INFRASTRUCTURE and contradiction_types:
            explanation = "Persisted infrastructure exclusion evidence supersedes the legacy family presentation."
        elif result.disposition == REJECTED and contradiction_types:
            explanation = "Persisted invalid, unsupported, dust, or noise evidence explains the legacy population."
        elif result.disposition == REVIEW and contradiction_types:
            explanation = "Observed contradictory evidence requires review and cannot be hidden by the legacy state."
        elif result.disposition == UNRESOLVED and missing_types:
            explanation = (
                "The package lacks independent control-bearing evidence; missing evidence remains unknown. "
                + ("Legacy confirmation history is contextual only." if "Legacy confirmation history" in reasons else "")
            ).strip()
        else:
            return DifferenceAnalysis(
                UNEXPECTED_DIFFERENCE, comparison.legacy_stage,
                result.disposition,
                "No approved evidence-based explanation accounts for this transition.",
            )
        return DifferenceAnalysis(
            EXPECTED_DIFFERENCE, comparison.legacy_stage,
            result.disposition, explanation,
        )

    @staticmethod
    def _metrics(records: list[DiagnosticRecord]) -> Mapping[str, Any]:
        dispositions = Counter(record.disposition.disposition for record in records)
        differences = Counter(record.difference.classification for record in records)
        return {
            "total_investigation_populations": sum(
                record.source == "INVESTIGATION_POPULATION" for record in records
            ),
            "total_shadow_records": len(records),
            "agreement_count": differences[MATCH],
            "expected_differences": differences[EXPECTED_DIFFERENCE],
            "unexpected_differences": differences[UNEXPECTED_DIFFERENCE],
            "infrastructure_populations": dispositions["INFRASTRUCTURE"],
            "rejected_populations": dispositions["REJECTED"],
            "review_populations": dispositions["REVIEW"],
            "operator_candidates": dispositions["OPERATOR_CANDIDATE"],
            "confirmed_operations": dispositions["CONFIRMED_OPERATION"],
            "retired_populations": dispositions["RETIRED"],
            "unresolved_populations": dispositions["UNRESOLVED"],
            "deterministic_replay_failures": sum(not record.replay.identical for record in records),
        }
