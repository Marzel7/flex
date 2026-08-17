import copy

import pytest

from scripts.run_psi0h_e1_source_collector_preflight import run
from src.evidence.contracts.psi0h_source_collector_preflight import (
    Psi0hSourceCollectorPreflightError, select_source_collector,
)


def test_real_preflight_selects_only_combined_bounded_path():
    result = run()
    assert result["status"] == "READY"
    assert result["selected"]["candidate_id"] == "migration-census-bounded-gettransaction-adapter"
    assert result["provider_requests"] == 0 and not result["authorization_materialized"]


def test_census_and_retention_alone_are_ineligible_for_distinct_reasons():
    rows = {row["candidate_id"]: row for row in run()["evaluated"]}
    assert "EXACT_ARTIFACT_ABSENT" in rows["pumpportal-migration-census-only"]["reasons"]
    assert "LIVE_EVENT_TIME_ABSENT" in rows["retained-acquisition-only"]["reasons"]


def test_missing_family_holds_without_selection():
    candidate = {"candidate_id": "x", "operation_neutral": True, "live_event_time": True,
                 "fresh_signature": True, "exact_artifact": True,
                 "supported_families": ["TransactionFact"], "existing_source_active": True,
                 "requires_provider_requests": False, "requires_service_change": False,
                 "code_identities": {"x.py": "a" * 64}}
    result = select_source_collector(candidates=[candidate])
    assert result["status"] == "HOLD" and result["selected"] is None


def test_ambiguous_or_malformed_selection_fails_closed():
    from scripts.run_psi0h_e1_source_collector_preflight import IDENTITIES
    eligible = {"candidate_id": "x", "operation_neutral": True, "live_event_time": True,
                "fresh_signature": True, "exact_artifact": True,
                "supported_families": ["TransactionFact", "AccountParticipationFact", "InstructionFact", "LaunchFact"],
                "existing_source_active": True, "requires_provider_requests": False,
                "requires_service_change": False, "code_identities": IDENTITIES}
    second = copy.deepcopy(eligible); second["candidate_id"] = "y"
    with pytest.raises(Psi0hSourceCollectorPreflightError, match="AMBIGUOUS"):
        select_source_collector(candidates=[eligible, second])
    broken = copy.deepcopy(eligible); broken["code_identities"] = {}
    with pytest.raises(Psi0hSourceCollectorPreflightError, match="CODE_IDENTITY"):
        select_source_collector(candidates=[broken])
