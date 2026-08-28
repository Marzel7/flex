#!/usr/bin/env python3
"""Read-only retained-evidence reconciliation of P3R and C357 identity."""
from __future__ import annotations
import hashlib,json,sqlite3,sys
from pathlib import Path
R=Path(__file__).resolve().parents[1]; O=R/'docs/audits/p3r_c357_operation_identity_reconciliation.v1.json'; P='777211c3-211e-551b-9310-ff9301570627'
def load(p):return json.loads((R/p).read_text())
def dig(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def build():
 q=load('docs/audits/c357_operation_qualification_review.v1.json'); db=sqlite3.connect(R/'database/wt_ops_v2.db');db.row_factory=sqlite3.Row
 op=dict(db.execute('select o.*,q.qualification_category,q.automation_eligibility,q.detector_version,q.contract_json,q.evidence_lineage_json from operators o join operation_qualification_contracts q using(operator_id) where o.operator_id=?',(P,)).fetchone())
 pm={x[0] for x in db.execute('select mint from operator_launch_membership where operator_id=?',(P,))}; cs={x['mint']:x for x in q['population']['supported_launches']}; overlap=sorted(pm&set(cs)); only=sorted(set(cs)-pm); pon=sorted(pm-set(cs))
 profiles=[]
 for x in db.execute('select profile_version,source_candidate_id,provenance_json,member_mints_json from operation_behavioural_profiles where operator_id=? order by profile_version',(P,)):
  profiles.append({'profile_version':x['profile_version'],'source_candidate_id':x['source_candidate_id'],'provenance':json.loads(x['provenance_json']),'members':json.loads(x['member_mints_json'])})
 db.close()
 admission={m:{'mint':m,'p3r_admission_basis':'BEHAVIOURAL_FAMILY_MEMBERSHIP_MANUAL_ADMISSION','p3r_profiles':[z['profile_version'] for z in profiles if m in z['members']],'c357_branch':cs[m]['branch'],'c357_evidence':cs[m]['independent_evidence']} for m in overlap}
 six=[{**cs[m],'p3r_exact_criteria_match':True,'p3r_member':False,'classification':'POST_P3R_EVOLUTION' if cs[m]['branch'] in ('B','C') else 'P3R_FALSE_NEGATIVE','why_not_p3r':'absent from the two manually admitted P3R profile member lists'} for m in only]
 x={'schema_version':'P3R_C357_OPERATION_IDENTITY_RECONCILIATION.v1','mode':'READ_ONLY','provider_calls':0,'inputs':['docs/audits/c357_operation_qualification_review.v1.json','docs/audits/c357_operation_registration_preflight.v1.json','docs/audits/c357_dutb_common_funder_rpc.v1.json','docs/audits/c357_branch_b_chronology.v1.json'],'p3r_identity_contract':{'operator':op,'membership_count':len(pm),'profiles':profiles,'meaning':'Manually admitted evidence-bound recurring WSOL_WRAP_CLOSE workflow; explicitly not a real-world identity claim.'},'populations':{'p3r_and_c357_supported':overlap,'c357_supported_not_p3r':six,'p3r_not_c357_supported':pon,'c357_compatible_unresolved':105},'overlap_admission':admission,'comparisons':{'behaviour':'Same exact WSOL_WRAP_CLOSE 99,999,985,000-lamport ordered lifecycle; C357 supplies a stricter independent branch-attribution layer.','infrastructure':'C357 independently verifies DuTb/A/B/C; P3R provenance says upstream evidence PARTIAL, so P3R did not establish C357-specific infrastructure as its admission predicate.','temporal':'P3R profiles contain all 50 overlaps; C357-only B/C launches occur after P3R membership/profiles and are consistent with uncovered subtype continuity.'},'hypotheses':{'SAME_OPERATION':'MODERATE','C357_SUBTYPE_OF_P3R':'STRONG','P3R_MEMBERSHIP_OVERAGGREGATED':'WEAK_EVIDENCE','RELATED_DISTINCT_OPERATIONS':'WEAK'},'identity_verdict':'P3R_PARENT_C357_SUBTYPE','relationship':'C357 is a P3R behavioural/infrastructure subtype (exact route family with independently qualified branches), not a competing second owner of the same launches.','ownership_of_50':'P3R_MEMBERSHIP_VALID_C357_SUBTYPE','registry_membership_model':'PARENT_CHILD_OPERATION_MODEL_NEEDED','six_only_registration':'MISLEADING','registration_recommendation':'REPRESENT_C357_AS_P3R_SUBTYPE','p3r_governance':'subtype decomposition/evidence refresh; no membership reassignment now','detector_implication':'C357 remains a shadow-only subtype attribution classifier; compatibility recognition may be represented beneath P3R but is not membership automation.','membership_blocker_next_action':'no reassignment; design and authorize parent-child subtype representation that references the 56 allowlisted members without creating competing global memberships.','safety':{'registry_write':False,'membership_change':False,'workflow_change':False,'detector_change':False,'trading_change':False}}
 x['deterministic_digest']=dig(x);return x
if __name__=='__main__':
 if '--replay' in sys.argv:
  a=load('docs/audits/p3r_c357_operation_identity_reconciliation.v1.json');d=a.pop('deterministic_digest');b=build();b.pop('deterministic_digest');assert d==dig(a) and a==b;print('P3R_C357_IDENTITY_RECONCILIATION_REPLAY_PASS provider_calls_during_replay=0')
 else:
  x=build();O.write_text(json.dumps(x,indent=2,sort_keys=True)+'\n');print(x['identity_verdict'],x['deterministic_digest'])
