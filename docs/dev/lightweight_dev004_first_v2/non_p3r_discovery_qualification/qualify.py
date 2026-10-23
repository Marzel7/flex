from pathlib import Path
exec(compile(Path('/private/tmp/dev004-schema-bootstrap-child.py').read_text().split('results=[]')[0],'isolation','exec'))
from src.ops import treasury_topology_classifier as topology,provisioning_edges
from src.ops.emerging_operator_service import EmergingOperatorService
checks={};details={}
def check(k,v):
 checks[k]=bool(v)
 if not v:raise AssertionError(k)
def insert(c,t,values):
 names=list(values);c.execute('INSERT OR IGNORE INTO '+t+'('+','.join(names)+') VALUES('+','.join('?' for _ in names)+')',[values[n] for n in names])
def openops():
 c=sqlite3.connect(str(out/'ops.sqlite'));c.row_factory=sqlite3.Row;return c
try:
 c=openops();provisioning_edges.ensure_schema(c)
 for i in range(5):
  mint=f'dev004-new-mint-{i}';sub=f'dev004-sub-{i}';creator=f'dev004-creator-{i}'
  provisioning_edges.capture_provisioning_relationship(c,source_mint=mint,treasury='dev004-new-root',subprov=sub,creator=creator,treasury_to_subprov_sig=f'fund-{i}',treasury_to_subprov_block_time=100+i,treasury_to_subprov_amount_sol=1.1,treasury_to_subprov_mechanism='PLAIN_XFER',subprov_to_creator_sig=f'wrap-{i}',subprov_to_creator_block_time=120+i,subprov_to_creator_amount_sol=1.,subprov_to_creator_mechanism='WSOL_WRAP_CLOSE',creator_launch_time=140+i)
  insert(c,'wt_walkback_queue',dict(mint=mint,creator=creator,status='complete',create_anchor_signature=f'create-{i}',create_anchor_audit_state='VALID'))
  insert(c,'wt_walkback_transaction_roles',dict(evidence_key=f'role-{i}',projection_version='synthetic-provider-free',mint=mint,signature=f'wrap-{i}',signers_json=json.dumps([sub,f'fanout-{i}']),outer_shape_json=json.dumps([{'type':x} for x in ['createAccountWithSeed','initializeAccount','closeAccount']]),inner_shape_json='[]',route_semantics='WSOL_WRAP_CLOSE',provenance_digest=f'synthetic-retained-{i}',first_observed_at=150,last_observed_at=150))
  insert(c,'wt_walkback_edge_candidates',dict(evidence_key=f'edge-{i}',mint=mint,wallet=sub,candidate_parent='dev004-new-root',signature=f'fund-{i}',block_time=100+i,amount_lamports=1100000000,mechanism='PLAIN_XFER',hop_depth=2,evidence_strength='STRONG',selection_status='SELECTED',first_observed_at=150,last_observed_at=150))
 c.commit()
 result=topology.surface_runtime_topology_candidate(c,'dev004-new-root',infrastructure_check=lambda w:False,now=200);c.commit();details['admission']=result;check('actual_owner_creates_review',result['action']=='inserted')
 review=dict(c.execute('select * from wt_treasury_review where treasury=?',('dev004-new-root',)).fetchone());details['review']=review;check('pending_review_not_confirmed',review['status']=='PENDING_REVIEW');check('provenance_preserved',len(json.loads(review['evidence_sigs']))==5)
 # Exact upstream writer and accepted topology/readers, not a hand-written review hint.
 service=EmergingOperatorService(str(out/'ops.sqlite'),str(out/'core.sqlite'))
 profiles=service._discovery_profiles(c,service._tables(c));check('x784_actual_profile',len(profiles['dev004-new-root']['launches'])==5)
 payload=service.list(debug=True);details['payload_keys']=list(payload);families=service._compose();matches=[f for f in families if 'wt_treasury_review_operational_like' in f.get('evidence_sources',[])];details['projected']=matches
 check('actual_projection_published',len(matches)==1);family=matches[0];check('review_only',family.get('projection_scope')=='DISCOVERY_ONLY' and family.get('registry_eligible') is False);identity=family['family_id']
 details['publication_cards']=payload.get('potential_operations_reconciled');check('public_list_publication',any(f.get('family_id')==identity for f in payload.get('potential_operations_reconciled',[])))
 c.close();c=openops();again=topology.surface_runtime_topology_candidate(c,'dev004-new-root',infrastructure_check=lambda w:False,now=201);c.commit();check('duplicate_single_review',c.execute('select count(*) from wt_treasury_review where treasury=?',('dev004-new-root',)).fetchone()[0]==1)
 reopened=EmergingOperatorService(str(out/'ops.sqlite'),str(out/'core.sqlite')).list(debug=True);check('restart_same_identity',any(f.get('family_id')==identity for f in reopened.get('potential_operations_reconciled',[])))
 # Alternative parent evidence remains retained without inflating selected membership.
 insert(c,'wt_walkback_edge_candidates',dict(evidence_key='competing-parent-edge',mint='dev004-new-mint-0',wallet='dev004-sub-0',candidate_parent='dev004-alternative-root',signature='alternative-funding-signature',block_time=99,amount_lamports=1100000000,mechanism='PLAIN_XFER',hop_depth=2,evidence_strength='STRONG',selection_status='ALTERNATIVE',rejection_reason='synthetic competing parent',first_observed_at=150,last_observed_at=150));c.commit()
 altprofiles=EmergingOperatorService(str(out/'ops.sqlite'),str(out/'core.sqlite'))._discovery_profiles(c,service._tables(c))
 check('alternative_parent_not_selected_member','dev004-alternative-root' not in altprofiles)
 check('competing_evidence_retained',c.execute("select selection_status,rejection_reason from wt_walkback_edge_candidates where evidence_key='competing-parent-edge'").fetchone()[0]=='ALTERNATIVE')
 provisioning_edges.capture_provisioning_relationship(c,source_mint='dev004-new-mint-0',treasury='dev004-new-root',subprov='dev004-sub-0',creator='dev004-creator-0',treasury_to_subprov_sig='fund-0',treasury_to_subprov_block_time=100,treasury_to_subprov_amount_sol=1.1,treasury_to_subprov_mechanism='PLAIN_XFER',subprov_to_creator_sig='wrap-0',subprov_to_creator_block_time=120,subprov_to_creator_amount_sol=1.,subprov_to_creator_mechanism='WSOL_WRAP_CLOSE',creator_launch_time=140);c.commit()
 check('duplicate_evidence_no_extra_edges',c.execute('select count(*) from wt_provisioning_edges').fetchone()[0]==10)
 check('duplicate_evidence_no_extra_sessions',c.execute('select count(*) from wt_provisioning_sessions').fetchone()[0]==5)
 repeat=EmergingOperatorService(str(out/'ops.sqlite'),str(out/'core.sqlite')).list(debug=True)
 check('idempotent_review_publication',len([f for f in repeat.get('potential_operations_reconciled',[]) if f.get('family_id')==identity])==1)
 details['whole_population_plan']=[list(x) for x in c.execute("explain query plan select distinct candidate_parent,mint from wt_walkback_edge_candidates where selection_status='SELECTED' and candidate_parent is not null and mint is not null")]
 check('insufficient_evidence',topology.surface_runtime_topology_candidate(c,'dev004-empty-root',infrastructure_check=lambda w:False)['action']=='not_qualified')
 check('infrastructure_exclusion',topology.classify_unknown_treasury(c,'dev004-new-root',infrastructure_check=lambda w:True)['reason']=='known_infrastructure')
 c.execute("update wt_treasury_review set status='REJECTED' where treasury='dev004-new-root'");c.commit();check('rejection_preserved',topology.surface_runtime_topology_candidate(c,'dev004-new-root',infrastructure_check=lambda w:False)['action']=='skipped_reviewed')
 check('rejected_not_x784_profile','wt_treasury_review_operational_like' not in EmergingOperatorService(str(out/'ops.sqlite'),str(out/'core.sqlite'))._discovery_profiles(c,service._tables(c)).get('dev004-new-root',{}).get('sources',set()))
 # Known treasury separation is the accepted pre-classification exclusion.
 insert(c,'wt_confirmed_treasuries',dict(treasury='dev004-new-root',method='SYNTHETIC_TEST_AUTHORITY',confidence='CONFIRMED'));c.commit();check('known_treasury_separation',topology.classify_unknown_treasury(c,'dev004-new-root',infrastructure_check=lambda w:False)['reason']=='already_confirmed')
 details['plans']=[list(x) for x in c.execute("explain query plan select mint from wt_walkback_edge_candidates where candidate_parent=? and selection_status='SELECTED'",('dev004-new-root',))]
 c.close();status='PARTIAL_FUNCTIONAL_CONTROLS_PASS_BOUNDS_PENDING'
except BaseException as exc:
 status='HOLD';details['error']=str(exc);details['traceback']=traceback.format_exc(limit=7)
result={'status':status,'checks':checks,'details':details,'blocked':blocked,'database_attempts':db_attempts,'sizes':size()}
(out/'result.json').write_text(json.dumps(result,indent=2,default=lambda x:sorted(x) if isinstance(x,set) else str(x))+'\n');print(json.dumps({'status':status,'checks':checks,'error':details.get('error'),'sizes':size()}))
