from src.ops.operation_attribution_evidence import decide
def f(family,state,det='PASS',other='PASS',group='x'):
 return {'family':family,'state':state,'independence_from_detector':det,'independence_from_other_families':other,'dependency_group':group}
def test_gate_fixtures():
 assert decide([f('FUNDING_CONTINUITY','PROVEN_STRONG','FAIL','FAIL','detector')]) == 'ATTRIBUTION_NOT_PROVEN'
 assert decide([f('FUNDING_CONTINUITY','PROVEN_STRONG','FAIL','FAIL','detector'),f('TRANSACTION_FINGERPRINT','PROVEN_MODERATE','FAIL','FAIL','detector'),f('TEMPORAL_FINGERPRINT','PROVEN_MODERATE','FAIL','FAIL','detector')]) == 'ATTRIBUTION_NOT_PROVEN'
 assert decide([f('FUNDING_CONTINUITY','PROVEN_STRONG')]) == 'ATTRIBUTION_NOT_PROVEN'
 assert decide([f('FUNDING_CONTINUITY','PROVEN_WEAK',group='one'),f('EARLY_EXECUTION_FINGERPRINT','PROVEN_WEAK',group='two')]) == 'ATTRIBUTION_NOT_PROVEN'
 assert decide([f('FUNDING_CONTINUITY','PROVEN_STRONG',group='one'),f('TEMPORAL_FINGERPRINT','PROVEN_MODERATE',group='one')]) == 'ATTRIBUTION_NOT_PROVEN'
 assert decide([f('TRANSACTION_FINGERPRINT','PROVEN_STRONG',group='one'),f('LIFECYCLE_BEHAVIOUR','PROVEN_STRONG',group='two')]) == 'ATTRIBUTION_NOT_PROVEN'
 assert decide([f('FUNDING_CONTINUITY','PROVEN_STRONG',group='one'),f('EARLY_EXECUTION_FINGERPRINT','PROVEN_MODERATE',group='two')]) == 'ATTRIBUTION_PROVEN'
 assert decide([f('CONTROLLER_CONTINUITY','PROVEN_STRONG',group='one'),f('EARLY_EXECUTION_FINGERPRINT','PROVEN_MODERATE',group='two')], ['material']) == 'ATTRIBUTION_NOT_PROVEN'
 assert decide([f('CONTROLLER_CONTINUITY','PROVEN_STRONG',group='controller'),f('EARLY_EXECUTION_FINGERPRINT','PROVEN_MODERATE',group='buyers')]) == 'ATTRIBUTION_PROVEN'
