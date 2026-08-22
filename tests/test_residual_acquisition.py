from src.discovery.residual_acquisition import freeze_budget

def test_budget_hard_gate_is_not_bypassable():
    assert freeze_budget(migration_transactions=20,upstream_histories=10,behaviour_transactions=5,cache_hits=2)['execution_state']=='MANUAL_GATE'
    assert freeze_budget(migration_transactions=201,upstream_histories=0,behaviour_transactions=0,cache_hits=0)['execution_state']=='HUMAN_APPROVAL_REQUIRED'
