from pathlib import Path
from src.ops.three_sw2_shadow_corpus import ThreeSw2ShadowCorpus

def test_frozen_plan_is_bounded_and_complete(tmp_path):
    task=ThreeSw2ShadowCorpus(operations_db=Path("database/wt_ops_v2.db"),main_db=Path("database/flex_complete_database.db"),cache_db=Path("database/transaction_first_lineage.db"),output_root=tmp_path)
    plan=task.plan()
    assert plan["frozen_launches"]==13 and plan["activation_edges"]==13
    assert plan["cached_transactions"]==24
    assert plan["known_transaction_fetches"]==1
    assert plan["signature_discovery_subjects"]==1
    assert plan["hard_rpc_ceiling"]==5 and plan["hard_credit_ceiling"]==50
    population=task.population()
    assert len({row["mint"] for row in population["launches"]})==13
    assert all(row["from_wallet"]=="3SW2zquY2mVTbNuw1ZCGgtoehq2evfU36PFd6TTqSXdK" for row in population["provisioning_edges"])
