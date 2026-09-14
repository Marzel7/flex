import sqlite3
from src.ops.operation_attribution_shadow import shadow_decide
def f(x,s,g): return {'family':x,'state':s,'independence_from_detector':'PASS','independence_from_other_families':'PASS','dependency_group':g}
def test_shadow_is_idempotent_and_non_membership():
 c=sqlite3.connect(':memory:')
 a,k=shadow_decide(c,candidate_id='nexus',proposed_operation_id='n',detector_contract='DIRECT_10K_CREATOR_PROVISIONING',families=[],production_decision_observed=False)
 b,k2=shadow_decide(c,candidate_id='nexus',proposed_operation_id='n',detector_contract='DIRECT_10K_CREATOR_PROVISIONING',families=[],production_decision_observed=False)
 assert a['attribution_state']=='ATTRIBUTION_NOT_PROVEN' and k==k2 and c.execute('select count(*) from operation_attribution_shadow_decisions').fetchone()[0]==1
def test_positive_and_infrastructure_shadow():
 c=sqlite3.connect(':memory:')
 p,_=shadow_decide(c,candidate_id='p',proposed_operation_id='p',detector_contract='d',families=[f('CONTROLLER_CONTINUITY','PROVEN_STRONG','c'),f('EARLY_EXECUTION_FINGERPRINT','PROVEN_MODERATE','e')],production_decision_observed=False)
 x,_=shadow_decide(c,candidate_id='x',proposed_operation_id='x',detector_contract='d',families=[f('FUNDING_CONTINUITY','PROVEN_STRONG','relay')],production_decision_observed=False,common_infrastructure_exclusions=[{'type':'RELAY','dependency_group':'relay'}])
 assert p['would_promote_if_enforced'] and x['attribution_state']=='ATTRIBUTION_NOT_PROVEN'
