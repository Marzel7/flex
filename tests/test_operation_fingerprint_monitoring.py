from src.ops.operation_fingerprint_monitoring import audit_confirmed_address_independence, build_fingerprint_health


def test_confirmed_fingerprint_contracts_do_not_require_literal_addresses():
    rows = audit_confirmed_address_independence()
    assert rows and all(not field["literal_address_required"] for row in rows for field in row["fields"])


def test_exact_uniqueness_is_transparent_and_near_matches_do_not_reduce_it():
    health = build_fingerprint_health({"display_name": "FOUR_STEP_30_SOL_14_479K_WSOL_LADDER", "qualification_contract": {}})
    assert health["qualified_uniqueness_percent"] == health["current_uniqueness_percent"] == 100.0
    assert health["near_match_count"] == 0 and "external_exact_matches" in health["formula"]


def test_provisional_900b_is_explicitly_not_confirmed_detection():
    health = build_fingerprint_health({"display_name": "WSOL_PROVISION_CLOSE_1_SOL_MINUS_15K", "qualification_contract": {}})
    assert health["detection_type"] == "PROVISIONAL-HYBRID"
