import sqlite3
from src.ops.operation_attribution_manual import propose,validate
def f(x,s,g):return {'family':x,'state':s,'independence_from_detector':'PASS','independence_from_other_families':'PASS','dependency_group':g}
def test_manual_flow():
 c=sqlite3.connect(':memory:');propose(c,'n','retained','DIRECT');assert validate(c,'n',[])['validation_state']=='ADDITIONAL_EVIDENCE_REQUIRED'
 propose(c,'p','fixture','DIRECT');d=validate(c,'p',[f('CONTROLLER_CONTINUITY','PROVEN_STRONG','a'),f('EARLY_EXECUTION_FINGERPRINT','PROVEN_MODERATE','b')]);assert d['attribution_state']=='ATTRIBUTION_PROVEN';assert c.execute('select count(*) from manual_attribution_decisions where proposal_id="p"').fetchone()[0]==1
