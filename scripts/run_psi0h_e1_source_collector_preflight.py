#!/usr/bin/env python3
"""Replay the PSI0H-E1 source selection without touching runtime data."""

from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evidence.contracts.psi0h_source_collector_preflight import select_source_collector


IDENTITIES = {
    "src/core/pumpportal_migration_census.py": "d8a7d14cce5c4a78a486bc9ce0d17cd578979845e1b7120c6b70128bf050ad55",
    "src/acquisition/transaction.py": "fc11a5401ccc869f433e99ee65687d8356f9a949d47a43d097fe8aea14a84605",
    "src/acquisition/factory.py": "f09c87343a18a9ee9906d929203d304fab9839a3b3d542f0f901344becc871bb",
    "src/acquisition/retained_observations.py": "1495f19806334659d1df71e5d3474593aa3881804a4c059aa76aa76c32b0738e",
    "src/evidence/normalizers.py": "ba3fdf508872593dea1e91d500a0c67b99032db9d1618487d76487bcc0ea18de",
    "src/evidence/primitives/engine.py": "9423bfa3b9358e0d64e27768c70eb0360fb6059535f5ea938082c9f4615072bb",
}


def run() -> dict:
    result = select_source_collector(candidates=[
        {"candidate_id": "pumpportal-migration-census-only", "operation_neutral": True,
         "live_event_time": True, "fresh_signature": True, "exact_artifact": False,
         "supported_families": [], "existing_source_active": True,
         "requires_provider_requests": False, "requires_service_change": False,
         "code_identities": {"src/core/pumpportal_migration_census.py": IDENTITIES["src/core/pumpportal_migration_census.py"]}},
        {"candidate_id": "retained-acquisition-only", "operation_neutral": True,
         "live_event_time": False, "fresh_signature": False, "exact_artifact": True,
         "supported_families": list(("TransactionFact", "AccountParticipationFact", "InstructionFact", "LaunchFact")),
         "existing_source_active": True, "requires_provider_requests": False,
         "requires_service_change": False,
         "code_identities": {key: IDENTITIES[key] for key in (
             "src/acquisition/factory.py", "src/acquisition/retained_observations.py",
             "src/evidence/normalizers.py", "src/evidence/primitives/engine.py")}},
        {"candidate_id": "migration-census-bounded-gettransaction-adapter",
         "operation_neutral": True, "live_event_time": True, "fresh_signature": True,
         "exact_artifact": True, "supported_families": list((
             "TransactionFact", "AccountParticipationFact", "InstructionFact", "LaunchFact")),
         "existing_source_active": True, "requires_provider_requests": True,
         "requires_service_change": False, "code_identities": IDENTITIES},
    ], maximum_units=20)
    result["proposal"] = {
        "future_interval_required": True, "maximum_migrations": 20,
        "maximum_getTransaction_requests": 20, "retries": 0, "pagination": 0,
        "existing_listener_restart_required": False,
        "new_isolated_adapter_required": True,
        "real_authorization_required": True,
    }
    return result


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True, separators=(",", ":")))
