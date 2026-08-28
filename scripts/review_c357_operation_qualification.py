#!/usr/bin/env python3
"""Deterministic, retained-evidence-only C357 operation qualification review."""
from __future__ import annotations
import hashlib,json,sqlite3,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/audits/c357_operation_qualification_review.v1.json'
CID='p3r-v2-c357da9d0d4d560311e4'; RUN='p3r-v2-2dec1d40604c1f7c08c8'
FINGERPRINT=['createAccountWithSeed','initializeAccount3','transfer','syncNative','closeAccount']
def load(name): return json.loads((ROOT/name).read_text())
def canon(x): return json.dumps(x,sort_keys=True,separators=(',',':'))
def dig(x): return hashlib.sha256(canon(x).encode()).hexdigest()
def exact_rows(db):
 q="select e.mint,e.wallet creator,e.candidate_parent funder,e.block_time,e.signature,a.instruction_order_json from wt_walkback_edge_candidates e join wt_walkback_atomic_flows a on a.signature=e.signature where e.selection_status='SELECTED' and e.amount_lamports=99999985000 and a.transfer_lamports=99997955720 and a.has_create=1 and a.has_sync_native=1 and a.has_close=1"
 return [dict(x) for x in db.execute(q)]
def build():
 db=sqlite3.connect(ROOT/'database/wt_ops_v2.db'); db.row_factory=sqlite3.Row
 rows=exact_rows(db); bymint={x['mint']:x for x in rows}
 frozen={x[0] for x in db.execute('select mint from p3r_v2_candidate_membership where candidate_id=? and run_id=?',(CID,RUN))}
 dutb=load('docs/audits/c357_dutb_common_funder_rpc.v1.json'); funders={x['wallet'] for x in dutb['dutb_to_direct_funders_via_wsol_close']['by_funder']}
 a=[x for x in rows if x['funder']=='CrncHWgsMzg9M3gWrVEQr4w93bqzyUdPUkqbXKMfDht5']
 c=[x for x in rows if x['funder']=='6hakQXcUcnk1EuuGMHsDPw9iP4GPwjUtHxxPAvGnPj3L']
 b=load('docs/audits/c357_branch_b_chronology.v1.json')['launches']
 b=[bymint[x['mint']] for x in b]
 # The frozen 71-member snapshot locates the historical population; its labels
 # are not evidence. Each inclusion below independently requires a branch path.
 d=[x for x in rows if x['mint'] in frozen and x['funder'] in funders]
 branches=[
  ('DUTB_WALLET_POOL',d,'DuTb temporary seeded-WSOL close delivery into direct funder'),
  ('A',a,'7nUCh -> Crnc retained upstream/direct-funder path'),
  ('B',b,'33my -> HXuf -> CZTx qualified role chronology; HXuf direct funder'),
  ('C',c,'E257 -> 6hak retained upstream/direct-funder path'),]
 supported={}
 for branch,items,evidence in branches:
  for x in items:
   if x['mint'] in supported: raise AssertionError('BRANCH_OVERLAP:'+x['mint'])
   assert json.loads(x['instruction_order_json'])==FINGERPRINT
   supported[x['mint']]={'mint':x['mint'],'creator':x['creator'],'direct_funder':x['funder'],'block_time':x['block_time'],'signature':x['signature'],'branch':branch,'exact_compatible_behaviour':True,'independent_evidence':evidence}
 assert [len(x[1]) for x in branches]==[48,5,2,1] and len(supported)==56
 dtime=[x['block_time'] for x in d]
 result={'schema_version':'C357_OPERATION_QUALIFICATION_REVIEW.v1','candidate_id':CID,'mode':'RETAINED_EVIDENCE_ONLY','provider_calls':0,
 'source_artifacts':{'shadow_replay':'docs/audits/c357_shadow_historical_replay.v1.json','dutb':'docs/audits/c357_dutb_common_funder_rpc.v1.json','branch_b':'docs/audits/c357_branch_b_chronology.v1.json','remaining_upstreams':'docs/audits/c357_remaining_upstream_funders.v1.json'},
 'population':{'exact_compatible_total':161,'independently_supported':56,'compatible_unresolved':105,'supported_launches':list(sorted(supported.values(),key=lambda x:x['mint']))},
 'branch_evidence':[{'branch':'DuTb','compatible_launches':48,'independent_infrastructure_evidence':'35 direct funders receive verified DuTb temporary seeded-WSOL close deliveries; 65 deliveries including coordinated initial/follow-up waves','strength':'STRONG','valid_window':[min(dtime),max(dtime)]},{'branch':'A','compatible_launches':5,'independent_infrastructure_evidence':'7nUCh upstream to Crnc direct funder retained path','strength':'MODERATE','valid_window':[min(x['block_time'] for x in a),max(x['block_time'] for x in a)]},{'branch':'B','compatible_launches':2,'independent_infrastructure_evidence':'33my provisioned HXuf, HXuf funded CZTx, then HXuf directly funded two exact launches','strength':'STRONG','valid_window':[min(x['block_time'] for x in b),max(x['block_time'] for x in b)]},{'branch':'C','compatible_launches':1,'independent_infrastructure_evidence':'E257 upstream to 6hak direct funder retained path','strength':'WEAK','valid_window':[c[0]['block_time'],c[0]['block_time']]}],
 'dutb_recurrence':{'provisioned_wallets':35,'wallets_later_used_for_compatible_launches':35,'compatible_launches_reached':48,'first_launch':min(dtime),'last_launch':max(dtime),'wallet_reuse':48-35,'creator_count':len({x['creator'] for x in d}),'verdict':'REUSABLE_OPERATIONAL_LAUNCH_INFRASTRUCTURE_STRONGLY_SUPPORTED'},
 'coordination_dimensions':{'coordinated_wallet_provisioning':'STRONG','multi_wallet_recurrence':'STRONG','reusable_funding_infrastructure':'STRONG','repeated_compatible_launch_behaviour':'STRONG','role_recurrence':'MODERATE','temporal_persistence':'MODERATE','post_provisioner_continuity':'MODERATE','branch_recurrence':'MODERATE'},
 'alternative_explanation':'ALTERNATIVE_WEAK','alternative_reason':'A synchronized common-source 35-wallet WSOL-close pool followed by 48 exact launches, plus independently evidenced A/B/C branches, is materially less consistent with isolated coincidental generic-infrastructure use; it does not identify a human controller.',
 'operation_existence_verdict':'C357_OPERATION_EXISTENCE_STRONGLY_SUPPORTED','attribution_verdict':'C357_ATTRIBUTION_PARTIAL','detector_readiness_verdict':'PRODUCTION_ATTRIBUTION_DETECTOR_NOT_READY','behaviour_detector':'QUALIFIED_C357_COMPATIBLE','attribution_detector':'NOT_READY_C357_ATTRIBUTED','common_human_controller':'NOT_PROVEN','infrastructure_rotation_supported':'YES','post_dutb_continuity':'OPERATION_CONTINUITY_BEYOND_ORIGINAL_PROVISIONER','safe_initial_operation_population':56,'unresolved_disposition':'C357_COMPATIBLE_UNRESOLVED: outside confirmed membership; shadow monitoring and later independent branch attribution only.','future_branch_policy':'C357_NEW_BRANCH_REVIEW_V1: exact behaviour, coherent multi-wallet evidence, independent continuity, then human attribution review; no automatic membership.','promotion_review_verdict':'QUALIFIED_FOR_OPERATION_REGISTRATION_REVIEW','registry_implication':'QUALIFIED_OPERATION_WITH_PARTIAL_ATTRIBUTION; monitoring-only, production attribution off, trading off, unresolved launches excluded.','safety':{'workflow':'PAUSED','membership_mutation':False,'detector_activation':False,'fingerprint_change':False,'snapshot_v2_change':False,'queue_change':False,'production_change':False,'trading_change':False}}
 db.close(); result['deterministic_digest']=dig(result); return result
if __name__=='__main__':
 if '--replay' in sys.argv:
  prior=load('docs/audits/c357_operation_qualification_review.v1.json'); observed=prior.pop('deterministic_digest'); fresh=build(); fresh.pop('deterministic_digest'); assert observed==dig(prior); assert prior==fresh; print('C357_OPERATION_QUALIFICATION_REPLAY_PASS provider_calls_during_replay=0')
 else:
  value=build(); OUT.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n'); print(value['deterministic_digest'])
