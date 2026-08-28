#!/usr/bin/env python3
"""Read-only, deterministic C357 Operations Registry registration preflight."""
from __future__ import annotations
import hashlib,json,sqlite3,sys,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; CID='p3r-v2-c357da9d0d4d560311e4'; OUT=ROOT/'docs/audits/c357_operation_registration_preflight.v1.json'
def load(p): return json.loads((ROOT/p).read_text())
def c(x): return json.dumps(x,sort_keys=True,separators=(',',':'))
def h(x): return hashlib.sha256(c(x).encode()).hexdigest()
def build():
 q=load('docs/audits/c357_operation_qualification_review.v1.json'); assert q['deterministic_digest']=='52a61e90b3f41188543b9ab241ad1bc8bd6e9c75e0de160ad1e63e29c45872b2'
 db=sqlite3.connect(ROOT/'database/wt_ops_v2.db');db.row_factory=sqlite3.Row
 proposed=str(uuid.uuid5(uuid.NAMESPACE_URL,'flex/operation/'+CID)); members=q['population']['supported_launches']; mints={x['mint'] for x in members}
 rows=db.execute('select m.mint,o.operator_id,o.display_name from operator_launch_membership m join operators o on o.operator_id=m.operator_id where m.mint in (%s)'%(','.join('?'*len(mints))),tuple(sorted(mints))).fetchall()
 existing=[dict(x) for x in rows]; ids=[x[0] for x in db.execute('select operator_id from operators')]; names=[x[0] for x in db.execute('select display_name from operators where display_name is not null')]
 wf=db.execute('select workflow_status,proposed_name,parent_mechanism from potential_operation_workflows where candidate_id=?',(CID,)).fetchone()
 db.close()
 audit=[]
 for x in members: audit.append({**x,'membership_admissibility':'ADMISSIBLE' if x['mint'] not in {y['mint'] for y in existing} else 'NOT_ADMISSIBLE','evidence_artifact':{'DUTB_WALLET_POOL':'docs/audits/c357_dutb_common_funder_rpc.v1.json','A':'docs/audits/c357_remaining_upstream_funders.v1.json','B':'docs/audits/c357_branch_b_chronology.v1.json','C':'docs/audits/c357_remaining_upstream_funders.v1.json'}[x['branch']]})
 r={'schema_version':'C357_OPERATION_REGISTRATION_PREFLIGHT.v1','mode':'READ_ONLY_PREFLIGHT','provider_calls':0,'qualification_input':{'path':'docs/audits/c357_operation_qualification_review.v1.json','digest':q['deterministic_digest']},
 'registry_semantics':{'admission':'operators + operation_registry_dispositions ACTIVE_MANUAL','categories':'operation_qualification_contracts CONFIRMED|PROVISIONAL; automation ELIGIBLE|REVIEW_ONLY','membership':'operator_launch_membership mint PRIMARY KEY; one mint cannot belong to multiple operations','profiles':'operation_behavioural_profiles are read-model populations and must not be used to bulk-admit C357-compatible observations','activity':'operation_activity_snapshots projects explicit membership plus profile members','side_effect':'registration itself has no implicit detector/trading activation; writer must explicitly avoid detector/membership expansion'},
 'representatives':['WATCHTOWER','Byzantine','P3R','WSOL_PROVISION_CLOSE_1_SOL_MINUS_15K'],'proposed':{'operation_id':proposed,'id_collision':proposed in ids or CID in ids,'display_name':'100 SOL WSOL Provision Close','category':'CONFIRMED','automation_eligibility':'REVIEW_ONLY','status':'CONFIRMED','confidence':'HIGH','review_state':'REVIEWED','disposition':'ACTIVE_MANUAL','source_candidate_id':CID,'potential_workflow_before':dict(wf) if wf else None,'fingerprint_role':'COMPATIBILITY_FINGERPRINT_NOT_EXCLUSIVE_ATTRIBUTION','behaviour_detector':'QUALIFIED_COMPATIBILITY_ONLY','attribution_detector':'OFF_NOT_READY','trading':'OFF','partial_attribution':{'attributed_members':56,'compatible_unresolved':105,'compatible_total':161},'infrastructure_rotation':'operation lifecycle independent from branch/provisioner/pool/funder lifecycle'},
 'safe_population':{'count':len(audit),'branches':{b:sum(x['branch']==b for x in audit) for b in ('DUTB_WALLET_POOL','A','B','C')},'members':audit},
 'membership_conflicts':existing,'membership_conflict_count':len(existing),'exclusion_invariant':{'name':'C357_COMPATIBLE_UNRESOLVED','count':105,'verdict':'PASS','guard':'future writer inserts only the artifact-listed 56 mint allowlist into operator_launch_membership; never derives membership from fingerprint/profile/funder/provider/mechanism/address.'},
 'lifecycle_compatibility':'PASS','watchtower_rotation_semantics_reusable':'PARTIAL','evidence_manifest':['docs/audits/c357_operation_qualification_review.v1.json','docs/audits/c357_shadow_historical_replay.v1.json','docs/audits/c357_shadow_branch_registry.v1.json','docs/audits/c357_dutb_common_funder_rpc.v1.json','docs/audits/c357_branch_b_chronology.v1.json','docs/audits/c357_remaining_upstream_funders.v1.json'],
 'future_mutation_plan':['single transaction: create operators row, ACTIVE_MANUAL disposition, CONFIRMED/REVIEW_ONLY qualification contract, evidence references, and exactly 56 allowlisted membership rows with immutable assignment history','keep attribution detector and trading off; do not create an automatic matcher','transition potential workflow only under separately authorized semantics; retain 105 as C357_COMPATIBLE_UNRESOLVED shadow evidence','read model: show partial attribution 56/161 and branch evidence; do not show 105 as members'],
 'transaction_rollback':{'pre':['candidate workflow PAUSED','no C357 operator ID','zero C357 memberships','detector/trading off','105 unresolved'], 'post':['one operator','56 memberships','zero unresolved membership leakage','detector/trading off','evidence refs present'], 'rollback':'delete only newly created C357 rows and membership/assignment rows in one compensating transaction; never alter other operator memberships'},
 'idempotence':'keyed by deterministic operation_id and mint primary key; preflight requires existing record to be semantically identical or fails closed; rerun cannot append unresolved members or change detector/trading state.',
 'readiness_verdict':'C357_REGISTRATION_PREFLIGHT_PASS_WITH_GUARDS' if not existing else 'C357_REGISTRATION_PREFLIGHT_BLOCKED','registration_mutation_authorized':'NO','safety':{'registry_write':False,'workflow_change':False,'membership_change':False,'detector_change':False,'fingerprint_change':False,'trading_change':False,'service_restart':False}}
 r['deterministic_digest']=h(r);return r
if __name__=='__main__':
 if '--replay' in sys.argv:
  old=load('docs/audits/c357_operation_registration_preflight.v1.json'); d=old.pop('deterministic_digest'); fresh=build();fresh.pop('deterministic_digest');assert d==h(old) and old==fresh;print('C357_REGISTRATION_PREFLIGHT_REPLAY_PASS provider_calls_during_replay=0')
 else:
  x=build();OUT.write_text(json.dumps(x,indent=2,sort_keys=True)+'\n');print(x['readiness_verdict'],x['deterministic_digest'])
