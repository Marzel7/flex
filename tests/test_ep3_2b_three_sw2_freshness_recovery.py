from pathlib import Path

from src.ops.three_sw2_freshness_recovery import ThreeSw2FreshnessRecovery


def test_recovery_plan_is_exactly_four_creators_and_bounded():
    recovery = ThreeSw2FreshnessRecovery(
        operations_db=Path("database/wt_ops_v2.db"),
        main_db=Path("database/flex_complete_database.db"),
        cache_db=Path("database/transaction_first_lineage.db"),
        output_root=Path("database/evidence_platform/three_sw2_shadow_ep3_2a"),
    )
    plan = recovery.plan()
    assert len(plan["subjects"]) == 4
    assert len({item["creator"] for item in plan["subjects"]}) == 4
    assert len({item["activation_reference"] for item in plan["subjects"]}) == 4
    assert plan["transaction_fetches"] == 0
    assert plan["hard_rpc_ceiling"] == 40
    assert plan["hard_credit_ceiling"] == 400
    assert plan["population_expansion"] is False

