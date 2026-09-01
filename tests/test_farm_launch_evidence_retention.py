import sqlite3

from src.core.deep_walkback import ensure_schema
from src.core.farm_detector import FunderTrace, _persist_farm_launch_evidence, ensure_farm_schema


def _tx():
    return {"blockTime": 10, "transaction": {"message": {"accountKeys": [
        {"pubkey":"source","signer":True}, {"pubkey":"creator","signer":False}],
        "instructions":[{"programId":"111","parsed":{"type":"transfer","info":{"source":"source","destination":"creator","lamports":1000}}}]
    }}, "meta":{"innerInstructions":[]}}


def test_farm_evidence_links_generic_role_without_rpc_and_is_idempotent():
    c=sqlite3.connect(':memory:'); ensure_schema(c); ensure_farm_schema(c)
    trace=FunderTrace('source',False,0.000001,'sig',10,_tx())
    first=_persist_farm_launch_evidence(c,mint='mint',creator='creator',trace=trace,launch_time=20); c.commit()
    second=_persist_farm_launch_evidence(c,mint='mint',creator='creator',trace=trace,launch_time=20); c.commit()
    assert first==second
    assert c.execute('select count(*) from wt_farm_launch_evidence').fetchone()[0]==1
    assert c.execute('select count(*) from wt_walkback_transaction_roles').fetchone()[0]==1
    assert c.execute('select transfer_source,transfer_destination,transfer_lamports from wt_walkback_transaction_roles').fetchone()==('source','creator',1000)
