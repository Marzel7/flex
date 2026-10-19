"""One bounded, review-only Living Potential Operation bridge for WSOL."""
from __future__ import annotations
import hashlib,json,sqlite3,time,uuid
from collections import Counter
from src.utils.db_locking import db_connect
from src.ops.generic_living_active_components import generic_dispatch_enabled,publish_generic_assessment_atomic,classify_relevance
from src.ops.generic_living_persisted_source_reader import read_generic_living_source_context
from src.ops.generic_living_reverse_resolver import LivingCandidateReverseIndex
from src.ops.generic_living_pipeline_v2 import compute_generic_living_assessment

WSOL_POTENTIAL_OPERATION_ID='potential-wsol-provision-close-100-sol-minus-15k'
WSOL_CANDIDATE_ID='p3r-v2-c357da9d0d4d560311e4'; WSOL_RUN='p3r-v2-2dec1d40604c1f7c08c8'; WSOL_AMOUNT=99_999_985_000
TRANSFER_POTENTIAL_OPERATION_ID='potential-eight-hop-plain-transfer-sequence'
TRANSFER_CANDIDATE_ID='p3r-v2-dc4953db7adb853337c4'
DDL='''
CREATE TABLE IF NOT EXISTS potential_operation_identity (potential_operation_id TEXT PRIMARY KEY,status TEXT NOT NULL,origin_json TEXT NOT NULL,created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS potential_operation_evidence_association (association_id TEXT PRIMARY KEY,potential_operation_id TEXT NOT NULL,evidence_key TEXT NOT NULL,evidence_type TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('INCLUDED','EXCLUDED','UNRESOLVED','CONTRADICTORY')),rationale_json TEXT NOT NULL,created_at INTEGER NOT NULL,UNIQUE(potential_operation_id,evidence_key,evidence_type,state));
CREATE TABLE IF NOT EXISTS potential_operation_assessment_version (assessment_id TEXT PRIMARY KEY,potential_operation_id TEXT NOT NULL,evidence_digest TEXT NOT NULL,freshness_key TEXT NOT NULL,payload_json TEXT NOT NULL,created_at INTEGER NOT NULL,UNIQUE(potential_operation_id,evidence_digest));
CREATE TABLE IF NOT EXISTS potential_operation_current (potential_operation_id TEXT PRIMARY KEY,assessment_id TEXT NOT NULL,freshness_key TEXT NOT NULL,updated_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS ix_poa_candidate ON potential_operation_assessment_version(potential_operation_id,created_at DESC);
CREATE INDEX IF NOT EXISTS ix_poea_reverse_lookup ON potential_operation_evidence_association(evidence_key,evidence_type,potential_operation_id);'''
def ensure_schema(c): c.executescript(DDL);c.commit()
def _digest(x): return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def _aid(*x): return str(uuid.uuid5(uuid.NAMESPACE_URL,':'.join(x)))

def _read(path):
 c=db_connect(path,timeout=30,row_factory=sqlite3.Row)
 try:
  m=[x[0] for x in c.execute('SELECT mint FROM p3r_v2_candidate_membership WHERE run_id=? AND candidate_id=? ORDER BY mint',(WSOL_RUN,WSOL_CANDIDATE_ID))]
  if not m: raise ValueError('WSOL canonical membership is absent')
  q=','.join('?'*len(m)); edges=[dict(x) for x in c.execute(f'SELECT rowid,* FROM wt_walkback_edge_candidates WHERE mint IN ({q}) ORDER BY mint,evidence_key',m)]; atomic=[dict(x) for x in c.execute(f'SELECT rowid,* FROM wt_walkback_atomic_flows WHERE mint IN ({q}) ORDER BY mint,evidence_key',m)]; queue=[dict(x) for x in c.execute(f'SELECT rowid,mint,funder_wallet,updated_at FROM wt_walkback_queue WHERE mint IN ({q}) ORDER BY mint',m)]
  h={n:c.execute(f'SELECT COALESCE(MAX(rowid),0) FROM {n}').fetchone()[0] for n in ('wt_walkback_queue','wt_walkback_edge_candidates','wt_walkback_atomic_flows')}
  return {'mints':m,'edges':edges,'atomic':atomic,'queue':queue,'mapped':c.execute('SELECT COUNT(*) FROM p3r_v2_candidate_membership WHERE candidate_id=?',(WSOL_CANDIDATE_ID,)).fetchone()[0],'high':h,'generation':':'.join(f'{h[n]:012d}' for n in h)}
 finally:c.close()

def _assocs(e):
 out=[(f'mint:{m}','FROZEN_C357_SELECTED_MEMBER','INCLUDED',{'lineage':'canonical selected-edge cohort'}) for m in e['mints']]
 out += [(f'funder:{f}','DIRECT_FUNDER_CONNECTIVITY','UNRESOLVED',{'common_control_proven':False,'meaning':'funding connectivity only'}) for f in sorted({x['funder_wallet'] for x in e['queue'] if x['funder_wallet']})]
 out += [
  ('population:p3r-v2-mapped','MAPPED_CANDIDATE_POPULATION','UNRESOLVED',{'count':e['mapped'],'not_membership':True}),
  ('population:compatible-unattributed','COMPATIBLE_LAUNCH_SET','EXCLUDED',{'reason':'compatible behaviour is not automatic attribution'}),
  ('population:alternative-edge','ALTERNATIVE_EDGE_SUPPORT','UNRESOLVED',{'count':len({x['mint'] for x in e['edges'] if x['selection_status']=='ALTERNATIVE'})}),
  ('population:atomic-flow','ATOMIC_FLOW_SUPPORT','INCLUDED',{'count':len(set(x['mint'] for x in e['atomic']))}),
  ('caveat:false-positives','FALSE_POSITIVE_COMPARISON','CONTRADICTORY',{'count':5,'histories_rpc_audited':False}),
  ('caveat:predecessor-window','HISTORY_SELECTION_LIMITATION','UNRESOLVED',{'bounded_window':True}),
 ]
 return out

def _payload(e):
 edges=e['edges']; selected={x['mint'] for x in edges if x['selection_status']=='SELECTED'}; alternative={x['mint'] for x in edges if x['selection_status']=='ALTERNATIVE'}; atomic={x['mint'] for x in e['atomic']}; funders={x['funder_wallet'] for x in e['queue'] if x['funder_wallet']}
 return {'schema_version':'living_potential_operation.wsol.v1','potential_operation_id':WSOL_POTENTIAL_OPERATION_ID,'candidate_lineage':{'candidate_id':WSOL_CANDIDATE_ID,'canonical_run_id':WSOL_RUN},'status':'PAUSED','promotion':'NO','detector_activation':'NO','registered_membership_change':'NO','attribution':{'conclusion':'INDEPENDENT_FUNDER_INFRASTRUCTURE','common_controller_proven':False,'direct_transfers_mean':'funding connectivity, not common control'},'typed_populations':{'frozen_canonical_c357_cohort':{'count':len(e['mints']),'source':'canonical P3R-v2 run'},'frozen_distinct_direct_funders':{'count':len(funders),'source':'wt_walkback_queue'},'current_persisted_p3r_v2_mapped_candidate_population':{'count':e['mapped'],'source':'all persisted P3R-v2 runs','not_proven_membership':True},'current_selected_edge_members':{'count':len(selected),'source':'wt_walkback_edge_candidates'},'current_alternative_edge_members':{'count':len(alternative),'source':'wt_walkback_edge_candidates','state':'UNRESOLVED'},'current_atomic_flow_members':{'count':len(atomic),'source':'wt_walkback_atomic_flows'},'snapshot_v2_matched_routes':{'count':99,'source':'retained historical snapshot'},'remaining_launch_linked_upstreams_resolved':{'count':69,'denominator':69,'source':'retained historical snapshot'},'ui_rendered_upstream_mappings':{'count':50,'source':'retained historical UI projection'}},'caveats':{'retained_false_positive_comparison_cases':5,'false_positive_histories_rpc_audited':False,'immediate_predecessor_history':'bounded-window limitation','direct_funder_overlap':'Deri1SyKp2GKERY8nu2hddGLmA4Yr1dPWzqweStDyTaB'},'source_high_waters':e['high'],'evidence_generation':e['generation']}

def bootstrap_wsol_potential_operation(path):
 t=time.perf_counter(); e=_read(path); read=time.perf_counter()-t; t=time.perf_counter(); p=_payload(e);compute=time.perf_counter()-t; a=_assocs(e); now=int(time.time()); c=db_connect(path,timeout=30);ensure_schema(c)
 try:
  c.execute('BEGIN'); origin={'display_name':'WSOL Provision Close · 100 SOL minus 15K','candidate_id':WSOL_CANDIDATE_ID,'canonical_run_id':WSOL_RUN,'stable_identity_contract':'independent of family hash, snapshot, assessment and clustering','registered_operation':False};c.execute('INSERT OR IGNORE INTO potential_operation_identity VALUES(?,?,?,?)',(WSOL_POTENTIAL_OPERATION_ID,'PAUSED',json.dumps(origin,sort_keys=True),now))
  for key,typ,state,rationale in a:c.execute('INSERT OR IGNORE INTO potential_operation_evidence_association VALUES(?,?,?,?,?,?,?)',(_aid(WSOL_POTENTIAL_OPERATION_ID,typ,key,state),WSOL_POTENTIAL_OPERATION_ID,key,typ,state,json.dumps(rationale,sort_keys=True),now))
  c.commit()
 finally:c.close()
 d=_digest(p); assessment_id=_aid(WSOL_POTENTIAL_OPERATION_ID,d); t=time.perf_counter(); c=db_connect(path,timeout=30)
 try:
  c.execute('BEGIN');c.execute('INSERT OR IGNORE INTO potential_operation_assessment_version VALUES(?,?,?,?,?,?)',(assessment_id,WSOL_POTENTIAL_OPERATION_ID,d,e['generation'],json.dumps(p,sort_keys=True),now));old=c.execute('SELECT assessment_id,freshness_key FROM potential_operation_current WHERE potential_operation_id=?',(WSOL_POTENTIAL_OPERATION_ID,)).fetchone()
  if old and old[1]==e['generation'] and old[0]!=assessment_id:raise ValueError('equal freshness conflict')
  advanced=not old or e['generation']>old[1]
  if advanced:c.execute('INSERT INTO potential_operation_current VALUES(?,?,?,?) ON CONFLICT(potential_operation_id) DO UPDATE SET assessment_id=excluded.assessment_id,freshness_key=excluded.freshness_key,updated_at=excluded.updated_at',(WSOL_POTENTIAL_OPERATION_ID,assessment_id,e['generation'],now))
  c.commit()
 finally:c.close()
 return {'assessment_id':assessment_id,'digest':d,'evidence_generation':e['generation'],'advanced_current':advanced,'idempotent':bool(old and old[0]==assessment_id),'read_seconds':read,'compute_seconds':compute,'write_seconds':time.perf_counter()-t,'typed_populations':p['typed_populations'],'associations':dict(Counter(x[2] for x in a)),'payload':p}

def bootstrap_transfer_potential_operation(path):
 """Generic second family: 8-hop PLAIN_XFER, no WSOL assumptions."""
 c=db_connect(path,timeout=30,row_factory=sqlite3.Row)
 try:
  m=[x[0] for x in c.execute('SELECT mint FROM p3r_v2_candidate_membership WHERE run_id=? AND candidate_id=? ORDER BY mint',(WSOL_RUN,TRANSFER_CANDIDATE_ID))];q=','.join('?'*len(m));edges=[dict(x) for x in c.execute(f'SELECT rowid,* FROM wt_walkback_edge_candidates WHERE mint IN ({q})',m)];atomic=[dict(x) for x in c.execute(f'SELECT rowid,* FROM wt_walkback_atomic_flows WHERE mint IN ({q})',m)];queue=[dict(x) for x in c.execute(f'SELECT rowid,mint,funder_wallet FROM wt_walkback_queue WHERE mint IN ({q})',m)];high={n:c.execute(f'SELECT COALESCE(MAX(rowid),0) FROM {n}').fetchone()[0] for n in ('wt_walkback_queue','wt_walkback_edge_candidates','wt_walkback_atomic_flows')};mapped=c.execute('SELECT COUNT(*) FROM p3r_v2_candidate_membership WHERE candidate_id=?',(TRANSFER_CANDIDATE_ID,)).fetchone()[0]
 finally:c.close()
 gen=':'.join(f'{high[n]:012d}' for n in high); selected={x['mint'] for x in edges if x['selection_status']=='SELECTED'};alt={x['mint'] for x in edges if x['selection_status']=='ALTERNATIVE'};funders={x['funder_wallet'] for x in queue if x['funder_wallet']};payload={'schema_version':'living_potential_operation.generic.v1','potential_operation_id':TRANSFER_POTENTIAL_OPERATION_ID,'candidate_lineage':{'candidate_id':TRANSFER_CANDIDATE_ID,'canonical_run_id':WSOL_RUN},'status':'PAUSED','promotion':'NO','detector_activation':'NO','registered_membership_change':'NO','typed_populations':{'mapped_population':{'count':mapped,'not_proven_membership':True},'selected_edge_members':{'count':len(selected)},'alternative_edge_members':{'count':len(alt)},'atomic_flow_members':{'count':len({x['mint'] for x in atomic})},'distinct_creators':{'count':len(m)},'distinct_direct_funders':{'count':len(funders)},'route_shape':{'value':'8-hop PLAIN_XFER'}},'caveats':{'attribution':'PAUSED review-only; route recurrence does not prove common control'},'source_high_waters':high,'evidence_generation':gen};a=[(f'mint:{x}','CANONICAL_SELECTED_MEMBER','INCLUDED',{}) for x in m]+[(f'funder:{x}','DIRECT_FUNDER','UNRESOLVED',{}) for x in funders]+[('population:mapped','MAPPED_POPULATION','UNRESOLVED',{'count':mapped}),('population:alternative','ALTERNATIVE_SUPPORT','UNRESOLVED',{'count':len(alt)}),('caveat:attribution','ATTRIBUTION_LIMIT','CONTRADICTORY',{})]
 now=int(time.time());c=db_connect(path,timeout=30);ensure_schema(c);c.execute('BEGIN');c.execute('INSERT OR IGNORE INTO potential_operation_identity VALUES(?,?,?,?)',(TRANSFER_POTENTIAL_OPERATION_ID,'PAUSED',json.dumps({'display_name':'8-hop Plain Transfer Sequence','candidate_id':TRANSFER_CANDIDATE_ID,'registered_operation':False},sort_keys=True),now));[c.execute('INSERT OR IGNORE INTO potential_operation_evidence_association VALUES(?,?,?,?,?,?,?)',(_aid(TRANSFER_POTENTIAL_OPERATION_ID,t,k,s),TRANSFER_POTENTIAL_OPERATION_ID,k,t,s,json.dumps(r,sort_keys=True),now)) for k,t,s,r in a];c.commit();d=_digest(payload);aid=_aid(TRANSFER_POTENTIAL_OPERATION_ID,d);c.execute('BEGIN');c.execute('INSERT OR IGNORE INTO potential_operation_assessment_version VALUES(?,?,?,?,?,?)',(aid,TRANSFER_POTENTIAL_OPERATION_ID,d,gen,json.dumps(payload,sort_keys=True),now));old=c.execute('SELECT assessment_id,freshness_key FROM potential_operation_current WHERE potential_operation_id=?',(TRANSFER_POTENTIAL_OPERATION_ID,)).fetchone();advanced=not old or gen>old[1];
 if advanced:c.execute('INSERT INTO potential_operation_current VALUES(?,?,?,?) ON CONFLICT(potential_operation_id) DO UPDATE SET assessment_id=excluded.assessment_id,freshness_key=excluded.freshness_key,updated_at=excluded.updated_at',(TRANSFER_POTENTIAL_OPERATION_ID,aid,gen,now))
 c.commit();c.close();return {'assessment_id':aid,'digest':d,'evidence_generation':gen,'advanced_current':advanced,'idempotent':bool(old and old[0]==aid),'typed_populations':payload['typed_populations'],'associations':dict(Counter(x[2] for x in a))}

def resolve_affected_potential_operations(path,*,mint=None,funder=None,mechanism=None,amount_lamports=None):
 keys=[]
 if mint:keys.append((f'mint:{mint}',None))
 if funder:keys.append((f'funder:{funder}','DIRECT_FUNDER_CONNECTIVITY'))
 if mechanism=='WSOL_WRAP_CLOSE' and amount_lamports==WSOL_AMOUNT:keys.append(('population:atomic-flow','ATOMIC_FLOW_SUPPORT'))
 if not keys:return []
 c=db_connect(path,timeout=30)
 try:return sorted({r[0] for k,t in keys for r in c.execute('SELECT potential_operation_id FROM potential_operation_evidence_association WHERE evidence_key=?'+(' AND evidence_type=?' if t else ''),(k,) if not t else (k,t))})
 finally:c.close()
def _generic_living_registry():
 cfg={'run_id':WSOL_RUN,'highwater_tables':{'queue':'wt_walkback_queue','edges':'wt_walkback_edge_candidates','atomic':'wt_walkback_atomic_flows'},'evidence_tables':{'queue':'wt_walkback_queue','edges':'wt_walkback_edge_candidates','atomic':'wt_walkback_atomic_flows'},'funder_column':{'queue':'funder_wallet'}}
 return {x['source_candidate_id']:x for x in ({'potential_operation_id':WSOL_POTENTIAL_OPERATION_ID,'source_candidate_id':WSOL_CANDIDATE_ID,'workflow_status':'PAUSED','freshness_sources':['queue','edges','atomic'],'association_rules':[{'source':'members','prefix':'mint','type':'GENERIC_MEMBER','state':'INCLUDED'},{'source':'funders','prefix':'funder','type':'GENERIC_FUNDER','state':'UNRESOLVED'}],'metrics':{'members':{'source':'members','op':'distinct','label':'Members'},'funders':{'source':'funders','op':'distinct','label':'Funders'}},'persisted_source':cfg},{'potential_operation_id':TRANSFER_POTENTIAL_OPERATION_ID,'source_candidate_id':TRANSFER_CANDIDATE_ID,'workflow_status':'PAUSED','freshness_sources':['queue','edges','atomic'],'association_rules':[{'source':'members','prefix':'mint','type':'GENERIC_MEMBER','state':'INCLUDED'},{'source':'funders','prefix':'funder','type':'GENERIC_FUNDER','state':'UNRESOLVED'}],'metrics':{'members':{'source':'members','op':'distinct','label':'Members'},'funders':{'source':'funders','op':'distinct','label':'Funders'}},'persisted_source':cfg})}
def _generic_runtime(path,context,dry_run=False,test_failure_injector=None):
 registry=_generic_living_registry(); index=LivingCandidateReverseIndex.from_association_ledger(registry,path); resolved=index.resolve(context); out=[]
 for spec in registry.values():
  if spec['potential_operation_id'] not in resolved: continue
  c=db_connect(path,timeout=30,row_factory=sqlite3.Row); row=c.execute('SELECT freshness_key FROM potential_operation_current WHERE potential_operation_id=?',(spec['potential_operation_id'],)).fetchone(); c.close(); evidence=read_generic_living_source_context(spec,path,{**context,'current_generation':row[0] if row else None}); classification=classify_relevance(spec,context,lambda s,e:evidence); item={'potential_operation_id':spec['potential_operation_id'],'relevance':classification,'published':False}
  if classification=='RELEVANT_NEW_EVIDENCE':
   result=compute_generic_living_assessment(spec,evidence); associations=[{'potential_operation_id':spec['potential_operation_id'],'evidence_identity':a['evidence_key'],'evidence_type':a['evidence_type'],'association_state':a['state'],'source_key':'derived','provenance':{}} for a in result['associations']]
   if not dry_run:
    c=db_connect(path,timeout=30)
    try: item.update(publish_generic_assessment_atomic(c,result,associations,created_at=int(time.time()),failure_injector=test_failure_injector))
    finally: c.close()
    item['published']=True
   item['evidence_generation']=result['evidence_generation']
  out.append(item)
 return {'dispatch_mode':'GENERIC','resolved_candidate_ids':resolved,'candidates':out}
def handle_walkback_evidence_update(path,**context):
 test_failure_injector=context.pop('test_failure_injector',None)
 if generic_dispatch_enabled(): return _generic_runtime(path,context,context.pop('dry_run',False),test_failure_injector)
 affected=resolve_affected_potential_operations(path,**{k:context.get(k) for k in ('mint','funder','mechanism','amount_lamports')});return {'affected_potential_operation_ids':affected,'recomputed':[bootstrap_wsol_potential_operation(path) if x==WSOL_POTENTIAL_OPERATION_ID else bootstrap_transfer_potential_operation(path) for x in affected if x in (WSOL_POTENTIAL_OPERATION_ID,TRANSFER_POTENTIAL_OPERATION_ID)],'automatic_global_integration':False}
def current_potential_operation(path,operation_id=WSOL_POTENTIAL_OPERATION_ID):
 operation_id=operation_id or WSOL_POTENTIAL_OPERATION_ID
 c=db_connect(path,timeout=30,row_factory=sqlite3.Row)
 try:
  r=c.execute('SELECT i.potential_operation_id,i.status,a.assessment_id,a.freshness_key,a.payload_json,a.created_at FROM potential_operation_identity i LEFT JOIN potential_operation_current x ON x.potential_operation_id=i.potential_operation_id LEFT JOIN potential_operation_assessment_version a ON a.assessment_id=x.assessment_id WHERE i.potential_operation_id=?',(operation_id,)).fetchone();return None if not r else {**dict(r),'payload':json.loads(r['payload_json']) if r['payload_json'] else None}
 finally:c.close()
def living_detail_projection(path,operation_id=WSOL_POTENTIAL_OPERATION_ID):
 """Read-only UI projection; never recomputes or invokes the walkback bridge."""
 c=db_connect(path,timeout=30,row_factory=sqlite3.Row)
 try:
  current=current_potential_operation(path,operation_id)
  if not current:return None
  states=dict(c.execute('SELECT state,count(*) FROM potential_operation_evidence_association WHERE potential_operation_id=? GROUP BY state',(operation_id,)))
  history=[dict(x) for x in c.execute('SELECT assessment_id,freshness_key,created_at FROM potential_operation_assessment_version WHERE potential_operation_id=? ORDER BY created_at',(operation_id,))]
  for x in history:x['current']=x['assessment_id']==current['assessment_id']
  return {'current':current,'association_counts':states,'history':history,'history_count':len(history),'global_automatic_walkback_updates':'NOT ENABLED'}
 finally:c.close()
def recompute_potential_operation(path,potential_operation_id,trigger_context=None,inject_failure=None):
 if potential_operation_id!=WSOL_POTENTIAL_OPERATION_ID:raise ValueError('only WSOL is authorized for real recomputation')
 if inject_failure:raise RuntimeError('injected')
 return bootstrap_wsol_potential_operation(path)
